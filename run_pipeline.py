import os
import csv
import time
import json
import requests
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
# 🚀 2. 데이터 수집 (자동 초기화 + userId 획득)
# ==========================================
print("▶️ [1단계] 데이터 수집 시작 (Real-time Logging & Auto-Reset Mode)...", flush=True)
processed_game_ids = set()

processed_nicknames = load_lines(PROCESSED_FILE)
pending_nicknames = load_lines(PENDING_FILE) - processed_nicknames

# 🔄 [자동 초기화 로직] 누적 탐색 유저가 11,000명 이상일 경우
if len(processed_nicknames) >= 11000:
    print("🔄 [자동 초기화] 탐색 유저가 11,000명을 초과하여 데이터를 물리적으로 백지화합니다.", flush=True)
    processed_nicknames.clear()
    pending_nicknames.clear()
    
    open(PROCESSED_FILE, "w").close()
    open(PENDING_FILE, "w").close()
    with open(CSV_DATASET, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerow(["gameId", "characterNum", "bestWeapon", "routeId", "mmrBefore", "mmrGain", "gameRank", "equipment"])
else:
    # 초기화 조건이 아닐 때만 기존 파일 읽어오기
    if os.path.exists(CSV_DATASET):
        with open(CSV_DATASET, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if row:
                    try: processed_game_ids.add(int(row[0]))
                    except ValueError: continue
    else:
        with open(CSV_DATASET, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerow(["gameId", "characterNum", "bestWeapon", "routeId", "mmrBefore", "mmrGain", "gameRank", "equipment"])

queue = deque(pending_nicknames)

# 대기열 시드 충전
if not queue:
    res_rank = requests.get(f"https://open-api.bser.io/v1/rank/top/{SEASON_ID}/{MATCHING_MODE}", headers=HEADERS)
    if res_rank.status_code == 200:
        for p in res_rank.json().get("topRanks", [])[:200]:
            nick = p.get("nickname")
            if nick and nick not in processed_nicknames:
                queue.append(nick)
                append_line(PENDING_FILE, nick)

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
        # 1. 닉네임으로 검색하여 암호화된 userId 획득
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
            user_id_str = res_user.json()["user"].get("userId")
            
            if user_id_str:
                time.sleep(REQUEST_INTERVAL)
                # 2. 올바른 엔드포인트 /v1/user/games/uid/{userId} 호출
                games_url = f"https://open-api.bser.io/v1/user/games/uid/{user_id_str}"
                res_games = requests.get(games_url, headers=HEADERS, timeout=10)
                last_req = time.time()

                if res_games.status_code == 200:
                    games_data = res_games.json().get("userGames", [])
                    for game in games_data[:RECENT_GAME_LIMIT]:
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
                                n_nick = p.get("nickname")
                                if n_nick and n_nick not in processed_nicknames and n_nick not in queue:
                                    queue.append(n_nick)
                                    append_line(PENDING_FILE, n_nick)

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
                                
        # 💡 유저 1명 탐색이 끝날 때마다 실시간 로그 출력
        print(f"🔍 {loop_count}. '{nickname}' 탐색 완료 ~ {saved_games_for_this_user}게임 저장됨 (총 누적: {len(processed_game_ids)}개)", flush=True)

    except Exception as e:
        print(f"⚠️ 에러 발생 (닉네임: {nickname}): {e}", flush=True)
        time.sleep(5)

    processed_nicknames.add(nickname)
    append_line(PROCESSED_FILE, nickname)

print(f"✅ 수집 종료. 현재 누적 게임 수: {len(processed_game_ids)}", flush=True)

# ==========================================
# 📊 3. 최적 루트 분석 (30판 하드 컷오프)
# ==========================================
print("▶️ [2단계] 최적 루트 분석 시작...", flush=True)
if os.path.exists(CSV_DATASET) and os.path.exists(MAPPING_CSV):
    df_game = pd.read_csv(CSV_DATASET)
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
                print("⚠️ 30판 이상 사용된 루트 데이터가 아직 없습니다.", flush=True)
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
