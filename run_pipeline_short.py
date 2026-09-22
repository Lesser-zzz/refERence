import os
import csv
import time
import json
import requests
from collections import deque
from urllib.parse import quote
import pandas as pd

# ==========================================
# ⚙️ 1. 환경 설정 및 타이머 (디버깅 모드)
# ==========================================
START_TIME = time.time()
MAX_EXECUTION_TIME = 0.5 * 3600  # 30분 제한
API_KEY = os.environ.get("BSER_API_KEY")

print(f"🔑 [디버그] 로드된 API_KEY 확인: {'설정됨 (길이: ' + str(len(API_KEY)) + ')' if API_KEY else '없음(None)'}")

HEADERS = {"x-api-key": API_KEY, "accept": "application/json"}
CSV_DATASET = "reference_dataset.csv"
MAPPING_CSV = "er_master_mapping.csv"
PENDING_FILE = "snowball_pending_add.txt"
PROCESSED_FILE = "snowball_processed.txt"
TOP_ROUTES_FILE = "top_reference_routes.csv"

SEASON_ID = 41
MATCHING_MODE = 3
MAX_LOOPS = 2000       
RECENT_GAME_LIMIT = 10  
REQUEST_INTERVAL = 1.1

def load_lines(filename):
    if not os.path.exists(filename): return set()
    with open(filename, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def append_line(filename, value):
    with open(filename, "a", encoding="utf-8") as f:
        f.write(f"{value}\n")

# ==========================================
# 🚀 2. 데이터 수집 (디버깅 로그 포함)
# ==========================================
print("▶️ [1단계] 데이터 수집 시작 (Debug Short Mode)...")
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
    print(f"🌐 [디버그] 랭크 탑 리스트 요청 URL: {rank_url}")
    res_rank = requests.get(rank_url, headers=HEADERS)
    print(f"🌐 [디버그] 랭크 API 응답 코드: {res_rank.status_code}")
    
    if res_rank.status_code == 200:
        rank_data = res_rank.json()
        top_ranks = rank_data.get("topRanks", [])
        print(f"🌐 [디버그] 가져온 랭커 수: {len(top_ranks)}")
        for p in top_ranks[:30]: 
            nick = p.get("nickname")
            if nick and nick not in processed_nicknames:
                queue.append(nick)
                append_line(PENDING_FILE, nick)
    else:
        print(f"❌ [에러] 랭크 정보를 불러오지 못했습니다. 응답 내용: {res_rank.text}")

loop_count = 0
last_req = 0.0

while queue and loop_count < MAX_LOOPS:
    if time.time() - START_TIME > MAX_EXECUTION_TIME:
        print("⏱️ 30분 제한 시간에 도달하여 수집 루프를 안전하게 종료합니다.")
        break

    nickname = queue.popleft()
    if nickname in processed_nicknames: continue
    loop_count += 1
    
    elapsed = time.time() - last_req
    if elapsed < REQUEST_INTERVAL: time.sleep(REQUEST_INTERVAL - elapsed)
    
    try:
        user_url = f"https://open-api.bser.io/v1/user/nickname?query={quote(nickname)}"
        res_user = requests.get(user_url, headers=HEADERS, timeout=10)
        last_req = time.time()
        
        if res_user.status_code == 200:
            user_json = res_user.json()
            if "user" in user_json:
                uid = user_json["user"].get("userNum")
                time.sleep(REQUEST_INTERVAL)
                
                games_url = f"https://open-api.bser.io/v1/user/games/uid/{uid}"
                res_games = requests.get(games_url, headers=HEADERS)
                last_req = time.time()
                
                if res_games.status_code == 200:
                    games_json = res_games.json()
                    user_games = games_json.get("userGames", [])
                    
                    for game in user_games[:RECENT_GAME_LIMIT]:
                        gid = game.get("gameId")
                        if not gid or gid in processed_game_ids: continue
                        
                        time.sleep(REQUEST_INTERVAL)
                        detail_url = f"https://open-api.bser.io/v1/games/{gid}"
                        res_detail = requests.get(detail_url, headers=HEADERS)
                        last_req = time.time()
                        
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
        else:
            print(f"⚠️ [디버그] 유저 검색 실패 ({nickname}): 상태 코드 {res_user.status_code}")
                    
    except Exception as e:
        print(f"⚠️ 에러 발생: {e}")
        time.sleep(5)
        
    processed_nicknames.add(nickname)
    append_line(PROCESSED_FILE, nickname)

print(f"✅ 수집 종료. 현재 누적 게임 수: {len(processed_game_ids)}")

# ==========================================
# 📊 3. 최적 루트 분석 시작
# ==========================================
print("▶️ [2단계] 최적 루트 분석 시작...")
if os.path.exists(CSV_DATASET) and os.path.exists(MAPPING_CSV):
    df_game = pd.read_csv(CSV_DATASET)
    df_map = pd.read_csv(MAPPING_CSV)

    if df_game.empty or len(df_game.columns) < 7:
        print("⚠️ 수집된 데이터가 비어있어 분석을 건너뜁니다.")
    else:
        valid_df = df_game[(df_game['routeId'] > 0) & (df_game['mmrBefore'] >= 7600)].copy()
        
        if valid_df.empty:
            print("⚠️ 수집된 미스릴+(7600점 이상) 데이터가 아직 부족합니다.")
        else:
            valid_df['rp_plus'] = valid_df['mmrGain'] > 0

            route_stats = valid_df.groupby(['characterNum', 'bestWeapon', 'routeId']).agg(
                pick_count=('routeId', 'count'),
                avg_rp_gain=('mmrGain', 'mean')
            ).reset_index()

            global_avg_rp = route_stats['avg_rp_gain'].mean()
            m = 3 

            route_stats['reference_score'] = (
                (route_stats['pick_count'] * route_stats['avg_rp_gain']) + (m * global_avg_rp)
            ) / (route_stats['pick_count'] + m)

            route_stats.sort_values(by=['characterNum', 'bestWeapon', 'reference_score'], ascending=[True, True, False], inplace=True)
            top_routes = route_stats.drop_duplicates(subset=['characterNum', 'bestWeapon'], keep='first').copy()

            final_df = pd.merge(top_routes, df_map, left_on=['characterNum', 'bestWeapon'], right_on=['characterNum', 'weaponNum'], how='inner')
            
            if not final_df.empty:
                final_df['avg_rp_gain'] = pd.to_numeric(final_df['avg_rp_gain']).round(1).astype(str) + '점'
                final_df['reference_score'] = pd.to_numeric(final_df['reference_score']).round(2)
                
                final_cols = ['characterName', 'weaponName', 'routeId', 'pick_count', 'avg_rp_gain', 'reference_score']
                final_df.sort_values(by='reference_score', ascending=False, inplace=True)

                final_df[final_cols].to_csv(TOP_ROUTES_FILE, index=False, encoding='utf-8-sig')
                print(f"🎉 [refERence] 최종 분석 결과가 {TOP_ROUTES_FILE} 파일로 저장되었습니다.")
            else:
                print("⚠️ 분석할 매칭 데이터가 부족하여 결과 파일을 생성하지 못했습니다.")
