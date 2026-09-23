import os
import pandas as pd
import requests
import json
from collections import Counter

API_KEY = os.environ.get("BSER_API_KEY")
HEADERS = {"x-api-key": API_KEY, "accept": "application/json"}

CSV_DATASET = "reference_dataset.csv"
TOP_ROUTES_FILE = "top_reference_routes.csv"

# 스킬 번호 매핑 (기본값)
SKILL_MAP = {1: 'Q', 2: 'W', 3: 'E', 4: 'R', 5: 'T'}

print("▶️ [3단계] 루트 API 기반 상세 가이드 데이터 추출 시작...")

if not os.path.exists(TOP_ROUTES_FILE) or not os.path.exists(CSV_DATASET):
    print("❌ 필요한 데이터 파일이 없습니다.")
    exit()

df_top = pd.read_csv(TOP_ROUTES_FILE)
df_raw = pd.read_csv(CSV_DATASET)

for _, row in df_top.iterrows():
    char_name = row['characterName']
    weapon = row['weaponName']
    route_id = row['routeId']
    
    # 1. 루트 상세 API 호출
    route_url = f"https://open-api.bser.io/v1/weaponRoutes/{routeId}"
    res = requests.get(route_url, headers=HEADERS)
    
    if res.status_code != 200:
        continue
        
    route_data = res.json().get('result', {})
    
    # --- [스킬 마스터리 추출] ---
    skill_path_raw = route_data.get('skillPath', "")
    skill_list = []
    
    # 1레벨부터 차례대로 어떤 스킬을 찍는지 리스트화
    if skill_path_raw:
        for s in skill_path_raw.split(','):
            s = s.strip()
            if s.isdigit() and int(s) in SKILL_MAP:
                skill_list.append(SKILL_MAP[int(s)])
    
    level_by_level = " - ".join(skill_list) if skill_list else "스킬 정보 없음"
    
    # --- [대체 아이템 선호도 추출] ---
    # 루트에 등록된 목표 전설 아이템(의도된 템) 리스트 파악을 원한다면
    # route_data 내의 targetItems 배열과 비교해야 합니다.
    # 여기서는 실전 통계에서 유저들이 가장 많이 '최종 착용'한 아이템 빈도를 추출합니다.
    
    match_data = df_raw[df_raw['routeId'] == route_id]
    all_equipments = []
    
    for eq_str in match_data['equipment'].dropna():
        try:
            eq_list = json.loads(eq_str) # [{"itemCode": 11111, "slotCode": "Weapon"}, ...]
            for eq in eq_list:
                item_code = eq.get('itemCode')
                if item_code:
                    all_equipments.append(item_code)
        except json.JSONDecodeError:
            continue
            
    # 가장 많이 입고 끝난 아이템 순위 집계
    item_counter = Counter(all_equipments)
    top_items = item_counter.most_common(5) # 상위 5개 장비 추출
    
    print(f"\n[{char_name} - {weapon}] (루트번호: {route_id})")
    print(f"⚔️ 1~20레벨 스킬 트리: {level_by_level}")
    print(f"🎒 실전 최종 착용 장비 빈도 (상위 5개 코드): {top_items}")
