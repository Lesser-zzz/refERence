import os
import pandas as pd
import requests
import json
import glob
import re
from collections import Counter

API_KEY = os.environ.get("BSER_API_KEY")
HEADERS = {"x-api-key": API_KEY, "accept": "application/json"}

TOP_ROUTES_FILE = "top_reference_routes.csv"
SKILL_MAP = {1: 'Q', 2: 'W', 3: 'E', 4: 'R', 5: 'T'}

print("▶️ [refERence] 루트 상세 가이드 데이터 추출 시작\n" + "="*60)

if not os.path.exists(TOP_ROUTES_FILE):
    print("❌ 필요한 1위 루트 데이터 파일이 없습니다. 메인 파이프라인을 먼저 실행해 주세요.")
    exit()

df_top = pd.read_csv(TOP_ROUTES_FILE)
df_top = df_top.dropna(subset=['routeId']) # 파일 하단 쓰레기값 방어

# 1. 분할된 모든 데이터셋을 읽어와 하나의 DataFrame으로 통합
all_dataset_files = glob.glob("reference_dataset*.csv")
if not all_dataset_files:
    print("❌ 수집된 매치 데이터 파일이 없습니다.")
    exit()

df_list = []
for file in all_dataset_files:
    try:
        df_list.append(pd.read_csv(file))
    except Exception:
        continue

if df_list:
    df_raw = pd.concat(df_list, ignore_index=True)
else:
    df_raw = pd.DataFrame()

# 💡 [해결 1] 언어팩 인코딩 강제 고정으로 아이템 이름 증발 완벽 차단
item_name_map = {}
l10n_res = requests.get("https://open-api.bser.io/v1/l10n/Korean", headers=HEADERS)
if l10n_res.status_code == 200:
    l10n_url = l10n_res.json().get('data', {}).get('l10Path')
    if l10n_url:
        res_txt = requests.get(l10n_url)
        res_txt.encoding = 'utf-8' # 인코딩 깨짐 방지 (핵심)
        
        for line in res_txt.text.splitlines():
            parts = re.split(r'[┃▒↕]', line)
            if len(parts) >= 2:
                key = parts[0].strip()
                val = parts[1].strip()
                if key.startswith("Item/Name/"):
                    num_str = key.replace("Item/Name/", "").strip()
                    if num_str.isdigit():
                        item_name_map[int(num_str)] = val

# 1위 루트들을 순회하며 데이터 추출
for _, row in df_top.iterrows():
    char_name = row['characterName']
    weapon = row['weaponName']
    try:
        route_id = int(float(row['routeId']))
    except ValueError:
        continue
    
    # 1. 루트 상세 API 찌르기 (스킬트리 확보)
    route_url = f"https://open-api.bser.io/v1/weaponRoutes/{route_id}"
    res = requests.get(route_url, headers=HEADERS)
    
    level_by_level = "스킬 정보 없음" # 루트 제작자가 진짜로 안 찍어둔 경우의 기본값
    if res.status_code == 200:
        route_data = res.json().get('result', {})
        skill_path_raw = route_data.get('skillPath') or \
                         route_data.get('recommendWeaponRoute', {}).get('skillPath') or \
                         route_data.get('route', {}).get('skillPath') or ""
        
        skill_list = []
        if isinstance(skill_path_raw, str) and skill_path_raw:
            for s in skill_path_raw.split(','):
                s = s.strip().upper()
                # 💡 [해결 2] API가 "1"로 주든 "Q"로 주든 전부 낚아채도록 수정
                if s.isdigit() and int(s) in SKILL_MAP:
                    skill_list.append(SKILL_MAP[int(s)])
                elif s in ['Q', 'W', 'E', 'R', 'T']:
                    skill_list.append(s)
        elif isinstance(skill_path_raw, list):
            for s in skill_path_raw:
                s = str(s).strip().upper()
                if s.isdigit() and int(s) in SKILL_MAP:
                    skill_list.append(SKILL_MAP[int(s)])
                elif s in ['Q', 'W', 'E', 'R', 'T']:
                    skill_list.append(s)
                    
        if skill_list:
            level_by_level = " - ".join(skill_list)

    # 2. 실전 매치 데이터에서 대체 아이템 통계 내기 (미스릴+ 랭크 데이터만)
    match_data = df_raw[(df_raw['routeId'] == route_id) & (df_raw['mmrBefore'] >= 7600)]
    all_equipments = []
    
    for eq_str in match_data['equipment'].dropna():
        eq_str = str(eq_str).strip()
        
        if '|' in eq_str:
            for code in eq_str.split('|'):
                if code.strip().isdigit():
                    all_equipments.append(int(code.strip()))
            continue
            
        try:
            parsed_eq = json.loads(eq_str) 
            if isinstance(parsed_eq, dict):
                for val in parsed_eq.values():
                    if str(val).isdigit():
                        all_equipments.append(int(val))
            elif isinstance(parsed_eq, list):
                for eq in parsed_eq:
                    if isinstance(eq, dict) and eq.get('itemCode'):
                        all_equipments.append(int(eq.get('itemCode')))
                    elif str(eq).isdigit():
                        all_equipments.append(int(eq))
        except json.JSONDecodeError:
            codes = re.findall(r'\b\d{6}\b', eq_str)
            for c in codes:
                all_equipments.append(int(c))
            
    item_counter = Counter(all_equipments)
    top_items = item_counter.most_common(10)
    
    top_items_named = [f"{item_name_map.get(code, code)}({count}회)" for code, count in top_items]
    
    print(f"\n👤 [{char_name} - {weapon}] (루트번호: {route_id})")
    print(f"⚔️ 1~20레벨 스킬 트리: {level_by_level}")
    print(f"🎒 실전 최종 착용 장비 빈도 (상위 10개): {', '.join(top_items_named)}")
    print("-" * 60)
