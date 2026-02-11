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
from datetime import datetime
from dataclasses import dataclass

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
from selenium.webdriver.chrome.options import Options as ChromeOptions
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import Select 
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException, TimeoutException, NoSuchWindowException

# -----------------------------------------------------------------------------
# 1. Firebase 초기화
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
daily_ref = db.collection('artifacts').document(appId).collection('public').document('data').collection('facility_data').document('daily_expenses')
quant_base_ref = db.collection('artifacts').document(appId).collection('public').document('data').collection('quantitative_monthly')

# -----------------------------------------------------------------------------
# 2. 스타일 시스템
# -----------------------------------------------------------------------------
st.set_page_config(page_title="2026 월별 지출관리", layout="wide", page_icon="🏢")

st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;900&display=swap');
        html, body, [class*="css"] { font-family: 'Noto Sans KR', sans-serif !important; color: #1e293b; font-size: 15px; }
        .stApp { background-color: #f1f5f9; }
        div[data-testid="stVerticalBlock"] > div[style*="flex-direction: column;"] > div[data-testid="stVerticalBlock"] {
            background-color: white; padding: 1rem 1.2rem; border-radius: 1rem; border: 1px solid #e2e8f0;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); margin-bottom: 0.5rem;
        }
        .metric-card {
            background: white; padding: 18px 22px; border-radius: 18px; border-left: 10px solid #3b82f6;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.08);
            transition: all 0.2s ease-in-out;
            border: 1px solid #f1f5f9; margin-bottom: 12px; width: 100%;
        }
        .metric-card:hover { transform: scale(1.02); }
        .metric-label { font-size: 0.95rem; font-weight: 800; color: #64748b; margin-bottom: 4px; }
        .metric-value { font-size: 1.8rem; font-weight: 900; color: #0f172a; letter-spacing: -1px; }
        .metric-unit { font-size: 1rem; font-weight: 600; color: #94a3b8; margin-left: 4px; }
        
        h1 { background: linear-gradient(135deg, #1e3a8a, #3b82f6); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 900; letter-spacing: -1px; font-size: 2rem; }
        .section-label { font-size: 1.1rem; font-weight: 800; color: #1e3a8a; margin-bottom: 12px; display: block; border-left: 5px solid #2563eb; padding-left: 10px; }
        .stButton button { background: linear-gradient(to right, #3b82f6, #2563eb) !important; color: white !important; border-radius: 8px; font-weight: 700; height: 38px !important; width: 100% !important; }
        .korean-amount { background-color: #f0f7ff; padding: 8px 15px; border-radius: 10px; border: 1px solid #cce3ff; color: #1e40af; font-weight: 800; margin-top: 5px; display: block; font-size: 1rem; text-align: right; }
        .log-box { background-color: #1e293b; color: #f8fafc; padding: 15px; border-radius: 12px; font-family: 'JetBrains Mono', monospace; font-size: 0.82rem; height: 320px; overflow-y: auto; margin-bottom: 15px; border: 1px solid #334155; line-height: 1.5; }
        
        .quant-header-blue { background-color: #eff6ff; border: 1px solid #bfdbfe; padding: 10px; border-radius: 10px; font-weight: 700; color: #1e40af; font-size: 13px; text-align: center; }
        .quant-header-orange { background-color: #fffaf5; border: 1px solid #fed7aa; padding: 10px; border-radius: 10px; font-weight: 700; color: #9a3412; font-size: 13px; text-align: center; }
        
        .stRadio [role=radiogroup] { flex-direction: row; justify-content: space-between; overflow-x: auto; }
        .stRadio div[role='radiogroup'] > label { background: #fff; border: 1px solid #e2e8f0; padding: 5px 10px; border-radius: 8px; font-size: 0.9rem; min-width: 50px; text-align: center; justify-content: center; }
        .stRadio div[role='radiogroup'] > label[data-checked='true'] { background: #2563eb; color: white; border-color: #2563eb; }
        
        .stDataFrame div[data-testid="stTable"] { font-size: 13px !important; }
    </style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 3. 유틸리티 함수 및 상태 관리 콜백
# -----------------------------------------------------------------------------
CATEGORIES = ["전기요금", "상하수도", "통신요금", "복합기임대", "공청기비데", "상품매입비", "수입금", "자체소수선", "부서업무비", "무인경비", "승강기점검", "신용카드수수료", "환경용역", "세탁용역", "야간경비", "수탁자산취득비", "일반재료비"]
MONTHS = list(range(1, 13))
YEARS = [2024, 2025, 2026]

QUICK_EXEC_CONFIG = {
    "수탁자산취득비": {"target": 12894000, "goal_q1": 5157600, "goal_h1": 9025800},
    "일반재료비": {"target": 14300000, "goal_q1": 5720000, "goal_h1": 10010000},
    "상품매입비": {"target": 7700000, "goal_q1": 3080000, "goal_h1": 5390000}
}

def update_amt(increment): 
    if 'amt_box' in st.session_state:
        st.session_state.amt_box += int(increment)

def reset_amt(): 
    if 'amt_box' in st.session_state:
        st.session_state.amt_box = 0

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

def save_data_cloud(data):
    try: doc_ref.set(data); return True
    except: return False

def load_data():
    try:
        doc = doc_ref.get()
        if doc.exists: return doc.to_dict()
        return {"records": []}
    except: return {"records": []}

def load_daily_expenses():
    try:
        doc = daily_ref.get()
        if doc.exists: return doc.to_dict().get("expenses", [])
        return []
    except: return []

def save_daily_expenses(expense_list):
    try: daily_ref.set({"expenses": expense_list, "last_updated": datetime.now().isoformat()}); return True
    except: return False

def load_quant_monthly(month):
    try:
        m_doc = quant_base_ref.document(str(month)).get()
        if m_doc.exists: return m_doc.to_dict().get("data", [])
        return []
    except: return []

def save_quant_monthly(month, data_list):
    try:
        quant_base_ref.document(str(month)).set({"data": data_list, "last_updated": datetime.now().isoformat()})
        return True
    except: return False

def number_to_korean(n):
    n = int(n); units = ["", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]
    digit_units = ["", "십", "백", "천"]; group_units = ["", "만", "억", "조"]
    res = []; s_num = str(int(n))[::-1]
    for i in range(0, len(s_num), 4):
        group, group_res = s_num[i:i+4], ""
        for j, digit in enumerate(group):
            d = int(digit); 
            if d > 0: group_res = units[d] + digit_units[j] + group_res
        if group_res: res.append(group_res + group_units[i // 4])
    return "금" + "".join(res[::-1]) + "원"

# -----------------------------------------------------------------------------
# 4. RPA 엔진 (유지)
# -----------------------------------------------------------------------------
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
        except: add_log("세션 유지 중", "warn")
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
            df_new = pd.read_excel(latest_file); df_new["집행금액"] = pd.to_numeric(df_new["집행금액"], errors='coerce').fillna(0)
            df_new = df_new[df_new["집행일자"].notna() & df_new["적요"].notna()]; df_new = df_new[~df_new["적요"].str.contains("계", na=False)]
            new_exp = df_new[["세목", "집행일자", "적요", "집행금액"]].to_dict('records')
            if save_daily_expenses(new_exp): st.session_state['daily_expenses'] = new_exp; add_log("동기화 성공!", "success")
        driver.quit(); time.sleep(1); st.rerun()
    except Exception as e:
        add_log(f"RPA 오류: {str(e)}", "error")
        if driver: driver.quit()

# -----------------------------------------------------------------------------
# 5. 상태 초기화 및 메인 데이터 로드
# -----------------------------------------------------------------------------
if 'amt_box' not in st.session_state: st.session_state.amt_box = 0
if 'data' not in st.session_state: st.session_state['data'] = load_data()
if 'daily_expenses' not in st.session_state: st.session_state['daily_expenses'] = load_daily_expenses()

data = st.session_state['data']
df_all = pd.DataFrame(data.get("records", []))
if not df_all.empty:
    df_all["amount"] = pd.to_numeric(df_all["amount"], errors='coerce').fillna(0).astype('float64')

# --- SIDEBAR ---
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3135/3135715.png", width=60)
    st.title("Manager Console")
    if st.button("💾 클라우드 수동 저장", use_container_width=True):
        if save_data_cloud(st.session_state['data']): st.success("백업 완료")
    if st.button("🔄 데이터 새로고침", use_container_width=True):
        st.session_state['data'] = load_data(); st.session_state['daily_expenses'] = load_daily_expenses(); st.rerun()
    st.divider(); st.caption(f"Connected: {appId}")

st.title("🏢 2026 월별 지출관리")
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📊 지출 현황/입력", "📈 항목별 지출 분석", "🚨 미집행 현황", "✅ 그룹웨어 문서 확인", "📂 일상경비 지출현황", "📂 1~12월 정량실적(비용)"])

# --- TAB 1 ~ 5 (기존 로직 유지) ---
with tab1:
    if not df_all.empty:
        df_26 = df_all[df_all["year"] == 2026].copy(); val_26 = df_26["amount"].sum()
        st.markdown(f"""<div class="metric-card"><div class="metric-label">🏢 2026년 총 지출 계획액</div><div class="metric-value">{format(int(val_26), ",")} <span class="metric-unit">원</span></div></div>""", unsafe_allow_html=True)
    c_s, c_i, c_p = st.columns([0.8, 1.2, 2.1])
    with c_s:
        st.markdown('<span class="section-label">🚀 신속집행 요약</span>', unsafe_allow_html=True)
        for cat in ["수탁자산취득비", "일반재료비", "상품매입비"]:
            df_t = df_26[df_26["category"] == cat] if not df_all.empty else pd.DataFrame(); conf = QUICK_EXEC_CONFIG.get(cat, {"goal_q1":1, "goal_h1":1})
            q1_v = pd.to_numeric(df_t[df_t["month"] <= 3]["amount"], errors='coerce').fillna(0).sum() if not df_t.empty else 0
            h1_v = pd.to_numeric(df_t[df_t["month"] <= 6]["amount"], errors='coerce').fillna(0).sum() if not df_t.empty else 0
            st.markdown(f"""<div class="exec-summary-item"><span class="exec-summary-cat">{cat}</span><div class="exec-summary-row"><span style="font-size:0.75rem; color:#64748b;">Q1(40%)</span><span class="exec-summary-val" style="color:#2563eb;">{(q1_v/conf['goal_q1'])*100:.1f}%</span></div><div class="exec-summary-row"><span style="font-size:0.75rem; color:#64748b;">H1(70%)</span><span class="exec-summary-val" style="color:#f59e0b;">{(h1_v/conf['goal_h1'])*100:.1f}%</span></div></div>""", unsafe_allow_html=True)
    with c_i:
        st.markdown('<span class="section-label">📝 지출액 신규 등록</span>', unsafe_allow_html=True)
        iy, ic, im = st.selectbox("연도", YEARS, index=2, key="y_sel"), st.selectbox("항목", CATEGORIES, key="c_sel"), st.selectbox("월", MONTHS, format_func=lambda x: f"{x}월", key="m_sel")
        st.number_input("금액 (원)", min_value=0, step=10000, key="amt_box")
        st.markdown(f'<div class="korean-amount">{number_to_korean(st.session_state.amt_box)}</div>', unsafe_allow_html=True)
        bc1, bc2, bc3 = st.columns(3); bc1.button("+10만", on_click=update_amt, args=(100000,), use_container_width=True); bc2.button("+100만", on_click=update_amt, args=(1000000,), use_container_width=True); bc3.button("🔄", on_click=reset_amt, use_container_width=True)
        st.button("💾 데이터 등록 및 저장", type="primary", use_container_width=True, on_click=save_and_register, args=(iy, ic, im))
    with c_p:
        st.markdown('<span class="section-label">🍩 2026 항목별 지출 비중</span>', unsafe_allow_html=True)
        cat_dist = df_26.groupby("category")["amount"].sum().reset_index() if not df_all.empty else pd.DataFrame(); cat_dist = cat_dist[cat_dist["amount"] > 0] if not cat_dist.empty else pd.DataFrame()
        if not cat_dist.empty:
            fig = alt.Chart(cat_dist).mark_arc(innerRadius=80, stroke="#fff").encode(theta=alt.Theta("amount:Q", stack=True), color=alt.Color("category:N", scale=alt.Scale(scheme='tableau20'), legend=alt.Legend(orient='bottom', columns=3, labelFontSize=12, title=None)), tooltip=[alt.Tooltip("category:N", title="항목"), alt.Tooltip("amount:Q", title="금액", format=",")]).properties(height=380).configure_view(strokeWidth=0)
            st.altair_chart(fig, use_container_width=True)
    st.markdown("---"); st.markdown(f'<span class="section-label">📅 2026 상세 지출 편집 그리드</span>', unsafe_allow_html=True)
    df_p = df_all[df_all["year"] == 2026].pivot(index="category", columns="month", values="amount"); df_p.columns = [f"{m}월" for m in df_p.columns]; df_d = df_p.applymap(lambda x: format(int(x), ","))
    ed = st.data_editor(df_d, use_container_width=True, height=550)
    if not df_d.equals(ed):
        curr = load_data()
        for cat in CATEGORIES:
            for m in MONTHS:
                val = str(ed.loc[cat, f"{m}월"]).replace(",", "")
                try: clean = float(val)
                except: clean = 0.0
                for r in curr["records"]:
                    if r["year"] == 2026 and r["category"] == cat and r["month"] == m: r["amount"] = clean; r["status"] = "지출"; break
        save_data_cloud(curr); st.rerun()

with tab2:
    st.markdown('<span class="section-label">항목별 통합 관리 센터</span>', unsafe_allow_html=True)
    sc = st.selectbox("관리 항목 선택", CATEGORIES, key="analysis_sel"); df_c = df_all[df_all["category"] == sc] if not df_all.empty else pd.DataFrame()
    if not df_c.empty:
        if sc in QUICK_EXEC_CONFIG:
            cf = QUICK_EXEC_CONFIG[sc]; q1_e = df_c[(df_c["year"] == 2026) & (df_c["month"] <= 3)]["amount"].sum(); h1_e = df_c[(df_c["year"] == 2026) & (df_c["month"] <= 6)]["amount"].sum()
            st.markdown(f"""<div class="quick-exec-card-scarlet"><div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;"><span class="quick-exec-badge-scarlet">🚀 2026 신속집행 특별관리 대상</span><span style="font-size:1.1rem; color:#be123c; font-weight:900;">대상액: {cf['target']:,}원</span></div><div style="display:grid; grid-template-columns: 1fr 1fr; gap:30px;"><div><div style="display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:12px;"><span style="font-weight:800; color:#1e3a8a; font-size:1.1rem;">● 1분기 목표 (40%)</span><span style="font-size:2rem; font-weight:900; color:#2563eb;">{(q1_e/cf['goal_q1'])*100:.1f}%</span></div><div style="background-color:#e2e8f0; height:14px; border-radius:12px; margin-top:10px; overflow:hidden;"><div style="background:linear-gradient(to right, #3b82f6, #2563eb); width:{min((q1_e/cf['goal_q1'])*100, 100):.1f}%; height:100%;"></div></div></div><div><div style="display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:12px;"><span style="font-weight:800; color:#be123c; font-size:1.1rem;">● 상반기 목표 (70%)</span><span style="font-size:2rem; font-weight:900; color:#e11d48;">{(h1_e/cf['goal_h1'])*100:.1f}%</span></div><div style="background-color:#e2e8f0; height:14px; border-radius:12px; margin-top:10px; overflow:hidden;"><div style="background:linear-gradient(to right, #fb7185, #e11d48); width:{min((h1_e/cf['goal_h1'])*100, 100):.1f}%; height:100%;"></div></div></div></div></div>""", unsafe_allow_html=True)
        m_cols = st.columns(3); v24, v25, v26 = df_c[df_c['year']==2024]['amount'].sum(), df_c[df_c['year']==2025]['amount'].sum(), df_c[df_c['year']==2026]['amount'].sum()
        m_cols[0].markdown(f'''<div class="metric-card" style="border-left-color: #94a3b8;"><div class="metric-label">📈 2024 실적</div><div class="metric-value">{int(v24):,}<span class="metric-unit">원</span></div></div>''', unsafe_allow_html=True); m_cols[1].markdown(f'''<div class="metric-card" style="border-left-color: #10b981;"><div class="metric-label">📈 2025 실적</div><div class="metric-value">{int(v25):,}<span class="metric-unit">원</span></div></div>''', unsafe_allow_html=True); m_cols[2].markdown(f'''<div class="metric-card" style="border-left-color: #3b82f6;"><div class="metric-label">📅 2026 계획</div><div class="metric-value">{int(v26):,}<span class="metric-unit">원</span></div></div>''', unsafe_allow_html=True)
        st_c = st.columns(3); s_up = {}
        for idx, y in enumerate(YEARS):
            with st_c[idx]: cur_no = df_c[(df_c["year"] == y) & (df_c["status"] == "미지출")]["month"].tolist(); s_up[y] = st.multiselect(f"{y}년 미지출 월", MONTHS, default=cur_no, format_func=lambda x: f"{x}월", key=f"ms_{sc}_{y}")
        df_p_c = df_c.pivot(index="month", columns="year", values="amount").fillna(0).reindex(columns=YEARS, fill_value=0); df_p_c.columns = [f"{c}년" for c in df_p_c.columns]; df_d_c = df_p_c.applymap(lambda x: format(int(x), ",")).reset_index(); df_d_c["월"] = df_d_c["month"].apply(lambda x: f"{x}월")
        ed_c = st.data_editor(df_d_c[["월", "2024년", "2025년", "2026년"]], use_container_width=True, hide_index=True, key=f"ed_{sc}", height=450)
        changed = any(set(df_c[(df_c["year"]==y) & (df_c["status"]=="미지출")]["month"].tolist()) != set(s_up[y]) for y in YEARS)
        if not df_d_c[["월", "2024년", "2025년", "2026년"]].equals(ed_c) or changed:
            curr = load_data()
            for idx, row in ed_c.iterrows():
                mv = idx + 1
                for y in YEARS:
                    va = str(row[f"{y}년"]).replace(",", ""); na = float(va) if va else 0.0; ns = "미지출" if mv in s_up[y] else "지출"
                    for r in curr["records"]:
                        if r["year"] == y and r["category"] == sc and r["month"] == mv: r["amount"], r["status"] = na, ns; break
            save_data_cloud(curr); st.rerun()

with tab3:
    st.markdown('<span class="section-label">🚨 지출 누락 점검</span>', unsafe_allow_html=True); now = datetime.now(); cy, cm = now.year, now.month
    st.info(f"📅 기준일: {cy}년 {cm}월 | 미래 지출은 자동 제외됩니다."); cs = st.columns(3)
    if not df_all.empty:
        for idx, y in enumerate(YEARS):
            with cs[idx]:
                st.subheader(f"📅 {y}년"); df_y = df_all[df_all["year"] == y]
                for cat in CATEGORIES:
                    cond = (df_y["category"] == cat) & (df_y["amount"] <= 0) & (df_y["status"] == "지출")
                    if y == cy: cond = cond & (df_y["month"] <= cm)
                    elif y > cy: cond = False 
                    missing = [] if isinstance(cond, bool) and cond == False else df_y[cond]["month"].tolist()
                    if missing: st.error(f"**{cat}**: {', '.join(map(str, sorted(missing)))}월 누락")
                    else: st.success(f"**{cat}**: 확인 완료", icon="✅")

with tab5:
    st.markdown('<span class="section-label">📂 일상경비 시스템 정확 동기화</span>', unsafe_allow_html=True); c_r, c_u = st.columns([1.2, 0.8])
    with c_r:
        st.info("💡 **정확도 업그레이드:** 메뉴 검색 후 경로(Span)를 직접 클릭하여 오작동을 방지합니다.")
        if st.button("🚀 시스템 데이터 자동 동기화 (RPA)", type="primary", use_container_width=True): run_daily_expense_rpa()
    with c_u:
        with st.expander("📥 엑셀 수동 업로드"):
            f = st.file_uploader("파일 선택", type=["xlsx", "csv"], key="daily_up")
            if f:
                df_u = pd.read_excel(f) if f.name.endswith('.xlsx') else pd.read_csv(f)
                if all(c in df_u.columns for c in ["세목", "집행일자", "적요", "집행금액"]):
                    df_u["집행금액"] = pd.to_numeric(df_u["집행금액"], errors='coerce').fillna(0); df_u = df_u[df_u["집행일자"].notna() & df_u["적요"].notna()]
                    save_daily_expenses(df_u[["세목", "집행일자", "적요", "집행금액"]].to_dict('records')); st.success("업로드 완료"); st.rerun()
    daily_data = st.session_state['daily_expenses']
    if daily_data:
        df_d = pd.DataFrame(daily_data); c1, c2, c3 = st.columns(3)
        c1.markdown(f'''<div class="metric-card" style="border-left-color: #3b82f6;"><div class="metric-label">💰 총 집행금액</div><div class="metric-value">{int(df_d["집행금액"].sum()):,}<span class="metric-unit">원</span></div></div>''', unsafe_allow_html=True); c2.markdown(f'''<div class="metric-card" style="border-left-color: #10b981;"><div class="metric-label">📝 집행 건수</div><div class="metric-value">{len(df_d)}<span class="metric-unit">건</span></div></div>''', unsafe_allow_html=True); c3.markdown(f'''<div class="metric-card" style="border-left-color: #f59e0b;"><div class="metric-label">🔝 최다 집행 세목</div><div class="metric-value" style="font-size: 1.4rem;">{df_d.groupby("세목")["집행금액"].sum().idxmax()}</div></div>''', unsafe_allow_html=True)
        fc1, fc2 = st.columns([1, 2]); fcat = st.selectbox("세목 필터", ["전체"] + sorted(df_d["세목"].unique().tolist()), key="daily_f1"); sq = st.text_input("적요 검색", placeholder="검색어 입력...", key="daily_f2")
        disp = df_d.copy();
        if fcat != "전체": disp = disp[disp["세목"] == fcat]
        if sq: disp = disp[disp["적요"].str.contains(sq, na=False)]
        disp["집행금액"] = disp["집행금액"].apply(lambda x: format(int(x), ",")); st.dataframe(disp, use_container_width=True, height=600, hide_index=True)

# --- TAB 6: 1~12월 정량실적(비용) (Fixed Hierarchy & Side-by-Side Layout) ---
with tab6:
    st.markdown('<span class="section-label">📂 1~12월 정량실적(비용) 센터</span>', unsafe_allow_html=True)
    t_month = st.radio("관리 월 선택", options=MONTHS, horizontal=True, format_func=lambda x: f"{x}월", label_visibility="collapsed")
    
    with st.expander(f"📥 {t_month}월 엑셀 데이터 업로드", expanded=False):
        q_f = st.file_uploader("엑셀 파일 선택", type=["xlsx"], key=f"q_up_{t_month}")
        if q_f:
            try:
                df_q = pd.read_excel(q_f)
                if len(df_q.columns) >= 5: df_q = df_q.iloc[:, :5]; df_q.columns = ["구분", "예산액", "예산배정", "지출액", "잔액"]
                df_q = df_q[df_q["구분"].notna()]
                noise_k = ["정책", "단위", "합계", "#rspan"]
                df_q = df_q[~df_q["구분"].astype(str).str.contains('|'.join(noise_k))]
                for col in ["예산액", "예산배정", "지출액", "잔액"]:
                    df_q[col] = df_q[col].astype(str).str.replace(',', '').replace('nan', '0'); df_q[col] = pd.to_numeric(df_q[col], errors='coerce').fillna(0)
                if st.button(f"🚀 {t_month}월 데이터 동기화", use_container_width=True):
                    if save_quant_monthly(t_month, df_q.to_dict('records')): st.success("성공"); st.rerun()
            except Exception as e: st.error(f"오류: {e}")

    q_data = load_quant_monthly(t_month)
    if q_data:
        df_q = pd.DataFrame(q_data).reset_index(drop=True)
        # 고유 ID 부여
        df_q['id'] = df_q.index
        
        # 1. 들여쓰기 기반 계층(Path) 생성
        def get_indent(s):
            cnt = 0
            for char in str(s):
                if char == '\u3000' or char == ' ': cnt += 1
                else: break
            return cnt
        df_q['level'] = df_q['구분'].apply(get_indent)
        
        # 이름 정리 (들여쓰기 제거)
        df_q['clean_name'] = df_q['구분'].str.strip()
        # 예산 코드 추출 (숫자만)
        df_q['budget_code'] = df_q['clean_name'].str.extract(r'^(\d+(?:-\d+)?)')[0]
        
        paths = []; stack = []
        for idx, row in df_q.iterrows():
            lvl = row['level']
            while stack and stack[-1][0] >= lvl: stack.pop()
            parent_p = stack[-1][1] if stack else "root"
            curr_p = f"{parent_p}/{idx}"
            paths.append(curr_p); stack.append((lvl, curr_p))
        df_q['path'] = paths
        
        # 2. UI 옵션 (전체 선택, 셋트 선택)
        col_opts = st.columns([0.4, 0.4, 0.2])
        with col_opts[0]:
            select_all = st.checkbox("✅ 전체 선택", value=False)
        with col_opts[1]:
            select_set = st.checkbox("✅ 실지출 셋트 선택 (제외 항목 적용)")
            st.caption("※ 제외: 성과급(109), 교육훈련비(201-12), 수선유지비(214~), 자산취득비(405~)")
        
        # 선택 로직 적용
        if select_all:
            df_q['선택'] = True
        elif select_set:
            def is_excluded(code):
                if pd.isna(code): return False
                code = str(code)
                if code.startswith('109'): return True
                if code == '201-12': return True
                if code.startswith('214'): return True
                if code.startswith('405'): return True
                return False
            df_q['선택'] = df_q['budget_code'].apply(lambda x: not is_excluded(x))
        elif '선택' not in df_q.columns:
            df_q['선택'] = False
            
        # 하위 펼치기 토글
        show_details = st.checkbox("🔍 하위 세목 펼쳐보기", value=True)
        
        # 3. [Side-by-Side Layout]
        col_table, col_dash = st.columns([1.6, 1])
        
        with col_table:
            display_df = df_q.copy()
            if not show_details:
                # 자식 노드 숨김 (path에 '/'가 2개 이상이면 자식으로 간주 - root/idx/child_idx)
                # 더 정확히는 level > 0 인 것들을 숨김? 아니면 최상위만 표시?
                # 여기선 단순화: level == 0 인 것만 표시 (최상위)
                display_df = display_df[display_df['level'] == 0]

            ed_q = st.data_editor(
                display_df,
                column_order=["선택", "구분", "예산액", "예산배정", "지출액", "잔액"],
                column_config={
                    "선택": st.column_config.CheckboxColumn("", width="small"),
                    "구분": st.column_config.TextColumn("예산 항목", width="large", disabled=True),
                    "예산액": st.column_config.NumberColumn("예산액", format="%,d", disabled=True),
                    "예산배정": st.column_config.NumberColumn("예산배정", format="%,d", disabled=True),
                    "지출액": st.column_config.NumberColumn("지출액", format="%,d", disabled=True),
                    "잔액": st.column_config.NumberColumn("잔액", format="%,d", disabled=True),
                },
                hide_index=True, use_container_width=True,
                height=(len(display_df) + 1) * 35 + 20 
            )
        
        with col_dash:
            st.markdown("##### 📊 실시간 스마트 합계")
            
            # [Smart Sum Logic V2]
            # 선택된 ID 추출
            selected_ids = set(ed_q[ed_q["선택"] == True]['id'])
            
            # 전체 데이터에서 Path 정보 참조
            # (ed_q는 필터링되었을 수 있으므로 원본 df_q 사용)
            # 그러나 선택 여부는 ed_q에서 왔으므로 매핑 필요.
            # 하지만 전체 선택/셋트 선택 시에는 df_q 전체에 적용됨.
            # 사용자가 수동으로 체크한 경우 ed_q에만 반영됨.
            # 따라서 ed_q의 선택 정보를 df_q에 업데이트해야 함.
            
            # Map selection back to main df
            # ed_q has 'id' because it's a copy of df_q subset
            # We iterate ed_q and update df_q selection
            # (Streamlit data editor returns modified df)
            
            # 최적화: ed_q의 선택된 ID만 가져와서 계산
            # 주의: 필터링되어 안 보이는 항목이 선택되어 있을 수도 있음 (셋트 선택 시)
            # -> V88.0에서는 '전체 데이터' 기준으로 합산해야 함.
            # -> ed_q는 '보이는 데이터'만 수정 가능.
            # -> 만약 필터링 된 상태에서 체크하면? -> 필터링 된 것만 체크됨.
            # -> 셋트 선택 로직은 전체 데이터 기준임.
            
            # 합산을 위해 선택된 모든 ID 확보
            # 1. 셋트/전체 선택 로직에 의해 이미 df_q['선택']이 설정됨
            # 2. 사용자가 에디터에서 수정한 내용은 ed_q에 있음
            # -> ed_q의 변경사항을 df_q에 반영
            if not display_df.equals(ed_q): # 변경 감지
                # ed_q의 id를 기준으로 df_q 업데이트
                for idx, row in ed_q.iterrows():
                    real_id = row['id']
                    df_q.loc[df_q['id'] == real_id, '선택'] = row['선택']
            
            # 이제 df_q 전체에서 선택된 항목을 기준으로 스마트 합산
            sel_rows = df_q[df_q['선택'] == True]
            sel_paths = set(sel_rows['path'])
            
            final_ids = set()
            for p in sel_paths:
                parts = p.split('/')
                is_descendant = False
                for i in range(1, len(parts)):
                    ancestor = "/".join(parts[:i])
                    if ancestor in sel_paths:
                        is_descendant = True; break
                if not is_descendant: final_ids.add(p)
            
            # path 매칭으로 최종 합산 행 도출 (path가 고유하므로 가능)
            calc_df = df_q[df_q['path'].isin(final_ids)]
            
            s_b = calc_df["예산액"].sum(); s_a = calc_df["예산배정"].sum()
            s_s = calc_df["지출액"].sum(); s_bal = calc_df["잔액"].sum()
            
            st.markdown(f'''
                <div class="metric-card" style="border-left-color: #3b82f6;">
                    <div class="metric-label">💰 선택 예산액</div><div class="metric-value">{int(s_b):,}</div>
                </div>
                <div class="metric-card" style="border-left-color: #10b981;">
                    <div class="metric-label">📅 선택 예산배정</div><div class="metric-value">{int(s_a):,}</div>
                </div>
                <div class="metric-card" style="border-left-color: #ef4444;">
                    <div class="metric-label">💸 선택 지출액</div><div class="metric-value">{int(s_s):,}</div>
                </div>
                <div class="metric-card" style="border-left-color: #f59e0b;">
                    <div class="metric-label">⚖️ 선택 잔액</div><div class="metric-value">{int(s_bal):,}</div>
                </div>
            ''', unsafe_allow_html=True)
            
            st.markdown("""<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px;"><div class="quant-header-blue">🟦 운영·관리(수탁)</div><div class="quant-header-orange">🟧 운영·관리(일상경비)</div></div>""", unsafe_allow_html=True)
    else:
        st.info("데이터가 없습니다. 엑셀 파일을 업로드해주세요.")