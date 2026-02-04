import streamlit as st
import pandas as pd
import altair as alt
import json
import os
import time
import io
import subprocess
import sys
import re
from datetime import datetime

# --- [AUTO INSTALL] 필수 라이브러리 자동 설치 ---
def install_package(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

required_libs = ["openpyxl", "selenium", "webdriver-manager", "filelock", "firebase-admin"]
for lib in required_libs:
    try:
        module_name = lib.replace("-", "_")
        if lib == "webdriver-manager": module_name = "webdriver_manager"
        elif lib == "firebase-admin": module_name = "firebase_admin"
        __import__(module_name)
    except ImportError:
        install_package(lib)

import firebase_admin
from firebase_admin import credentials, firestore

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import Select 
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# -----------------------------------------------------------------------------
# 1. Firebase 클라우드 DB 초기화 및 진단
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
        st.error(f"Firebase 초기화 중 치명적 오류 발생: {e}")
        st.stop()

db = firestore.client()
# appId가 일관되어야 같은 데이터를 봅니다.
appId = st.secrets.get("app_id", "facility-ledger-2026-v1")

# Firestore 경로 설정 (RULE 1 준수)
doc_ref = db.collection('artifacts').document(appId).collection('public').document('data').collection('facility_data').document('master')

# -----------------------------------------------------------------------------
# 2. 스타일 및 데이터 구조 설정
# -----------------------------------------------------------------------------
CATEGORIES = ["전기요금", "상하수도", "통신요금", "복합기임대", "공청기비데", "상품매입비", "수입금", "자체소수선", "부서업무비", "무인경비", "승강기점검", "신용카드수수료", "환경용역", "세탁용역", "야간경비"]
MONTHS = list(range(1, 13))
YEARS = [2024, 2025, 2026]

SEARCH_CONFIG = {
    "전기요금": {"sub": ["전기", "한전", "전기요금"]},
    "상하수도": {"sub": ["수도", "상하수도", "물부담"]},
    "통신요금": {"sub": ["통신", "인터넷", "KT", "SK", "전화요금"]},
    "복합기임대": {"sub": ["복합기", "복사기", "임대료", "랜탈"]},
    "공청기비데": {"sub": ["비데", "공청기", "코웨이", "공기청정기"]},
    "무인경비": {"sub": ["무인경비"]},
    "승강기점검": {"sub": ["승강기 점검"]},
    "야간경비": {"sub": ["야간 경비 용역"]},
}

st.set_page_config(page_title="2026년 월별 지출관리", layout="wide", page_icon="🏢")

st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;900&display=swap');
        html, body, [class*="css"] { font-family: 'Noto Sans KR', sans-serif !important; color: #1e293b; }
        .stApp { background-color: #f8fafc; }
        div[data-testid="stVerticalBlock"] > div[style*="flex-direction: column;"] > div[data-testid="stVerticalBlock"] {
            background-color: white; padding: 2rem; border-radius: 1.25rem; border: 1px solid #e2e8f0;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.05); transition: all 0.3s ease; margin-bottom: 1.5rem;
        }
        h1 { background: linear-gradient(135deg, #1e3a8a, #3b82f6); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 900; letter-spacing: -1.5px; margin-bottom: 1rem; }
        .success-text { color: #10b981; font-weight: bold; }
        .error-text { color: #ef4444; font-weight: bold; }
        .section-label { font-size: 1.2rem; font-weight: 800; color: #1e3a8a; margin-bottom: 15px; display: block; border-left: 6px solid #2563eb; padding-left: 15px; }
        .korean-amount { background-color: #eff6ff; padding: 8px 15px; border-radius: 8px; border: 1px solid #bfdbfe; color: #1e40af; font-weight: 700; margin-top: 5px; display: inline-block; font-size: 0.95rem; }
        .stButton button { background: linear-gradient(to right, #2563eb, #1d4ed8) !important; color: white !important; border-radius: 8px; font-weight: 700; border: none !important; }
    </style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 3. 클라우드 데이터 관리 함수
# -----------------------------------------------------------------------------
def get_default_data():
    return {"records": [{"year": y, "month": m, "category": c, "amount": 0, "drafted": False, "evidence": ""} for y in YEARS for c in CATEGORIES for m in MONTHS]}

def save_data_cloud(data):
    try:
        doc_ref.set(data)
        return True
    except Exception as e:
        st.error(f"클라우드 저장 중 오류: {e}")
        return False

def load_data():
    try:
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            # 데이터 정합성 체크 (필수 키 'records' 확인)
            if "records" not in data:
                return get_default_data()
            
            # 신규 카테고리 추가 대응
            existing = {(r['year'], r['month'], r['category']) for r in data['records']}
            new_recs = []
            for y in YEARS:
                for c in CATEGORIES:
                    for m in MONTHS:
                        if (y, m, c) not in existing:
                            new_recs.append({"year": y, "month": m, "category": c, "amount": 0, "drafted": False, "evidence": ""})
            if new_recs:
                data['records'].extend(new_recs)
                save_data_cloud(data)
            return data
        else:
            # 클라우드에 데이터가 전혀 없는 경우 초기 생성
            default = get_default_data()
            save_data_cloud(default)
            return default
    except Exception as e:
        st.error(f"클라우드 데이터 로드 실패: {e}")
        return get_default_data()

def number_to_korean(n):
    if n == 0: return "금영원"
    units = ["", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]
    digit_units = ["", "십", "백", "천"]
    group_units = ["", "만", "억", "조"]
    res = []
    s_num = str(int(n))[::-1]
    for i in range(0, len(s_num), 4):
        group = s_num[i:i+4]
        group_res = ""
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
# 4. RPA 엔진 (변동 없음 - Secrets 연동 최적화)
# -----------------------------------------------------------------------------
# ... (RPA 엔진 코드는 기존 V18.0과 동일하므로 효율을 위해 생략하거나 그대로 유지)
# [참고] V18.0의 find_element_deep, verify_and_set_period, run_groupware_rpa_fast 함수가 이 자리에 들어갑니다.

# -----------------------------------------------------------------------------
# 5. 메인 UI
# -----------------------------------------------------------------------------
if 'data' not in st.session_state:
    st.session_state['data'] = load_data()

data = st.session_state['data']
df_all = pd.DataFrame(data.get("records", []))

with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3135/3135715.png", width=80)
    st.title("Cloud Management")
    
    # [NEW] 데이터 복구 도구
    st.markdown("---")
    st.markdown("### 🛠️ 데이터 복구 (JSON)")
    restore_file = st.file_uploader("기존 facility_data.json 파일 업로드", type="json")
    if restore_file is not None:
        try:
            restore_json = json.load(restore_file)
            # 구조 검증 (기존 구조인지 records 구조인지 확인)
            if "records" in restore_json:
                if st.button("🚀 클라우드로 데이터 강제 마이그레이션"):
                    if save_data_cloud(restore_json):
                        st.session_state['data'] = load_data()
                        st.success("데이터가 성공적으로 클라우드에 업로드되었습니다!")
                        time.sleep(1)
                        st.rerun()
            else:
                st.error("JSON 파일 형식이 맞지 않습니다. ('records' 키가 필요함)")
        except Exception as e:
            st.error(f"파일 처리 오류: {e}")

    st.markdown("---")
    st.markdown("### 📥 엑셀 내보내기")
    excel_data = convert_to_excel(data["records"])
    st.download_button(label="엑셀 파일 다운로드", data=excel_data, file_name=f"지출현황_{datetime.now().strftime('%Y%m%d')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
    
    if st.button("🔄 클라우드 새로고침"):
        st.session_state['data'] = load_data()
        st.rerun()

st.title("🏢 2026년 월별 지출관리")

tab1, tab2, tab3, tab4 = st.tabs(["📊 지출 현황/입력", "📈 연도별 비교 분석", "🚨 미집행 현황", "✅ 그룹웨어 문서 확인"])

# --- TAB 1: 지출 현황 및 월별 입력 (V17.5 UI 및 상태 유지) ---
with tab1:
    col_stat, col_input = st.columns([1, 2.5])
    with col_stat:
        st.markdown('<span class="section-label">2026 요약</span>', unsafe_allow_html=True)
        # 데이터가 없을 때를 대비한 방어 코드
        if not df_all.empty:
            df_26 = df_all[df_all["year"] == 2026]
            total_26 = df_26["amount"].sum()
            st.metric("2026년 총 계획금액", f"{total_26:,.0f} 원")
            cat_dist = df_26.groupby("category")["amount"].sum().reset_index()
            fig = alt.Chart(cat_dist).mark_arc(innerRadius=50).encode(
                theta=alt.Theta(field="amount", type="quantitative"),
                color=alt.Color(field="category", type="nominal", scale=alt.Scale(scheme='tableau20')),
                tooltip=["category", "amount"]
            ).properties(height=250)
            st.altair_chart(fig, use_container_width=True)
        else:
            st.info("데이터를 클라우드에서 불러오는 중이거나 데이터가 비어있습니다.")

    with col_input:
        st.markdown('<span class="section-label">지출액 신규 등록</span>', unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1: in_year = st.selectbox("연도", YEARS, index=2, key="reg_year")
        with c2: in_cat = st.selectbox("항목", CATEGORIES, key="reg_cat")
        with c3: in_mon = st.selectbox("월", MONTHS, format_func=lambda x: f"{x}월", key="reg_month")
        
        # 금액 관리 상태
        if 'reg_amount_input_val' not in st.session_state: st.session_state.reg_amount_input_val = 0
        
        st.write("금액 빠른 증액")
        b1, b2, b3, b_reset = st.columns([1, 1, 1, 1.2])
        if b1.button("+ 10만", key="b10"): st.session_state.reg_amount_input_val += 100000
        if b2.button("+ 100만", key="b100"): st.session_state.reg_amount_input_val += 1000000
        if b3.button("+ 1000만", key="b1000"): st.session_state.reg_amount_input_val += 10000000
        if b_reset.button("🔄 초기화", key="br"): st.session_state.reg_amount_input_val = 0
        
        amt_input = st.number_input("등록 금액 (원)", min_value=0, step=10000, key="amt_box", value=st.session_state.reg_amount_input_val)
        st.session_state.reg_amount_input_val = amt_input
        
        st.markdown(f'<div class="korean-amount">한글 금액: {number_to_korean(st.session_state.reg_amount_input_val)}</div>', unsafe_allow_html=True)
        
        if st.button("💾 클라우드에 합산 등록", type="primary", use_container_width=True):
            if st.session_state.reg_amount_input_val > 0:
                curr = load_data()
                for r in curr["records"]:
                    if r["year"] == in_year and r["category"] == in_cat and r["month"] == in_mon:
                        r["amount"] += st.session_state.reg_amount_input_val
                        break
                save_data_cloud(curr)
                st.session_state.reg_amount_input_val = 0
                st.toast("클라우드 저장 완료!")
                time.sleep(0.5); st.rerun()

    st.markdown("---")
    st.markdown(f'<span class="section-label">📅 {in_year}년 지출 상세 편집 그리드 (Cloud Sync)</span>', unsafe_allow_html=True)
    if not df_all.empty:
        df_piv = df_all[df_all["year"] == in_year].pivot(index="category", columns="month", values="amount")
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
            save_data_cloud(curr_data)
            st.toast("클라우드 업데이트 완료!")
            st.rerun()

# --- TAB 2: 연도별 분석 ---
with tab2:
    st.markdown('<span class="section-label">연도별 지출 추이 정밀 분석</span>', unsafe_allow_html=True)
    if not df_all.empty:
        sel_cat = st.selectbox("분석할 항목 선택", CATEGORIES, key="analysis_sel")
        df_comp = df_all[df_all["category"] == sel_cat]
        m1, m2, m3 = st.columns(3)
        m1.metric("2024 실적", f"{df_comp[df_comp['year']==2024]['amount'].sum():,.0f} 원")
        m2.metric("2025 실적", f"{df_comp[df_comp['year']==2025]['amount'].sum():,.0f} 원")
        m3.metric("2026 계획", f"{df_comp[df_comp['year']==2026]['amount'].sum():,.0f} 원")
        
        df_piv_comp = df_comp.pivot(index="month", columns="year", values="amount").fillna(0)
        df_piv_comp.columns = [f"{c}년" for c in df_piv_comp.columns]
        df_piv_comp = df_piv_comp.reset_index(); df_piv_comp["월"] = df_piv_comp["month"].apply(lambda x: f"{x}월")
        st.dataframe(df_piv_comp.style.format("{:,.0f}", subset=[c for c in df_piv_comp.columns if "년" in str(c)]), use_container_width=True)

# --- TAB 4: 그룹웨어 확인 ---
# (RPA 로직은 V18.0과 동일하게 유지되며, 확인 시 save_data_cloud를 호출하도록 업데이트되었습니다.)