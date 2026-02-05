import streamlit as st
import pandas as pd
import altair as alt
import json
import os
import time
import io
import re
from datetime import datetime

# Firebase Admin SDK 관련 임포트
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except ImportError:
    st.error("라이브러리 로드 실패: 'firebase-admin'이 설치되어 있지 않습니다. requirements.txt를 확인하세요.")

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import Select 
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# -----------------------------------------------------------------------------
# 1. Firebase 클라우드 DB 초기화 (중복 방지 로직)
# -----------------------------------------------------------------------------
if not firebase_admin._apps:
    try:
        if "firebase" not in st.secrets:
            st.error("Secrets 설정에 [firebase] 섹션이 없습니다.")
            st.stop()
            
        fb_creds = dict(st.secrets["firebase"])
        if "private_key" in fb_creds:
            fb_creds["private_key"] = fb_creds["private_key"].replace("\\n", "\n")
        
        cred = credentials.Certificate(fb_creds)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        if "already exists" not in str(e):
            st.error(f"Firebase 초기화 중 오류: {e}")
            st.stop()

db = firestore.client()
appId = st.secrets.get("app_id", "facility-ledger-2026-v1")
doc_ref = db.collection('artifacts').document(appId).collection('public').document('data').collection('facility_data').document('master')

# -----------------------------------------------------------------------------
# 2. 스타일 및 디자인 시스템 (High Visibility & Premium 3D UI)
# -----------------------------------------------------------------------------
st.set_page_config(page_title="2026 월별 지출관리", layout="wide", page_icon="🏢")

st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;900&display=swap');
        
        /* 전체 기본 폰트 크기 상향 */
        html, body, [class*="css"] { font-family: 'Noto Sans KR', sans-serif !important; color: #1e293b; font-size: 16px; }
        .stApp { background-color: #f1f5f9; }
        
        /* 컨테이너 스타일 */
        div[data-testid="stVerticalBlock"] > div[style*="flex-direction: column;"] > div[data-testid="stVerticalBlock"] {
            background-color: white; padding: 1.2rem 1.5rem; border-radius: 1.2rem; border: 1px solid #e2e8f0;
            box-shadow: 0 4px 10px rgba(0, 0, 0, 0.03); margin-bottom: 0.8rem;
        }

        /* 3D 메트릭 카드 디자인 */
        .metric-card {
            background: white; padding: 22px 28px; border-radius: 22px; border-left: 12px solid #3b82f6;
            box-shadow: 0 15px 30px -5px rgba(0, 0, 0, 0.08), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            border: 1px solid #f1f5f9; border-left-width: 12px;
            margin-bottom: 15px;
            width: 100%;
            cursor: default;
        }
        .metric-card:hover {
            transform: translateY(-8px);
            box-shadow: 0 25px 40px -10px rgba(0, 0, 0, 0.12);
        }
        .metric-label { font-size: 1.05rem; font-weight: 800; color: #64748b; margin-bottom: 6px; display: flex; align-items: center; gap: 8px; }
        .metric-value { font-size: 2.4rem; font-weight: 900; color: #0f172a; letter-spacing: -1.5px; line-height: 1.2; }
        .metric-unit { font-size: 1.2rem; font-weight: 600; color: #94a3b8; margin-left: 6px; }

        h1 { background: linear-gradient(135deg, #1e3a8a, #3b82f6); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 900; letter-spacing: -1px; margin-bottom: 1.5rem; font-size: 2.2rem; }
        .section-label { font-size: 1.15rem; font-weight: 800; color: #1e3a8a; margin-bottom: 15px; display: block; border-left: 6px solid #2563eb; padding-left: 12px; }
        
        .stButton button { 
            background: linear-gradient(to right, #3b82f6, #2563eb) !important; 
            color: white !important; border-radius: 10px; font-weight: 700; border: none !important;
            padding: 0.5rem 1.2rem !important; transition: all 0.2s; height: 45px !important;
            width: 100% !important; font-size: 1.05rem !important;
            box-shadow: 0 4px 6px -1px rgba(59, 130, 246, 0.3);
        }
        .stButton button:hover { transform: translateY(-2px); box-shadow: 0 10px 15px -3px rgba(59, 130, 246, 0.4); }
        
        .korean-amount { background-color: #f0f7ff; padding: 8px 15px; border-radius: 10px; border: 1px solid #cce3ff; color: #1e40af; font-weight: 800; margin-top: 5px; display: block; font-size: 1rem; text-align: right; }
        
        .exec-summary-item {
            background-color: #f8fafc; padding: 12px 16px; border-radius: 12px; border: 1px solid #f1f5f9; margin-bottom: 10px;
        }
        .exec-summary-cat { font-weight: 800; color: #334155; font-size: 0.85rem; margin-bottom: 6px; display: block; }
        .exec-summary-row { display: flex; justify-content: space-between; align-items: center; margin-top: 4px; }
        .exec-summary-val { font-weight: 900; font-size: 1.05rem; }
        
        /* 🚀 Tab 2 신속집행 카드 (다홍색 테마) */
        .quick-exec-card-scarlet {
            background-color: #fef2f2; 
            border: 2px solid #e11d48; 
            border-radius: 20px; 
            padding: 25px; margin-bottom: 30px; 
            box-shadow: 0 10px 20px rgba(225, 29, 72, 0.1);
        }
        .quick-exec-badge-scarlet { 
            background-color: #e11d48; 
            color: white; padding: 6px 14px; 
            border-radius: 10px; font-weight: 800; font-size: 0.95rem; 
        }
        
        .stTabs [data-baseweb="tab"] { height: 55px; font-weight: 800; font-size: 1.1rem; }
        .stSelectbox, .stNumberInput { margin-bottom: 10px !important; }
        
        /* 사이드바 스타일 */
        [data-testid="stSidebar"] { background-color: #ffffff !important; border-right: 1px solid #e2e8f0; }

        /* RPA 로그용 스타일 */
        .success-text { color: #10b981; font-weight: 800; }
        .error-text { color: #ef4444; font-weight: 800; }
        .warning-text { color: #f59e0b; font-weight: 800; }
        .log-box { background-color: #f8fafc; padding: 10px; border-radius: 8px; font-family: monospace; font-size: 0.85rem; line-height: 1.5; border: 1px solid #e2e8f0; }
    </style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 3. 데이터 관리 로직
# -----------------------------------------------------------------------------
CATEGORIES = ["전기요금", "상하수도", "통신요금", "복합기임대", "공청기비데", "상품매입비", "수입금", "자체소수선", "부서업무비", "무인경비", "승강기점검", "신용카드수수료", "환경용역", "세탁용역", "야간경비", "수탁자산취득비", "일반재료비"]
MONTHS = list(range(1, 13))
YEARS = [2024, 2025, 2026]

QUICK_EXEC_CONFIG = {
    "수탁자산취득비": {"target": 12894000, "goal_q1": 5157600, "goal_h1": 9025800},
    "일반재료비": {"target": 14300000, "goal_q1": 5720000, "goal_h1": 10010000},
    "상품매입비": {"target": 7700000, "goal_q1": 3080000, "goal_h1": 5390000}
}

def ensure_data_integrity(data):
    if not isinstance(data, dict) or "records" not in data: data = {"records": []}
    existing = {(r['year'], r['month'], r['category']) for r in data['records']}
    new_recs = []
    for y in YEARS:
        for c in CATEGORIES:
            for m in MONTHS:
                if (y, m, c) not in existing:
                    new_recs.append({"year": y, "month": m, "category": c, "amount": 0.0, "drafted": False, "evidence": "", "status": "지출"})
    for r in data['records']:
        if "status" not in r: r["status"] = "지출"
    if new_recs: data['records'].extend(new_recs)
    return data, len(new_recs) > 0

def save_data_cloud(data):
    try: final_data, _ = ensure_data_integrity(data); doc_ref.set(final_data); return True
    except Exception as e: st.error(f"저장 실패: {e}"); return False

def load_data():
    try:
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict(); validated, updated = ensure_data_integrity(data)
            if updated: save_data_cloud(validated)
            return validated
        else:
            default = {"records": []}; ensure_data_integrity(default); save_data_cloud(default); return default
    except Exception: return {"records": []}

def number_to_korean(n):
    n = int(n)
    if n == 0: return "금영원"
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
    return "금" + "".join(res[::-1]) + "원"

# -----------------------------------------------------------------------------
# 4. RPA 엔진 (완전 복구)
# -----------------------------------------------------------------------------
def find_element_deep(driver, by, value, timeout=10):
    start_time = time.time()
    while time.time() - start_time < timeout:
        driver.switch_to.default_content()
        try:
            el = driver.find_element(by, value)
            if el.is_displayed(): return el
        except: pass
        def search_frames():
            frames = driver.find_elements(By.TAG_NAME, "frame") + driver.find_elements(By.TAG_NAME, "iframe")
            for frame in frames:
                try:
                    driver.switch_to.frame(frame)
                    try:
                        el = driver.find_element(by, value)
                        if el: return el
                    except: pass
                    res = search_frames()
                    if res: return res
                    driver.switch_to.parent_frame()
                except: pass
            return None
        found = search_frames()
        if found: return found
        time.sleep(0.5)
    return None

def verify_and_set_period(driver, target_year, add_log, force=False):
    for attempt in range(3):
        date_start_el = find_element_deep(driver, By.ID, "registDate")
        date_end_el = find_element_deep(driver, By.ID, "registDate3")
        if date_start_el and date_end_el:
            v_start = date_start_el.get_attribute("value")
            if not force and str(target_year) in v_start: return True
        add_log(f"연도/기간 재설정 중 ({target_year}년)...")
        year_sel = find_element_deep(driver, By.ID, "szDocDeptYear")
        if year_sel:
            Select(year_sel).select_by_value(str(target_year))
            driver.execute_script("arguments[0].dispatchEvent(new Event('change'));", year_sel)
            driver.execute_script("if(typeof changeDocdeptTree == 'function'){ changeDocdeptTree(''); }")
            time.sleep(3)
            date_start_el = find_element_deep(driver, By.ID, "registDate")
            date_end_el = find_element_deep(driver, By.ID, "registDate3")
            if date_start_el and date_end_el:
                driver.execute_script("arguments[0].value = arguments[1];", date_start_el, f"{target_year}-01-01")
                driver.execute_script("arguments[0].value = arguments[1];", date_end_el, f"{target_year}-12-31")
                driver.execute_script("arguments[0].dispatchEvent(new Event('change'));", date_start_el)
                time.sleep(1)
                if str(target_year) in date_start_el.get_attribute("value"):
                    add_log(f"기간 고정 성공: {target_year}년", "success")
                    return True
        time.sleep(1)
    return False

def is_valid_document(title, target_year, target_month, category):
    exclusions = ["점검기록부", "점검기록", "설비점검", "카페테리아", "부과계획", "부과안내", "점검표", "안내"]
    clean_title = title.replace(" ", "")
    if any(ex in clean_title for ex in exclusions): return False
    if f"{target_year}년" not in title and f"{str(target_year)[2:]}년" not in title: return False
    month_pattern = rf"(?<!\d){target_month}월"
    if not re.search(month_pattern, title): return False
    match_keyword = category.replace("요금", "").replace("임대", "").replace("수수료", "").replace("점검", "")
    if match_keyword not in title: return False
    actions = ["납부", "지출", "집행", "결의", "청구", "지급", "수납", "대금", "정산"]
    if any(act in title for act in actions): return True
    return False

def run_groupware_rpa_fast(target_year, target_category):
    status_box = st.status(f"🚀 그룹웨어 스캔 중...", expanded=True)
    log_container = st.empty(); logs = []
    def add_log(msg, type="info"):
        color_class = "success-text" if type == "success" else "error-text" if type == "error" else "warning-text" if type == "warn" else ""
        logs.append(f'<span class="{color_class}">[{"OK" if type=="success" else "!!" if type=="error" else "??" if type=="warn" else ">>"}] {msg}</span>')
        log_container.markdown(f'<div class="log-box">{"<br>".join(logs)}</div>', unsafe_allow_html=True)
    try:
        USER_ID = st.secrets["groupware"]["id"]; USER_PW = st.secrets["groupware"]["pw"]
    except: add_log("Secrets 설정 확인 필요", "error"); return
    options = webdriver.ChromeOptions()
    options.add_argument("--no-sandbox"); options.add_argument("--disable-dev-shm-usage"); options.add_argument("--window-size=1280,1024"); options.add_argument("--headless")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    try:
        driver.get("http://192.168.1.245:8888/index.jsp")
        add_log("접속 시도...")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "Name")))
        driver.find_element(By.NAME, "Name").send_keys(USER_ID)
        driver.find_element(By.NAME, "Password").send_keys(USER_PW + Keys.RETURN)
        time.sleep(3)
        menu = find_element_deep(driver, By.ID, "menu_4")
        if menu: driver.execute_script("arguments[0].click();", menu); time.sleep(1)
        reg = find_element_deep(driver, By.CSS_SELECTOR, "[title='기록물등록대장']")
        if reg: driver.execute_script("arguments[0].click();", reg); time.sleep(2)
        full_data = load_data(); updated_count = 0
        core_keyword = target_category.replace("요금", "").replace("임대", "").replace("수수료", "").replace("점검", "")
        search_words = list(dict.fromkeys([core_keyword, target_category]))
        all_titles = []
        for word in search_words:
            if not verify_and_set_period(driver, target_year, add_log): return
            add_log(f"'{word}' 검색 중...")
            search_input = find_element_deep(driver, By.ID, "sj")
            if search_input:
                driver.execute_script("arguments[0].value = '';", search_input)
                driver.execute_script("arguments[0].value = arguments[1];", search_input, word)
                search_input.send_keys(Keys.RETURN)
                try: driver.execute_script("if(typeof fncSearch == 'function') fncSearch();")
                except: pass
                time.sleep(4)
                links = driver.find_elements(By.CSS_SELECTOR, "a[title]")
                all_titles.extend([l.get_attribute("title") for l in links if l.get_attribute("title")])
        all_titles = list(set(all_titles))
        for month in MONTHS:
            found = False
            for t in all_titles:
                if is_valid_document(t, target_year, month, target_category):
                    for item in full_data["records"]:
                        if item["year"] == target_year and item["category"] == target_category and item["month"] == month:
                            if not item["drafted"]:
                                item["drafted"], item["evidence"] = True, t
                                updated_count += 1
                                add_log(f"[{month}월] 발견!")
                            found = True; break
                    if found: break
        if updated_count > 0:
            save_data_cloud(full_data); st.session_state['data'] = full_data
            add_log(f"최종 {updated_count}건 업데이트 성공!", "success")
    except Exception as e: add_log(f"오류: {str(e)}", "error")
    finally: driver.quit(); st.rerun()

# -----------------------------------------------------------------------------
# 5. 상태 관리 및 메인 UI
# -----------------------------------------------------------------------------
if 'amt_box' not in st.session_state: st.session_state.amt_box = 0
if 'data' not in st.session_state: st.session_state['data'] = load_data()

def update_amt(increment): st.session_state.amt_box += int(increment)
def reset_amt(): st.session_state.amt_box = 0
def save_and_register(year, cat, mon):
    if st.session_state.amt_box > 0:
        curr = st.session_state['data']
        for r in curr["records"]:
            if r["year"] == year and r["category"] == cat and r["month"] == mon:
                r["amount"] += float(st.session_state.amt_box); r["status"] = "지출"; break
        if save_data_cloud(curr):
            st.session_state['data'] = curr; st.session_state.amt_box = 0; st.toast("✅ 성공적으로 등록되었습니다.")

data = st.session_state['data']
df_all = pd.DataFrame(data.get("records", []))
df_all["amount"] = pd.to_numeric(df_all["amount"], errors='coerce').fillna(0).astype('float64')

# --- SIDEBAR (컨트롤 버튼) ---
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3135/3135715.png", width=60)
    st.title("Cloud Control")
    st.markdown("---")
    if st.button("💾 클라우드 수동 저장", use_container_width=True):
        if save_data_cloud(st.session_state['data']): st.success("백업 성공!")
    if st.button("🔄 데이터 새로고침", use_container_width=True):
        st.session_state['data'] = load_data(); st.rerun()
    st.markdown("---")
    st.caption(f"Connected: {appId}")

st.title("🏢 2026 월별 지출관리")
tab1, tab2, tab3, tab4 = st.tabs(["📊 지출 현황/입력", "📈 항목별 지출 분석", "🚨 미집행 현황", "✅ 그룹웨어 문서 확인"])

# --- TAB 1: 지출 현황/입력 ---
with tab1:
    if not df_all.empty:
        df_26 = df_all[df_all["year"] == 2026].copy()
        val_26 = df_26["amount"].sum()
        st.markdown(f"""
            <div class="metric-card">
                <div class="metric-label">🏢 2026년 총 지출 계획액</div>
                <div class="metric-value">{format(int(val_26), ",")} <span class="metric-unit">원</span></div>
            </div>
        """, unsafe_allow_html=True)

    col_summary, col_input, col_chart = st.columns([0.8, 1.2, 2.1])
    
    with col_summary:
        st.markdown('<span class="section-label">🚀 신속집행 요약</span>', unsafe_allow_html=True)
        if not df_all.empty:
            for cat in ["수탁자산취득비", "일반재료비", "상품매입비"]:
                df_target = df_26[df_26["category"] == cat]
                conf = QUICK_EXEC_CONFIG[cat]
                q1_sum = pd.to_numeric(df_target[df_target["month"] <= 3]["amount"], errors='coerce').fillna(0).sum()
                h1_sum = pd.to_numeric(df_target[df_target["month"] <= 6]["amount"], errors='coerce').fillna(0).sum()
                q1_r = (q1_sum / conf["goal_q1"]) * 100 if conf["goal_q1"] > 0 else 0
                h1_r = (h1_sum / conf["goal_h1"]) * 100 if conf["goal_h1"] > 0 else 0
                st.markdown(f"""
                    <div class="exec-summary-item">
                        <span class="exec-summary-cat">{cat}</span>
                        <div class="exec-summary-row"><span style="font-size:0.75rem; color:#64748b;">Q1(40%)</span><span class="exec-summary-val" style="color:#2563eb;">{q1_r:.1f}%</span></div>
                        <div class="exec-summary-row"><span style="font-size:0.75rem; color:#64748b;">H1(70%)</span><span class="exec-summary-val" style="color:#f59e0b;">{h1_r:.1f}%</span></div>
                    </div>
                """, unsafe_allow_html=True)

    with col_input:
        st.markdown('<span class="section-label">📝 지출액 신규 등록</span>', unsafe_allow_html=True)
        in_y = st.selectbox("연도 선택", YEARS, index=2, key="y_sel")
        in_c = st.selectbox("항목 선택", CATEGORIES, key="c_sel")
        in_m = st.selectbox("월 선택", MONTHS, format_func=lambda x: f"{x}월", key="m_sel")
        st.number_input("등록 금액 입력 (원)", min_value=0, step=10000, key="amt_box")
        st.markdown(f'<div class="korean-amount">{number_to_korean(st.session_state.amt_box)}</div>', unsafe_allow_html=True)
        st.write("---")
        b1, b2, b3 = st.columns(3)
        b1.button("+10만", on_click=update_amt, args=(100000,), use_container_width=True)
        b2.button("+100만", on_click=update_amt, args=(1000000,), use_container_width=True)
        b3.button("초기화", on_click=reset_amt, use_container_width=True)
        st.button("💾 데이터 등록 및 저장", type="primary", use_container_width=True, on_click=save_and_register, args=(in_y, in_c, in_m))

    with col_chart:
        st.markdown('<span class="section-label">🍩 2026 항목별 지출 비중</span>', unsafe_allow_html=True)
        if not df_all.empty:
            cat_dist = df_26.groupby("category")["amount"].sum().reset_index()
            cat_dist = cat_dist[cat_dist["amount"] > 0]
            if not cat_dist.empty:
                fig = alt.Chart(cat_dist).mark_arc(innerRadius=80, stroke="#fff").encode(
                    theta=alt.Theta("amount:Q", stack=True),
                    color=alt.Color("category:N", scale=alt.Scale(scheme='tableau20'), 
                        legend=alt.Legend(orient='bottom', columns=3, labelFontSize=12, symbolSize=50, title=None)
                    ),
                    tooltip=[alt.Tooltip("category:N", title="항목"), alt.Tooltip("amount:Q", title="금액", format=",")]
                ).properties(height=380).configure_view(strokeWidth=0)
                st.altair_chart(fig, use_container_width=True)
            else:
                st.info("데이터가 없습니다.")

    st.markdown("---")
    st.markdown(f'<span class="section-label">📅 2026 상세 지출 편집 그리드</span>', unsafe_allow_html=True)
    if not df_all.empty:
        df_p = df_all[df_all["year"] == 2026].pivot(index="category", columns="month", values="amount")
        df_p.columns = [f"{m}월" for m in df_p.columns]
        df_d = df_p.applymap(lambda x: format(int(x), ","))
        ed = st.data_editor(df_d, use_container_width=True, height=550)
        if not df_d.equals(ed):
            curr = load_data()
            for cat in CATEGORIES:
                for m in MONTHS:
                    val = str(ed.loc[cat, f"{m}월"]).replace(",", "")
                    try: clean = float(val)
                    except: clean = 0.0
                    for r in curr["records"]:
                        if r["year"] == 2026 and r["category"] == cat and r["month"] == m:
                            r["amount"] = clean; r["status"] = "지출"; break
            save_data_cloud(curr); st.rerun()

# --- TAB 2: 항목별 지출 분석 ---
with tab2:
    st.markdown('<span class="section-label">항목별 통합 관리 센터</span>', unsafe_allow_html=True)
    if not df_all.empty:
        sel_cat = st.selectbox("관리 항목 선택", CATEGORIES, key="analysis_sel")
        df_comp = df_all[df_all["category"] == sel_cat]
        
        if sel_cat in QUICK_EXEC_CONFIG:
            conf = QUICK_EXEC_CONFIG[sel_cat]
            q1_exec = df_comp[(df_comp["year"] == 2026) & (df_comp["month"] <= 3)]["amount"].sum()
            h1_exec = df_comp[(df_comp["year"] == 2026) & (df_comp["month"] <= 6)]["amount"].sum()
            q1_rate = (q1_exec / conf["goal_q1"])*100 if conf["goal_q1"] > 0 else 0
            h1_rate = (h1_exec / conf["goal_h1"])*100 if conf["goal_h1"] > 0 else 0
            
            st.markdown(f"""
                <div class="quick-exec-card-scarlet">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;">
                        <span class="quick-exec-badge-scarlet">🚀 2026 신속집행 특별관리 대상</span>
                        <span style="font-size:1.1rem; color:#be123c; font-weight:900;">대상액: {conf['target']:,}원</span>
                    </div>
                    <div style="display:grid; grid-template-columns: 1fr 1fr; gap:30px;">
                        <div>
                            <div style="display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:12px;">
                                <span style="font-weight:800; color:#1e3a8a; font-size:1.1rem;">● 1분기 목표 (40%)</span>
                                <span style="font-size:2rem; font-weight:900; color:#2563eb;">{q1_rate:.1f}%</span>
                            </div>
                            <div style="background-color:#e2e8f0; height:14px; border-radius:12px; margin-top:10px; overflow:hidden;">
                                <div style="background:linear-gradient(to right, #3b82f6, #2563eb); width:{min(q1_rate, 100):.1f}%; height:100%;"></div>
                            </div>
                        </div>
                        <div>
                            <div style="display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:12px;">
                                <span style="font-weight:800; color:#be123c; font-size:1.1rem;">● 상반기 목표 (70%)</span>
                                <span style="font-size:2rem; font-weight:900; color:#e11d48;">{h1_rate:.1f}%</span>
                            </div>
                            <div style="background-color:#e2e8f0; height:14px; border-radius:12px; margin-top:10px; overflow:hidden;">
                                <div style="background:linear-gradient(to right, #fb7185, #e11d48); width:{min(h1_rate, 100):.1f}%; height:100%;"></div>
                            </div>
                        </div>
                    </div>
                </div>
            """, unsafe_allow_html=True)

        m_cs = st.columns(3)
        v24, v25, v26 = df_comp[df_comp['year']==2024]['amount'].sum(), df_comp[df_comp['year']==2025]['amount'].sum(), df_comp[df_comp['year']==2026]['amount'].sum()
        m_cs[0].markdown(f'''<div class="metric-card" style="border-left-color: #94a3b8;"><div class="metric-label">📈 2024 실적</div><div class="metric-value">{int(v24):,}<span class="metric-unit">원</span></div></div>''', unsafe_allow_html=True)
        m_cs[1].markdown(f'''<div class="metric-card" style="border-left-color: #10b981;"><div class="metric-label">📈 2025 실적</div><div class="metric-value">{int(v25):,}<span class="metric-unit">원</span></div></div>''', unsafe_allow_html=True)
        m_cs[2].markdown(f'''<div class="metric-card" style="border-left-color: #3b82f6;"><div class="metric-label">📅 2026 계획</div><div class="metric-value">{int(v26):,}<span class="metric-unit">원</span></div></div>''', unsafe_allow_html=True)
        
        st.markdown(f"""<div style="background:#f8fafc; padding:15px; border-radius:12px; border:1px dashed #cbd5e1; margin-top:20px;"><h4 style="margin:0; color:#1e3a8a; font-size:1.1rem; font-weight:800;">🚫 [{sel_cat}] 미지출 설정 (체크 시 누락 목록에서 제외)</h4></div>""", unsafe_allow_html=True)
        st_c = st.columns(3)
        s_up = {}
        for idx, y in enumerate(YEARS):
            with st_c[idx]: 
                cur_no = df_comp[(df_comp["year"] == y) & (df_comp["status"] == "미지출")]["month"].tolist()
                s_up[y] = st.multiselect(f"{y}년 미지출 월 선택", MONTHS, default=cur_no, format_func=lambda x: f"{x}월", key=f"ms_{sel_cat}_{y}")
        
        df_p_c = df_comp.pivot(index="month", columns="year", values="amount").fillna(0)
        df_p_c.columns = [f"{c}년" for c in df_p_c.columns]
        df_d_c = df_p_c.applymap(lambda x: format(int(x), ",")).reset_index()
        df_d_c["월"] = df_d_c["month"].apply(lambda x: f"{x}월")
        ed_c = st.data_editor(df_d_c[["월", "2024년", "2025년", "2026년"]], use_container_width=True, hide_index=True, key=f"ed_{sel_cat}", height=450)
        
        changed = any(set(df_comp[(df_comp["year"]==y) & (df_comp["status"]=="미지출")]["month"].tolist()) != set(s_up[y]) for y in YEARS)
        if not df_d_c[["월", "2024년", "2025년", "2026년"]].equals(ed_c) or changed:
            curr = load_data()
            for idx, row in ed_c.iterrows():
                mv = idx + 1
                for y in YEARS:
                    va = str(row[f"{y}년"]).replace(",", "")
                    try: na = float(va)
                    except: na = 0.0
                    ns = "미지출" if mv in s_up[y] else "지출"
                    for r in curr["records"]:
                        if r["year"] == y and r["category"] == sel_cat and r["month"] == mv:
                            r["amount"], r["status"] = na, ns; break
            save_data_cloud(curr); st.rerun()

# --- TAB 3: 미집행 현황 ---
with tab3:
    st.markdown('<span class="section-label">🚨 지출 누락 점검</span>', unsafe_allow_html=True)
    now = datetime.now(); cy, cm = now.year, now.month
    st.info(f"📅 기준일: {cy}년 {cm}월 | 현재 월 이후의 미래 계획은 누락에서 자동 제외됩니다.")
    if not df_all.empty:
        cs = st.columns(3)
        for idx, y in enumerate(YEARS):
            with cs[idx]:
                st.subheader(f"📅 {y}년 현황")
                df_y = df_all[df_all["year"] == y]
                for cat in CATEGORIES:
                    cond = (df_y["category"] == cat) & (df_y["amount"] <= 0) & (df_y["status"] == "지출")
                    if y == cy: cond = cond & (df_y["month"] <= cm)
                    elif y > cy: cond = False 
                    missing = [] if isinstance(cond, bool) else df_y[cond]["month"].tolist()
                    if missing: st.error(f"**{cat}**: {', '.join(map(str, sorted(missing)))}월 누락")
                    else: st.success(f"**{cat}**: 확인 완료", icon="✅")

# --- TAB 4: 그룹웨어 문서 확인 ---
with tab4:
    c_rl, c_rr = st.columns([1, 3])
    with c_rl:
        st.markdown('<span class="section-label">RPA 스캔 제어</span>', unsafe_allow_html=True)
        rc = st.radio("확인 항목 선택", CATEGORIES, key="r_c")
        ry = st.radio("대상 연도", [2025, 2026], horizontal=True, key="r_y")
        st.divider()
        if st.button("📄 그룹웨어 스캔 시작", type="primary", use_container_width=True): 
            run_groupware_rpa_fast(ry, rc)
    with c_rr:
        st.markdown(f'<span class="section-label">{ry}년 {rc} 기안 및 증빙 현황</span>', unsafe_allow_html=True)
        df_r = df_all[(df_all["year"] == ry) & (df_all["category"] == rc)]
        if not df_r.empty:
            df_pr = df_r.pivot(index="category", columns="month", values="drafted")
            df_pr.columns = [f"{m}월" for m in df_pr.columns]
            st.data_editor(df_pr, use_container_width=True, disabled=True)
            with st.expander("📄 상세 증빙 문서 확인"):
                for _, row in df_r.iterrows():
                    if row["drafted"]: st.caption(f"**{row['month']}월**: {row['evidence']}")