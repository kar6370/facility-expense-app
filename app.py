import streamlit as st
import pandas as pd
import altair as alt
import json
import os
import time
import io
import re
import glob
import math
import hashlib
from datetime import datetime
from dataclasses import dataclass
import warnings

# 시스템 경고(Warning) 도스창 도배 차단
warnings.filterwarnings('ignore')
pd.options.mode.chained_assignment = None

# Firebase Admin SDK 관련 임포트
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except ImportError:
    st.error("라이브러리 로드 실패: 'firebase-admin'이 설치되어 있지 않습니다.")

# -----------------------------------------------------------------------------
# 1. 페이지 설정
# -----------------------------------------------------------------------------
st.set_page_config(page_title="2026 월별 지출관리", layout="wide", page_icon="🏢")

# -----------------------------------------------------------------------------
# 2. 글로벌 상수, 설정 및 과거 실적 데이터베이스 내장
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

BUDGET_MAPPING = {
    "101-01": ("인건비", "보수"), "101-03": ("인건비", "공무직(무기계약)근로자보수"), "101-04": ("인건비", "기간제근로자등보수"),
    "107-03": ("퇴직급여", "퇴직급여"), "109-01": ("평가급및성과금등", "일반직평가급등"), "109-02": ("평가급및성과금등", "공무직(무기계약)근로자평가급등"),
    "201-01": ("일반운영비", "사무관리비"), "201-02": ("일반운영비", "공공운영비"), "201-03": ("일반운영비", "행사운영비"),
    "201-11": ("일반운영비", "지급수수료"), "201-12": ("일반운영비", "교육훈련비"), "201-13": ("일반운영비", "임차료"),
    "201-14": ("일반운영비", "회의비"), "201-15": ("일반운영비", "복리후생비"), "201-21": ("일반운영비", "공공요금및제세"),
    "202-01": ("여비", "국내여비"), "202-08": ("여비", "공무직(무기계약직)근로자등여비"),
    "204-02": ("직무수행경비", "직급보조비"), "206-01": ("재료비", "일반재료비"), "207-02": ("연구개발비", "전산개발비"),
    "214-05": ("수선유지교체비", "수선유지비"), "215": ("동력비", "동력비"),
    "217-01": ("관서업무비", "정원가산업무비"), "217-02": ("관서업무비", "부서업무비"), "233": ("상품매입비", "상품매입비"),
    "301-09": ("일반보전금", "행사실비지원금"), "304-01": ("연금부담금등", "연금부담금"),
    "304-02": ("연금부담금등", "국민건강보험부담금등"), "304-03": ("연금부담금등", "공무직(무기계약)부담금관련"),
    "304-05": ("연금부담금등", "기간제근로자부담금관련"), "802-11": ("반환금기타", "대행사업비반환금"),
    "405-12": ("자산취득비", "수탁자산취득비")
}

# 2024~2025 유실 데이터 영구 복구용 내장 데이터
HISTORICAL_DATA = {
    2024: {
        "전기요금": [12561820, 12073930, 22545410, 8170188, 6459680, 5748710, 6928710, 10029560, 8288670, 6146590, 5670020, 8709400],
        "상하수도": [401210, 739720, 1377500, 844660, 1503310, 718050, 637780, 599160, 1287740, 725140, 847570, 451900],
        "통신요금": [1023050, 1045830, 1043690, 1044690, 1042290, 1033580, 1028770, 1040450, 1034740, 1032310, 1033430, 1042740],
        "복합기임대": [416900, 318890, 581100, 224260, 234160, 307930, 522720, 481470, 405760, 254800, 316490, 244790]
    },
    2025: {
        "전기요금": [11782300, 11836830, 9452350, 7074860, 6167830, 6167830, 8266720, 0, 8551300, 7147870, 7589840, 0],
        "상하수도": [681420, 495360, 555710, 533980, 577430, 635370, 461560, 476040, 647440, 456730, 0, 0],
        "통신요금": [1041570, 1035290, 1040490, 1033540, 1033280, 0, 1032120, 1035370, 1029710, 1045480, 0, 0],
        "복합기임대": [388410, 233330, 345310, 306500, 237160, 263950, 644200, 0, 0, 0, 0, 0],
        "공청기비데": [883900, 883900, 883900, 883900, 883900, 883900, 883900, 883900, 883900, 883900, 883900, 883900]
    }
}

# -----------------------------------------------------------------------------
# 3. Firebase 서비스 초기화 (Quota 방어 포함)
# -----------------------------------------------------------------------------
if 'quota_exceeded' not in st.session_state:
    st.session_state['quota_exceeded'] = False

try:
    firebase_admin.get_app()
except ValueError:
    if "firebase" in st.secrets:
        fb_creds = dict(st.secrets["firebase"])
        if "private_key" in fb_creds:
            fb_creds["private_key"] = fb_creds["private_key"].replace("\\n", "\n")
        cred = credentials.Certificate(fb_creds)
        firebase_admin.initialize_app(cred)

db = firestore.client() if firebase_admin._apps else None
appId = st.secrets.get("app_id", "facility-ledger-2026-v1")

doc_ref = db.collection('artifacts').document(appId).collection('public').document('data').collection('facility_data').document('master') if db else None
daily_ref = db.collection('artifacts').document(appId).collection('public').document('data').collection('facility_data').document('daily_expenses') if db else None
rapid_monthly_ref = db.collection('artifacts').document(appId).collection('public').document('data').collection('facility_data').document('rapid_monthly_v3') if db else None
quant_base_ref = db.collection('artifacts').document(appId).collection('public').document('data').collection('quantitative_monthly') if db else None

# -----------------------------------------------------------------------------
# 4. Quota Exceeded (429) & Timeout 방어 엔진
# -----------------------------------------------------------------------------
def check_quota_error(e):
    err_str = str(e).lower()
    if "quota exceeded" in err_str or "429" in err_str or "timeout" in err_str:
        st.session_state['quota_exceeded'] = True
        st.toast("🚨 Firebase 일일 무료 용량 소진! 앱 멈춤을 방지하기 위해 로컬 백업 모드로 전환되었습니다.", icon="⚠️")
        return True
    return False

# -----------------------------------------------------------------------------
# 데이터 처리 및 유틸리티 함수
# -----------------------------------------------------------------------------
def clean_numeric(val):
    if pd.isna(val): return 0.0
    if isinstance(val, (int, float)):
        if math.isnan(val) or math.isinf(val): return 0.0
        return float(val)
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
    
    existing_map = {(r['year'], r['month'], r['category']): r for r in data['records']}
    
    new_recs = []
    for y in YEARS:
        for c in CATEGORIES:
            for m in MONTHS:
                hist_val = 0.0
                if y in HISTORICAL_DATA and c in HISTORICAL_DATA[y]:
                    hist_val = float(HISTORICAL_DATA[y][c][m-1])
                    
                key = (y, m, c)
                
                if key not in existing_map:
                    new_recs.append({
                        "year": y, 
                        "month": m, 
                        "category": c, 
                        "amount": hist_val, 
                        "status": "지출" if hist_val > 0 else "미지출"
                    })
                else:
                    if y in [2024, 2025] and existing_map[key]['amount'] == 0.0 and hist_val > 0.0:
                        existing_map[key]['amount'] = hist_val
                        existing_map[key]['status'] = "지출"
                        
    if new_recs: data['records'].extend(new_recs)
    return data

def load_data():
    if not st.session_state['quota_exceeded'] and doc_ref:
        try:
            doc = doc_ref.get(timeout=3.0)
            if doc.exists: return doc.to_dict()
        except Exception as e:
            check_quota_error(e)

    if os.path.exists("local_master.json"):
        try:
            with open("local_master.json", "r", encoding="utf-8") as f: return json.load(f)
        except: pass
    return {"records": []}

def save_data_cloud(data):
    saved = False
    if not st.session_state['quota_exceeded'] and doc_ref:
        try:
            doc_ref.set(data, timeout=3.0)
            saved = True
        except Exception as e:
            check_quota_error(e)
            
    try:
        with open("local_master.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        saved = True
    except: pass
    return saved

def save_and_register(year, cat, mon):
    if st.session_state.amt_box > 0:
        curr = st.session_state['data']
        for r in curr["records"]:
            if r["year"] == year and r["category"] == cat and r["month"] == mon:
                r["amount"] += float(st.session_state.amt_box)
                r["status"] = "지출"
                break
        if save_data_cloud(curr):
            st.session_state['data'] = curr
            st.session_state.amt_box = 0
            st.toast("✅ 지출 등록 완료")

def load_daily_expenses():
    if not st.session_state['quota_exceeded'] and daily_ref:
        try:
            doc = daily_ref.get(timeout=3.0)
            if doc.exists: return doc.to_dict().get("expenses", [])
        except Exception as e:
            check_quota_error(e)

    if os.path.exists("local_daily.json"):
        try:
            with open("local_daily.json", "r", encoding="utf-8") as f: return json.load(f).get("expenses", [])
        except: pass
    return []

def save_daily_expenses(expense_list):
    safe_list = []
    for item in expense_list:
        safe_item = {}
        for k, v in item.items():
            if isinstance(v, (int, float)):
                safe_item[k] = float(v) if not (math.isnan(v) or math.isinf(v)) else 0.0
            elif pd.isna(v):
                safe_item[k] = ""
            else:
                safe_val = str(v)
                safe_item[k] = safe_val if safe_val not in ["nan", "NaT", "None", "inf", "-inf"] else ""
        safe_list.append(safe_item)
        
    data_to_save = {"expenses": safe_list, "last_updated": datetime.now().isoformat()}
    saved = False
    
    if not st.session_state['quota_exceeded'] and daily_ref:
        try:
            daily_ref.set(data_to_save, timeout=4.0)
            saved = True
        except Exception as e:
            check_quota_error(e)

    try:
        with open("local_daily.json", "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        saved = True
    except: pass
    return saved

def get_default_rapid_df():
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

def load_rapid_df():
    if not st.session_state['quota_exceeded'] and rapid_monthly_ref:
        try:
            doc = rapid_monthly_ref.get(timeout=3.0)
            if doc.exists:
                data = doc.to_dict().get("data", [])
                if data:
                    df = pd.DataFrame(data)
                    if not df.empty and "세목" in df.columns:
                        for c in ["대상액", "집행예정액", "실제집행액"]:
                            if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
                        return df
        except Exception as e:
            check_quota_error(e)
            
    if os.path.exists("local_rapid.json"):
        try:
            with open("local_rapid.json", "r", encoding="utf-8") as f:
                data = json.load(f).get("data", [])
                if data:
                    df = pd.DataFrame(data)
                    if not df.empty and "세목" in df.columns:
                        for c in ["대상액", "집행예정액", "실제집행액"]:
                            if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
                        return df
        except: pass
    return get_default_rapid_df()

def save_rapid_df(df):
    records = df.to_dict('records')
    safe_records = []
    for r in records:
        safe_r = {}
        for k, v in r.items():
            if isinstance(v, (int, float)):
                safe_r[k] = float(v) if not (math.isnan(v) or math.isinf(v)) else 0.0
            elif pd.isna(v):
                safe_r[k] = ""
            else:
                safe_val = str(v)
                safe_r[k] = safe_val if safe_val not in ["nan", "NaT", "None", "inf", "-inf"] else ""
        safe_records.append(safe_r)
        
    data_to_save = {"data": safe_records, "last_updated": datetime.now().isoformat()}
    saved = False
    
    if not st.session_state['quota_exceeded'] and rapid_monthly_ref:
        try:
            rapid_monthly_ref.set(data_to_save, timeout=3.0)
            saved = True
        except Exception as e:
            check_quota_error(e)

    try:
        with open("local_rapid.json", "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        saved = True
    except: pass
    return saved

def load_quant_monthly(year, month):
    if not st.session_state['quota_exceeded'] and quant_base_ref:
        try:
            m_doc = quant_base_ref.document(f"{year}_{month}").get(timeout=3.0)
            if m_doc.exists: return m_doc.to_dict().get("data", [])
        except Exception as e:
            check_quota_error(e)
            
    if os.path.exists(f"local_quant_{year}_{month}.json"):
        try:
            with open(f"local_quant_{year}_{month}.json", "r", encoding="utf-8") as f:
                return json.load(f).get("data", [])
        except: pass
    return []

def save_quant_monthly(year, month, data_list):
    data_to_save = {"data": data_list, "last_updated": datetime.now().isoformat()}
    saved = False
    
    if not st.session_state['quota_exceeded'] and quant_base_ref:
        try:
            quant_base_ref.document(f"{year}_{month}").set(data_to_save, timeout=3.0)
            saved = True
        except Exception as e:
            check_quota_error(e)
            
    try:
        with open(f"local_quant_{year}_{month}.json", "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        saved = True
    except: pass
    return saved

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

# -----------------------------------------------------------------------------
# ★ [V288 핵심] 공통 매핑 및 동기화 함수 (사용자 절대 원칙 적용)
# -----------------------------------------------------------------------------
def get_mapped_category(desc, row_cat, budget_subj=""):
    desc_no_space = str(desc).replace(" ", "").strip()
    row_cat_no_space = str(row_cat).replace(" ", "").strip()
    budget_no_space = str(budget_subj).replace(" ", "").strip()
    
    # 0. 100% 완전 배제 키워드 (기름걸레는 그 어떤 항목으로도 묶이지 않고 공중 분해됨)
    if "기름" in desc_no_space and "걸레" in desc_no_space: return None
    if "기름" in row_cat_no_space and "걸레" in row_cat_no_space: return None
    if "기름" in budget_no_space and "걸레" in budget_no_space: return None
        
    # 1. [사용자 절대 요청] 예산과목명 (I열) 절대 기준 (오직 엑셀 I열의 값을 최우선으로 믿음)
    if budget_no_space:
        excluded_subjects = ["행사", "교육", "프로그램", "여비", "업무추진비", "강사료", "보전금"]
        if not any(ex in budget_no_space for ex in excluded_subjects):
            if "일반재료비" in budget_no_space: return "일반재료비"
            if "상품매입비" in budget_no_space: return "상품매입비"
            if "수탁자산취득비" in budget_no_space: return "수탁자산취득비"
            if "자체소수선" in budget_no_space: return "자체소수선"

    # 2. 예산과 무관하게 무조건 먼저 빼내야 하는 특수 공과금
    if any(k in desc_no_space for k in ["상하수도", "상수도", "하수도", "수도요금", "수도료"]):
        return "상하수도"
    if any(k in desc_no_space for k in ["전기요금", "전기료", "한전", "한국전력", "전력요금"]):
        if not any(k in desc_no_space for k in ["공사", "대행", "수수료", "충전", "전기차"]):
            return "전기요금"
    if "미디어" in desc_no_space and any(k in desc_no_space for k in ["제습", "습기"]):
        return "미디어실제습기"

    # 3. 적요 배제 키워드 (사업비 성격 배제)
    excluded_desc_keywords = ["체험", "교육", "프로그램", "다산클래스", "오픈스페이스", "행사", "클래스", "활동"]
    if any(ex in desc_no_space for ex in excluded_desc_keywords):
        return None

    # 4. 명시적 일치 (카테고리명이 적요에 대놓고 있는 경우)
    for cat_name in CATEGORIES:
        if cat_name in row_cat_no_space or cat_name in desc_no_space:
            if cat_name in ["전기요금", "환경용역", "상품매입비", "세탁용역", "일반재료비", "자체소수선"]: 
                continue 
            return cat_name

    # 5. 나머지 키워드 (포괄적인 단어 모두 제거, 오직 확실한 용역/수수료만 매핑)
    keyword_map = {
        "통신요금": ["통신요금", "통신비", "인터넷", "케이블"],
        "복합기임대": ["복합기임대", "복합기렌탈"], 
        "공청기비데": ["공기청정기렌탈", "비데렌탈"],
        "부서업무비": ["부서업무비"], 
        "무인경비": ["무인경비용역"],
        "승강기점검": ["승강기유지보수", "승강기점검"], 
        "신용카드수수료": ["신용카드수수료"], 
        "환경용역": ["환경용역", "청소용역", "미화용역"],  # '청소' 단독어 절대 금지
        "세탁용역": ["세탁"], # [V288] 세탁용역 정상 작동을 위해 '세탁' 키워드 복구 (기름걸레는 이미 0번에서 막힘)
        "야간경비": ["야간경비용역", "당직용역"],
        "상품매입비": ["상품매입", "자판기식음료", "종량제봉투"]
    }
    
    for m_cat, kws in keyword_map.items():
        if any(k in desc_no_space for k in kws):
            return m_cat
            
    return None

def sync_daily_to_master_auto():
    master_data = load_data()
    master_data = ensure_data_integrity(master_data)
    daily = st.session_state.get('daily_expenses', [])
    
    if not daily:
        for r in master_data.get('records', []):
            if r['year'] == 2026 and r['category'] != "수탁자산취득비":
                r['amount'] = 0.0
                r['status'] = "미지출"
        save_data_cloud(master_data)
        st.session_state['data'] = master_data
        return True
    
    sums_map = {} 
    
    for row in daily:
        desc = str(row.get('적요', '')).strip()
        amt_v = clean_numeric(row.get('집행금액', 0))
        date_raw = str(row.get('집행일자', '')).strip()
        
        year_found = None
        month_found = None
        
        ym_match = re.search(r'(\d{4})\s*년\s*(\d{1,2})\s*월', desc)
        if ym_match:
            year_found = int(ym_match.group(1))
            month_found = int(ym_match.group(2))
        
        if (not year_found or not month_found) and date_raw:
            date_num = re.sub(r'[^0-9]', '', str(date_raw))
            if len(date_num) >= 8:
                year_found = int(date_num[:4])
                month_found = int(date_num[4:6])
        
        if not month_found:
            m_match = re.search(r'(\d{1,2})\s*월', desc)
            if m_match: month_found = int(m_match.group(1))
                
        if not year_found and month_found:
            year_found = 2026
            
        if not year_found or not month_found or not (1 <= month_found <= 12):
            continue
        
        matched_cat = get_mapped_category(desc, row.get('세목', ''), row.get('예산과목', ''))
                    
        if matched_cat:
            if year_found not in sums_map: sums_map[year_found] = {}
            if matched_cat not in sums_map[year_found]: sums_map[year_found][matched_cat] = {}
            sums_map[year_found][matched_cat][month_found] = sums_map[year_found][matched_cat].get(month_found, 0) + amt_v

    data_changed = False
    for r in master_data.get('records', []):
        if r['year'] == 2026:
            cat, month = r['category'], r['month']
            
            if cat == "수탁자산취득비":
                continue
            
            new_val = sums_map.get(2026, {}).get(cat, {}).get(month, 0.0)
            
            if clean_numeric(r['amount']) != new_val:
                r['amount'] = new_val
                r['status'] = "지출" if new_val > 0 else "미지출"
                data_changed = True
                    
    if data_changed: 
        save_data_cloud(master_data)
        st.session_state['data'] = master_data
        return True
    return False

# -----------------------------------------------------------------------------
# 엑셀 파싱 엔진 
# -----------------------------------------------------------------------------
def parse_expense_excel(df_raw):
    header_idx = -1; found_cols = {}
    keyword_detect = {
        '집행일자': ['일자','날짜','집행일','결의일자'], 
        '적요': ['적요','내용','품명','건명'], 
        '집행금액': ['금액','집행액','지출액','결재금액'],
        '예산과목': ['예산과목', '과목명', '항목', '예산항목', '편성목']
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
        
        budget_idx = found_cols.get('예산과목', -1)
        if budget_idx != -1 and len(row_arr) > budget_idx:
            budget_subj = str(row_arr[budget_idx]).strip() if pd.notna(row_arr[budget_idx]) else ""
        else:
            budget_subj = str(row_arr[8]).strip() if len(row_arr) > 8 and pd.notna(row_arr[8]) else ""
        
        semok_str = ""
        if b_val and (not f_val or not g_val):
            if b_val in BUDGET_MAPPING: f_val, g_val = BUDGET_MAPPING[b_val]
        
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
        
        if semok_str in ["nan", "NaT", "None"]: semok_str = ""
        if date_val in ["nan", "NaT", "None"]: date_val = ""
        if desc in ["nan", "NaT", "None"]: desc = ""
        if budget_subj in ["nan", "NaT", "None"]: budget_subj = ""
        
        new_processed.append({
            "세목": semok_str, 
            "집행일자": date_val, 
            "적요": desc, 
            "집행금액": amt_val,
            "예산과목": budget_subj
        })
        
    return new_processed

def parse_special_expense_excel(df_raw):
    header_idx = -1
    amt_idx = -1
    
    for i in range(min(25, len(df_raw))):
        row_vals = [re.sub(r'\s+', '', str(v)) for v in df_raw.iloc[i]]
        for idx, val in enumerate(row_vals):
            if any(kw in val for kw in ['금액', '집행액', '지출액', '결재금액', '청구액', '지급액']):
                header_idx = i
                amt_idx = idx
                break
        if header_idx != -1: break
        
    data_rows = df_raw.iloc[header_idx+1:].copy() if header_idx != -1 else df_raw.copy()
    new_processed = []
    
    for _, row in data_rows.iterrows():
        row_arr = row.values
        if len(row_arr) <= 10: 
            continue
            
        month_val = str(row_arr[1]).strip() 
        title_val = str(row_arr[10]).strip() 
        title_no_space = title_val.replace(" ", "")
        
        if not title_val or "합계" in title_val: 
            continue
            
        if "기름" in title_no_space and "걸레" in title_no_space:
            continue
        
        mapped_cat = ""
        if "신용카드" in title_no_space and "수수료" in title_no_space: mapped_cat = "신용카드수수료"
        elif "무인경비" in title_no_space: mapped_cat = "무인경비"
        elif "고객편의기기" in title_no_space or "공기청정기" in title_no_space or "비데" in title_no_space: mapped_cat = "공청기비데"
        elif "야간경비" in title_no_space: mapped_cat = "야간경비"
        elif "청소용역" in title_no_space or "환경용역" in title_no_space or "미화용역" in title_no_space: mapped_cat = "환경용역"
        
        if not mapped_cat: 
            continue 
        
        month = 0
        m_match = re.search(r'(\d{1,2})월', month_val)
        if m_match: 
            month = int(m_match.group(1))
        else:
            m_match_num = re.search(r'^0?(\d{1,2})$', month_val)
            if m_match_num: 
                month = int(m_match_num.group(1))
        
        if month == 0:
            m_match_t = re.search(r'(\d{1,2})\s*월', title_val)
            if m_match_t: 
                month = int(m_match_t.group(1))
                
        if month == 0 or not (1 <= month <= 12): 
            continue
        
        amt_val = 0.0
        if amt_idx != -1 and len(row_arr) > amt_idx:
            amt_val = clean_numeric(row_arr[amt_idx])
        else:
            nums = [clean_numeric(x) for x in row_arr[11:] if pd.notna(x) and clean_numeric(x) > 0]
            if nums: amt_val = max(nums)
            
        if amt_val <= 0: 
            continue
        
        date_str = f"2026-{month:02d}-01"
        title_val = title_val if title_val not in ["nan", "NaT", "None"] else ""
        
        new_processed.append({
            "세목": mapped_cat, 
            "집행일자": date_str, 
            "적요": title_val, 
            "집행금액": amt_val,
            "예산과목": mapped_cat 
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
# 6. 세션 데이터 초기화 
# -----------------------------------------------------------------------------
if st.session_state['quota_exceeded']:
    st.error("🚨 **[치명적 알림] 파이어베이스(Firebase) 하루 무료 사용량(Quota)을 초과했습니다!**\n\n앱이 무한 로딩에 빠지는 것을 방지하기 위해 강제로 연결을 차단하고 **오프라인 로컬 모드로 전환**했습니다. 오늘 작업하신 데이터는 내 컴퓨터(JSON 파일)에만 안전하게 저장되며, 내일 무료 용량이 초기화되면 다시 클라우드로 동기화할 수 있습니다.")

if 'amt_box' not in st.session_state: st.session_state.amt_box = 0
if 'data' not in st.session_state: st.session_state['data'] = load_data()

if 'rapid_df' not in st.session_state: 
    st.session_state['rapid_df'] = load_rapid_df()
else:
    if not isinstance(st.session_state['rapid_df'], pd.DataFrame) or st.session_state['rapid_df'].empty or '세목' not in st.session_state['rapid_df'].columns:
        st.session_state['rapid_df'] = load_rapid_df()

if 'daily_expenses' not in st.session_state: st.session_state['daily_expenses'] = load_daily_expenses()
if 'tree_expanded' not in st.session_state or st.session_state['tree_expanded'] is None: 
    st.session_state['tree_expanded'] = set()
if 'tree_states' not in st.session_state: st.session_state['tree_states'] = {}
if 'last_file_hash' not in st.session_state: st.session_state['last_file_hash'] = None
if 'last_sp_file_hash' not in st.session_state: st.session_state['last_sp_file_hash'] = None

if st.session_state.get('daily_expenses') and 'initial_sync_done' not in st.session_state:
    sync_daily_to_master_auto()
    st.session_state['initial_sync_done'] = True

master_data_raw = st.session_state['data']
master_data_raw = ensure_data_integrity(master_data_raw)
df_all = pd.DataFrame(master_data_raw.get("records", []))
if not df_all.empty: df_all["amount"] = pd.to_numeric(df_all["amount"], errors='coerce').fillna(0).astype('float64')

# --- 사이드바 ---
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3135/3135715.png", width=60)
    st.title("지출 관리 콘솔")
    if st.button("💾 데이터 수동 백업"):
        if save_data_cloud(st.session_state['data']): st.success("로컬/클라우드 저장 완료!")
    if st.button("🔄 데이터 강제 새로고침"):
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
st.title("🏢 2026 월별 지출관리 및 실시간 분석 (클라우드/로컬 하이브리드)")
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
        bc1, bc2, bc3 = st.columns(3)
        bc1.button("+10만", on_click=update_amt, args=(100000,))
        bc2.button("+100만", on_click=update_amt, args=(1000000,))
        bc3.button("🔄 리셋", on_click=reset_amt)
        st.button("💾 데이터 저장", type="primary", on_click=save_and_register, args=(iy, ic, im))
    with c_p:
        st.markdown('<b style="font-size:1.1rem; color:#1e3a8a; border-left:5px solid #2563eb; padding-left:10px;">🍩 지출 비중 분포</b>', unsafe_allow_html=True)
        cat_dist = df_26.groupby("category")["amount"].sum().reset_index() if not df_all.empty else pd.DataFrame()
        if not cat_dist.empty and cat_dist["amount"].sum() > 0:
            fig = alt.Chart(cat_dist).mark_arc(innerRadius=85, stroke="#fff").encode(theta="amount:Q", color=alt.Color("category:N", scale=alt.Scale(scheme='tableau20'))).properties(height=380)
            st.altair_chart(fig, use_container_width=True)
            
    st.markdown("---"); st.markdown('<div class="section-header">📅 2026 전체 상세 지출 통합 그리드 (전수 편집 가능)</div>', unsafe_allow_html=True)
    if not df_all.empty:
        df_p = df_26.pivot(index="category", columns="month", values="amount").fillna(0).reindex(index=CATEGORIES, columns=MONTHS, fill_value=0)
        df_p.columns = [f"{m}월" for m in df_p.columns]
        df_d = df_p.map(lambda x: format(int(x), ","))
        
        ed = st.data_editor(df_d, height=550, key="main_editor_v288")
        
        if st.button("💾 통합 그리드 수정 내역 클라우드/로컬 저장", type="primary", key="btn_save_tab1_v288"):
            curr = load_data()
            curr = ensure_data_integrity(curr)
            for cat in CATEGORIES:
                for m in MONTHS:
                    if cat in ed.index and f"{m}월" in ed.columns:
                        val = str(ed.loc[cat, f"{m}월"]).replace(",", "")
                        clean = clean_numeric(val)
                        for r in curr["records"]:
                            if r["year"] == 2026 and r["category"] == cat and r["month"] == m: 
                                r["amount"] = clean
                                r["status"] = "지출" if clean > 0 else "미지출"
                                break
            if save_data_cloud(curr):
                st.session_state['data'] = curr
                st.success("✅ 저장 성공!")
                time.sleep(0.5)
                st.rerun()

# --- TAB 2: 항목별 지출 분석 ---
with tabs[1]:
    st.markdown('<div class="section-header">📈 지능형 분석 및 실시간 연동 (Semantic Sync)</div>', unsafe_allow_html=True)
    if st.button("🔄 지출내역 수동 연동 실행", type="primary"):
        if sync_daily_to_master_auto():
            st.success("✅ 동기화 완료! 실적이 갱신되었습니다."); time.sleep(1); st.rerun()
    
    sc = st.selectbox("관리 항목 선택", CATEGORIES, key="analysis_sel_v288")
    df_c = df_all[df_all["category"] == sc] if not df_all.empty else pd.DataFrame()
    
    if not df_c.empty:
        if sc in QUICK_EXEC_CONFIG:
            cf = QUICK_EXEC_CONFIG[sc]; q1_e = df_c[(df_c["year"] == 2026) & (df_c["month"] <= 3)]["amount"].sum(); h1_e = df_c[(df_c["year"] == 2026) & (df_c["month"] <= 6)]["amount"].sum()
            html_scarlet = f"""<div class="quick-exec-card-scarlet"><div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;"><span class="quick-exec-badge-scarlet">🚀 2026 신속집행 특별관리 대상</span><span style="font-size:1.1rem; color:#be123c; font-weight:900;">대상액: {cf['target']:,}원</span></div><div style="display:grid; grid-template-columns: 1fr 1fr; gap:30px;"><div><div style="display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:12px;"><span style="font-weight:800; color:#1e3a8a; font-size:1.1rem;">● 1분기 목표 (40%)</span><span style="font-size:2.2rem; font-weight:900; color:#2563eb;">{(q1_e/cf['goal_q1'])*100 if cf['goal_q1']>0 else 0:.1f}%</span></div><div style="background-color:#e2e8f0; height:14px; border-radius:12px; overflow:hidden;"><div style="background:linear-gradient(to right, #3b82f6, #2563eb); width:{min((q1_e/cf['goal_q1'])*100 if cf['goal_q1']>0 else 0, 100):.1f}%; height:100%;"></div></div></div><div><div style="display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:12px;"><span style="font-weight:800; color:#be123c; font-size:1.1rem;">● 상반기 목표 (70%)</span><span style="font-size:2.2rem; font-weight:900; color:#e11d48;">{(h1_e/cf['goal_h1'])*100 if cf['goal_h1']>0 else 0:.1f}%</span></div><div style="background-color:#e2e8f0; height:14px; border-radius:12px; margin-top:10px; overflow:hidden;"><div style="background:linear-gradient(to right, #fb7185, #e11d48); width:{min((h1_e/cf['goal_h1'])*100 if cf['goal_h1']>0 else 0, 100):.1f}%; height:100%;"></div></div></div></div></div>"""
            st.markdown(html_scarlet, unsafe_allow_html=True)
        
        m_cols = st.columns(3); v24, v25, v26 = df_c[df_c['year']==2024]['amount'].sum(), df_c[df_c['year']==2025]['amount'].sum(), df_c[df_c['year']==2026]['amount'].sum()
        m_cols[0].markdown(f'''<div class="metric-card" style="border-left-color: #94a3b8;"><div class="metric-label">📊 2024 실적</div><div class="metric-value">{int(v24):,}<span style="font-size:0.9rem; margin-left:5px; color:#94a3b8;">원</span></div></div>''', unsafe_allow_html=True); m_cols[1].markdown(f'''<div class="metric-card" style="border-left-color: #10b981;"><div class="metric-label">📊 2025 실적</div><div class="metric-value">{int(v25):,}<span style="font-size:0.9rem; margin-left:5px; color:#94a3b8;">원</span></div></div>''', unsafe_allow_html=True); m_cols[2].markdown(f'''<div class="metric-card" style="border-left-color: #3b82f6;"><div class="metric-label">📅 2026 연동 실적</div><div class="metric-value">{int(v26):,}<span style="font-size:0.9rem; margin-left:5px; color:#94a3b8;">원</span></div></div>''', unsafe_allow_html=True)
        
        df_p_c = df_c.pivot(index="month", columns="year", values="amount").fillna(0).reindex(columns=YEARS, fill_value=0); df_p_c.columns = [f"{c}년" for c in df_p_c.columns]; df_d_c = df_p_c.map(lambda x: format(int(x), ",")).reset_index(); df_d_c["월"] = df_d_c["month"].apply(lambda x: f"{x}월")
        
        ed_c = st.data_editor(df_d_c[["월", "2024년", "2025년", "2026년"]], hide_index=True, key=f"ed_v288_{sc}", height=450)
        
        if st.button("💾 분석 데이터 수정 내역 영구 저장", type="primary", key=f"btn_save_tab2_v288_{sc}"):
            curr = load_data()
            curr = ensure_data_integrity(curr)
            for idx, row in ed_c.iterrows():
                mv = int(str(row["월"]).replace("월", ""))
                for y in YEARS:
                    va = str(row[f"{y}년"]).replace(",", "")
                    na = float(va) if va else 0.0
                    for r in curr["records"]:
                        if r["year"] == y and r["category"] == sc and r["month"] == mv:
                            r["amount"] = na
                            r["status"] = "지출" if na > 0 else "미지출"
                            break
            if save_data_cloud(curr):
                st.session_state['data'] = curr
                st.success("✅ 저장 성공!")
                time.sleep(0.5)
                st.rerun()

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

# --- TAB 4: 일상경비 지출현황 ---
with tabs[3]:
    st.markdown('<div class="section-header">📂 일상경비 데이터베이스 관리</div>', unsafe_allow_html=True)
    
    with st.expander("📥 [일반] 일상경비 지출내역 엑셀 업로드 (I열 예산과목 기준 자동 매핑)"):
        f = st.file_uploader("일반 양식 엑셀 파일 선택", type=["xlsx", "csv"], key="daily_up_v288")
        if f:
            file_content = f.read(); file_hash = hashlib.md5(file_content).hexdigest(); f.seek(0)
            if st.session_state['last_file_hash'] != file_hash:
                with st.spinner("일반 엑셀 양식을 분석 중입니다..."):
                    df_raw = pd.read_excel(f, header=None, engine='openpyxl') if f.name.endswith('.xlsx') else pd.read_csv(f, header=None)
                    new_processed = parse_expense_excel(df_raw)
                    if new_processed is not None and new_processed:
                        merged_expenses, added_count = merge_expenses(st.session_state.get('daily_expenses', []), new_processed)
                        
                        if save_daily_expenses(merged_expenses):
                            st.session_state['daily_expenses'] = merged_expenses
                            st.session_state['last_file_hash'] = file_hash
                            sync_daily_to_master_auto()
                            st.success(f"✅ 일반 동기화 완료! 새로운 내역 {added_count}건이 안전하게 저장되었습니다.")
                            time.sleep(1.5); st.rerun()
                    else: st.error("❌ 엑셀 헤더를 찾을 수 없습니다. 파일 양식을 확인해주세요.")
                    
    with st.expander("📥 [특수] 5대 용역/수수료 전용 파일 업로드 (B열 지급월, K열 문서제목 기준)"):
        st.info("📌 전용 관리 항목: **신용카드수수료, 무인경비, 공청기비데, 야간경비, 환경(청소)용역**\n\n특수 양식은 B열의 월(Month) 정보와 K열의 제목을 바탕으로 100% 강제 분리되어 기록됩니다.")
        f_sp = st.file_uploader("특수 양식 엑셀 파일 선택", type=["xlsx", "csv"], key="special_up_v288")
        if f_sp:
            sp_file_content = f_sp.read(); sp_file_hash = hashlib.md5(sp_file_content).hexdigest(); f_sp.seek(0)
            if st.session_state.get('last_sp_file_hash') != sp_file_hash:
                with st.spinner("특수 양식을 분석하여 5대 항목을 강제 추출 중입니다..."):
                    df_raw_sp = pd.read_excel(f_sp, header=None, engine='openpyxl') if f_sp.name.endswith('.xlsx') else pd.read_csv(f_sp, header=None)
                    new_processed_sp = parse_special_expense_excel(df_raw_sp)
                    if new_processed_sp is not None and new_processed_sp:
                        merged_expenses_sp, added_count_sp = merge_expenses(st.session_state.get('daily_expenses', []), new_processed_sp)
                        
                        if save_daily_expenses(merged_expenses_sp):
                            st.session_state['daily_expenses'] = merged_expenses_sp
                            st.session_state['last_sp_file_hash'] = sp_file_hash
                            sync_daily_to_master_auto()
                            st.success(f"✅ 5대 특수 항목 완료! {added_count_sp}건이 저장되었습니다.")
                            time.sleep(1.5); st.rerun()
                    else: st.error("❌ 처리할 수 있는 특수 항목(5대 용역/수수료) 데이터를 찾지 못했습니다.")
    
    daily_data = st.session_state.get('daily_expenses', [])
    if daily_data:
        df_d = pd.DataFrame(daily_data)
        df_d['세목'] = df_d['세목'].astype(str)
        
        def extract_budget_code(s):
            codes = re.findall(r'(?<!\d)(\d{3}(?:-\d{2})?)(?!\d)', s.strip())
            return codes[-1] if codes else s.strip()
            
        df_d = df_d.assign(temp_code=df_d['세목'].apply(extract_budget_code))
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
            save_daily_expenses(daily_data)
            st.session_state['daily_expenses'] = daily_data
            
        df_d = df_d.assign(세목=cleaned_semok)
        df_d = df_d.drop(columns=['temp_code'])
        
        c1, c2 = st.columns(2)
        c1.markdown(f'<div class="metric-card"><div class="metric-label">💰 누적 집행 총액</div><div class="metric-value">{int(df_d["집행금액"].sum()):,}<span style="font-size:1rem; color:#94a3b8;">원</span></div></div>', unsafe_allow_html=True)
        c2.markdown(f'<div class="metric-card" style="border-left-color:#10b981;"><div class="metric-label">📝 누적 지출 건수</div><div class="metric-value">{len(df_d)} 건</div></div>', unsafe_allow_html=True)
        
        fc1, fc2, fc3 = st.columns([1.5, 2, 1])
        fcat = fc1.selectbox("세목 필터", ["전체"] + sorted(df_d["세목"].unique().tolist()), key="f1_v288")
        sq = fc2.text_input("적요 내용 검색", key="f2_v288")
        
        disp = df_d.copy()
        if fcat != "전체": disp = disp[disp["세목"] == fcat]
        if sq: disp = disp[disp["적요"].str.contains(sq, na=False)]
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            export_df = disp[['집행일자', '세목', '적요', '집행금액']].copy()
            if '예산과목' in disp.columns:
                export_df['예산과목'] = disp['예산과목']
            export_df.to_excel(writer, index=False, sheet_name='일상경비지출내역')
        excel_data = output.getvalue()
        
        with fc3:
            st.markdown("<div style='margin-top: 29px;'></div>", unsafe_allow_html=True)
            st.download_button(
                label="📥 현재 내역 엑셀 다운로드",
                data=excel_data,
                file_name=f"일상경비지출내역_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            
        disp = disp.assign(_idx=disp.index, 삭제선택=False)
        disp = disp.assign(집행금액_str=disp['집행금액'].map(lambda x: format(int(x), ",")))
        
        if '예산과목' in disp.columns:
            disp = disp[['삭제선택', '집행일자', '세목', '예산과목', '적요', '집행금액_str', '_idx', '집행금액']]
        else:
            disp = disp[['삭제선택', '집행일자', '세목', '적요', '집행금액_str', '_idx', '집행금액']]
        
        st.markdown("<div class='text-sm text-gray-500 mb-2'>💡 잘못 입력된 내역을 삭제하려면 <b>체크박스 선택</b> 후 하단의 <b>삭제 버튼</b>을 누르세요. 전체 재구축 시 <b>초기화 버튼</b>을 누르세요.</div>", unsafe_allow_html=True)
        
        col_config = {
            "삭제선택": st.column_config.CheckboxColumn("삭제 선택", default=False),
            "_idx": None, "집행금액": None,
            "집행일자": st.column_config.TextColumn(disabled=True),
            "세목": st.column_config.TextColumn(disabled=True),
            "적요": st.column_config.TextColumn(disabled=True),
            "집행금액_str": st.column_config.TextColumn("집행금액(원)", disabled=True)
        }
        if '예산과목' in disp.columns:
            col_config['예산과목'] = st.column_config.TextColumn(disabled=True)
            
        edited_df = st.data_editor(
            disp, 
            height=500, 
            hide_index=True,
            column_config=col_config,
            key="daily_editor_v288"
        )
        
        del_c1, del_c2, del_c3 = st.columns([2, 2, 6])
        with del_c1:
            if st.button("🗑️ 선택 항목 삭제", key="btn_del_sel_v288"):
                to_delete = edited_df[edited_df['삭제선택'] == True]['_idx'].tolist()
                if to_delete:
                    new_daily = [item for i, item in enumerate(daily_data) if i not in to_delete]
                    if save_daily_expenses(new_daily):
                        st.session_state['daily_expenses'] = new_daily
                        sync_daily_to_master_auto()
                        st.success(f"✅ {len(to_delete)}건의 내역이 삭제되었습니다.")
                        time.sleep(1.0); st.rerun()
                else:
                    st.warning("먼저 삭제할 항목의 체크박스를 선택해주세요.")
                    
        with del_c2:
            if st.button("⚠️ 전체 데이터 초기화", key="btn_del_all_v288"):
                if save_daily_expenses([]):
                    st.session_state['daily_expenses'] = []
                    sync_daily_to_master_auto()
                    st.success("✅ 모든 데이터가 완전히 초기화되었습니다. (수탁자산취득비 보존)")
                    time.sleep(1.0); st.rerun()
    else:
        st.info("현재 저장된 일상경비 데이터가 없습니다. 엑셀 파일을 업로드해주세요.")

# --- TAB 5: 신속집행 대시보드 ---
with tabs[4]:
    st.markdown('<div class="section-header">📂 2026 상반기 신속집행 실시간 관리 (계획 대비 실적)</div>', unsafe_allow_html=True)
    
    df_view = st.session_state.get('rapid_df', get_default_rapid_df()).copy()
    
    if not isinstance(df_view, pd.DataFrame) or df_view.empty or "세목" not in df_view.columns:
        df_view = get_default_rapid_df()
        st.session_state['rapid_df'] = df_view.copy()
        
    for c in ["대상액", "집행예정액", "실제집행액"]:
        if c in df_view.columns: df_view[c] = pd.to_numeric(df_view[c], errors="coerce").fillna(0.0)
    
    if not df_all.empty and not df_view.empty and "세목" in df_view.columns:
        for idx, row in df_view.iterrows():
            cat = row['세목']
            try:
                m_num = int(str(row['월']).replace('월', ''))
                actual_val = df_all[(df_all['year'] == 2026) & (df_all['category'] == cat) & (df_all['month'] == m_num)]['amount'].sum()
                
                if cat == "일반재료비":
                    actual_val += df_all[(df_all['year'] == 2026) & (df_all['category'] == "상하수도") & (df_all['month'] == m_num)]['amount'].sum()
                    
                df_view.loc[idx, '실제집행액'] = float(actual_val)
            except: pass

    daily_list = st.session_state.get('daily_expenses', [])
    df_daily = pd.DataFrame(daily_list) if daily_list else pd.DataFrame(columns=["집행일자", "적요", "집행금액", "세목", "예산과목"])
    if not df_daily.empty:
        df_daily = df_daily.assign(MappedCategory=df_daily.apply(lambda r: get_mapped_category(r.get('적요',''), r.get('세목',''), r.get('예산과목','')), axis=1))

    current_m = datetime.now().month

    summary_list = []
    for cat in CORE_TARGETS:
        if df_view.empty or "세목" not in df_view.columns:
            continue

        sub = df_view[df_view["세목"] == cat].copy()
        t_amt = float(sub["대상액"].max()) if not sub.empty else 0.0
        
        e_amt = float(df_all[(df_all['year'] == 2026) & (df_all['category'] == cat)]['amount'].sum()) if not df_all.empty else 0.0
        
        if cat == "일반재료비":
            e_amt += float(df_all[(df_all['year'] == 2026) & (df_all['category'] == "상하수도")]['amount'].sum()) if not df_all.empty else 0.0
            
        sub = sub.assign(month_num=sub['월'].apply(lambda x: int(str(x).replace('월',''))))
        plan_to_date = sub[sub['month_num'] <= current_m]["집행예정액"].sum()
        
        summary_list.append({
            "세목": cat, "대상액": t_amt, "누적계획": plan_to_date, "집행액": e_amt, 
            "총달성률": (e_amt/t_amt*100) if t_amt > 0 else 0, "계획대비달성률": (e_amt/plan_to_date*100) if plan_to_date > 0 else 0
        })
        
    df_summary = pd.DataFrame(summary_list)

    if not df_summary.empty:
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
            if not df_view.empty and "세목" in df_view.columns:
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
            
            st.markdown(f"###### 📋 [ {cat} ] 실제 지출 상세 내역 (I열 예산과목명 기준 연동)")
            if not df_daily.empty:
                if cat == "일반재료비":
                    cat_daily = df_daily[df_daily['MappedCategory'].isin(['일반재료비', '상하수도'])].copy()
                else:
                    cat_daily = df_daily[df_daily['MappedCategory'] == cat].copy()
                
                def check_2026(d_str, desc_str):
                    if '2026' in str(d_str) or '2026' in str(desc_str): return True
                    if not re.search(r'\d{4}', str(d_str)) and not re.search(r'\d{4}', str(desc_str)): return True
                    return False
                
                cat_daily = cat_daily[cat_daily.apply(lambda x: check_2026(x['집행일자'], x['적요']), axis=1)].copy()
                
                if not cat_daily.empty:
                    disp_cols = ['집행일자', '적요', '집행금액']
                    if '예산과목' in cat_daily.columns: disp_cols.insert(1, '예산과목')
                    
                    disp_df = cat_daily[disp_cols].copy()
                    disp_df = disp_df.sort_values("집행일자", ascending=False)
                    disp_df = disp_df.assign(집행금액_str=disp_df['집행금액'].apply(lambda x: f"{int(x):,} 원"))
                    
                    if '예산과목' in disp_df.columns:
                        final_df = disp_df[['집행일자', '예산과목', '적요', '집행금액_str']]
                    else:
                        final_df = disp_df[['집행일자', '적요', '집행금액_str']]
                    
                    st.dataframe(final_df, hide_index=True)
                else:
                    st.info(f"아직 2026년 '{cat}' 항목으로 분류된 일상경비 지출 내역이 없습니다.")
            else:
                st.info("업로드된 일상경비 데이터가 없습니다. 먼저 '📂 일상경비 동기화' 탭에서 데이터를 업로드해주세요.")
    
    st.markdown('<div class="section-header">📝 대상액 / 집행예정액 계획 수정</div>', unsafe_allow_html=True)
    if not df_view.empty and "세목" in df_view.columns:
        edited_df = st.data_editor(
            df_view, 
            hide_index=True, 
            column_config={
                "세목": st.column_config.TextColumn(disabled=True), 
                "월": st.column_config.TextColumn(disabled=True), 
                "대상액": st.column_config.NumberColumn("대상액 (원)", format="%,d"), 
                "집행예정액": st.column_config.NumberColumn("집행예정액 (원)", format="%,d"), 
                "실제집행액": st.column_config.NumberColumn("실제집행액 (자동 연동됨)", format="%,d", disabled=True)
            }, 
            key="rapid_editor_v288"
        )

        if st.button("💾 대상액/집행예정액 영구 저장", type="primary", key="save_rapid_btn_v288"):
            st.session_state['rapid_df'] = edited_df
            if save_rapid_df(edited_df): 
                st.success("✅ 저장 성공!"); time.sleep(0.5); st.rerun()

# --- TAB 6: 정량실적 ---
with tabs[5]:
    st.markdown('<div class="section-header">📊 1~12월 정량실적 및 무결성 합계</div>', unsafe_allow_html=True)
    c_y, c_m = st.columns([1, 3])
    sel_year = c_y.radio("조회 연도", YEARS, index=2, horizontal=True, key="ry_v288_q")
    sel_months = c_m.multiselect("조회 월 선택", MONTHS, default=[1], format_func=lambda x: f"{x}월", key="rm_v288_q")
    
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
        master_df = master_df.assign(lvl_raw=master_df['구분'].apply(get_ws_cnt))
        unique_indents = sorted(master_df['lvl_raw'].unique())
        rank_map = {val: i for i, val in enumerate(unique_indents)}
        master_df = master_df.assign(lvl_sys=master_df['lvl_raw'].map(rank_map).astype(int))
        
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
                    if cols[0].button(icon_chk, key=f"chk_v288_{idx}"):
                        new_st = 0 if state in (1, 2) else 1
                        set_subtree_state_v202(idx, new_st, children_map, st.session_state['tree_states'])
                        refresh_ancestors_v202(idx, parent_map, children_map, st.session_state['tree_states']); st.rerun()
                    if has_children:
                        expanded_set = st.session_state.get('tree_expanded', set())
                        ex_icon = "▼" if idx in expanded_set else "▶"
                        if cols[1].button(ex_icon, key=f"tog_v288_{idx}"):
                            if idx in expanded_set: expanded_set.remove(idx)
                            else: expanded_set.add(idx)
                            st.session_state['tree_expanded'] = expanded_set; st.rerun()
                    label_html = f'<div class="tree-label" style="padding-left: {indent_px}px;">{row["구분"].strip()}</div>'
                    cols[2].markdown(label_html, unsafe_allow_html=True)
                    cols[3].write(f"{int(row['예산액']):,}")
                    cols[4].write(f"{int(row['예산배정']):,}")
    else: st.info("데이터가 없습니다.")