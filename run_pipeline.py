import os
import csv
import time
import json
import requests
import glob  # 💡 분할된 파일들을 한 번에 불러오기 위해 추가
from collections import deque
from urllib.parse import quote
import pandas as pd

# ==========================================
# ⚙️ 1. 환경 설정 및 타이머
# ==========================================
START_TIME = time.time()
MAX_EXECUTION_TIME = 5.5 * 3600
API_KEY = os.environ.get("BSER_API_KEY")
HEADERS = {"x-api-key": API_KEY, "accept": "application/json"}

CSV_DATASET = "reference_dataset.csv"
MAPPING_CSV = "er_master_mapping.csv"
PENDING_FILE = "snowball_pending_add.txt"
PROCESSED_FILE = "snowball_processed.txt"
TOP_ROUTES_FILE = "top_reference_routes.csv"

SEASON_ID = 41
MATCHING_MODE = 3
MAX_LOOPS = 50000
RECENT_GAME_LIMIT = 50
REQUEST_INTERVAL = 1.1

def load_lines(filename):
    if not os.path.exists(filename): return set()
    with open(filename, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def append_line(filename, value):
    with open(filename, "a", encoding="utf-8") as f:
        f.write(f"{value}\n")

# ==========================================
# 🚀 2. 데이터 수집 (무한 누적 + 분할 파일 통합)
# ==========================================
print("▶️ [1단계] 데이터 수집 시작 (Archive Integration Mode)...", flush=True)
processed_game_ids = set()

# 💡 핵심 1: 분할된 과거 데이터(till260927 등)와 현재 데이터를 모두 읽어와 중복 수집을 원천 차단합니다.
all_dataset_files = glob.glob("reference_dataset*.csv")
for file in all_dataset_files:
    with open(file, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if row:
                try:
                    processed_game_ids.add(int(row[0]))
                except ValueError:
                    continue

# 💡 핵심 2: 새로운 데이터는 오직 가장 가벼운 현재 파일(CSV_DATASET)에만 추가합니다.
if not os.path.exists(CSV_DATASET):
    with open(CSV_DATASET, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerow(["gameId", "characterNum", "bestWeapon", "routeId", "mmrBefore", "mmrGain", "gameRank", "equipment"])

processed_nicknames = load_lines(PROCESSED_FILE)
pending_nicknames = load_lines(PENDING_FILE) - processed_nicknames

# 🔄 [유저 방문 기록 초기화] 11,000명 도달 시 랭커 재방문을 위해 txt 파일만 백지화
if len(processed_nicknames) >= 11000:
    print("🔄 [탐색 대기열 리셋] 11,000명 탐색 완료. 상위권 유저의 새로운 전적을 수집하기 위해 방문 기록을 초기화합니다.", flush=True)
    processed_nicknames.clear()
    pending_nicknames.clear()
    open(PROCESSED_FILE, "w").close()
    open(PENDING_FILE, "w").close()

queue = deque(pending_nicknames)
loop_count = 0
last_req = 0.0

# [스노우볼 탐색 로직 (userNum 기반)]
while queue and loop_count < MAX_LOOPS:
    if time.time() - START_TIME > MAX_EXECUTION_TIME:
        print("⏱️ 시간 제한 도달. 수집 종료.", flush=True)
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
                                
                                equipment = json.dumps(p.get("equipment", []))
                                
                                match_rows.append([
                                    gid, p.get("characterNum", 0), p.get("bestWeapon", 0), 
                                    p.get("routeIdOfStart", p.get("routeId", 0)), 
                                    p.get("mmrBefore", 0), p.get("mmrGain", 0), p.get("gameRank", 0),
                                    equipment
                                ])
                            
                            if match_rows:
                                with open(CSV_DATASET, "a", encoding="utf-8-sig", newline="") as f:
                                    csv.writer(f).writerows(match_rows)
                                processed_game_ids.add(gid)
    except Exception as e:
        time.sleep(5)
        
    processed_nicknames.add(nickname)
    append_line(PROCESSED_FILE, nickname)

# ==========================================
# 📊 3. 최적 루트 추출 (분할 파일 전체 병합 분석)
# ==========================================
print("▶️ [2단계] 최적 루트 분석 시작...", flush=True)

# 💡 핵심 3: 분석할 때는 glob으로 찾은 모든 과거/현재 분할 파일들을 가져와 하나로 합쳐서(pd.concat) 통계를 냅니다.
all_dataset_files = glob.glob("reference_dataset*.csv")

if all_dataset_files and os.path.exists(MAPPING_CSV):
    df_list = []
    for file in all_dataset_files:
        try:
            df = pd.read_csv(file)
            df_list.append(df)
        except Exception:
            continue
            
    if df_list:
        df_game = pd.concat(df_list, ignore_index=True)
    else:
        df_game = pd.DataFrame()
        
    df_map = pd.read_csv(MAPPING_CSV)
    
    if df_game.empty or len(df_game.columns) < 8:
        print("⚠️ 수집된 데이터가 비어있어 분석을 건너뜁니다.", flush=True)
    else:
        valid_df = df_game[(df_game['routeId'] > 0) & (df_game['mmrBefore'] >= 7600)].copy()
        
        if valid_df.empty:
            print("⚠️ 수집된 미스릴+(7600점 이상) 데이터가 아직 부족합니다.", flush=True)
        else:
            valid_df['rp_plus'] = valid_df['mmrGain'] > 0
            route_stats = valid_df.groupby(['characterNum', 'bestWeapon', 'routeId']).agg(
                pick_count=('routeId', 'count'),
                avg_rp_gain=('mmrGain', 'mean')
            ).reset_index()

            route_stats = route_stats[route_stats['pick_count'] >= 30].copy()

            if route_stats.empty:
                print("⚠️ 30판 이상 사용된 루트 데이터가 아직 없습니다. 데이터 누적을 계속합니다.", flush=True)
            else:
                global_avg_rp = route_stats['avg_rp_gain'].mean()
                m = 30  
                route_stats['reference_score'] = ((route_stats['pick_count'] * route_stats['avg_rp_gain']) + (m * global_avg_rp)) / (route_stats['pick_count'] + m)
                route_stats.sort_values(by=['characterNum', 'bestWeapon', 'reference_score'], ascending=[True, True, False], inplace=True)
                top_routes = route_stats.drop_duplicates(subset=['characterNum', 'bestWeapon'], keep='first').copy()
                final_df = pd.merge(top_routes, df_map, left_on=['characterNum', 'bestWeapon'], right_on=['characterNum', 'weaponNum'], how='inner')
                
                if not final_df.empty:
                    final_df['avg_rp_gain'] = pd.to_numeric(final_df['avg_rp_gain']).round(1).astype(str) + '점'
                    final_df['reference_score'] = pd.to_numeric(final_df['reference_score']).round(2)
                    final_cols = ['characterName', 'weaponName', 'routeId', 'pick_count', 'avg_rp_gain', 'reference_score']
                    final_df.sort_values(by='reference_score', ascending=False, inplace=True)
                    final_df[final_cols].to_csv(TOP_ROUTES_FILE, index=False, encoding='utf-8-sig')
                    print(f"🎉 과거 분할 데이터까지 성공적으로 통합하여 {TOP_ROUTES_FILE}을 최신화했습니다.", flush=True)
