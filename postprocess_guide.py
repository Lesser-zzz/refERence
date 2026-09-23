import os
import pandas as pd
import requests
import json
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

# 언어팩(l10n)으로 아이템 코드 -> 한글 이름 변환기 구축
item_name_map = {}
l10n_res = requests.get("https://open-api.bser.io/v1/l10n/Korean", headers=HEADERS)
if l10n_res.status_code == 200:
    l10n_url = l10n_res.json().get('data', {}).get('l10Path')
    if l10n_url:
        res_txt = requests.get(l10n_url)
        for line in res_txt.text.splitlines():
            parts = line.split("┃")
            if len(parts) >= 2 and parts[0].startswith("Item/Name/"):
                item_code = parts[0].replace("Item/Name/", "").strip()
                if item_code.isdigit():
                    item_name_map[int(item_code)] = parts[1].strip()

# 1위 루트들을 순회하며 데이터 추출
for _, row in df_top.iterrows():
    char_name = row['characterName']
    weapon = row['weaponName']
    route_id = row['routeId']
    
    # 1. 루트 상세 API 찌르기 (스킬트리 확보)
    route_url = f"https://open-api.bser.io/v1/weaponRoutes/{route_id}"
    res = requests.get(route_url, headers=HEADERS)
    
    level_by_level = "스킬 정보 없음"
    if res.status_code == 200:
        route_data = res.json().get('result', {})
        skill_path_raw = route_data.get('skillPath', "")
        
        skill_list = []
        if skill_path_raw:
            for s in skill_path_raw.split(','):
                s = s.strip()
                if s.isdigit() and int(s) in SKILL_MAP:
                    skill_list.append(SKILL_MAP[int(s)])
        if skill_list:
            level_by_level = " - ".join(skill_list)

    # 2. 실전 매치 데이터에서 대체 아이템(최종 착용 장비) 통계 내기
    match_data = df_raw[df_raw['routeId'] == route_id]
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
    
    top_items_named = [f"{item_name_map.get(code, code)}({count}회)" for code, count in top_items]
    
    print(f"\n👤 [{char_name} - {weapon}] (루트번호: {route_id})")
    print(f"⚔️ 1~20레벨 스킬 트리: {level_by_level}")
    print(f"🎒 실전 최종 착용 장비 빈도 (상위 10개): {', '.join(top_items_named)}")
    print("-" * 60)
