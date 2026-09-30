import os
import csv
import time
import json
import requests
import glob
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

CURRENT_DATASET_PREFIX = "reference_dataset"
MAPPING_CSV = "er_master_mapping.csv"
TOP_ROUTES_FILE = "top_reference_routes.csv"

SEASON_ID = 41
MATCHING_MODE = 3
MAX_LOOPS = 50000

# 💡 탐색 깊이 상향 (30판)
RECENT_GAME_LIMIT = 30  
REQUEST_INTERVAL = 1.1

# ==========================================
# 🔄 1-5. 데이터셋 용량 검사 및 백업(Archive) 로직
# ==========================================
CSV_DATASET = f"{CURRENT_DATASET_PREFIX}.csv"

if os.path.exists(CSV_DATASET) and os.path.getsize(CSV_DATASET) > 70 * 1024 * 1024:
    timestamp = time.strftime("%Y%m%d_%H%M")
    backup_name = f"{CURRENT_DATASET_PREFIX}_{timestamp}.csv"
    os.rename(CSV_DATASET, backup_name)
    print(f"📦 데이터셋 용량이 70MB를 초과하여 새 파일로 백업되었습니다: {backup_name}", flush=True)

# ==========================================
# 🚀 2. 데이터 수집 (Ranked & 7600+ Only Mode)
# ==========================================
print("▶️ [1단계] 데이터 수집 시작 (미스릴+ 랭크 전용, 딥 서치 모드)...", flush=True)
processed_game_ids = set()

all_dataset_files = glob.glob(f"{CURRENT_DATASET_PREFIX}*.csv")
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

total_accumulated_games = len(processed_game_ids)

if not os.path.exists(CSV_DATASET):
    with open(CSV_DATASET, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerow(["gameId", "characterNum", "bestWeapon", "routeId", "mmrBefore", "mmrGain", "gameRank", "equipment"])

processed_nicknames = set()
queue = deque()

# 💡 초기 시드 대폭 확장 (200명 -> 500명)
res_rank = requests.get(f"https://open-api.bser.io/v1/rank/top/{SEASON_ID}/{MATCHING_MODE}", headers=HEADERS)
if res_rank.status_code == 200:
    for p in res_rank.json().get("topRanks", [])[:500]:
        nick = p.get("nickname")
        if nick:
            queue.append(nick)

loop_count = 0
last_req = 0.0

while queue and loop_count < MAX_LOOPS:
    if time.time() - START_TIME > MAX_EXECUTION_TIME:
        print("⏱️ 5.5시간 제한 도달. 안전하게 루프 종료.", flush=True)
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

        if res_user.status_code == 429:
            time.sleep(10)
            queue.appendleft(nickname)
            loop_count -= 1
            continue
            
        saved_games_for_this_user = 0

        if res_user.status_code == 200 and "user" in res_user.json():
            # 💡 정상 수집을 위한 암호화된 userId 획득
            user_id_str = res_user.json()["user"].get("userId") 
            
            if user_id_str:
                games_data = []
                next_token = ""
                
                # 💡 API 페이징 로직: next 토큰을 활용해 목표 판수(30판) 도달 시까지 반복 호출
                while len(games_data) < RECENT_GAME_LIMIT:
                    time.sleep(REQUEST_INTERVAL)
                    games_url = f"https://open-api.bser.io/v1/user/games/uid/{user_id_str}"
                    if next_token:
                        games_url += f"?next={next_token}"
                        
                    res_games = requests.get(games_url, headers=HEADERS, timeout=10)
                    last_req = time.time()
                    
                    if res_games.status_code == 200:
                        res_json = res_games.json()
                        fetched_games = res_json.get("userGames", [])
                        games_data.extend(fetched_games)
                        
                        next_token = res_json.get("next")
                        if not next_token or not fetched_games:
                            break
                    else:
                        break

                # 💡 시즌 ID 및 매칭 모드 철벽 필터링 (과거 시즌 오염 방지)
                for game in games_data[:RECENT_GAME_LIMIT]:
                    if game.get("seasonId") != SEASON_ID or game.get("matchingMode") != MATCHING_MODE: 
                        continue

                    gid = game.get("gameId")
                    if not gid or gid in processed_game_ids: continue

                    time.sleep(REQUEST_INTERVAL)
                    detail_url = f"https://open-api.bser.io/v1/games/{gid}"
                    res_detail = requests.get(detail_url, headers=HEADERS, timeout=10)
                    last_req = time.time()

                    if res_detail.status_code == 200:
                        detail_data = res_detail.json().get("userGames", [])
                        match_rows = []
                        for p in detail_data:
                            # 미스릴(7600점) 이상 유저만 큐입 및 수집
                            if p.get("mmrBefore", 0) >= 7600:
                                n_nick = p.get("nickname")
                                if n_nick and n_nick not in processed_nicknames and n_nick not in queue:
                                    queue.append(n_nick)

                                eq_data = json.dumps(p.get("equipment", []))
                                match_rows.append([
                                    gid, p.get("characterNum", 0), p.get("bestWeapon", 0),
                                    p.get("routeIdOfStart", p.get("routeId", 0)),
                                    p.get("mmrBefore", 0), p.get("mmrGain", 0), p.get("gameRank", 0),
                                    eq_data
                                ])

                        if match_rows:
                            with open(CSV_DATASET, "a", encoding="utf-8-sig", newline="") as f:
                                csv.writer(f).writerows(match_rows)
                            processed_game_ids.add(gid)
                            saved_games_for_this_user += 1
                            total_accumulated_games += 1

        print(f"🔍 {loop_count}. '{nickname}' 탐색 완료 ~ {saved_games_for_this_user}게임 저장됨 (총 누적: {total_accumulated_games}개)", flush=True)

    except Exception as e:
        time.sleep(5)

    processed_nicknames.add(nickname)


# ==========================================
# 📊 3. 최적 루트 분석 (대표성 10%p 컷오프 + 성과 분리)
# ==========================================
print("▶️ [2단계] 최적 루트 분석 시작...", flush=True)
all_dataset_files = glob.glob(f"{CURRENT_DATASET_PREFIX}*.csv")

if all_dataset_files and os.path.exists(MAPPING_CSV):
    df_list = []
    for file in all_dataset_files:
        try:
            df_list.append(pd.read_csv(file))
        except Exception:
            continue
            
    if df_list:
        df_game = pd.concat(df_list, ignore_index=True)
    else:
        df_game = pd.DataFrame()
        
    df_map = pd.read_csv(MAPPING_CSV)

    if df_game.empty or len(df_game.columns) < 8:
        print("⚠ 수집된 데이터가 비어있어 분석을 건너뜁니다.", flush=True)
    else:
        valid_df = df_game[(df_game['routeId'] > 0) & (df_game['mmrBefore'] >= 7600)].copy()

        if valid_df.empty:
            print("⚠️ 수집된 미스릴+(7600점 이상) 데이터가 아직 부족합니다.", flush=True)
        else:
            # 1. 집계 
            route_stats = valid_df.groupby(['characterNum', 'bestWeapon', 'routeId']).agg(
                pick_count=('routeId', 'count'),
                avg_rp_gain=('mmrGain', 'mean'),
                median_rp_gain=('mmrGain', 'median')
            ).reset_index()

            # 2. 50판 하드 컷오프
            route_stats = route_stats[route_stats['pick_count'] >= 50].copy()

            if route_stats.empty:
                print("⚠️ 50판 이상 사용된 루트 데이터가 아직 없습니다.", flush=True)
            else:
                # 3. 픽률 계산 및 10%p 이내 후보 선정
                route_stats['group_total_picks'] = route_stats.groupby(['characterNum', 'bestWeapon'])['pick_count'].transform('sum')
                route_stats['pick_rate'] = (route_stats['pick_count'] / route_stats['group_total_picks']) * 100
                
                route_stats['max_pick_rate'] = route_stats.groupby(['characterNum', 'bestWeapon'])['pick_rate'].transform('max')
                candidates = route_stats[route_stats['pick_rate'] >= (route_stats['max_pick_rate'] - 10.0)].copy()

                # 4. 평균 RP로 승부
                candidates.sort_values(
                    by=['characterNum', 'bestWeapon', 'avg_rp_gain'], 
                    ascending=[True, True, False], 
                    inplace=True
                )
                
                top_routes = candidates.drop_duplicates(subset=['characterNum', 'bestWeapon'], keep='first').copy()
                final_df = pd.merge(top_routes, df_map, left_on=['characterNum', 'bestWeapon'], right_on=['characterNum', 'weaponNum'], how='inner')

                if not final_df.empty:
                    # 5. 데이터 포맷팅 (7개 열 구조)
                    final_df['pick_rate'] = final_df['pick_rate'].round(1).astype(str) + '%'
                    final_df['avg_rp_gain'] = pd.to_numeric(final_df['avg_rp_gain']).round(1).astype(str) + '점'
                    final_df['median_rp_gain'] = pd.to_numeric(final_df['median_rp_gain']).round(1).astype(str) + '점'
                    
                    final_cols = ['characterName', 'weaponName', 'routeId', 'pick_count', 'pick_rate', 'avg_rp_gain', 'median_rp_gain']
                    final_df.sort_values(by='pick_count', ascending=False, inplace=True)
                    
                    final_df[final_cols].to_csv(TOP_ROUTES_FILE, index=False, encoding='utf-8-sig')
                    
                    # 6. 파싱 에러 방지용 7열 꼬리표
                    with open(TOP_ROUTES_FILE, "a", encoding="utf-8-sig") as f:
                        f.write(f"현재 총 누적 : {total_accumulated_games},,,,,, \n")
                        
                    print(f"🎉 최종 분석 결과 저장 완료. (총 누적 데이터: {total_accumulated_games}건)", flush=True)
