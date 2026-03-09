import streamlit as st
import pandas as pd
import altair as alt
import json
import os
import time
import io
import re
import glob
import socket
import random
import urllib.request
import hashlib
from datetime import datetime
from dataclasses import dataclass

# Firebase Admin SDK 관련 임포트
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except ImportError:
    st.error("라이브러리 로드 실패: 'firebase-admin'이 설치되어 있지 않습니다.")

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options as ChromeOptions
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import Select 
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException, TimeoutException, NoSuchWindowException

# -----------------------------------------------------------------------------
# 1. 페이지 설정 (최상단 배치로 가로폭 확장 보장)
# -----------------------------------------------------------------------------
st.set_page_config(page_title="2026 월별 지출관리", layout="wide", page_icon="🏢")

# -----------------------------------------------------------------------------
# 2. 글로벌 상수, 설정 및 예산 매핑 테이블
# -----------------------------------------------------------------------------
CATEGORIES = ["전기요금", "상하수도", "통신요금", "복합기임대", "공청기비데", "상품매입비", "수입금", "자체소수선", "부서업무비", "무인경비", "승강기점검", "신용카드수수료", "환경용역", "세탁용역", "야간경비", "수탁자산취득비", "일반재료비", "미디어실제습기"]
MONTHS = list(range(1, 13))
YEARS = [2024, 2025, 2026]

CORE_CONFIG = {
    "수탁자산취득비": {
        "icon": "💎", "color": "#2563eb", "bg": "#eff6ff", "border": "#3b82f6",
        "details": { 
            1: "기집행 완료", 2: "CCTV, 포충기 구입 및 댄스스테이지, 댄스연습실 등 블라인드 설치 (6,250천원)", 3: "전략기획부 지원예산 4,444천원 집행 예정<br/>※ 심장충격기 2,200천원 구입 예정 (6,644천원)", 
            4: "-", 5: "-", 6: "조경관리비 일부 집행 예정 (33,219천원)" 
        }
    },
    "일반재료비": {
        "icon": "🌿", "color": "#059669", "bg": "#f0fdf4", "border": "#10b981",
        "details": { 
            1: "시설 유지보수 필수자재 구입 (3,703천원)", 2: "시설 유지보수 필수자재 구입 (3,000천원)", 3: "상하수도 요금 지출 (550천원)", 
            4: "상하수도 요금 지출 (550천원)", 5: "시설 유지보수 필수자재 구입 (2,500천원)<br/>상하수도 요금 지출 (550천원)", 6: "상하수도 요금 지출 (550천원)" 
        }
    },
    "상품매입비": {
        "icon": "🛒", "color": "#d97706", "bg": "#fffbeb", "border": "#f59e0b",
        "details": { 1: "스마트 자판기 식음료 구입 (1,997천원)", 2: "-", 3: "스마트 자판기 식음료 구입 (1,500천원)", 4: "-", 5: "스마트 자판기 식음료 구입 (2,000천원)", 6: "-" }
    }
}

CORE_TARGETS = list(CORE_CONFIG.keys())

QUICK_EXEC_CONFIG = {
    "수탁자산취득비": {"target": 93464000, "goal_q1": 37385600, "goal_h1": 65424800, "goal_q1_rate": 0.40, "goal_h1_rate": 0.70},
    "일반재료비": {"target": 14300000, "goal_q1": 5720000, "goal_h1": 10010000, "goal_q1_rate": 0.40, "goal_h1_rate": 0.70},
    "상품매입비": {"target": 5450000, "goal_q1": 2180000, "goal_h1": 3815000, "goal_q1_rate": 0.40, "goal_h1_rate": 0.70}
}

# 예산 과목/세목 매핑 테이블 (코드로 이름 찾기)
BUDGET_MAPPING = {
    "101-01": ("인건비", "보수"),
    "101-03": ("인건비", "공무직(무기계약)근로자보수"),
    "101-04": ("인건비", "기간제근로자등보수"),
    "107-03": ("퇴직급여", "퇴직급여"),
    "109-01": ("평가급및성과금등", "일반직평가급등"),
    "109-02": ("평가급및성과금등", "공무직(무기계약)근로자평가급등"),
    "201-01": ("일반운영비", "사무관리비"),
    "201-02": ("일반운영비", "공공운영비"),
    "201-03": ("일반운영비", "행사운영비"),
    "201-11": ("일반운영비", "지급수수료"),
    "201-12": ("일반운영비", "교육훈련비"),
    "201-13": ("일반운영비", "임차료"),
    "201-14": ("일반운영비", "회의비"),
    "201-15": ("일반운영비", "복리후생비"),
    "201-21": ("일반운영비", "공공요금및제세"),
    "202-01": ("여비", "국내여비"),
    "202-08": ("여비", "공무직(무기계약직)근로자등여비"),
    "204-02": ("직무수행경비", "직급보조비"),
    "206-01": ("재료비", "일반재료비"),
    "207-02": ("연구개발비", "전산개발비"),
    "214-05": ("수선유지교체비", "수선유지비"),
    "215": ("동력비", "동력비"),
    "217-01": ("관서업무비", "정원가산업무비"),
    "217-02": ("관서업무비", "부서업무비"),
    "233": ("상품매입비", "상품매입비"),
    "301-09": ("일반보전금", "행사실비지원금"),
    "304-01": ("연금부담금등", "연금부담금"),
    "304-02": ("연금부담금등", "국민건강보험부담금등"),
    "304-03": ("연금부담금등", "공무직(무기계약)부담금관련"),
    "304-05": ("연금부담금등", "기간제근로자부담금관련"),
    "802-11": ("반환금기타", "대행사업비반환금"),
    "405-12": ("자산취득비", "수탁자산취득비")
}

# -----------------------------------------------------------------------------
# 3. Firebase 서비스 초기화
# -----------------------------------------------------------------------------
if not firebase_admin._apps:
    fb_creds = dict(st.secrets["firebase"])
    if "private_key" in fb_creds:
        fb_creds["private_key"] = fb_creds["private_key"].replace("\\n", "\n")
    cred = credentials.Certificate(fb_creds)
    firebase_admin.initialize_app(cred)

db = firestore.client()
appId = st.secrets.get("app_id", "facility-ledger-2026-v1")
doc_ref = db.collection('artifacts').document(appId).collection('public').document('data').collection('facility_data').document('master')
daily_ref = db.collection('artifacts').document(appId).collection('public').document('data').collection('facility_data').document('daily_expenses')
rapid_monthly_ref = db.collection('artifacts').document(appId).collection('public').document('data').collection('facility_data').document('rapid_monthly_v2')
quant_base_ref = db.collection('artifacts').document(appId).collection('public').document('data').collection('quantitative_monthly')

# -----------------------------------------------------------------------------
# 4. 핵심 데이터 처리 및 유틸리티 함수
# -----------------------------------------------------------------------------
def clean_numeric(val):
    if pd.isna(val): return 0.0
    if isinstance(val, (int, float)): return float(val)
    s = str(val).split('#')[0]
    s = re.sub(r'[^0-9\.\-]', '', s) 
    try: return float(s) if s else 0.0
    except: return 0.0

def number_to_korean(n):
    n = int(n)
    if n == 0: return "영원"
    units = ["", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]
    digit_units = ["", "십", "백", "천"]
    group_units = ["", "만", "억", "조"]
    res = []
    s_num = str(int(n))[::-1]
    for i in range(0, len(s_num), 4):
        group, group_res = s_num[i:i+4], ""
        for j, digit in enumerate(group):
            d = int(digit)
            if d > 0: group_res = units[d] + digit_units[j] + group_res
        if group_res: res.append(group_res + group_units[i // 4])
    return "금 " + "".join(res[::-1]) + "원"

def update_amt(increment): 
    if 'amt_box' in st.session_state: st.session_state.amt_box += int(increment)

def reset_amt(): 
    if 'amt_box' in st.session_state: st.session_state.amt_box = 0

def ensure_data_integrity(data):
    if not isinstance(data, dict) or "records" not in data: data = {"records": []}
    existing = {(r['year'], r['month'], r['category']) for r in data['records']}
    new_recs = []
    for y in YEARS:
        for c in CATEGORIES:
            for m in MONTHS:
                if (y, m, c) not in existing:
                    new_recs.append({"year": y, "month": m, "category": c, "amount": 0.0, "status": "지출"})
    if new_recs: data['records'].extend(new_recs)
    return data

def load_data():
    try:
        doc = doc_ref.get()
        if doc.exists: return doc.to_dict()
        return {"records": []}
    except: return {"records": []}

def save_data_cloud(data):
    try: doc_ref.set(data); return True
    except: return False

def save_and_register(year, cat, mon):
    if st.session_state.amt_box > 0:
        curr = st.session_state['data']
        for r in curr["records"]:
            if r["year"] == year and r["category"] == cat and r["month"] == mon:
                r["amount"] += float(st.session_state.amt_box)
                r["status"] = "지출"
                break
        save_data_cloud(curr)
        st.session_state['data'] = curr
        st.session_state.amt_box = 0
        st.toast("✅ 지출 등록 완료")

def load_daily_expenses():
    try:
        doc = daily_ref.get()
        if doc.exists: return doc.to_dict().get("expenses", [])
        return []
    except: return []

def save_daily_expenses(expense_list):
    try: daily_ref.set({"expenses": expense_list, "last_updated": datetime.now().isoformat()}); return True
    except: return False

def load_rapid_df():
    try:
        doc = rapid_monthly_ref.get()
        if doc.exists:
            df = pd.DataFrame(doc.to_dict().get("data", []))
            for c in ["대상액", "집행예정액", "실제집행액"]:
                if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
            return df
        rows = []
        targets = {"수탁자산취득비": 93464000, "일반재료비": 14300000, "상품매입비": 5450000}
        plans = {
            "수탁자산취득비": {2: 6250000, 3: 6644000, 6: 33219000},
            "일반재료비": {1: 3703000, 2: 3000000, 3: 550000, 4: 550000, 5: 3050000, 6: 550000},
            "상품매입비": {1: 1997000, 3: 1500000, 5: 2000000}
        }
        for cat in CORE_TARGETS:
            for m in range(1, 7):
                p_amt = float(plans.get(cat, {}).get(m, 0.0))
                a_amt = p_amt if m == 1 else 0.0
                rows.append({"세목": cat, "월": f"{m}월", "대상액": targets.get(cat, 0) if m == 1 else 0, "집행예정액": p_amt, "실제집행액": a_amt})
        return pd.DataFrame(rows)
    except: return pd.DataFrame()

def save_rapid_df(df):
    try:
        rapid_monthly_ref.set({"data": df.to_dict('records'), "last_updated": datetime.now().isoformat()})
        return True
    except: return False

def merge_expenses(old_list, new_list):
    def get_key(item):
        return f"{item.get('집행일자','')}_{item.get('적요','')}_{item.get('집행금액',0)}"
    existing_keys = {get_key(x) for x in old_list}
    merged_list = list(old_list)
    added_count = 0
    for item in new_list:
        if get_key(item) not in existing_keys:
            merged_list.append(item); added_count += 1
    try: merged_list.sort(key=lambda x: str(x.get('집행일자','')), reverse=True)
    except: pass
    return merged_list, added_count

def load_quant_monthly(year, month):
    try:
        m_doc = quant_base_ref.document(f"{year}_{month}").get()
        return m_doc.to_dict().get("data", []) if m_doc.exists else []
    except: return []

def save_quant_monthly(year, month, data_list):
    try:
        quant_base_ref.document(f"{year}_{month}").set({"data": data_list, "last_updated": datetime.now().isoformat()}); return True
    except: return False

# -----------------------------------------------------------------------------
# ★ 공통 매핑 및 동기화 함수
# -----------------------------------------------------------------------------
def get_mapped_category(desc, row_cat):
    desc = str(desc).strip()
    row_cat = str(row_cat).strip()
    
    for cat_name in CATEGORIES:
        if cat_name in row_cat or cat_name in desc.replace(" ", ""):
            return cat_name
            
    keyword_map = {
        "전기요금": ["전기요금", "전기"], "상하수도": ["상하수도", "상수도", "하수도"], "통신요금": ["통신요금", "통신비", "인터넷", "전화"],
        "복합기임대": ["복합기", "임대", "렌탈"], "공청기비데": ["공기청정기", "비데", "공청기"],
        "미디어실제습기": ["미디어", "제습", "습기", "미디어실"], "상품매입비": ["상품", "매입", "자판기", "식음료", "종량제"],
        "자체소수선": ["수선", "보수", "수리"], "부서업무비": ["업무비", "부서"], "무인경비": ["무인경비", "경비", "보안"],
        "승강기점검": ["승강기", "엘리베이터", "점검"], "신용카드수수료": ["수수료", "신용카드", "카드수수료"],
        "환경용역": ["환경", "용역", "미화"], "세탁용역": ["세탁"], "야간경비": ["야간경비", "야간"],
        "수탁자산취득비": ["수탁자산", "자산취득", "CCTV", "방화벽", "포충기", "블라인드", "심장충격기"],
        "일반재료비": ["일반재료", "재료비", "자재", "유지보수", "소모품", "부속품"]
    }
    
    for m_cat, kws in keyword_map.items():
        if any(k in desc for k in kws):
            return m_cat
    return None

def sync_daily_to_master_auto():
    master_data = load_data()
    master_data = ensure_data_integrity(master_data)
    daily = st.session_state.get('daily_expenses', [])
    if not daily: return False
    
    sums_map = {}
    
    for row in daily:
        desc = str(row.get('적요', '')).strip()
        amt_v = clean_numeric(row.get('집행금액', 0))
        date_raw = str(row.get('집행일자', '')).strip()
        
        month_found = None
        month_match = re.search(r'2026(?:년)?\s*(\d{1,2})(?:월)?', desc)
        if month_match: month_found = int(month_match.group(1))
        if not month_found:
            dt_parts = re.split(r'[./-]', date_raw)
            if len(dt_parts) >= 2:
                if len(dt_parts[0]) == 4: month_found = int(dt_parts[1])
                else: month_found = int(dt_parts[0])
        if not month_found:
            simple_month = re.search(r'(\d{1,2})월', desc)
            if simple_month: month_found = int(simple_month.group(1))
        
        if not month_found or not (1 <= month_found <= 12): continue
        
        matched_cat = get_mapped_category(desc, row.get('세목', ''))
                    
        if matched_cat:
            if matched_cat not in sums_map: sums_map[matched_cat] = {}
            sums_map[matched_cat][month_found] = sums_map[matched_cat].get(month_found, 0) + amt_v

    data_changed = False
    for r in master_data.get('records', []):
        if r['year'] == 2026:
            cat, month = r['category'], r['month']
            new_val = sums_map.get(cat, {}).get(month, 0.0)
            if clean_numeric(r['amount']) != new_val:
                r['amount'] = new_val
                r['status'] = "지출" if new_val > 0 else "미지출"
                data_changed = True
                    
    if data_changed: 
        save_data_cloud(master_data); st.session_state['data'] = master_data; return True
    return False

# -----------------------------------------------------------------------------
# 엑셀 파싱 엔진 (새로운 양식)
# -----------------------------------------------------------------------------
def parse_expense_excel(df_raw):
    header_idx = -1; found_cols = {}
    keyword_detect = {
        '집행일자': ['일자','날짜','집행일','결의일자'], 
        '적요': ['적요','내용','품명','건명'], 
        '집행금액': ['금액','집행액','지출액','결재금액']
    }
    
    for i in range(min(25, len(df_raw))):
        row_vals = [re.sub(r'\s+', '', str(v)) for v in df_raw.iloc[i]]
        temp_map = {}
        for k, kws in keyword_detect.items():
            for idx, val in enumerate(row_vals):
                if any(kw in val for kw in kws): temp_map[k] = idx; break
        if len(temp_map) >= 3: 
            header_idx = i; found_cols = temp_map; break
            
    if header_idx == -1: return None
        
    data_rows = df_raw.iloc[header_idx+1:].copy()
    new_processed = []
    
    row_header_vals = [re.sub(r'\s+', '', str(v)) for v in df_raw.iloc[header_idx]]
    fallback_semok_idx = -1
    for idx, val in enumerate(row_header_vals):
        if any(kw in val for kw in ['세목','과목','항목']): fallback_semok_idx = idx; break

    for _, row in data_rows.iterrows():
        row_arr = row.values
        desc_idx = found_cols.get('적요', -1)
        desc = str(row_arr[desc_idx]).strip() if desc_idx != -1 and pd.notna(row_arr[desc_idx]) else ""
        if not desc or "합계" in desc: continue
        
        b_val = str(row_arr[1]).strip() if len(row_arr) > 1 and pd.notna(row_arr[1]) else ""
        f_val = str(row_arr[5]).strip() if len(row_arr) > 5 and pd.notna(row_arr[5]) else ""
        g_val = str(row_arr[6]).strip() if len(row_arr) > 6 and pd.notna(row_arr[6]) else ""
        
        semok_str = ""
        
        if b_val and (not f_val or not g_val):
            if b_val in BUDGET_MAPPING:
                f_val, g_val = BUDGET_MAPPING[b_val]
        
        if re.match(r'^\d+(-\d+)?$', b_val) and f_val and g_val:
            b_prefix = b_val.split('-')[0]
            semok_str = f"[{b_prefix}]{f_val} - [{b_val}]{g_val}"
        else:
            if fallback_semok_idx != -1 and pd.notna(row_arr[fallback_semok_idx]):
                semok_str = str(row_arr[fallback_semok_idx]).strip()
            elif re.match(r'^\d+(-\d+)?$', b_val):
                semok_str = b_val
        
        date_idx = found_cols.get('집행일자', -1)
        date_val = str(row_arr[date_idx]).strip() if date_idx != -1 and pd.notna(row_arr[date_idx]) else ""
        
        amt_idx = found_cols.get('집행금액', -1)
        amt_val = clean_numeric(row_arr[amt_idx]) if amt_idx != -1 else 0
        
        new_processed.append({
            "세목": semok_str, 
            "집행일자": date_val, 
            "적요": desc, 
            "집행금액": amt_val
        })
        
    return new_processed

# -----------------------------------------------------------------------------
# 트리 구조 유틸리티
# -----------------------------------------------------------------------------
def build_global_hierarchy_maps(df):
    parent_map, children_map = {}, {i: [] for i in df.index}
    stack = []
    for idx, row in df.iterrows():
        lvl = row['lvl_sys']
        while stack and stack[-1][0] >= lvl: stack.pop()
        parent_id = stack[-1][1] if stack else None
        parent_map[idx] = parent_id
        if parent_id is not None: children_map[parent_id].append(idx)
        stack.append((lvl, idx))
    return parent_map, children_map

def dfs_sum_v202(node_id, col_n, df, children_map, states):
    s = states.get(node_id, 0)
    if s == 0: return 0.0
    if s == 1: return float(df.loc[node_id, col_n])
    return sum(dfs_sum_v202(c, col_n, df, children_map, states) for c in children_map.get(node_id, []))

# -----------------------------------------------------------------------------
# 5. RPA 엔진
# -----------------------------------------------------------------------------
def run_daily_expense_rpa():
    log_container = st.empty(); logs = []
    def add_log(msg, type="info"):
        cls = "success-log" if type=="success" else "error-log" if type=="error" else "warn-log" if type=="warn" else ""
        logs.append(f'[{datetime.now().strftime("%H:%M:%S")}] <span class="{cls}">>> {msg}</span>')
        log_container.markdown(f'<div class="log-box">{"<br>".join(logs)}</div>', unsafe_allow_html=True)
    RAW_HOST, PORT = "14.53.46.247", 57013
    URL = f"http://{RAW_HOST}:{PORT}/home.do"
    driver = None
    try:
        add_log("통합시스템 자동 동기화를 시작합니다.")
        download_path = os.path.join(os.getcwd(), "temp_daily_sync")
        if not os.path.exists(download_path): os.makedirs(download_path)
        for f in glob.glob(os.path.join(download_path, "*")): os.remove(f)
        options = ChromeOptions()
        options.page_load_strategy = 'normal'
        options.add_experimental_option("prefs", {"download.default_directory": download_path, "safebrowsing.enabled": True})
        options.add_argument("--no-sandbox"); options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu"); options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.set_page_load_timeout(45); driver.get(URL)
        USER_ID, USER_PW = st.secrets["groupware"]["id"], st.secrets["groupware"]["pw"]
        try:
            wait = WebDriverWait(driver, 15)
            login_field = wait.until(EC.presence_of_element_located((By.NAME, "userid")))
            login_field.send_keys(USER_ID); driver.find_element(By.NAME, "password").send_keys(USER_PW + Keys.ENTER); add_log("로그인 완료")
        except: add_log("로그인 세션 확인 중", "warn")
        time.sleep(2); search_input = find_element_deep(driver, By.ID, "menu_search")
        if search_input:
            search_input.click(); search_input.clear(); search_input.send_keys("지출예산통제원장(사업별)"); time.sleep(2)
            try:
                target_path = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), '재무회계 / 자금관리 / 지출예산통제원장(사업별)')]")))
                driver.execute_script("arguments[0].click();", target_path); add_log("메뉴 진입 성공", "success")
            except: driver.find_element(By.XPATH, "//a[contains(text(), '지출예산통제원장(사업별)')]").click()
        time.sleep(2); main_h = driver.current_window_handle
        pop_btn = find_element_deep(driver, By.ID, "btn_DtlBizPop")
        if pop_btn: driver.execute_script("arguments[0].click();", pop_btn)
        WebDriverWait(driver, 10).until(lambda d: len(d.window_handles) > 1)
        new_handles = [h for h in driver.window_handles if h != main_h]
        driver.switch_to.window(new_handles[-1]); driver.maximize_window(); add_log("팝업창 전환 완료", "success")
        search_box = find_element_deep(driver, By.ID, "searchText", timeout=10)
        if search_box:
            search_box.clear(); search_box.send_keys("정약용")
            search_btn = find_element_deep(driver, By.XPATH, "//span[text()='조회']", timeout=7)
            if search_btn: driver.execute_script("arguments[0].click();", search_btn); time.sleep(2)
            target_td = find_element_deep(driver, By.XPATH, "//td[contains(@title, '정약용 펀그라운드 운영·관리(일상경비)')]", timeout=10)
            if target_td:
                driver.execute_script("arguments[0].click();", target_td); chk_img = find_element_deep(driver, By.CSS_SELECTOR, "img[src*='item_chk']", timeout=7)
                if chk_img: chk_img.click()
                move_btn = find_element_deep(driver, By.XPATH, "//span[text()='>>']", timeout=7)
                if move_btn: move_btn.click()
                time.sleep(1); select_final = find_element_deep(driver, By.XPATH, "//span[text()='선택']", timeout=7)
                if select_final: select_final.click()
        driver.switch_to.window(main_h); add_log("메인 작업 화면 복귀")
        find_element_deep(driver, By.XPATH, "//span[text()='조회']").click(); time.sleep(3)
        find_element_deep(driver, By.XPATH, "//span[text()='엑셀다운로드']").click()
        latest_file = None
        for _ in range(40):
            files = [f for f in glob.glob(os.path.join(download_path, "*.xlsx")) if "crdownload" not in f]
            if files: latest_file = max(files, key=os.path.getctime); break
            time.sleep(1)
        if latest_file:
            df_raw = pd.read_excel(latest_file, header=None)
            new_processed = parse_expense_excel(df_raw)
            if new_processed:
                current_exp = st.session_state.get('daily_expenses', [])
                merged_exp, added_count = merge_expenses(current_exp, new_processed)
                if save_daily_expenses(merged_exp): 
                    st.session_state['daily_expenses'] = merged_exp
                    sync_daily_to_master_auto() 
                    add_log(f"동기화 성공! ({added_count}건 추가됨)", "success")
        driver.quit(); time.sleep(1); st.rerun()
    except Exception as e:
        add_log(f"RPA 오류: {str(e)}", "error")
        if driver: driver.quit()

def find_element_deep(driver, by, value, timeout=12):
    try:
        el = WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, value)))
        if el.is_displayed(): return el
    except: pass
    driver.switch_to.default_content()
    def search_in_frames():
        frames = driver.find_elements(By.TAG_NAME, "frame") + driver.find_elements(By.TAG_NAME, "iframe")
        for frame in frames:
            try:
                driver.switch_to.frame(frame)
                els = driver.find_elements(by, value)
                if els and els[0].is_displayed(): return els[0]
                res = search_in_frames(); 
                if res: return res
                driver.switch_to.parent_frame()
            except: pass
        return None
    return search_in_frames()

# -----------------------------------------------------------------------------
# 6. 세션 데이터 초기화 
# -----------------------------------------------------------------------------
if 'amt_box' not in st.session_state: st.session_state.amt_box = 0
if 'data' not in st.session_state: st.session_state['data'] = load_data()
if 'rapid_df' not in st.session_state: st.session_state['rapid_df'] = load_rapid_df()
if 'daily_expenses' not in st.session_state: st.session_state['daily_expenses'] = load_daily_expenses()
if 'tree_expanded' not in st.session_state or st.session_state['tree_expanded'] is None: 
    st.session_state['tree_expanded'] = set()
if 'tree_states' not in st.session_state: st.session_state['tree_states'] = {}
if 'last_file_hash' not in st.session_state: st.session_state['last_file_hash'] = None

master_data_raw = st.session_state['data']
master_data_raw = ensure_data_integrity(master_data_raw)
df_all = pd.DataFrame(master_data_raw.get("records", []))
if not df_all.empty: df_all["amount"] = pd.to_numeric(df_all["amount"], errors='coerce').fillna(0).astype('float64')

if st.session_state.get('daily_expenses') and 'initial_sync_done' not in st.session_state:
    sync_daily_to_master_auto()
    st.session_state['initial_sync_done'] = True

# --- 사이드바 ---
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3135/3135715.png", width=60)
    st.title("지출 관리 콘솔")
    if st.button("💾 데이터 수동 백업", use_container_width=True):
        if save_data_cloud(st.session_state['data']): st.success("저장 완료")
    if st.button("🔄 데이터 강제 새로고침", use_container_width=True):
        st.session_state['data'] = load_data(); st.session_state['daily_expenses'] = load_daily_expenses(); st.rerun()
    st.divider(); st.caption(f"시스템 ID: {appId}")

# --- 스타일 가이드 ---
st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;900&display=swap');
        html, body, [class*="css"] { font-family: 'Noto Sans KR', sans-serif !important; color: #1e293b; }
        .stApp { background-color: #f1f5f9; }
        .block-container { padding-top: 2rem; padding-left: 5rem; padding-right: 5rem; max-width: 98% !important; }
        .section-header { font-size: 1.4rem; font-weight: 900; color: #1e3a8a; margin: 35px 0 20px 0; border-left: 7px solid #2563eb; padding-left: 15px; }
        .metric-card { background: white; padding: 22px; border-radius: 18px; border-left: 10px solid #3b82f6; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.08); margin-bottom: 12px; }
        .metric-value { font-size: 2rem; font-weight: 900; color: #0f172a; letter-spacing: -1px; }
        .quick-exec-card-scarlet { background: white; border: 1px solid #fda4af; padding: 25px; border-radius: 20px; border-left: 12px solid #e11d48; margin-bottom: 25px; box-shadow: 0 10px 25px -5px rgba(225, 29, 72, 0.15); }
        .timeline-card { background: white; border: 1px solid #e2e8f0; border-radius: 16px; padding: 18px; margin-bottom: 12px; transition: all 0.3s; position: relative; min-height: 230px; display: flex; flex-direction: column; }
        .current-badge { position: absolute; top: -12px; right: 12px; background: #2563eb; color: white; padding: 4px 12px; border-radius: 10px; font-size: 0.8rem; font-weight: 900; }
        .theme-blue { border-left: 12px solid #2563eb; }
        .theme-green { border-left: 12px solid #059669; }
        .theme-orange { border-left: 12px solid #d97706; }
        .budget-card-simple { background: white; border: 1px solid #e2e8f0; padding: 22px; border-radius: 16px; margin-bottom: 12px; border-left: 10px solid #10b981; }
        .korean-amount { background-color: #f0f7ff; padding: 10px 18px; border-radius: 12px; border: 1px solid #cce3ff; color: #1e40af; font-weight: 800; margin-top: 5px; display: block; font-size: 1.1rem; text-align: right; }
    </style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 7. 메인 UI
# -----------------------------------------------------------------------------
st.title("🏢 2026 월별 지출관리 및 실시간 분석")
tabs = st.tabs(["📊 실적 현황", "📈 항목별 지출 분석", "🚨 미집행 누락", "📂 일상경비 동기화", "🚀 신속집행 대시보드", "📂 1~12월 정량실적"])

# --- TAB 1: 실적 현황 ---
with tabs[0]:
    if not df_all.empty:
        df_26 = df_all[df_all["year"] == 2026].copy(); val_26 = df_26["amount"].sum()
        st.markdown(f"""<div class="metric-card"><div class="metric-label">🏢 2026년 누적 지출액 (자동 연동 중)</div><div class="metric-value">{format(int(val_26), ",")} <span style="font-size:1rem; color:#94a3b8;">원</span></div></div>""", unsafe_allow_html=True)
    c_s, c_i, c_p = st.columns([0.8, 1.2, 2.1])
    with c_s:
        st.markdown('<b style="font-size:1.1rem; color:#1e3a8a; border-left:5px solid #2563eb; padding-left:10px;">🚀 상반기 신속집행</b>', unsafe_allow_html=True)
        for cat in ["수탁자산취득비", "일반재료비", "상품매입비"]:
            df_t = df_26[df_26["category"] == cat] if not df_all.empty else pd.DataFrame(); conf = QUICK_EXEC_CONFIG.get(cat, {"goal_q1":1, "goal_h1":1})
            q1_v = pd.to_numeric(df_t[df_t["month"] <= 3]["amount"], errors='coerce').fillna(0).sum()
            h1_v = pd.to_numeric(df_t[df_t["month"] <= 6]["amount"], errors='coerce').fillna(0).sum()
            q1_p = (q1_v / conf['goal_q1']) * 100 if conf['goal_q1'] > 0 else 0
            h1_p = (h1_v / conf['goal_h1']) * 100 if conf['goal_h1'] > 0 else 0
            st.markdown(f"""<div style="background:white; border:1px solid #e2e8f0; padding:15px; border-radius:15px; margin-bottom:12px;"><b style="color:#1e3a8a;">{cat}</b><br><small style="color:#64748b;">1Q: {q1_p:.1f}% | 1H: {h1_p:.1f}%</small></div>""", unsafe_allow_html=True)
    with c_i:
        st.markdown('<b style="font-size:1.1rem; color:#1e3a8a; border-left:5px solid #2563eb; padding-left:10px;">📝 지출액 직접 등록</b>', unsafe_allow_html=True)
        iy, ic, im = st.selectbox("연도", YEARS, index=2, key="y_reg"), st.selectbox("항목", CATEGORIES, key="c_reg"), st.selectbox("월", MONTHS, format_func=lambda x: f"{x}월", key="m_reg")
        st.number_input("금액 (원)", min_value=0, step=10000, key="amt_box")
        st.markdown(f'<div class="korean-amount">{number_to_korean(st.session_state.amt_box)}</div>', unsafe_allow_html=True)
        bc1, bc2, bc3 = st.columns(3); bc1.button("+10만", on_click=update_amt, args=(100000,), use_container_width=True); bc2.button("+100만", on_click=update_amt, args=(1000000,), use_container_width=True); bc3.button("🔄 리셋", on_click=reset_amt, use_container_width=True)
        st.button("💾 데이터 저장", type="primary", use_container_width=True, on_click=save_and_register, args=(iy, ic, im))
    with c_p:
        st.markdown('<b style="font-size:1.1rem; color:#1e3a8a; border-left:5px solid #2563eb; padding-left:10px;">🍩 지출 비중 분포</b>', unsafe_allow_html=True)
        cat_dist = df_26.groupby("category")["amount"].sum().reset_index() if not df_all.empty else pd.DataFrame()
        if not cat_dist.empty and cat_dist["amount"].sum() > 0:
            fig = alt.Chart(cat_dist).mark_arc(innerRadius=85, stroke="#fff").encode(theta="amount:Q", color=alt.Color("category:N", scale=alt.Scale(scheme='tableau20'))).properties(height=380)
            st.altair_chart(fig, use_container_width=True)

# --- TAB 2: 항목별 지출 분석 ---
with tabs[1]:
    st.markdown('<div class="section-header">📈 지능형 분석 및 실시간 연동 (Semantic Sync)</div>', unsafe_allow_html=True)
    if st.button("🔄 미디어실 포함 모든 지출내역 연동 실행", type="primary", use_container_width=True):
        if sync_daily_to_master_auto():
            st.success("✅ 동기화 완료! 실적이 갱신되었습니다."); time.sleep(1); st.rerun()
        else: st.info("ℹ️ 현재 업데이트할 새로운 내역이 없습니다.")
    
    sc = st.selectbox("관리 항목 선택", CATEGORIES, key="analysis_sel_v252")
    df_c = df_all[df_all["category"] == sc] if not df_all.empty else pd.DataFrame()
    
    if not df_c.empty:
        if sc in QUICK_EXEC_CONFIG:
            cf = QUICK_EXEC_CONFIG[sc]; q1_e = df_c[(df_c["year"] == 2026) & (df_c["month"] <= 3)]["amount"].sum(); h1_e = df_c[(df_c["year"] == 2026) & (df_c["month"] <= 6)]["amount"].sum()
            html_scarlet = f"""<div class="quick-exec-card-scarlet"><div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;"><span class="quick-exec-badge-scarlet">🚀 2026 신속집행 특별관리 대상</span><span style="font-size:1.1rem; color:#be123c; font-weight:900;">대상액: {cf['target']:,}원</span></div><div style="display:grid; grid-template-columns: 1fr 1fr; gap:30px;"><div><div style="display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:12px;"><span style="font-weight:800; color:#1e3a8a; font-size:1.1rem;">● 1분기 목표 (40%)</span><span style="font-size:2.2rem; font-weight:900; color:#2563eb;">{(q1_e/cf['goal_q1'])*100 if cf['goal_q1']>0 else 0:.1f}%</span></div><div style="background-color:#e2e8f0; height:14px; border-radius:12px; overflow:hidden;"><div style="background:linear-gradient(to right, #3b82f6, #2563eb); width:{min((q1_e/cf['goal_q1'])*100 if cf['goal_q1']>0 else 0, 100):.1f}%; height:100%;"></div></div></div><div><div style="display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:12px;"><span style="font-weight:800; color:#be123c; font-size:1.1rem;">● 상반기 목표 (70%)</span><span style="font-size:2.2rem; font-weight:900; color:#e11d48;">{(h1_e/cf['goal_h1'])*100 if cf['goal_h1']>0 else 0:.1f}%</span></div><div style="background-color:#e2e8f0; height:14px; border-radius:12px; margin-top:10px; overflow:hidden;"><div style="background:linear-gradient(to right, #fb7185, #e11d48); width:{min((h1_e/cf['goal_h1'])*100 if cf['goal_h1']>0 else 0, 100):.1f}%; height:100%;"></div></div></div></div></div>"""
            st.markdown(html_scarlet, unsafe_allow_html=True)
        
        m_cols = st.columns(3); v24, v25, v26 = df_c[df_c['year']==2024]['amount'].sum(), df_c[df_c['year']==2025]['amount'].sum(), df_c[df_c['year']==2026]['amount'].sum()
        m_cols[0].markdown(f'''<div class="metric-card" style="border-left-color: #94a3b8;"><div class="metric-label">📊 2024 실적</div><div class="metric-value">{int(v24):,}<span style="font-size:0.9rem; margin-left:5px; color:#94a3b8;">원</span></div></div>''', unsafe_allow_html=True); m_cols[1].markdown(f'''<div class="metric-card" style="border-left-color: #10b981;"><div class="metric-label">📊 2025 실적</div><div class="metric-value">{int(v25):,}<span style="font-size:0.9rem; margin-left:5px; color:#94a3b8;">원</span></div></div>''', unsafe_allow_html=True); m_cols[2].markdown(f'''<div class="metric-card" style="border-left-color: #3b82f6;"><div class="metric-label">📅 2026 연동 실적</div><div class="metric-value">{int(v26):,}<span style="font-size:0.9rem; margin-left:5px; color:#94a3b8;">원</span></div></div>''', unsafe_allow_html=True)
        
        df_p_c = df_c.pivot(index="month", columns="year", values="amount").fillna(0).reindex(columns=YEARS, fill_value=0); df_p_c.columns = [f"{c}년" for c in df_p_c.columns]; df_d_c = df_p_c.map(lambda x: format(int(x), ",")).reset_index(); df_d_c["월"] = df_d_c["month"].apply(lambda x: f"{x}월")
        st.data_editor(df_d_c[["월", "2024년", "2025년", "2026년"]], use_container_width=True, hide_index=True, key=f"ed_v252_{sc}", height=450)

# --- TAB 3: 미집행 현황 ---
with tabs[2]:
    st.markdown('<span class="section-label">🚨 지출 누락 점검</span>', unsafe_allow_html=True); now = datetime.now(); cy, cm = now.year, now.month
    st.info(f"📅 기준일: {cy}년 {cm}월 | 누락된 지출 내역을 확인하세요.")
    if not df_all.empty:
        for idx, y in enumerate(YEARS):
            with st.container():
                st.subheader(f"📅 {y}년"); df_y = df_all[df_all["year"] == y]
                for cat in CATEGORIES:
                    cond = (df_y["category"] == cat) & (df_y["amount"] <= 0) & (df_y["status"] == "지출")
                    if y == cy: cond = cond & (df_y["month"] < cm)
                    elif y > cy: cond = False 
                    missing = df_y[cond]["month"].tolist() if not isinstance(cond, bool) else []
                    if missing: st.error(f"**{cat}**: {', '.join(map(str, sorted(missing)))}월 누락")

# --- TAB 4: 일상경비 지출현황 (V252 엑셀 다운로드 버튼 추가) ---
with tabs[3]:
    st.markdown('<div class="section-header">📂 일상경비 데이터베이스 (세목명 자동 병합)</div>', unsafe_allow_html=True)
    with st.expander("📥 엑셀 수동 업로드 (B, F, G열 스마트 조합)"):
        f = st.file_uploader("엑셀 또는 CSV 파일 선택", type=["xlsx", "csv"], key="daily_up_v252")
        if f:
            file_content = f.read(); file_hash = hashlib.md5(file_content).hexdigest(); f.seek(0)
            if st.session_state['last_file_hash'] != file_hash:
                with st.spinner("새로운 엑셀 양식을 분석 중입니다..."):
                    df_raw = pd.read_excel(f, header=None, engine='openpyxl') if f.name.endswith('.xlsx') else pd.read_csv(f, header=None)
                    new_processed = parse_expense_excel(df_raw)
                    if new_processed is not None and new_processed:
                        merged_expenses, added_count = merge_expenses(st.session_state.get('daily_expenses', []), new_processed)
                        if save_daily_expenses(merged_expenses):
                            st.session_state['daily_expenses'] = merged_expenses; st.session_state['last_file_hash'] = file_hash
                            sync_daily_to_master_auto()
                            st.success(f"✅ 동기화 완료! 새로운 내역 {added_count}건이 병합되었습니다."); time.sleep(1); st.rerun()
                    else: st.error("❌ 엑셀 헤더를 찾을 수 없습니다. 파일 양식을 확인해주세요.")
    
    daily_data = st.session_state.get('daily_expenses', [])
    if daily_data:
        df_d = pd.DataFrame(daily_data)
        df_d['세목'] = df_d['세목'].astype(str)
        
        def extract_budget_code(s):
            codes = re.findall(r'(?<!\d)(\d{3}(?:-\d{2})?)(?!\d)', s.strip())
            return codes[-1] if codes else s.strip()
            
        df_d['temp_code'] = df_d['세목'].apply(extract_budget_code)
        best_names = df_d.groupby('temp_code')['세목'].apply(lambda x: max(x.astype(str), key=len)).to_dict()
        
        def get_full_semok_name(code, current_best):
            if current_best and "[" in current_best and "]" in current_best: return current_best
            if code in BUDGET_MAPPING:
                f_val, g_val = BUDGET_MAPPING[code]
                b_prefix = code.split('-')[0]
                return f"[{b_prefix}]{f_val} - [{code}]{g_val}"
            return current_best if current_best else code

        cleaned_semok = df_d['temp_code'].apply(lambda c: get_full_semok_name(c, best_names.get(c, "")))
        
        changed = False
        for idx, row in df_d.iterrows():
            if daily_data[idx].get('세목', '') != cleaned_semok.iloc[idx]:
                daily_data[idx]['세목'] = cleaned_semok.iloc[idx]; changed = True
                
        if changed:
            st.session_state['daily_expenses'] = daily_data
            save_daily_expenses(daily_data)
            
        df_d['세목'] = cleaned_semok
        df_d = df_d.drop(columns=['temp_code'])
        
        c1, c2 = st.columns(2)
        c1.markdown(f'<div class="metric-card"><div class="metric-label">💰 누적 집행 총액</div><div class="metric-value">{int(df_d["집행금액"].sum()):,}<span style="font-size:1rem; color:#94a3b8;">원</span></div></div>', unsafe_allow_html=True)
        c2.markdown(f'<div class="metric-card" style="border-left-color:#10b981;"><div class="metric-label">📝 누적 지출 건수</div><div class="metric-value">{len(df_d)} 건</div></div>', unsafe_allow_html=True)
        
        fc1, fc2, fc3 = st.columns([1.5, 2, 1])
        fcat = fc1.selectbox("세목 필터", ["전체"] + sorted(df_d["세목"].unique().tolist()), key="f1_v252")
        sq = fc2.text_input("적요 내용 검색", key="f2_v252")
        
        disp = df_d.copy(); 
        if fcat != "전체": disp = disp[disp["세목"] == fcat]
        if sq: disp = disp[disp["적요"].str.contains(sq, na=False)]
        
        # [V252 핵심] 엑셀 다운로드 버튼을 위한 데이터 변환 (순수 숫자 유지)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            disp.to_excel(writer, index=False, sheet_name='일상경비지출내역')
        excel_data = output.getvalue()
        
        with fc3:
            st.markdown("<div style='margin-top: 29px;'></div>", unsafe_allow_html=True)
            st.download_button(
                label="📥 현재 내역 엑셀 다운로드",
                data=excel_data,
                file_name=f"일상경비지출내역_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        
        # 화면 표출용으로만 포맷팅 적용
        disp["집행금액"] = disp["집행금액"].map(lambda x: format(int(x), ",")); 
        st.dataframe(disp, use_container_width=True, height=650, hide_index=True)

# --- TAB 5: 신속집행 대시보드 ---
with tabs[4]:
    st.markdown('<div class="section-header">📂 2026 상반기 신속집행 실시간 관리 (계획 대비 실적)</div>', unsafe_allow_html=True)
    
    df_view = st.session_state['rapid_df'].copy()
    for c in ["대상액", "집행예정액", "실제집행액"]:
        if c in df_view.columns: df_view[c] = pd.to_numeric(df_view[c], errors="coerce").fillna(0.0)
    
    if not df_all.empty:
        for idx, row in df_view.iterrows():
            cat = row['세목']
            try:
                m_num = int(str(row['월']).replace('월', ''))
                actual_val = df_all[(df_all['year'] == 2026) & (df_all['category'] == cat) & (df_all['month'] == m_num)]['amount'].sum()
                df_view.loc[idx, '실제집행액'] = float(actual_val)
            except: pass
        st.session_state['rapid_df'] = df_view.copy()

    daily_list = st.session_state.get('daily_expenses', [])
    df_daily = pd.DataFrame(daily_list) if daily_list else pd.DataFrame(columns=["집행일자", "적요", "집행금액", "세목"])
    if not df_daily.empty:
        df_daily['MappedCategory'] = df_daily.apply(lambda r: get_mapped_category(r.get('적요',''), r.get('세목','')), axis=1)

    current_m = datetime.now().month

    summary_list = []
    for cat in CORE_TARGETS:
        sub = df_view[df_view["세목"] == cat]
        t_amt = float(sub["대상액"].max()) if not sub.empty else 0.0
        e_amt = float(sub["실제집행액"].sum()) if not sub.empty else 0.0
        
        sub['month_num'] = sub['월'].apply(lambda x: int(str(x).replace('월','')))
        plan_to_date = sub[sub['month_num'] <= current_m]["집행예정액"].sum()
        
        summary_list.append({
            "세목": cat, "대상액": t_amt, "누적계획": plan_to_date, "집행액": e_amt, 
            "총달성률": (e_amt/t_amt*100) if t_amt > 0 else 0, "계획대비달성률": (e_amt/plan_to_date*100) if plan_to_date > 0 else 0
        })
        
    df_summary = pd.DataFrame(summary_list)

    k1, k2, k3 = st.columns(3)
    k1.markdown(f'<div class="metric-card"><div class="metric-label">🎯 상반기 총 대상액</div><div class="metric-value">{int(df_summary["대상액"].sum()):,} 원</div></div>', unsafe_allow_html=True)
    k2.markdown(f'<div class="metric-card" style="border-left-color:#10b981;"><div class="metric-label">✅ 현재까지 총 집행 실적</div><div class="metric-value">{int(df_summary["집행액"].sum()):,} 원</div></div>', unsafe_allow_html=True)
    k3.markdown(f'<div class="metric-card" style="border-left-color:#f59e0b;"><div class="metric-label">📊 평균 달성률 (총액 대비)</div><div class="metric-value">{(df_summary["집행액"].sum()/df_summary["대상액"].sum()*100 if df_summary["대상액"].sum()>0 else 0):.1f}%</div></div>', unsafe_allow_html=True)

    c_cols = st.columns(3)
    for i, row in df_summary.iterrows():
        cat = row['세목']; conf = CORE_CONFIG[cat]
        with c_cols[i]:
            html_card = f"""<div class="budget-card-simple" style="border-left-color:{conf['border']}; height:100%;">
<div class="budget-simple-title"><span>{conf['icon']} {cat}</span><span class="rate-badge" style="font-size:0.9rem;">총 {row['총달성률']:.1f}%</span></div>
<div class="budget-simple-row"><span class="val-target">전체 대상액</span><span style="font-weight:700;">{int(row['대상액']):,}원</span></div>
<div class="budget-simple-row"><span class="val-target">{current_m}월 누적 계획</span><span style="color:#64748b; font-weight:700;">{int(row['누적계획']):,}원</span></div>
<div class="budget-simple-row"><span class="val-target">{current_m}월 누적 실적</span><span style="color:{conf['color']}; font-weight:900;">{int(row['집행액']):,}원</span></div>
<hr style="margin: 12px 0; border-top: 1px dashed #e2e8f0;">
<div style="font-size:0.85rem; font-weight:bold; color:#475569; display:flex; justify-content:space-between;"><span>현재 페이스 (계획 대비)</span><span style="color:{conf['color']}; font-size:1.1rem;">{row['계획대비달성률']:.1f}%</span></div>
<div style="background:#f1f5f9; height:12px; border-radius:10px; overflow:hidden; margin-top:6px;">
<div style="background:linear-gradient(90deg, {conf['border']}, {conf['color']}); width:{min(row['계획대비달성률'], 100)}%; height:100%;"></div>
</div></div>"""
            st.markdown(html_card, unsafe_allow_html=True)

    for cat in CORE_TARGETS:
        conf = CORE_CONFIG[cat]
        with st.expander(f"📅 {cat} 상세 트래킹 및 일상경비 지출 내역", expanded=False):
            t_cols = st.columns(6); cat_df = df_view[df_view["세목"] == cat].sort_values("월").reset_index(drop=True)
            for idx, m_row in cat_df.iterrows():
                m_idx = idx + 1; p_val, a_val = m_row['집행예정액'], m_row['실제집행액']
                card_class = "timeline-card " + ("theme-blue" if cat=="수탁자산취득비" else ("theme-green" if cat=="일반재료비" else "theme-orange"))
                if m_idx < current_m: card_class += " timeline-past"; opacity = "0.7"
                elif m_idx == current_m: card_class += " timeline-current"; opacity = "1.0"
                else: card_class += " timeline-future"; opacity = "1.0"
                with t_cols[idx]:
                    badge = '<div class="current-badge">현재월</div>' if m_idx == current_m else ""
                    html_timeline = f"""<div class="{card_class}" style="opacity:{opacity};">{badge}<div class="month-label"><span>{m_idx}월</span></div><div class="detail-box" style="font-size:0.75rem; color:#475569; margin:10px 0; min-height:50px;">{conf['details'].get(m_idx, "-")}</div><div class="plan-box">계획: {int(p_val):,}</div><div class="actual-box" style="color:#10b981;">{int(a_val):,} 원</div></div>"""
                    st.markdown(html_timeline, unsafe_allow_html=True)
            
            st.markdown(f"###### 📋 [ {cat} ] 실제 지출 상세 내역 (일상경비 연동)")
            if not df_daily.empty:
                cat_daily = df_daily[df_daily['MappedCategory'] == cat]
                if not cat_daily.empty:
                    disp_df = cat_daily[['집행일자', '적요', '집행금액']].copy()
                    disp_df = disp_df.sort_values("집행일자", ascending=False)
                    disp_df['집행금액'] = disp_df['집행금액'].apply(lambda x: f"{int(x):,} 원")
                    st.dataframe(disp_df, use_container_width=True, hide_index=True)
                else:
                    st.info(f"아직 '{cat}' 항목으로 분류된 일상경비 지출 내역이 없습니다.")
            else:
                st.info("업로드된 일상경비 데이터가 없습니다. 먼저 '📂 일상경비 동기화' 탭에서 데이터를 업로드해주세요.")
    
    st.markdown('<div class="section-header">📝 대상액 / 집행예정액 계획 수정</div>', unsafe_allow_html=True)
    edited_df = st.data_editor(
        st.session_state['rapid_df'], 
        use_container_width=True, 
        hide_index=True, 
        column_config={
            "세목": st.column_config.TextColumn(disabled=True), 
            "월": st.column_config.TextColumn(disabled=True), 
            "대상액": st.column_config.NumberColumn("대상액 (원)", format="%,d"), 
            "집행예정액": st.column_config.NumberColumn("집행예정액 (원)", format="%,d"), 
            "실제집행액": st.column_config.NumberColumn("실제집행액 (자동 연동됨)", format="%,d", disabled=True)
        }, 
        key="rapid_editor_v252"
    )
    if edited_df is not None and not edited_df.equals(st.session_state['rapid_df']):
        st.session_state['rapid_df'] = edited_df; st.rerun()

    if st.button("💾 데이터 클라우드 영구 저장", type="primary", use_container_width=True, key="save_rapid_btn_v252"):
        if save_rapid_df(st.session_state['rapid_df']): st.success("클라우드 저장 성공"); time.sleep(0.5); st.rerun()

# --- TAB 6: 정량실적 ---
with tabs[5]:
    st.markdown('<div class="section-header">📊 1~12월 정량실적 및 무결성 합계</div>', unsafe_allow_html=True)
    c_y, c_m = st.columns([1, 3])
    sel_year = c_y.radio("조회 연도", YEARS, index=2, horizontal=True, key="ry_v252_q")
    sel_months = c_m.multiselect("조회 월 선택", MONTHS, default=[1], format_func=lambda x: f"{x}월", key="rm_v252_q")
    
    all_data_list = []
    for m in sorted(sel_months):
        raw = load_quant_monthly(sel_year, m)
        if raw:
            m_df = pd.DataFrame(raw); m_df["month_label"] = f"{m}월 지출액"; m_df["original_idx"] = m_df.index; all_data_list.append(m_df)
            
    if all_data_list:
        full_df = pd.concat(all_data_list, ignore_index=True)
        pivot_spent = full_df.pivot_table(index='original_idx', columns='month_label', values='지출액', aggfunc='sum').reset_index()
        m_info = full_df.groupby('original_idx').agg({'구분': 'first', '예산액': 'max', '예산배정': 'max'}).reset_index()
        master_df = pd.merge(m_info, pivot_spent, on='original_idx', how='left').fillna(0).sort_values("original_idx").reset_index(drop=True)
        
        def get_ws_cnt(s): return len(str(s)) - len(str(s).lstrip())
        master_df['lvl_raw'] = master_df['구분'].apply(get_ws_cnt)
        unique_indents = sorted(master_df['lvl_raw'].unique())
        rank_map = {val: i for i, val in enumerate(unique_indents)}
        master_df['lvl_sys'] = master_df['lvl_raw'].map(rank_map).astype(int)
        
        parent_map, children_map = build_global_hierarchy_maps(master_df)
        base_row = master_df[master_df["구분"].str.contains("사업예산")]
        base_id = int(base_row.index[0]) if not base_row.empty else 0

        tree_col, float_col = st.columns([0.75, 0.25])
        with float_col:
            st.markdown('<div class="sticky-summary">', unsafe_allow_html=True); st.markdown("##### 📊 실시간 합계 패널")
            for m in sorted(sel_months):
                col_n = f"{m}월 지출액"; val = dfs_sum_v202(base_id, col_n, master_df, children_map, st.session_state['tree_states'])
                st.markdown(f'<div class="metric-card" style="border-left-color:#10b981; padding:15px;"><div class="metric-label">{m}월 선택 실적</div><div class="metric-value" style="font-size:1.6rem;">{int(val):,} 원</div></div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
        with tree_col:
            for idx, row in master_df.iterrows():
                p_id = parent_map.get(idx)
                if p_id is not None and p_id not in st.session_state.get('tree_expanded', set()): continue
                state = st.session_state['tree_states'].get(idx, 0); icon_chk = "✅" if state == 1 else ("➖" if state == 2 else "⬜")
                has_children = len(children_map.get(idx, [])) > 0; indent_px = row['lvl_sys'] * 12 
                with st.container():
                    cols = st.columns([0.4, 0.4, 4.0, 1.2, 1.2] + [1.2]*len(sel_months))
                    if cols[0].button(icon_chk, key=f"chk_v252_{idx}"):
                        new_st = 0 if state in (1, 2) else 1
                        set_subtree_state_v202(idx, new_st, children_map, st.session_state['tree_states'])
                        refresh_ancestors_v202(idx, parent_map, children_map, st.session_state['tree_states']); st.rerun()
                    if has_children:
                        expanded_set = st.session_state.get('tree_expanded', set())
                        ex_icon = "▼" if idx in expanded_set else "▶"
                        if cols[1].button(ex_icon, key=f"tog_v252_{idx}"):
                            if idx in expanded_set: expanded_set.remove(idx)
                            else: expanded_set.add(idx)
                            st.session_state['tree_expanded'] = expanded_set; st.rerun()
                    label_html = f'<div class="tree-label" style="padding-left: {indent_px}px;">{row["구분"].strip()}</div>'
                    cols[2].markdown(label_html, unsafe_allow_html=True)
                    cols[3].write(f"{int(row['예산액']):,}")
                    cols[4].write(f"{int(row['예산배정']):,}")
    else: st.info("데이터가 없습니다.")