import streamlit as st
import pandas as pd
import altair as alt
import json
import os
import time
import io
import subprocess
import sys
from filelock import FileLock
from datetime import datetime

# --- [AUTO INSTALL] 필수 라이브러리 자동 설치 ---
def install_package(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

required_libs = ["openpyxl", "selenium", "webdriver-manager"]
for lib in required_libs:
    try:
        module_name = lib.replace("-", "_")
        if lib == "webdriver-manager": module_name = "webdriver_manager"
        __import__(module_name)
    except ImportError:
        install_package(lib)

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import Select 
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains

# -----------------------------------------------------------------------------
# 1. 앱 설정 및 스타일링 (Premium 3D Design)
# -----------------------------------------------------------------------------
st.set_page_config(page_title="2026 시설 지출 관리", layout="wide", page_icon="🏢")

st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;900&display=swap');
        html, body, [class*="css"] { font-family: 'Noto Sans KR', sans-serif !important; color: #1e293b; }
        .stApp { background-color: #f8fafc; }
        
        /* 3D 카드 스타일 */
        div[data-testid="stVerticalBlock"] > div[style*="flex-direction: column;"] > div[data-testid="stVerticalBlock"] {
            background-color: white; padding: 1.8rem; border-radius: 1rem; border: 1px solid #e2e8f0;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); transition: transform 0.2s ease;
        }
        div[data-testid="stVerticalBlock"] > div[style*="flex-direction: column;"] > div[data-testid="stVerticalBlock"]:hover {
            transform: translateY(-2px); box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
        }
        
        /* 메트릭 */
        [data-testid="stMetric"] { background: white; padding: 15px; border-radius: 12px; border-left: 5px solid #3b82f6; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }
        [data-testid="stMetricValue"] { font-size: 1.8rem !important; font-weight: 800; color: #0f172a; }
        
        /* 헤더 */
        h1 { background: linear-gradient(135deg, #1e40af, #3b82f6); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 900; letter-spacing: -1px; }
        
        /* 탭 */
        .stTabs [data-baseweb="tab"] { height: 50px; background-color: white; border-radius: 8px; font-weight: 700; color: #64748b; border: 1px solid #e2e8f0; }
        .stTabs [aria-selected="true"] { background-color: #2563eb !important; color: white !important; border: none; }
        
        /* 버튼 */
        .stButton button { background: linear-gradient(to right, #2563eb, #1d4ed8); color: white; font-weight: bold; border-radius: 8px; border: none; }
        .stButton button:hover { transform: translateY(-1px); box-shadow: 0 6px 10px -1px rgba(37, 99, 235, 0.3); }
        
        /* 분석 탭 강조 */
        .big-selector-container { background: linear-gradient(to bottom right, #eff6ff, #dbeafe); border: 2px solid #3b82f6; border-radius: 16px; padding: 25px; text-align: center; margin-bottom: 30px; }
        .big-selector-label { color: #1e40af; font-weight: 800; font-size: 1.3rem; margin-bottom: 12px; display: block; }
        
        /* 로그 박스 */
        .log-box { font-family: monospace; font-size: 0.85rem; background-color: #1e293b; color: #cbd5e1; padding: 10px; border-radius: 8px; max-height: 250px; overflow-y: auto; white-space: pre-wrap; }
    </style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 2. 데이터 설정
# -----------------------------------------------------------------------------
CATEGORIES = [
    "전기요금", "상하수도", "통신요금", "복합기임대", "공청기비데", 
    "상품매입비", "수입금", "자체소수선", "부서업무비", "무인경비", 
    "승강기점검", "신용카드수수료", "환경용역", "세탁용역", "야간경비"
]
MONTHS = list(range(1, 13))
DATA_FILE = "facility_data.json"
LOCK_FILE = "facility_data.json.lock"

SEARCH_KEYWORDS = {
    "전기요금": "전기요금", "상하수도": "상하수도", "통신요금": "통신요금",
    "복합기임대": "복합기", "공청기비데": "비데",
    "상품매입비": "상품매입", "수입금": "수입금", "자체소수선": "소수선",
    "부서업무비": "부서업무", "무인경비": "무인경비", "승강기점검": "승강기",
    "신용카드수수료": "신용카드", "환경용역": "환경용역", "세탁용역": "세탁",
    "야간경비": "야간경비"
}

INITIAL_HISTORY = {cat: {"2024": [0]*12, "2025": [0]*12} for cat in CATEGORIES}

# 기초 데이터 매핑 (업로드된 파일 기반)
INITIAL_HISTORY["전기요금"]["2024"] = [12561820, 12073930, 22545410, 8170188, 6459680, 5748710, 6928710, 10029560, 8288670, 6146590, 5670020, 8709400]
INITIAL_HISTORY["전기요금"]["2025"] = [11782300, 11836830, 9452350, 7074860, 6167830, 6167830, 8266720, 0, 8551300, 7147870, 7589840, 0]
INITIAL_HISTORY["상하수도"]["2024"] = [401210, 739720, 1377500, 844660, 1503310, 718050, 637780, 599160, 1287740, 725140, 847570, 451900]
INITIAL_HISTORY["상하수도"]["2025"] = [681420, 495360, 555710, 533980, 577430, 635370, 461560, 476040, 647440, 456730, 0, 0]
INITIAL_HISTORY["야간경비"]["2024"] = [4463000]*7 + [0]*5
INITIAL_HISTORY["환경용역"]["2024"] = [14857910, 14464440, 14437260, 14563810, 0, 14569520] + [0]*6
INITIAL_HISTORY["무인경비"]["2024"] = [341000]*6 + [0]*6
INITIAL_HISTORY["승강기점검"]["2024"] = [312400]*12
INITIAL_HISTORY["상품매입비"]["2024"] = [1936800, 1988280, 1956500, 0, 0, 0, 0, 0, 0, 0, 0, 0]
INITIAL_HISTORY["자체소수선"]["2024"] = [4013000, 4397000, 20796000, 3059000, 5927000, 3632000, 4971000, 2868000, 3119000, 956000, 0, 0]
INITIAL_HISTORY["부서업무비"]["2024"] = [0, 366860] + [0]*10
INITIAL_HISTORY["신용카드수수료"]["2024"] = [128670, 156910, 140970, 0, 198170, 187700, 0, 131360, 281910, 0, 0, 0]

# -----------------------------------------------------------------------------
# 3. 데이터 관리 함수
# -----------------------------------------------------------------------------
def load_data():
    if not os.path.exists(DATA_FILE):
        data_2026 = []
        data_2025_meta = [] 
        for cat in CATEGORIES:
            for m in MONTHS:
                data_2026.append({"year": 2026, "month": m, "category": cat, "amount": 0, "drafted": False, "evidence": ""})
                data_2025_meta.append({"year": 2025, "month": m, "category": cat, "drafted": False})
        return {"plan_2026": data_2026, "meta_2025": data_2025_meta, "history": INITIAL_HISTORY}
    
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        # 카테고리 마이그레이션 (도시가스 삭제 등 반영)
        if "plan_2026" in data:
            existing_cats_26 = {item["category"] for item in data["plan_2026"]}
            for cat in CATEGORIES:
                if cat not in existing_cats_26:
                    for m in MONTHS:
                        data["plan_2026"].append({"year": 2026, "month": m, "category": cat, "amount": 0, "drafted": False, "evidence": ""})
                        if "meta_2025" in data:
                             data["meta_2025"].append({"year": 2025, "month": m, "category": cat, "drafted": False})
            data["plan_2026"] = [i for i in data["plan_2026"] if i["category"] in CATEGORIES]
            data["meta_2025"] = [i for i in data["meta_2025"] if i["category"] in CATEGORIES]
            
        if "history" in data:
            for cat in CATEGORIES:
                if cat not in data["history"]:
                    data["history"][cat] = INITIAL_HISTORY.get(cat, {"2024": [0]*12, "2025": [0]*12})
            for k in list(data["history"].keys()):
                if k not in CATEGORIES: del data["history"][k]
        else: data["history"] = INITIAL_HISTORY
        
        return data

def save_data_safely(data):
    lock = FileLock(LOCK_FILE)
    with lock:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

def convert_to_excel(data):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_plan = pd.DataFrame(data["plan_2026"])
        if "drafted" in df_plan.columns:
            df_plan["기안여부"] = df_plan["drafted"].apply(lambda x: "완료" if x else "미완료")
            df_plan.drop(columns=["drafted"], inplace=True)
        if "evidence" in df_plan.columns: df_plan.rename(columns={"evidence": "문서제목"}, inplace=True)
        df_plan.to_excel(writer, index=False, sheet_name='2026년 계획')
        
        rows = []
        for cat, years in data["history"].items():
            for year, amounts in years.items():
                row = {"항목": cat, "연도": year}
                for i, amt in enumerate(amounts): row[f"{i+1}월"] = amt
                rows.append(row)
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name='과거실적')
    return output.getvalue()

# -----------------------------------------------------------------------------
# 4. RPA 크롤링 로직 (V15.1 - 연도 선택 및 입력 강화)
# -----------------------------------------------------------------------------
def find_element_with_frame(driver, by, value, timeout=8):
    """모든 프레임을 뒤져서 요소를 찾고 해당 프레임으로 전환"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        driver.switch_to.default_content()
        try:
            el = driver.find_element(by, value)
            if el.is_displayed(): return el
        except: pass
        
        frames = driver.find_elements(By.TAG_NAME, "frame") + driver.find_elements(By.TAG_NAME, "iframe")
        for frame in frames:
            try:
                driver.switch_to.frame(frame)
                el = driver.find_element(by, value)
                if el: return el # 찾으면 해당 프레임 유지
            except: driver.switch_to.parent_frame()
        time.sleep(0.5)
    return None

def clear_obstructions(driver):
    try:
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        time.sleep(0.5)
        driver.switch_to.default_content()
        driver.find_element(By.TAG_NAME, "body").click()
    except: pass

def run_groupware_crawling(target_year, target_category):
    status_box = st.status(f"🚀 '{target_category}' ({target_year}년) 확인 중... (화면 확인)", expanded=True)
    log_container = st.empty()
    logs = []

    def add_log(msg, success=True):
        icon = "✅" if success else "ℹ️"
        logs.append(f"{icon} {msg}")
        log_container.markdown(f'<div class="log-box">{"<br>".join(logs)}</div>', unsafe_allow_html=True)

    options = webdriver.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,1024")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    try:
        driver.get("http://192.168.1.245:8888/index.jsp")
        add_log("접속 시도...")
        
        # 1. 로그인
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "Name")))
        driver.find_element(By.NAME, "Name").send_keys("김재균")
        driver.find_element(By.NAME, "Password").send_keys("1q2w3e4r!1" + Keys.RETURN)
        time.sleep(5)
        add_log("로그인 성공")

        # 2. 문서함 클릭
        menu = find_element_with_frame(driver, By.ID, "menu_4")
        if menu:
            driver.execute_script("arguments[0].click();", menu)
            add_log("문서함 진입")
            time.sleep(3)
        else:
            add_log("문서함(menu_4) 못찾음", False)
        
        # 3. 연도 설정 (핵심: 재귀 탐색으로 찾기)
        year_sel = find_element_with_frame(driver, By.ID, "szDocDeptYear")
        if year_sel:
            try:
                # Select로 시도
                Select(year_sel).select_by_value(str(target_year))
                # JS로 강제 트리거 (중요)
                driver.execute_script("if(typeof changeDocdeptTree == 'function'){ changeDocdeptTree(''); }")
                driver.execute_script("arguments[0].dispatchEvent(new Event('change'));", year_sel)
                
                add_log(f"연도 {target_year}년 설정 완료")
                time.sleep(3) # 리로딩 대기
            except Exception as e:
                add_log(f"연도 변경 중 오류: {str(e)}", False)
        else:
            add_log("연도 선택창(szDocDeptYear) 못찾음", False)

        # 4. 기록물등록대장 클릭
        reg = find_element_with_frame(driver, By.CSS_SELECTOR, "[title='기록물등록대장']")
        if not reg: reg = find_element_with_frame(driver, By.PARTIAL_LINK_TEXT, "기록물")
        if reg:
            driver.execute_script("arguments[0].click();", reg)
            add_log("기록물 등록대장 진입")
            time.sleep(3)
        else:
            add_log("등록대장 못찾음", False)

        # 5. 검색 및 실행
        clear_obstructions(driver)
        search_input = find_element_with_frame(driver, By.ID, "sj")
        search_word = SEARCH_KEYWORDS.get(target_category, target_category)
        
        updated_count = 0
        
        if search_input:
            add_log(f"'{search_word}' 입력 및 검색 시도...")
            
            # JS 강제 입력
            driver.execute_script(f"arguments[0].value = '{search_word}';", search_input)
            search_input.send_keys(Keys.RETURN)
            
            # fncSearch 호출
            try: driver.execute_script("if(typeof fncSearch == 'function') fncSearch();")
            except: pass
            
            time.sleep(4)
            
            # 목록 수집 (Title 속성)
            links = driver.find_elements(By.CSS_SELECTOR, "a[title]")
            titles = [l.get_attribute("title") for l in links if l.get_attribute("title")]
            add_log(f"문서 제목 {len(titles)}건 수집됨")
            if titles:
                for t in titles[:3]: add_log(f"예: {t}")
            
            # 데이터 매칭
            full_data = load_data()
            key = "plan_2026" if target_year == 2026 else "meta_2025"
            
            for month in range(1, 13):
                patterns = [f"{target_year}년 {month}월", f"{target_year}.{month:02d}", f"{month}월분", f"{month}월"]
                for t in titles:
                    if search_word in t and any(p in t for p in patterns):
                        for item in full_data[key]:
                            if item["category"] == target_category and item["month"] == month:
                                if not item["drafted"]:
                                    item["drafted"] = True
                                    item["evidence"] = t
                                    updated_count += 1
                        break
            
            if updated_count > 0:
                save_data_safely(full_data)
                st.session_state['data'] = full_data 
                add_log(f"{updated_count}건 체크 완료!", True)
            else:
                add_log("일치하는 문서를 찾지 못했습니다.", False)
        else:
            add_log("검색창(sj)을 찾지 못했습니다.", False)

    except Exception as e:
        add_log(f"오류 발생: {str(e)}", False)
    finally:
        time.sleep(3)
        driver.quit()
        st.rerun() # 앱 강제 새로고침

# -----------------------------------------------------------------------------
# 5. 메인 UI
# -----------------------------------------------------------------------------
if 'data' not in st.session_state:
    st.session_state['data'] = load_data()

data = st.session_state['data']

with st.sidebar:
    st.header("📂 데이터 관리")
    excel_file = convert_to_excel(data)
    st.download_button("📥 전체 데이터 엑셀 다운로드", excel_file, '시설지출관리_데이터.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', use_container_width=True)
    st.info("좌측 '기안 체크리스트' 탭에서 RPA 자동확인을 실행할 수 있습니다.")

c1, c2 = st.columns([3, 1])
with c1:
    st.title("🏢 2026 시설 지출 관리")
    st.caption("V15.1 Final (Stability Fix)")

tab1, tab2, tab3, tab4 = st.tabs(["📊 2026 대시보드", "📈 연도별 분석(증감)", "🚨 미집행 확인", "✅ 기안 체크리스트"])

# --- TAB 1 ---
with tab1:
    with st.container():
        c_in, c_chart = st.columns([1.2, 2])
        with c_in:
            st.markdown("### 📝 지출 등록")
            with st.form("entry"):
                s_cat = st.selectbox("항목", CATEGORIES)
                s_month = st.selectbox("월", MONTHS, format_func=lambda x: f"{x}월")
                s_amount = st.number_input("금액 (원)", min_value=0, step=1000, format="%d")
                s_drafted = st.checkbox("기안 완료 (수동)")
                if st.form_submit_button("💾 저장", use_container_width=True):
                    lock = FileLock(LOCK_FILE)
                    with lock:
                        curr = load_data()
                        for i in curr["plan_2026"]:
                            if i["category"] == s_cat and i["month"] == s_month:
                                i["amount"] = s_amount
                                i["drafted"] = s_drafted
                                break
                        with open(DATA_FILE, "w", encoding="utf-8") as f: json.dump(curr, f, indent=4)
                        st.session_state['data'] = curr
                    st.toast("저장되었습니다.", icon="✅")
                    time.sleep(0.5); st.rerun()

    st.divider()
    df_2026 = pd.DataFrame(data["plan_2026"])
    total = df_2026["amount"].sum()
    st.metric("2026년 총 지출 계획", f"{total:,} 원")
    chart = alt.Chart(df_2026).mark_bar(cornerRadius=5).encode(
        x=alt.X('month:O', title='월', axis=alt.Axis(labelAngle=0, labelFontSize=14)),
        y=alt.Y('amount:Q', title='금액(원)', axis=alt.Axis(format=',.0f', labelFontSize=12)),
        color=alt.Color('category', title='항목', scale=alt.Scale(scheme='tableau20')),
        tooltip=['category', 'month', alt.Tooltip('amount', format=',')]
    ).properties(height=350)
    st.altair_chart(chart, use_container_width=True)

# --- TAB 2 ---
with tab2:
    st.markdown('<div class="big-selector-container"><span class="big-selector-label">🔍 분석할 항목을 선택하세요</span>', unsafe_allow_html=True)
    comp_cat = st.selectbox("분석 항목", CATEGORIES, label_visibility="collapsed")
    st.write("")
    
    if comp_cat in data["history"]:
        h24 = data["history"][comp_cat]["2024"]
        h25 = data["history"][comp_cat]["2025"]
        p26 = [next((x["amount"] for x in data["plan_2026"] if x["category"]==comp_cat and x["month"]==m),0) for m in MONTHS]
        
        c1, c2, c3 = st.columns(3)
        c1.metric("2024 실적", f"{sum(h24):,} 원")
        c2.metric("2025 실적", f"{sum(h25):,} 원", f"{sum(h25)-sum(h24):+,}")
        c3.metric("2026 계획", f"{sum(p26):,} 원", f"{sum(p26)-sum(h25):+,}")
        
        rows = []
        for i, m in enumerate(MONTHS):
            rows.append({
                "월": f"{m}월", "2024년": h24[i], "2025년": h25[i],
                "증감(24-25)": h25[i] - h24[i], "2026년": p26[i], "증감(25-26)": p26[i] - h25[i]
            })
        
        df_t = pd.DataFrame(rows)
        # [Fix: ValueError 해결] 숫자 컬럼만 선택하여 포맷팅
        num_cols = ["2024년", "2025년", "증감(24-25)", "2026년", "증감(25-26)"]
        for col in num_cols: df_t[col] = pd.to_numeric(df_t[col], errors='coerce').fillna(0)

        st.dataframe(
            df_t.style.format("{:,.0f}", subset=num_cols).applymap(
                lambda v: f'color: {"#ef4444" if v>0 else "#3b82f6" if v<0 else "#94a3b8"}; font-weight: bold', 
                subset=["증감(24-25)", "증감(25-26)"]
            ), use_container_width=True, height=480
        )
        
        edited_h = st.data_editor(df_t[["월", "2024년", "2025년"]], use_container_width=True)
        v24 = edited_h["2024년"].tolist(); v25 = edited_h["2025년"].tolist()
        if v24 != h24 or v25 != h25:
            data["history"][comp_cat]["2024"] = v24
            data["history"][comp_cat]["2025"] = v25
            save_data_safely(data); st.session_state['data'] = load_data(); st.rerun()

# --- TAB 3 ---
with tab3:
    c1, c2 = st.columns(2)
    def get_z(arr): return [f"{i+1}월" for i,v in enumerate(arr) if v==0]
    with c1:
        st.markdown("#### 2025년 실적 누락")
        for c in CATEGORIES:
            if c in data["history"]:
                z = get_z(data["history"][c]["2025"])
                if z: st.error(f"**{c}**: {', '.join(z)}")
    with c2:
        st.markdown("#### 2024년 실적 누락")
        for c in CATEGORIES:
            if c in data["history"]:
                z = get_z(data["history"][c]["2024"])
                if z: st.info(f"**{c}**: {', '.join(z)}")

# --- TAB 4 ---
with tab4:
    c1, c2 = st.columns([1, 3])
    with c1:
        st.markdown("#### 🔍 RPA 설정")
        sel_cat = st.radio("항목 선택", CATEGORIES)
        sel_year = st.radio("연도 선택", [2025, 2026], horizontal=True)
        
        if st.button(f"🔄 '{sel_cat}' 자동 확인 시작", type="primary"):
            run_groupware_crawling(sel_year, sel_cat)
        
        st.write("")
        if st.button("🛑 중단 및 초기화", type="secondary"): st.rerun()

    with c2:
        st.markdown(f"#### ✅ {sel_year}년 {sel_cat} 기안 현황")
        year_key = "plan_2026" if sel_year == 2026 else "meta_2025"
        
        filtered = [i for i in data[year_key] if i["category"] == sel_cat]
        if filtered:
            df = pd.DataFrame(filtered)
            df_p = df.pivot(index="category", columns="month", values="drafted")
            # [Fix] 컬럼명 문자열 변환 (KeyError 방지)
            df_p.columns = [f"{c}월" for c in df_p.columns]
            
            col_cfg = {f"{m}월": st.column_config.CheckboxColumn(f"{m}월", width="small") for m in MONTHS}
            edited_p = st.data_editor(df_p, column_config=col_cfg, use_container_width=True)
            
            if not df_p.equals(edited_p):
                for m in MONTHS:
                    key = f"{m}월"
                    if key in edited_p.columns:
                        val = bool(edited_p.loc[sel_cat, key])
                        for item in data[year_key]:
                            if item["category"] == sel_cat and item["month"] == m:
                                item["drafted"] = val; break
                save_data_safely(data); st.session_state['data'] = load_data(); st.rerun()
                
            with st.expander("📄 확인된 문서 제목 보기"):
                for i in filtered:
                    if i.get("drafted") and i.get("evidence"):
                        st.caption(f"**{i['month']}월**: {i['evidence']}")
        else: st.info("데이터가 없습니다.")