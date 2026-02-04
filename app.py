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
# 1. Firebase 클라우드 DB 초기화
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
        st.error(f"Firebase 초기화 중 오류: {e}")
        st.stop()

db = firestore.client()
appId = st.secrets.get("app_id", "facility-ledger-2026-v1")
doc_ref = db.collection('artifacts').document(appId).collection('public').document('data').collection('facility_data').document('master')

# -----------------------------------------------------------------------------
# 2. 스타일 및 디자인 시스템 (Premium UI)
# -----------------------------------------------------------------------------
st.set_page_config(page_title="2026 월별 지출관리", layout="wide", page_icon="🏢")

st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;900&display=swap');
        html, body, [class*="css"] { font-family: 'Noto Sans KR', sans-serif !important; color: #1e293b; }
        .stApp { background-color: #f8fafc; }
        
        /* 메인 카드 스타일 */
        div[data-testid="stVerticalBlock"] > div[style*="flex-direction: column;"] > div[data-testid="stVerticalBlock"] {
            background-color: white; padding: 2.2rem; border-radius: 1.5rem; border: 1px solid #e2e8f0;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.05); margin-bottom: 1.5rem;
        }
        
        /* 프리미엄 메트릭 카드 */
        .metric-card {
            background: white; padding: 24px; border-radius: 20px; border-left: 10px solid #3b82f6;
            box-shadow: 0 10px 20px rgba(0,0,0,0.03); transition: all 0.3s ease;
            display: flex; flex-direction: column; justify-content: center;
            border: 1px solid #f1f5f9; border-left-width: 10px;
        }
        .metric-card:hover { transform: translateY(-5px); shadow: 0 15px 30px rgba(0,0,0,0.08); }
        .metric-label { font-size: 1rem; font-weight: 800; color: #64748b; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
        .metric-value { font-size: 2.4rem; font-weight: 900; color: #0f172a; letter-spacing: -1.5px; line-height: 1.2; }
        .metric-unit { font-size: 1.1rem; font-weight: 600; color: #94a3b8; margin-left: 6px; }

        h1 { background: linear-gradient(135deg, #1e3a8a, #3b82f6); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 900; letter-spacing: -1.5px; }
        .section-label { font-size: 1.2rem; font-weight: 800; color: #1e3a8a; margin-bottom: 15px; display: block; border-left: 6px solid #2563eb; padding-left: 15px; }
        
        /* 버튼 스타일 */
        .stButton button { 
            background: linear-gradient(to right, #2563eb, #1d4ed8) !important; 
            color: white !important; border-radius: 12px; font-weight: 700; border: none !important;
            padding: 0.75rem 2rem !important; transition: all 0.3s;
        }
        .stButton button:hover { transform: translateY(-2px); box-shadow: 0 10px 25px rgba(37, 99, 235, 0.4); }
        
        /* 사이드바 저장 버튼 특화 */
        .save-btn-container button { 
            background: linear-gradient(to right, #10b981, #059669) !important;
            margin-bottom: 20px !important;
        }

        .korean-amount { background-color: #eff6ff; padding: 10px 18px; border-radius: 10px; border: 1px solid #bfdbfe; color: #1e40af; font-weight: 800; margin-top: 8px; display: inline-block; font-size: 1rem; }
    </style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 3. 데이터 관리 함수
# -----------------------------------------------------------------------------
CATEGORIES = ["전기요금", "상하수도", "통신요금", "복합기임대", "공청기비데", "상품매입비", "수입금", "자체소수선", "부서업무비", "무인경비", "승강기점검", "신용카드수수료", "환경용역", "세탁용역", "야간경비"]
MONTHS = list(range(1, 13))
YEARS = [2024, 2025, 2026]

def ensure_data_integrity(data):
    if not isinstance(data, dict) or "records" not in data:
        data = {"records": data if isinstance(data, list) else []}
    existing = {(r['year'], r['month'], r['category']) for r in data['records']}
    new_recs = []
    updated = False
    for y in YEARS:
        for c in CATEGORIES:
            for m in MONTHS:
                if (y, m, c) not in existing:
                    new_recs.append({"year": y, "month": m, "category": c, "amount": 0, "drafted": False, "evidence": ""})
                    updated = True
    if new_recs: data['records'].extend(new_recs)
    return data, updated

def save_data_cloud(data):
    try:
        final_data, _ = ensure_data_integrity(data)
        doc_ref.set(final_data)
        return True
    except Exception as e:
        st.error(f"저장 실패: {e}")
        return False

def load_data():
    try:
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            validated, updated = ensure_data_integrity(data)
            if updated: save_data_cloud(validated)
            return validated
        else:
            default = {"records": []}
            default, _ = ensure_data_integrity(default)
            save_data_cloud(default)
            return default
    except Exception:
        return {"records": []}

def number_to_korean(n):
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

def convert_to_excel(data_records):
    output = io.BytesIO()
    df = pd.DataFrame(data_records)
    df = df.sort_values(by=['year', 'month', 'category'])
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='전체지출내역')
    return output.getvalue()

# -----------------------------------------------------------------------------
# 4. RPA 고도화 엔진
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
    log_container = st.empty()
    logs = []
    def add_log(msg, type="info"):
        color_class = "success-text" if type == "success" else "error-text" if type == "error" else "warning-text" if type == "warn" else ""
        logs.append(f'<span class="{color_class}">[{"OK" if type=="success" else "!!" if type=="error" else "??" if type=="warn" else ">>"}] {msg}</span>')
        log_container.markdown(f'<div class="log-box">{"<br>".join(logs)}</div>', unsafe_allow_html=True)

    try:
        USER_ID = st.secrets["groupware"]["id"]
        USER_PW = st.secrets["groupware"]["pw"]
    except:
        add_log("Secrets 설정 확인 필요", "error"); return

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
# 5. 메인 UI 및 상태 관리
# -----------------------------------------------------------------------------
# [중요] 금액 입력창 상태 초기화
if 'amt_box' not in st.session_state:
    st.session_state.amt_box = 0

# 증액 콜백 함수
def update_amt(increment):
    st.session_state.amt_box += increment

# 초기화 콜백 함수
def reset_amt():
    st.session_state.amt_box = 0

if 'data' not in st.session_state:
    st.session_state['data'] = load_data()

data = st.session_state['data']
df_all = pd.DataFrame(data.get("records", []))

with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3135/3135715.png", width=70)
    st.title("Cloud System")
    
    st.markdown('<div class="save-btn-container">', unsafe_allow_html=True)
    if st.button("💾 현재까지 변경사항을 저장", use_container_width=True):
        if save_data_cloud(st.session_state['data']):
            st.success("클라우드에 안전하게 백업되었습니다!")
            st.toast("동기화 완료")
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown("---")
    st.markdown("### 🛠️ 데이터 복구 및 이전")
    restore_file = st.file_uploader("로컬 JSON 파일 업로드", type="json")
    if restore_file is not None:
        try:
            restore_json = json.loads(restore_file.read())
            recs = restore_json.get("records", restore_json)
            if recs:
                st.info(f"데이터 확인됨 ({len(recs)}건)")
                if st.button("🚀 클라우드 DB로 즉시 자동 전송", use_container_width=True, type="primary"):
                    migrated, _ = ensure_data_integrity({"records": recs})
                    if save_data_cloud(migrated):
                        st.session_state['data'] = migrated
                        st.success("이전 및 자동 저장 성공!"); st.balloons()
                        time.sleep(1); st.rerun()
        except Exception as e: st.error(f"처리 오류: {e}")

    st.markdown("---")
    if not df_all.empty:
        excel_data = convert_to_excel(data["records"])
        st.download_button(label="📥 전체 엑셀 다운로드", data=excel_data, file_name=f"지출현황_{datetime.now().strftime('%Y%m%d')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
    if st.button("🔄 클라우드 데이터 새로고침", use_container_width=True): 
        st.session_state['data'] = load_data(); st.rerun()

st.title("🏢 2026 시설 지출 통합관리")

tab1, tab2, tab3, tab4 = st.tabs(["📊 지출 현황/입력", "📈 항목별 지출 분석", "🚨 미집행 현황", "✅ 그룹웨어 문서 확인"])

# --- TAB 1: 지출 현황 및 월별 입력 ---
with tab1:
    col_stat, col_input = st.columns([1, 2.5])
    with col_stat:
        st.markdown('<span class="section-label">2026 요약</span>', unsafe_allow_html=True)
        if not df_all.empty:
            df_26 = df_all[df_all["year"] == 2026]
            if not df_26.empty:
                val_26 = df_26["amount"].sum()
                st.markdown(f"""
                    <div class="metric-card" style="border-left-color: #2563eb;">
                        <div class="metric-label">🏢 2026년 총 지출 계획</div>
                        <div class="metric-value">{val_26:,.0f}<span class="metric-unit">원</span></div>
                    </div>
                """, unsafe_allow_html=True)
                cat_dist = df_26.groupby("category")["amount"].sum().reset_index()
                fig = alt.Chart(cat_dist).mark_arc(innerRadius=60).encode(
                    theta="amount:Q", color=alt.Color("category:N", scale=alt.Scale(scheme='tableau20')), tooltip=["category", "amount"]
                ).properties(height=280)
                st.altair_chart(fig, use_container_width=True)
    with col_input:
        st.markdown('<span class="section-label">지출액 신규 등록</span>', unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1: in_year = st.selectbox("연도", YEARS, index=2)
        with c2: in_cat = st.selectbox("항목", CATEGORIES)
        with c3: in_mon = st.selectbox("월", MONTHS, format_func=lambda x: f"{x}월")
        
        st.write("금액 빠른 증액")
        bc1, bc2, bc3, bcr = st.columns([1, 1, 1, 1.2])
        # [핵심 수정] on_click 콜백을 사용하여 상태를 즉각 갱신
        bc1.button("+ 10만", on_click=update_amt, args=(100000,))
        bc2.button("+ 100만", on_click=update_amt, args=(1000000,))
        bc3.button("+ 1000만", on_click=update_amt, args=(10000000,))
        bcr.button("🔄 초기화", on_click=reset_amt)
        
        # [핵심 수정] key="amt_box"를 통해 session_state와 직접 연결
        st.number_input("등록 금액", min_value=0, step=10000, key="amt_box")
        
        # 실시간 한글 금액 표시
        st.markdown(f'<div class="korean-amount">한글 금액: {number_to_korean(st.session_state.amt_box)}</div>', unsafe_allow_html=True)
        
        if st.button("💾 지출액 저장 및 등록", type="primary", use_container_width=True):
            if st.session_state.amt_box > 0:
                for r in st.session_state['data']["records"]:
                    if r["year"] == in_year and r["category"] == in_cat and r["month"] == in_mon:
                        r["amount"] += st.session_state.amt_box; break
                save_data_cloud(st.session_state['data'])
                st.session_state.amt_box = 0 # 등록 후 초기화
                st.toast("정상적으로 등록되었습니다.")
                time.sleep(0.5); st.rerun()

    st.markdown("---")
    st.markdown(f'<span class="section-label">📅 {in_year}년 지출 상세 편집 그리드</span>', unsafe_allow_html=True)
    if not df_all.empty:
        df_piv_data = df_all[df_all["year"] == in_year]
        if not df_piv_data.empty:
            df_piv = df_piv_data.pivot(index="category", columns="month", values="amount")
            df_piv.columns = [f"{m}월" for m in df_piv.columns]
            edited_grid = st.data_editor(df_piv, use_container_width=True, height=450)
            if not df_piv.equals(edited_grid):
                curr_data = load_data()
                for cat in CATEGORIES:
                    for m in MONTHS:
                        new_v = edited_grid.loc[cat, f"{m}월"]
                        for r in curr_data["records"]:
                            if r["year"] == in_year and r["category"] == cat and r["month"] == m:
                                r["amount"] = int(new_v); break
                save_data_cloud(curr_data); st.toast("클라우드 동기화 완료"); st.rerun()

# --- TAB 2: 프리미엄 연도별 비교 분석 ---
with tab2:
    st.markdown('<span class="section-label">연도별 지출 추이 정밀 분석</span>', unsafe_allow_html=True)
    if not df_all.empty:
        sel_cat = st.selectbox("분석 항목 선택", CATEGORIES, key="analysis_sel")
        df_comp = df_all[df_all["category"] == sel_cat]
        
        m1, m2, m3 = st.columns(3)
        v24 = df_comp[df_comp['year']==2024]['amount'].sum()
        v25 = df_comp[df_comp['year']==2025]['amount'].sum()
        v26 = df_comp[df_comp['year']==2026]['amount'].sum()
        
        m1.markdown(f'<div class="metric-card" style="border-left-color: #94a3b8;"><div class="metric-label">📈 2024 실적</div><div class="metric-value">{v24:,.0f}<span class="metric-unit">원</span></div></div>', unsafe_allow_html=True)
        m2.markdown(f'<div class="metric-card" style="border-left-color: #10b981;"><div class="metric-label">📈 2025 실적</div><div class="metric-value">{v25:,.0f}<span class="metric-unit">원</span></div></div>', unsafe_allow_html=True)
        m3.markdown(f'<div class="metric-card" style="border-left-color: #3b82f6;"><div class="metric-label">📅 2026 계획</div><div class="metric-value">{v26:,.0f}<span class="metric-unit">원</span></div></div>', unsafe_allow_html=True)
        
        st.write("")
        df_piv_comp = df_comp.pivot(index="month", columns="year", values="amount").fillna(0)
        df_piv_comp.columns = [f"{c}년" for c in df_piv_comp.columns]
        df_piv_comp = df_piv_comp.reset_index(); df_piv_comp["월"] = df_piv_comp["month"].apply(lambda x: f"{x}월")
        st.dataframe(df_piv_comp.style.format("{:,.0f}", subset=[c for c in df_piv_comp.columns if "년" in str(c)]), use_container_width=True)

# --- TAB 3: 미집행 현황 ---
with tab3:
    st.markdown('<span class="section-label">지출 누락 및 미집행 항목 점검</span>', unsafe_allow_html=True)
    if not df_all.empty:
        c1, c2, c3 = st.columns(3)
        for idx, y in enumerate([2024, 2025, 2026]):
            with [c1, c2, c3][idx]:
                st.subheader(f"📅 {y}년")
                df_y = df_all[df_all["year"] == y]
                for cat in CATEGORIES:
                    missing_months = df_y[(df_y["category"] == cat) & (df_y["amount"] <= 0)]["month"].tolist()
                    if missing_months:
                        st.error(f"**{cat}**: {', '.join(map(str, sorted(missing_months)))}월 미입력")
                    else:
                        st.success(f"**{cat}**: 입력 완료", icon="✅")

# --- TAB 4: 그룹웨어 확인 ---
with tab4:
    c_rpa_l, c_rpa_r = st.columns([1, 3])
    with c_rpa_l:
        st.markdown('<span class="section-label">RPA 제어</span>', unsafe_allow_html=True)
        r_cat = st.radio("확인 항목", CATEGORIES, key="r_cat_tab")
        r_year = st.radio("대상 연도", [2025, 2026], horizontal=True, index=0)
        st.divider()
        if st.button("📄 그룹웨어 문서함 확인", type="primary", use_container_width=True):
            run_groupware_rpa_fast(r_year, r_cat)
    with c_rpa_r:
        st.markdown(f'<span class="section-label">{r_year}년 {r_cat} 기안 현황</span>', unsafe_allow_html=True)
        df_rpa_chk = df_all[(df_all["year"] == r_year) & (df_all["category"] == r_cat)]
        if not df_rpa_chk.empty:
            df_p_rpa = df_rpa_chk.pivot(index="category", columns="month", values="drafted")
            df_p_rpa.columns = [f"{m}월" for m in df_p_rpa.columns]
            st.data_editor(df_p_rpa, use_container_width=True, disabled=True)
            with st.expander("📄 그룹웨어 증빙 문서"):
                for idx, row in df_rpa_chk.iterrows():
                    if row["drafted"]: st.caption(f"**{row['month']}월**: {row['evidence']}")