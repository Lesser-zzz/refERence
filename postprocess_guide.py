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
OUTPUT_TXT_FILE = "reference_guide_output.txt"
SKILL_MAP = {1: 'Q', 2: 'W', 3: 'E', 4: 'R', 5: 'T', '1': 'Q', '2': 'W', '3': 'E', '4': 'R', '5': 'T', 'Q':'Q', 'W':'W', 'E':'E', 'R':'R', 'T':'T'}

print("▶️ [refERence] 루트 상세 가이드 데이터 추출 시작\n" + "="*60)

if not os.path.exists(TOP_ROUTES_FILE):
    print("❌ 필요한 1위 루트 데이터 파일이 없습니다. 메인 파이프라인을 먼저 실행해 주세요.")
    exit()

df_top = pd.read_csv(TOP_ROUTES_FILE)
df_top = df_top.dropna(subset=['routeId'])

all_dataset_files = glob.glob("reference_dataset*.csv")
df_list = []
for file in all_dataset_files:
    try:
        df_list.append(pd.read_csv(file))
    except Exception:
        continue

df_raw = pd.concat(df_list, ignore_index=True) if df_list else pd.DataFrame()

item_name_map = {}
l10n_res = requests.get("https://open-api.bser.io/v1/l10n/Korean", headers=HEADERS)
if l10n_res.status_code == 200:
    l10n_url = l10n_res.json().get('data', {}).get('l10Path')
    if l10n_url:
        res_txt = requests.get(l10n_url)
        res_txt.encoding = 'utf-8'
        for line in res_txt.text.splitlines():
            parts = re.split(r'[┃▒↕]', line)
            if len(parts) >= 2 and parts[0].startswith("Item/Name/"):
                num_str = parts[0].replace("Item/Name/", "").strip()
                if num_str.isdigit() and len(num_str) == 6:
                    item_name_map[int(num_str)] = parts[1].strip()

def find_skill_path(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in ['skillpath', 'skillorder', 'skillorders'] and v:
                return v
            res = find_skill_path(v)
            if res: return res
    elif isinstance(obj, list):
        for item in obj:
            res = find_skill_path(item)
            if res: return res
    return None

with open(OUTPUT_TXT_FILE, 'w', encoding='utf-8') as f:
    f.write("=== refERence 상세 가이드 초안 ===\n\n")

    for _, row in df_top.iterrows():
        char_name = row['characterName']
        weapon = row['weaponName']
        try:
            route_id = int(float(row['routeId']))
        except ValueError:
            continue
        
        # 💡 [해결] 님블뉴런의 파편화된 주소를 모두 찔러보는 폴백(Fallback) 체인
        route_urls = [
            f"https://open-api.bser.io/v1/recommendWeaponRoutes/{route_id}",
            f"https://open-api.bser.io/v1/weaponRoutes/{route_id}",
            f"https://open-api.bser.io/v1/weaponRoutes/recommend/{route_id}"
        ]
        
        res = None
        for url in route_urls:
            temp_res = requests.get(url, headers=HEADERS)
            if temp_res.status_code == 200:
                res = temp_res
                break
        
        level_by_level = "스킬 정보 없음 (미등록)" 
        target_item_codes = set()
        
        if res:
            route_data = res.json()
            skill_path_raw = find_skill_path(route_data)
            
            skill_list = []
            if isinstance(skill_path_raw, str) and skill_path_raw:
                for s in skill_path_raw.split(','):
                    s = s.strip().upper()
                    if s in SKILL_MAP:
                        skill_list.append(SKILL_MAP[s])
            elif isinstance(skill_path_raw, list):
                for s in skill_path_raw:
                    s = str(s).strip().upper()
                    if s in SKILL_MAP:
                        skill_list.append(SKILL_MAP[s])
                        
            if skill_list:
                level_by_level = " - ".join(skill_list)

            # 원본 목표 전설템 추출
            target_item_codes = set(int(c) for c in re.findall(r'\b[1-5]\d{5}\b', res.text))
        else:
            level_by_level = "API 조회 실패 (404 Not Found 등)"

        # 실전 매치 데이터에서 통계 내기
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
                codes = re.findall(r'\b[1-5]\d{5}\b', eq_str)
                for c in codes:
                    all_equipments.append(int(c))
                
        item_counter = Counter(all_equipments)
        top_items = item_counter.most_common(12)
        
        original_targets = []
        alternative_items = []
        
        for code, count in top_items:
            item_str = f"{item_name_map.get(code, code)}({count}회)"
            if res and code in target_item_codes:
                original_targets.append(item_str)
            else:
                alternative_items.append(item_str)
        
        original_targets_str = ', '.join(original_targets)
        # API 통신 실패 시 오류 안내 명시
        if not res:
            original_targets_str = "API 응답 실패로 조회 불가"
        elif not original_targets:
            original_targets_str = "통계상 채택률 0% (원본 목표템과 일치하는 실전 장비 없음)"

        output_text = (
            f"👤 [{char_name} - {weapon}] (루트번호: {route_id})\n"
            f"⚔️ 1~20레벨 스킬 트리: {level_by_level}\n"
            f"🎯 루트 원본 목표템: {original_targets_str}\n"
            f"🔄 실전 대체 아이템: {', '.join(alternative_items)}\n"
            f"{'-' * 60}\n"
        )
        
        print(output_text, end="")
        f.write(output_text)

print("✅ reference_guide_output.txt 파일이 성공적으로 생성되었습니다.")
