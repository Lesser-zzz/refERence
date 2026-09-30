import os
import pandas as pd
import requests
import json
import re
from collections import Counter

API_KEY = os.environ.get("BSER_API_KEY")
HEADERS = {"x-api-key": API_KEY, "accept": "application/json"}

CSV_DATASET = "reference_dataset.csv"
TOP_ROUTES_FILE = "top_reference_routes.csv"
SKILL_MAP = {1: 'Q', 2: 'W', 3: 'E', 4: 'R', 5: 'T'}

print("▶️ [refERence] 루트 상세 가이드 데이터 추출 시작\n" + "="*60)

if not os.path.exists(TOP_ROUTES_FILE) or not os.path.exists(CSV_DATASET):
    print("❌ 필요한 데이터 파일이 없습니다. 메인 파이프라인을 먼저 실행해 주세요.")
    exit()

df_top = pd.read_csv(TOP_ROUTES_FILE)
df_raw = pd.read_csv(CSV_DATASET)

# 💡 [핵심 1] 실제 파일은 건드리지 않고, 램(메모리)에서만 '현재 총 누적' 텍스트 행을 날려버림
df_top = df_top.dropna(subset=['routeId'])

# 언어팩(l10n)으로 아이템 코드 -> 한글 이름 변환기 구축
item_name_map = {}
l10n_res = requests.get("https://open-api.bser.io/v1/l10n/Korean", headers=HEADERS)
if l10n_res.status_code == 200:
    l10n_url = l10n_res.json().get('data', {}).get('l10Path')
    if l10n_url:
        res_txt = requests.get(l10n_url)
        for line in res_txt.text.splitlines():
            # 💡 [핵심 2] 님블뉴런의 모든 특수 구분자(┃, ▒, ↕)를 분할하고 /Eng 등 영문 더미 차단
            parts = re.split(r'[┃▒↕]', line)
            if len(parts) >= 2:
                key = parts[0].strip()
                val = parts[1].strip()
                if re.match(r'^Item/Name/\d+$', key):
                    item_code = key.replace("Item/Name/", "")
                    item_name_map[int(item_code)] = val

# 1위 루트들을 순회하며 데이터 추출
for _, row in df_top.iterrows():
    char_name = row['characterName']
    weapon = row['weaponName']
    
    # 램에서 텍스트를 날렸기 때문에 float(NaN) 에러 없이 깔끔하게 정수로 변환됨
    route_id = int(float(row['routeId']))
    
    # 1. 루트 상세 API 찌르기 (스킬트리 확보)
    route_url = f"https://open-api.bser.io/v1/weaponRoutes/{route_id}"
    res = requests.get(route_url, headers=HEADERS)
    
    level_by_level = "스킬 정보 없음"
    if res.status_code == 200:
        route_data = res.json().get('result', {})
        
        # 💡 [핵심 3] 스킬 트리가 recommendWeaponRoute 내부에 숨어있는 경우까지 완벽 추적
        skill_path_raw = route_data.get('skillPath', "")
        if not skill_path_raw and 'recommendWeaponRoute' in route_data:
            skill_path_raw = route_data['recommendWeaponRoute'].get('skillPath', "")
            
        skill_list = []
        if skill_path_raw:
            for s in skill_path_raw.split(','):
                s = s.strip()
                if s.isdigit() and int(s) in SKILL_MAP:
                    skill_list.append(SKILL_MAP[int(s)])
        if skill_list:
            level_by_level = " - ".join(skill_list)

    # 2. 실전 매치 데이터에서 대체 아이템(최종 착용 장비) 통계 내기 (미스릴+ 랭크만 필터링)
    match_data = df_raw[(df_raw['routeId'] == route_id) & (df_raw['mmrBefore'] >= 7600)]
    all_equipments = []
    
    for eq_str in match_data['equipment'].dropna():
        try:
            eq_list = json.loads(eq_str) 
            for eq in eq_list:
                item_code = eq.get('itemCode')
                if item_code:
                    all_equipments.append(item_code)
        except json.JSONDecodeError:
            continue
            
    item_counter = Counter(all_equipments)
    top_items = item_counter.most_common(10)
    
    # 숫자 코드를 한글 명칭으로 변환 (언어팩에 없으면 숫자로라도 표시)
    top_items_named = [f"{item_name_map.get(int(code), code)}({count}회)" for code, count in top_items]
    
    print(f"\n👤 [{char_name} - {weapon}] (루트번호: {route_id})")
    print(f"⚔️ 1~20레벨 스킬 트리: {level_by_level}")
    print(f"🎒 실전 최종 착용 장비 빈도 (상위 10개): {', '.join(top_items_named)}")
    print("-" * 60)
