import os
import csv
import time
import json
import requests
from collections import deque
from urllib.parse import quote
import pandas as pd

# ==========================================
# ⚙️ 1. 환경 설정 및 타이머 (전적 디버깅 모드)
# ==========================================
START_TIME = time.time()
MAX_EXECUTION_TIME = 0.5 * 3600  # 30분 제한
API_KEY = os.environ.get("BSER_API_KEY")

print(f"🔑 [디버그] API_KEY 길이: {len(API_KEY) if API_KEY else 0}")

HEADERS = {"x-api-key": API_KEY, "accept": "application/json"}
CSV_DATASET = "reference_dataset.csv"
MAPPING_CSV = "er_master_mapping.csv"
PENDING_FILE = "snowball_pending_add.txt"
PROCESSED_FILE = "snowball_processed.txt"
TOP_ROUTES_FILE = "top_reference_routes.csv"

SEASON_ID = 41
MATCHING_MODE = 3
MAX_LOOPS = 5        # 디버깅을 위해 루프를 딱 5번만 돌려보고 원인 파악
RECENT_GAME_LIMIT = 5  
REQUEST_INTERVAL = 1.1

def load_lines(filename):
    if not os.path.exists(filename): return set()
    with open(filename, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def append_line(filename, value):
    with open(filename, "a", encoding="utf-8") as f:
        f.write(f"{value}\n")

# ==========================================
# 🚀 2. 데이터 수집 (전적 상세 디버깅)
# ==========================================
print("▶️ [1단계] 데이터 수집 시작 (전적 디버그 모드)...")
processed_game_ids = set()

if os.path.exists(CSV_DATASET):
    with open(CSV_DATASET, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if row: 
                try:
                    processed_game_ids.add(int(row[0]))
                except ValueError:
                    continue
else:
    with open(CSV_DATASET, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerow(["gameId", "characterNum", "bestWeapon", "routeId", "mmrBefore", "mmrGain", "gameRank"])

processed_nicknames = load_lines(PROCESSED_FILE)
pending_nicknames = load_lines(PENDING_FILE) - processed_nicknames
queue = deque(pending_nicknames)

if not queue:
    rank_url = f"https://open-api.bser.io/v1/rank/top/{SEASON_ID}/{MATCHING_MODE}"
    res_rank = requests.get(rank_url, headers=HEADERS)
    if res_rank.status_code == 200:
        for p in res_rank.get("topRanks", [])[:20]: 
            nick = p.get("nickname")
            if nick and nick not in processed_nicknames:
                queue.append(nick)
                append_line(PENDING_FILE, nick)

loop_count = 0
last_req = 0.0

while queue and loop_count < MAX_LOOPS:
    nickname = queue.popleft()
    if nickname in processed_nicknames: continue
    loop_count += 1
    
    elapsed = time.time() - last_req
    if elapsed < REQUEST_INTERVAL: time.sleep(REQUEST_INTERVAL - elapsed)
    
    try:
        # 1. 닉네임으로 유저 번호 조회
        user_url = f"https://open-api.bser.io/v1/user/nickname?query={quote(nickname)}"
        res_user = requests.get(user_url, headers=HEADERS, timeout=10)
        last_req = time.time()
        
        print(f"🔍 [디버그] 유저 검색 ({nickname}) 응답 코드: {res_user.status_code}")
        if res_user.status_code == 200:
            user_json = res_user.json()
            if "user" in user_json:
                uid = user_json["user"].get("userNum")
                print(f"👤 [디버그] 닉네임 '{nickname}'의 userNum: {uid}")
                
                time.sleep(REQUEST_INTERVAL)
                # 2. 유저 전적 리스트 조회
                games_url = f"https://open-api.bser.io/v1/user/games/uid/{uid}"
                res_games = requests.get(games_url, headers=HEADERS)
                last_req = time.time()
                
                print(f"🎮 [디버그] 전적 API 응답 코드: {res_games.status_code}")
                if res_games.status_code == 200:
                    games_json = res_games.json()
                    user_games = games_json.get("userGames", [])
                    print(f"🎮 [디버그] 가져온 전적 게임 수: {len(user_games)}")
                    
                    if len(user_games) > 0:
                        print(f"📦 [디버그] 첫 번째 게임 샘플 데이터: {user_games[0]}")
                    
                    for game in user_games[:RECENT_GAME_LIMIT]:
                        gid = game.get("gameId")
                        if not gid or gid in processed_game_ids: continue
                        
                        time.sleep(REQUEST_INTERVAL)
                        detail_url = f"https://open-api.bser.io/v1/games/{gid}"
                        res_detail = requests.get(detail_url, headers=HEADERS)
                        last_req = time.time()
                        
                        print(f"📄 [디버그] 매치 상세({gid}) 응답 코드: {res_detail.status_code}")
                        if res_detail.status_code == 200:
                            detail_json = res_detail.json()
                            match_rows = []
                            for p in detail_json.get("userGames", []):
                                n_nick = p.get("nickname")
                                if n_nick and n_nick not in processed_nicknames and n_nick not in queue:
                                    queue.append(n_nick)
                                    append_line(PENDING_FILE, n_nick)
                                
                                match_rows.append([
                                    gid, p.get("characterNum", 0), p.get("bestWeapon", 0), 
                                    p.get("routeIdOfStart", p.get("routeId", 0)), 
                                    p.get("mmrBefore", 0), p.get("mmrGain", 0), p.get("gameRank", 0)
                                ])
                            
                            if match_rows:
                                with open(CSV_DATASET, "a", encoding="utf-8-sig", newline="") as f:
                                    csv.writer(f).writerows(match_rows)
                                processed_game_ids.add(gid)
                                print(f"✨ [성공] 매치 {gid} 데이터 수집 완료! (행 수: {len(match_rows)})")
                else:
                    print(f"❌ [에러] 전적 조회 실패 내용: {res_games.text}")
        else:
            print(f"❌ [에러] 유저 검색 실패 내용: {res_user.text}")
                    
    except Exception as e:
        print(f"⚠️ 예외 발생: {e}")
        
    processed_nicknames.add(nickname)
    append_line(PROCESSED_FILE, nickname)

print(f"✅ 디버그 수집 종료. 현재 누적 게임 수: {len(processed_game_ids)}")
