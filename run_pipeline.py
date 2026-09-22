import os
import csv
import time
import json
import requests
from collections import deque
from urllib.parse import quote
import pandas as pd

# ==========================================
# ⚙️ 1. 환경 설정 및 타이머 (GitHub Actions)
# ==========================================
START_TIME = time.time()
MAX_EXECUTION_TIME = 5.3 * 3600  # 5시간 18분 (액션 강제종료 방지)
API_KEY = os.environ.get("BSER_API_KEY")

HEADERS = {"x-api-key": API_KEY, "accept": "application/json"}
CSV_DATASET = "reference_dataset.csv"
MAPPING_CSV = "er_master_mapping.csv"
PENDING_FILE = "snowball_pending_add.txt"
PROCESSED_FILE = "snowball_processed.txt"
TOP_ROUTES_FILE = "top_reference_routes.csv"

SEASON_ID = 41
MATCHING_MODE = 3
MAX_LOOPS = 5000
REQUEST_INTERVAL = 1.1

def load_lines(filename):
    if not os.path.exists(filename): return set()
    with open(filename, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def append_line(filename, value):
    with open(filename, "a", encoding="utf-8") as f:
        f.write(f"{value}\n")

# ==========================================
# 🚀 2. 데이터 수집 (스노우볼 샘플링)
# ==========================================
print("▶️ [1단계] 데이터 수집 시작...")
processed_game_ids = set()
if os.path.exists(CSV_DATASET):
    with open(CSV_DATASET, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if row: processed_game_ids.add(int(row[0]))
else:
    with open(CSV_DATASET, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerow(["gameId", "characterNum", "bestWeapon", "routeId", "mmrBefore", "mmrGain", "gameRank"])

processed_nicknames = load_lines(PROCESSED_FILE)
pending_nicknames = load_lines(PENDING_FILE) - processed_nicknames
queue = deque(pending_nicknames)

# 시드 충전
if not queue:
    res_rank = requests.get(f"https://open-api.bser.io/v1/rank/top/{SEASON_ID}/{MATCHING_MODE}", headers=HEADERS).json()
    for p in res_rank.get("topRanks", [])[:50]:
        nick = p.get("nickname")
        if nick and nick not in processed_nicknames:
            queue.append(nick)
            append_line(PENDING_FILE, nick)

loop_count = 0
last_req = 0.0

while queue and loop_count < MAX_LOOPS:
    if time.time() - START_TIME > MAX_EXECUTION_TIME:
        print("⏱️ 안전 종료 시간 도달. 수집 루프를 중단합니다.")
        break

    nickname = queue.popleft()
    if nickname in processed_nicknames: continue
    loop_count += 1
    
    elapsed = time.time() - last_req
    if elapsed < REQUEST_INTERVAL: time.sleep(REQUEST_INTERVAL - elapsed)
    
    try:
        res_user = requests.get(f"https://open-api.bser.io/v1/user/nickname?query={quote(nickname)}", headers=HEADERS, timeout=10)
        last_req = time.time()
        
        if res_user.status_code == 200 and "user" in res_user.json():
            uid = res_user.json()["user"].get("userNum")
            time.sleep(REQUEST_INTERVAL)
            res_games = requests.get(f"https://open-api.bser.io/v1/user/games/uid/{uid}", headers=HEADERS).json()
            last_req = time.time()
            
            for game in res_games.get("userGames", [])[:20]:
                gid = game.get("gameId")
                if not gid or gid in processed_game_ids: continue
                
                time.sleep(REQUEST_INTERVAL)
                res_detail = requests.get(f"https://open-api.bser.io/v1/games/{gid}", headers=HEADERS).json()
                last_req = time.time()
                
                match_rows = []
                for p in res_detail.get("userGames", []):
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
                    
    except Exception as e:
        print(f"⚠️ 에러 발생: {e}")
        
    processed_nicknames.add(nickname)
    append_line(PROCESSED_FILE, nickname)

print(f"✅ 수집 종료. 현재 누적 게임 수: {len(processed_game_ids)}")

# ==========================================
# 📊 3. 최적 루트 추출 (베이지안 스코어)
# ==========================================
print("▶️ [2단계] 최적 루트 분석 시작...")
if os.path.exists(CSV_DATASET) and os.path.exists(MAPPING_CSV):
    df_game = pd.read_csv(CSV_DATASET)
    df_map = pd.read_csv(MAPPING_CSV)

    valid_df = df_game[(df_game['routeId'] > 0) & (df_game['mmrBefore'] >= 7600)].copy()
    valid_df['rp_plus'] = valid_df['mmrGain'] > 0

    route_stats = valid_df.groupby(['characterNum', 'bestWeapon', 'routeId']).agg(
        pick_count=('routeId', 'count'),
        avg_rp_gain=('mmrGain', 'mean')
    ).reset_index()

    global_avg_rp = route_stats['avg_rp_gain'].mean()
    m = 30 # 실전 신뢰도 최소 픽 수

    route_stats['reference_score'] = (
        (route_stats['pick_count'] * route_stats['avg_rp_gain']) + (m * global_avg_rp)
    ) / (route_stats['pick_count'] + m)

    route_stats.sort_values(by=['characterNum', 'bestWeapon', 'reference_score'], ascending=[True, True, False], inplace=True)
    top_routes = route_stats.drop_duplicates(subset=['characterNum', 'bestWeapon'], keep='first').copy()

    final_df = pd.merge(top_routes, df_map, left_on=['characterNum', 'bestWeapon'], right_on=['characterNum', 'weaponNum'], how='inner')
    
    final_df['avg_rp_gain'] = final_df['avg_rp_gain'].round(1).astype(str) + '점'
    final_df['reference_score'] = final_df['reference_score'].round(2)
    final_cols = ['characterName', 'weaponName', 'routeId', 'pick_count', 'avg_rp_gain', 'reference_score']
    final_df.sort_values(by='reference_score', ascending=False, inplace=True)

    # 결과물 저장
    final_df[final_cols].to_csv(TOP_ROUTES_FILE, index=False, encoding='utf-8-sig')
    print("🎉 [refERence] 최종 분석 결과가 top_reference_routes.csv 파일로 저장되었습니다.")
