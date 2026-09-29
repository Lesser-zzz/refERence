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
PENDING_FILE = "snowball_pending_add.txt"
PROCESSED_FILE = "snowball_processed.txt"
TOP_ROUTES_FILE = "top_reference_routes.csv"

SEASON_ID = 41
MATCHING_MODE = 3
MAX_LOOPS = 50000
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
# 🔄 1-5. 데이터셋 용량 검사 및 백업(Archive) 로직
# ==========================================
# 💡 핵심: 엉뚱한 과거 백업 파일이 잡히지 않도록, 작업 중인 메인 파일을 항상 고정
CSV_DATASET = f"{CURRENT_DATASET_PREFIX}.csv"

# 용량이 70MB (70 * 1024 * 1024 바이트)를 초과한다면 과거 기록으로 백업(Archive) 처리
if os.path.exists(CSV_DATASET) and os.path.getsize(CSV_DATASET) > 70 * 1024 * 1024:
    timestamp = time.strftime("%Y%m%d_%H%M")
    backup_name = f"{CURRENT_DATASET_PREFIX}_{timestamp}.csv"
    os.rename(CSV_DATASET, backup_name)
    print(f"📦 데이터셋 용량이 70MB를 초과하여 새 파일로 백업되었습니다: {backup_name}", flush=True)

# ==========================================
# 🚀 2. 데이터 수집 (Ranked & 7600+ Only Mode)
# ==========================================
print("▶️ [1단계] 데이터 수집 시작 (미스릴+ 랭크 전용)...", flush=True)
processed_game_ids = set()

# 분할된 모든 데이터셋(방금 백업된 파일 포함)을 읽어와 중복 게임 ID 원천 차단
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

# 과거 분할 데이터들을 읽어온 직후의 총 누적 게임 수
total_accumulated_games = len(processed_game_ids)

# 메인 파일이 없다면(방금 백업되어 사라졌거나 첫 실행이라면) 헤더를 포함하여 새로 생성
if not os.path.exists(CSV_DATASET):
    with open(CSV_DATASET, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerow(["gameId", "characterNum", "bestWeapon", "routeId", "mmrBefore", "mmrGain", "gameRank", "equipment"])

processed_nicknames = load_lines(PROCESSED_FILE)
pending_nicknames = load_lines(PENDING_FILE) - processed_nicknames

# 🔄 [유저 방문 기록 초기화] 11,000명 도달 시 랭커 재방문을 위해 txt 파일 백지화
if len(processed_nicknames) >= 11000:
    print("🔄 [탐색 대기열 리셋] 11,000명 탐색 완료. 상위권 유저 전적 갱신을 위해 기록을 초기화합니다.", flush=True)
    processed_nicknames.clear()
    pending_nicknames.clear()
    open(PROCESSED_FILE, "w").close()
    open(PENDING_FILE, "w").close()

queue = deque(pending_nicknames)

# 💡 대기열이 10명 미만으로 간당간당하면 랭커 시드를 든든하게 다시 충전
if len(queue) < 10:
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
                games_url = f"https://open-api.bser.io/v1/user/games/uid/{user_id_str}"
                res_games = requests.get(games_url, headers=HEADERS, timeout=10)
                last_req = time.time()

                if res_games.status_code == 200:
                    games_data = res_games.json().get("userGames", [])
                    for game in games_data[:RECENT_GAME_LIMIT]:
                        
                        # 💡 철벽 1: 일반/코발트 등 랭크(3)가 아닌 게임은 완벽 차단
                        if game.get("matchingMode") != MATCHING_MODE: 
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
                                # 💡 철벽 2: 7600점(미스릴) 이상인 유저만 데이터 적재 및 스노우볼 대기열 추가
                                if p.get("mmrBefore", 0) >= 7600:
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
                                total_accumulated_games += 1

        print(f"🔍 {loop_count}. '{nickname}' 탐색 완료 ~ {saved_games_for_this_user}게임 저장됨 (총 누적: {total_accumulated_games}개)", flush=True)

    except Exception as e:
        time.sleep(5)

    processed_nicknames.add(nickname)
    append_line(PROCESSED_FILE, nickname)


# ==========================================
# 📊 3. 최적 루트 분석 (분할 파일 전체 병합)
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
                    
                    # 1. 정규 데이터 저장
                    final_df[final_cols].to_csv(TOP_ROUTES_FILE, index=False, encoding='utf-8-sig')
                    
                    # 2. 파싱 에러 완벽 차단: 줄바꿈 제거 + 6열 형식(쉼표 5개) 유지
                    with open(TOP_ROUTES_FILE, "a", encoding="utf-8-sig") as f:
                        f.write(f"현재 총 누적 : {total_accumulated_games},,,,,\n")
                        
                    print(f"🎉 최종 분석 결과 저장 완료. (총 누적 데이터: {total_accumulated_games}건)", flush=True)
