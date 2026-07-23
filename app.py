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
import textwrap
from datetime import datetime
from dataclasses import dataclass
import warnings

# 시스템 경고(Warning) 도스창 도배 차단
warnings.filterwarnings('ignore')
pd.options.mode.chained_assignment = None

# Firebase Admin SDK 관련 임포트
# - 회사 PC/로컬 실행에서는 Firebase 설정이 없는 경우가 많으므로 앱이 죽지 않도록 로컬 모드로 자동 전환
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    FIREBASE_AVAILABLE = True
except ImportError:
    firebase_admin = None
    credentials = None
    firestore = None
    FIREBASE_AVAILABLE = False

# -----------------------------------------------------------------------------
# 1. 페이지 설정
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="2026 예산집행관리 | 정약용 펀그라운드",
    layout="wide",
    page_icon="🏢",
    initial_sidebar_state="expanded",
)

# -----------------------------------------------------------------------------
# 2. 글로벌 상수, 설정 및 과거 실적 데이터베이스 내장
# -----------------------------------------------------------------------------
CATEGORIES = ["전기요금", "상하수도", "통신요금", "복합기임대", "공청기비데", "상품매입비", "수입금", "자체소수선", "부서업무비", "무인경비", "승강기점검", "신용카드수수료", "환경용역", "세탁용역", "야간경비", "수탁자산취득비", "일반재료비", "미디어실제습기"]
MONTHS = list(range(1, 13))
YEARS = [2024, 2025, 2026]

# 저장 키는 기존 JSON·엑셀 연계를 위해 유지하고, 화면에만 정식 명칭을 표시합니다.
# 아래 항목은 지방공기업 예산과목의 "세목"이 아니라 앱의 분석용 "집행관리 항목"입니다.
CATEGORY_LABELS = {
    "전기요금": "전기요금",
    "상하수도": "상하수도요금",
    "통신요금": "통신요금",
    "복합기임대": "복합기 임차",
    "공청기비데": "공기청정기·비데 관리용역",
    "상품매입비": "상품매입비",
    "수입금": "수입금",
    "자체소수선": "자체 소수선",
    "부서업무비": "부서업무비",
    "무인경비": "무인경비용역",
    "승강기점검": "승강기 유지관리",
    "신용카드수수료": "신용카드 수수료",
    "환경용역": "청소·환경관리용역",
    "세탁용역": "세탁용역",
    "야간경비": "야간경비용역",
    "수탁자산취득비": "수탁자산취득비",
    "일반재료비": "일반재료비",
    "미디어실제습기": "미디어실 제습기",
}


def display_category(value):
    """기존 저장 키를 변경하지 않고 사용자 화면용 명칭을 반환합니다."""
    return CATEGORY_LABELS.get(str(value), str(value))


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


# 신속집행 목표율 기준
# - 행안부: 1분기 28.0%, 상반기 61.5%
# - 남양주시: 1분기 40.0%, 상반기 70.0%
QUICK_EXEC_GOAL_RATES = {
    "행안부": {"q1": 0.28, "h1": 0.615, "color": "#2563eb", "bg": "#eff6ff"},
    "남양주시": {"q1": 0.40, "h1": 0.70, "color": "#be123c", "bg": "#fff1f2"},
}

def render_quick_goal_card(label, period_label, actual_amount, target_amount, goal_rate, color, bg):
    """항목별 분석 화면 신속집행 목표 비교 카드 HTML 생성."""
    target_goal_amount = float(target_amount) * float(goal_rate) if target_amount else 0.0
    actual_rate = (float(actual_amount) / float(target_amount) * 100.0) if target_amount else 0.0
    goal_percent = float(goal_rate) * 100.0
    progress_vs_goal = (float(actual_amount) / target_goal_amount * 100.0) if target_goal_amount > 0 else 0.0
    status = "달성" if actual_rate >= goal_percent else "미달"
    status_color = "#059669" if status == "달성" else "#dc2626"
    status_bg = "#dcfce7" if status == "달성" else "#fee2e2"
    width = min(max(progress_vs_goal, 0), 100)
    return textwrap.dedent(f"""
    <div style="border:1px solid #dbeafe; border-radius:16px; padding:14px; background:#ffffff; box-shadow:0 2px 8px rgba(15,23,42,0.04);">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
            <div style="font-weight:900; color:#0f172a;">{label} · {period_label}</div>
            <span style="background:{status_bg}; color:{status_color}; padding:4px 10px; border-radius:999px; font-size:0.8rem; font-weight:900;">{status}</span>
        </div>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:10px;">
            <div style="background:{bg}; border-radius:10px; padding:8px;">
                <div style="font-size:0.78rem; color:#475569; font-weight:700;">목표율</div>
                <div style="font-size:1.25rem; color:{color}; font-weight:900;">{goal_percent:.1f}%</div>
            </div>
            <div style="background:#f8fafc; border-radius:10px; padding:8px;">
                <div style="font-size:0.78rem; color:#475569; font-weight:700;">현재 실적률</div>
                <div style="font-size:1.25rem; color:#0f172a; font-weight:900;">{actual_rate:.1f}%</div>
            </div>
        </div>
        <div style="background:#e5e7eb; height:12px; border-radius:999px; overflow:hidden;">
            <div style="background:linear-gradient(90deg, #60a5fa, {color}); width:{width:.1f}%; height:100%;"></div>
        </div>
        <div style="display:flex; justify-content:space-between; margin-top:7px; font-size:0.78rem; font-weight:800; color:#475569;">
            <span>실적 {int(actual_amount):,}원</span>
            <span>목표 {int(target_goal_amount):,}원</span>
        </div>
    </div>
    """).strip()

def render_quick_goal_comparison_html(category, target_amount, q1_actual, h1_actual):
    rows = []
    for label, cfg in QUICK_EXEC_GOAL_RATES.items():
        q1 = render_quick_goal_card(label, "1분기", q1_actual, target_amount, cfg["q1"], cfg["color"], cfg["bg"])
        h1 = render_quick_goal_card(label, "상반기", h1_actual, target_amount, cfg["h1"], cfg["color"], cfg["bg"])
        rows.append(textwrap.dedent(f"""
        <div style="margin-top:14px;">
            <div style="font-size:1.05rem; font-weight:900; color:#1e3a8a; margin-bottom:8px;">{label} 목표율</div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:14px;">{q1}{h1}</div>
        </div>
        """).strip())
    return textwrap.dedent(f"""
    <div class="quick-exec-card-scarlet" style="padding:18px;">
        <div style="display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:6px; flex-wrap:wrap;">
            <span class="quick-exec-badge-scarlet">🚀 2026 신속집행 특별관리 대상</span>
            <span style="font-size:1.05rem; color:#be123c; font-weight:900;">{category} 대상액: {int(target_amount):,}원</span>
        </div>
        <div style="font-size:0.88rem; color:#64748b; font-weight:700; margin-bottom:4px;">행안부 목표와 남양주시 목표를 동시에 비교합니다.</div>
        {''.join(rows)}
    </div>
    """).strip()


def render_overview_quick_exec_card(category, q1_actual, h1_actual, conf):
    """실적 현황 탭용 상반기 신속집행 요약 카드."""
    target = float(conf.get("target", 0) or 0)
    h1_rate = (float(h1_actual) / target * 100.0) if target > 0 else 0.0
    q1_rate = (float(q1_actual) / target * 100.0) if target > 0 else 0.0
    h1_width = min(max(h1_rate, 0), 100)
    q1_width = min(max(q1_rate, 0), 100)
    mo_h1_rate = QUICK_EXEC_GOAL_RATES["행안부"]["h1"] * 100
    city_h1_rate = QUICK_EXEC_GOAL_RATES["남양주시"]["h1"] * 100
    mo_q1_rate = QUICK_EXEC_GOAL_RATES["행안부"]["q1"] * 100
    city_q1_rate = QUICK_EXEC_GOAL_RATES["남양주시"]["q1"] * 100
    status = "달성" if h1_rate >= city_h1_rate else ("행안부 달성" if h1_rate >= mo_h1_rate else "미달")
    status_color = "#059669" if h1_rate >= city_h1_rate else ("#2563eb" if h1_rate >= mo_h1_rate else "#dc2626")
    status_bg = "#dcfce7" if h1_rate >= city_h1_rate else ("#dbeafe" if h1_rate >= mo_h1_rate else "#fee2e2")
    return textwrap.dedent(f"""
    <div style="background:#ffffff; border:1px solid #dbeafe; border-left:8px solid #2563eb; border-radius:16px; padding:14px; margin-bottom:12px; box-shadow:0 4px 12px rgba(15,23,42,0.06);">
        <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:10px;">
            <div style="font-weight:900; color:#1e3a8a; font-size:0.98rem;">{category}</div>
            <span style="background:{status_bg}; color:{status_color}; padding:4px 9px; border-radius:999px; font-size:0.74rem; font-weight:900;">{status}</span>
        </div>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:10px;">
            <div style="background:#f8fafc; border-radius:10px; padding:8px;">
                <div style="font-size:0.72rem; color:#64748b; font-weight:800;">대상액</div>
                <div style="font-size:0.95rem; color:#0f172a; font-weight:900;">{int(target):,}원</div>
            </div>
            <div style="background:#eff6ff; border-radius:10px; padding:8px;">
                <div style="font-size:0.72rem; color:#1d4ed8; font-weight:800;">상반기 실적률</div>
                <div style="font-size:1.05rem; color:#1d4ed8; font-weight:900;">{h1_rate:.1f}%</div>
            </div>
        </div>
        <div style="font-size:0.78rem; color:#475569; font-weight:900; display:flex; justify-content:space-between; margin-bottom:4px;">
            <span>상반기 집행액 {int(h1_actual):,}원</span>
            <span>남양주시 목표 {city_h1_rate:.1f}%</span>
        </div>
        <div style="position:relative; height:18px; background:#e5e7eb; border-radius:999px; overflow:hidden; margin-bottom:18px;">
            <div style="height:100%; width:{h1_width:.1f}%; background:linear-gradient(90deg,#60a5fa,#2563eb); border-radius:999px;"></div>
            <div title="행안부 상반기 목표" style="position:absolute; left:{mo_h1_rate:.1f}%; top:0; height:100%; width:3px; background:#2563eb;"></div>
            <div title="남양주시 상반기 목표" style="position:absolute; left:{city_h1_rate:.1f}%; top:0; height:100%; width:3px; background:#dc2626;"></div>
        </div>
        <div style="display:flex; justify-content:space-between; font-size:0.72rem; color:#64748b; font-weight:800; margin-top:-14px; margin-bottom:10px;">
            <span>행안부 {mo_h1_rate:.1f}%</span><span>남양주시 {city_h1_rate:.1f}%</span>
        </div>
        <div style="font-size:0.74rem; color:#64748b; font-weight:800; display:flex; justify-content:space-between; margin-bottom:3px;">
            <span>1분기 참고: {q1_rate:.1f}%</span><span>행안부 {mo_q1_rate:.1f}% / 남양주시 {city_q1_rate:.1f}%</span>
        </div>
        <div style="height:8px; background:#eef2f7; border-radius:999px; overflow:hidden;">
            <div style="height:100%; width:{q1_width:.1f}%; background:linear-gradient(90deg,#bfdbfe,#60a5fa); border-radius:999px;"></div>
        </div>
    </div>
    """).strip()

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


ASSET_ACQUISITION_KEYWORDS = [
    "수탁자산취득비", "자산취득비", "405-12", "조달구매", "조달수수료",
    "자동심장충격기", "심장충격기", "AED", "포충기", "방화벽", "보안장비",
    "DDOS", "디도스", "CCTV", "장비구매"
]

def is_asset_acquisition_text(*values):
    """적요/세목/예산과목 중 수탁자산취득비성 지출 여부를 판단합니다."""
    text = " ".join([str(v) for v in values if v is not None]).replace(" ", "").upper()
    if not text or text in ["NAN", "NAT", "NONE"]:
        return False
    return any(str(k).replace(" ", "").upper() in text for k in ASSET_ACQUISITION_KEYWORDS)


def is_known_laundry_accrual_case(desc="", amount=0):
    """2026년 세탁용역 1~2월분 일괄 지급건 판정.

    실제 지급은 3월 4,781,810원이지만 항목별 분석에서는
    1월 1,908,830원 / 2월 2,872,980원으로 귀속 반영합니다.
    문서제목에 세탁이라는 단어가 누락되어도 금액 기준으로 잡습니다.
    """
    try:
        amt = int(round(float(clean_numeric(amount))))
    except Exception:
        amt = 0
    text = str(desc).replace(" ", "")
    return amt == 4781810 or ("세탁" in text and amt == 4781810)

def force_mapped_category_for_known_cases(desc, row_cat="", budget_subj="", amount=0):
    """예산과목/세목이 비어 있거나 엑셀 양식이 달라도 반드시 잡아야 하는 예외 분류."""
    if is_known_laundry_accrual_case(desc, amount):
        return "세탁용역"
    if is_asset_acquisition_text(desc, row_cat, budget_subj):
        return "수탁자산취득비"
    return None


# [V11] 수탁자산취득비 수동 반영 데이터
# 이 항목은 사용자가 제공한 지출일자/적요/지급명령금액 기준으로
# 일상경비 동기화 업로드 여부와 무관하게 항목별 지출 분석에 반영합니다.
MANUAL_ASSET_ACQUISITION_ROWS = [
    {"집행일자": "2026-02-25", "적요": "정약용 펀그라운드 자동심장충격기 조달구매_조달수수료", "집행금액": 1990690, "세목": "수탁자산취득비", "예산과목": "수탁자산취득비", "업로드구분": "수동입력_수탁자산취득비"},
    {"집행일자": "2026-03-31", "적요": "정약용 펀그라운드 포충기 조달구매", "집행금액": 967180, "세목": "수탁자산취득비", "예산과목": "수탁자산취득비", "업로드구분": "수동입력_수탁자산취득비"},
    {"집행일자": "2026-04-14", "적요": "나라장터 이용수수료(2026년 정약용 펀그라운드 조달구매)", "집행금액": 20000, "세목": "수탁자산취득비", "예산과목": "수탁자산취득비", "업로드구분": "수동입력_수탁자산취득비"},
    {"집행일자": "2026-04-16", "적요": "남양주도시공사 차세대 방화벽 조달구매_정편", "집행금액": 1094000, "세목": "수탁자산취득비", "예산과목": "수탁자산취득비", "업로드구분": "수동입력_수탁자산취득비"},
    {"집행일자": "2026-04-20", "적요": "남양주도시공사 웹 방화벽 조달구매_정편", "집행금액": 1509000, "세목": "수탁자산취득비", "예산과목": "수탁자산취득비", "업로드구분": "수동입력_수탁자산취득비"},
    {"집행일자": "2026-04-24", "적요": "남양주도시공사 DDoS 보안장비 조달구매_정편", "집행금액": 1841000, "세목": "수탁자산취득비", "예산과목": "수탁자산취득비", "업로드구분": "수동입력_수탁자산취득비"},
]

def get_manual_asset_monthly_sums():
    monthly = {}
    for row in MANUAL_ASSET_ACQUISITION_ROWS:
        try:
            month = int(str(row.get("집행일자", ""))[5:7])
            amount = float(clean_numeric(row.get("집행금액", 0)))
        except Exception:
            continue
        if 1 <= month <= 12:
            monthly[month] = monthly.get(month, 0.0) + amount
    return monthly

def add_manual_asset_to_sums_map(sums_map):
    if 2026 not in sums_map:
        sums_map[2026] = {}
    if "수탁자산취득비" not in sums_map[2026]:
        sums_map[2026]["수탁자산취득비"] = {}
    for month, amount in get_manual_asset_monthly_sums().items():
        sums_map[2026]["수탁자산취득비"][month] = sums_map[2026]["수탁자산취득비"].get(month, 0.0) + amount
    return sums_map

# [V12] 세탁용역 귀속월 수동 반영 데이터
# 실제 지급은 3월 4,781,810원이지만, 항목별 지출 분석에서는
# 1월 1,908,830원 / 2월 2,872,980원으로 귀속월 기준 반영합니다.
MANUAL_LAUNDRY_ACCRUAL_ROWS = [
    {"집행일자": "2026-01-01", "적요": "세탁용역 1월분 (3월 일괄 지급 4,781,810원 중 귀속월 보정)", "집행금액": 1908830, "세목": "세탁용역", "예산과목": "세탁용역", "업로드구분": "수동입력_세탁용역귀속"},
    {"집행일자": "2026-02-01", "적요": "세탁용역 2월분 (3월 일괄 지급 4,781,810원 중 귀속월 보정)", "집행금액": 2872980, "세목": "세탁용역", "예산과목": "세탁용역", "업로드구분": "수동입력_세탁용역귀속"},
]

def get_manual_laundry_monthly_sums():
    monthly = {}
    for row in MANUAL_LAUNDRY_ACCRUAL_ROWS:
        try:
            month = int(str(row.get("집행일자", ""))[5:7])
            amount = float(clean_numeric(row.get("집행금액", 0)))
        except Exception:
            continue
        if 1 <= month <= 12:
            monthly[month] = monthly.get(month, 0.0) + amount
    return monthly

def add_manual_laundry_to_sums_map(sums_map):
    if 2026 not in sums_map:
        sums_map[2026] = {}
    if "세탁용역" not in sums_map[2026]:
        sums_map[2026]["세탁용역"] = {}
    for month, amount in get_manual_laundry_monthly_sums().items():
        # 세탁용역 수동 귀속월 보정은 업로드 파싱과 무관하게 고정 반영합니다.
        # 같은 금액이 이미 들어가 있더라도 최소 보정액은 보장합니다.
        current = sums_map[2026]["세탁용역"].get(month, 0.0)
        sums_map[2026]["세탁용역"][month] = max(float(current), float(amount))
    return sums_map

def apply_manual_laundry_to_master_data(data):
    """세탁용역 1~2월 귀속월 보정분을 master records에 반영합니다."""
    data = ensure_data_integrity(data)
    manual_sums = get_manual_laundry_monthly_sums()
    for r in data.get("records", []):
        if r.get("year") == 2026 and r.get("category") == "세탁용역" and r.get("month") in manual_sums:
            manual = float(manual_sums[r.get("month")])
            current = float(clean_numeric(r.get("amount", 0)))
            if current < manual:
                r["amount"] = manual
                r["status"] = "지출"
    return data

def apply_manual_asset_to_master_data(data):
    """수동 수탁자산취득비를 master records에 반영합니다.
    저장 데이터가 0원이어도 화면과 신속집행 대시보드에서 즉시 보이도록 보정합니다.
    """
    data = ensure_data_integrity(data)
    manual_sums = get_manual_asset_monthly_sums()
    for r in data.get("records", []):
        if r.get("year") == 2026 and r.get("category") == "수탁자산취득비" and r.get("month") in manual_sums:
            current = float(clean_numeric(r.get("amount", 0)))
            manual = float(manual_sums[r.get("month")])
            if current < manual:
                r["amount"] = manual
                r["status"] = "지출"
    return data

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

if FIREBASE_AVAILABLE:
    try:
        firebase_admin.get_app()
    except ValueError:
        try:
            if "firebase" in st.secrets:
                fb_creds = dict(st.secrets["firebase"])
                if "private_key" in fb_creds:
                    fb_creds["private_key"] = fb_creds["private_key"].replace("\\n", "\n")
                cred = credentials.Certificate(fb_creds)
                firebase_admin.initialize_app(cred)
        except Exception:
            # secrets 미설정/인증 실패 시 로컬 JSON 저장 모드로 계속 실행
            pass

db = firestore.client() if (FIREBASE_AVAILABLE and firebase_admin and firebase_admin._apps) else None
try:
    appId = st.secrets.get("app_id", "facility-ledger-2026-v1")
except Exception:
    appId = "facility-ledger-2026-v1"

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
    if not isinstance(data, dict) or "records" not in data:
        data = {"records": []}

    # [V37] 월별 마스터 레코드는 (연도, 월, 관리항목)당 반드시 1건만 유지합니다.
    # 과거 버전에서 같은 키의 행이 여러 개 생성된 경우 분석 화면의 group/sum 과정에서
    # 공청기비데 등이 정확히 2배로 표시되는 문제가 있었습니다.
    # 중복 행은 합산하지 않고, 같은 키 중 가장 큰 금액(동기화된 정상값)을 대표값으로 보존합니다.
    deduped_map = {}
    for raw in data.get("records", []):
        try:
            key = (int(raw.get('year')), int(raw.get('month')), str(raw.get('category', '')).strip())
        except Exception:
            continue
        if not key[2] or not (1 <= key[1] <= 12):
            continue
        rec = dict(raw)
        rec['year'], rec['month'], rec['category'] = key
        rec['amount'] = float(clean_numeric(rec.get('amount', 0)))
        rec['status'] = "지출" if rec['amount'] > 0 else "미지출"
        prev = deduped_map.get(key)
        if prev is None or rec['amount'] > float(clean_numeric(prev.get('amount', 0))):
            deduped_map[key] = rec

    data['records'] = list(deduped_map.values())
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
        amount_to_add = float(st.session_state.amt_box)

        # 2026년 값은 일상경비 자료 재집계 시 마스터가 다시 계산됩니다.
        # 직접 입력분도 원천 거래로 보존해야 다음 재집계에서 사라지지 않으므로
        # 일상경비 저장소에 "수동 집계조정" 거래로 함께 기록합니다.
        if int(year) == 2026:
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            manual_row = {
                "세목": cat,
                "집행일자": f"{int(year)}-{int(mon):02d}-01",
                "적요": f"{display_category(cat)} 수동 집계조정 ({timestamp})",
                "집행금액": amount_to_add,
                "예산과목": cat,
                "업로드구분": "수동집계조정",
            }
            daily_rows = list(st.session_state.get("daily_expenses", []))
            daily_rows.append(manual_row)
            if save_daily_expenses(daily_rows):
                st.session_state["daily_expenses"] = daily_rows
                sync_daily_to_master_auto()
                st.session_state.amt_box = 0
                st.toast("지출액 조정분을 원천내역과 월별 집계에 반영했습니다.")
            return

        curr = st.session_state['data']
        for r in curr["records"]:
            if r["year"] == year and r["category"] == cat and r["month"] == mon:
                r["amount"] += amount_to_add
                r["status"] = "지출"
                break
        if save_data_cloud(curr):
            st.session_state['data'] = curr
            st.session_state.amt_box = 0
            st.toast("지출액 조정분을 월별 집계에 반영했습니다.")

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
# ★ [V292 핵심] 공통 매핑 함수 (일반재료비 11.5M, 상하수도 2.3M 완벽 보장)
# -----------------------------------------------------------------------------
def get_mapped_category(desc, row_cat, budget_subj=""):
    desc_no_space = str(desc).replace(" ", "").strip()
    row_cat_no_space = str(row_cat).replace(" ", "").strip()
    budget_no_space = str(budget_subj).replace(" ", "").strip()

    # [V10] 금액/적요 기반 강제 예외: 세탁용역 귀속월 보정건, 수탁자산취득비성 조달구매
    # amount는 기존 시그니처 호환을 위해 이 함수에서는 직접 받지 않지만,
    # desc/row_cat/budget_subj에 명확한 자산취득 키워드가 있으면 최우선 분류합니다.
    if is_asset_acquisition_text(desc, row_cat, budget_subj):
        return "수탁자산취득비"

    # [V5] 특수 업로드/수동 세목이 이미 관리항목명으로 들어온 경우 최우선 인정
    # 예: 5대 용역 파일에서 "고객편의기기 관리용역"을 "공청기비데"로 변환해 저장한 행
    for cat_name in CATEGORIES:
        cat_no_space = str(cat_name).replace(" ", "")
        if cat_no_space and (cat_no_space in row_cat_no_space or cat_no_space in budget_no_space):
            return cat_name
    
    # 0. 기름/걸레/마포 관련 완벽 배제 (환경용역 15,600,000원 100% 보장)
    if any(k in desc_no_space or k in row_cat_no_space or k in budget_no_space for k in ["기름", "걸레", "마포"]):
        return None

    # 1. 엑셀 I열(예산과목) 최우선
    #    ※ 일반재료비는 상하수도요금 등 세부 적요와 관계없이 모두 일반재료비로 집계
    #       기존에는 적요에 '상하수도'가 있으면 먼저 상하수도로 빠져 일반재료비 집계에서 누락됨
    if budget_no_space:
        if "일반재료비" in budget_no_space: return "일반재료비"
        if "상품매입비" in budget_no_space: return "상품매입비"
        if "수탁자산취득비" in budget_no_space: return "수탁자산취득비"
        if "자체소수선" in budget_no_space: return "자체소수선"
        
    # 2. 특수 공과금 (수도) - 예산과목이 없는 자료에서만 별도 분류
    if any(k in desc_no_space or k in row_cat_no_space for k in ["상하수도요금", "수도요금", "수도료", "수도대금", "상하수도", "상수도", "하수도"]):
        return "상하수도"
        
    # 2-1. 특수 공과금 (전기요금)
    if any(k in desc_no_space or k in row_cat_no_space for k in ["전기요금", "전기료", "한전", "한국전력", "전력요금"]):
        if not any(k in desc_no_space for k in ["공사", "대행", "수수료", "충전", "전기차", "수리", "교체"]):
            return "전기요금"
            
    # 1-2. 미디어실
    if "미디어" in desc_no_space and any(k in desc_no_space for k in ["제습", "습기"]):
        return "미디어실제습기"

    # 2-2. 수탁자산취득비: 예산과목 컬럼이 없는 지출명령 자료는 적요 키워드로 보완 분류
    # 예: 자동심장충격기 조달구매, 포충기 조달구매, 방화벽/보안장비 조달구매 등
    if is_asset_acquisition_text(desc, row_cat, budget_subj):
        return "수탁자산취득비"

    # 3. 명시적 카테고리명 일치
    safe_categories = ["환경용역", "상품매입비", "세탁용역", "일반재료비", "자체소수선", "부서업무비"]
    for cat_name in safe_categories:
        if cat_name in row_cat_no_space or cat_name in desc_no_space:
            return cat_name

    # 4. 세부 키워드 매핑
    keyword_map = {
        "통신요금": ["통신요금", "통신비", "인터넷", "케이블"],
        "복합기임대": ["복합기임대", "복합기렌탈"], 
        "공청기비데": ["고객편의기기관리용역", "고객편의기기", "공기청정기렌탈", "공기청정기", "공청기", "비데렌탈", "비데"],
        "무인경비": ["무인경비용역"],
        "승강기점검": ["승강기유지보수", "승강기점검"], 
        "신용카드수수료": ["신용카드수수료"], 
        "환경용역": ["청소용역", "미화용역"],
        "세탁용역": ["세탁"],
        "야간경비": ["야간경비용역", "당직용역"],
        "상품매입비": ["상품매입", "자판기식음료", "종량제봉투"]
    }
    
    for m_cat, kws in keyword_map.items():
        if any(k in desc_no_space for k in kws):
            return m_cat
            
    return None

def is_water_charge_row(desc, row_cat="", budget_subj=""):
    """상하수도 별도 집계용 판정 함수.

    일반재료비 전체 집계는 유지하되, 그중 상하수도 요금만
    '상하수도' 관리항목에도 별도로 반영하기 위해 사용합니다.
    """
    text = " ".join([str(desc), str(row_cat), str(budget_subj)]).replace(" ", "")
    if not text or text in ["nan", "NaT", "None"]:
        return False
    water_keywords = [
        "상하수도요금", "상하수도", "상수도", "하수도",
        "수도요금", "수도료", "수도대금", "물사용료"
    ]
    return any(k in text for k in water_keywords)



def get_accrual_splits_for_special_cases(category, year, month, amount, desc):
    """항목별 분석용 귀속월 보정 예외 처리.

    기본은 지급월 그대로 1건 반영합니다.
    단, 세탁용역 2026년 3월 지급 4,781,810원 건은
    실제로 1월분 1,908,830원 + 2월분 2,872,980원 일괄 지급 건이므로
    분석 화면에서는 귀속월 기준으로 분할합니다.
    """
    try:
        y = int(year)
        m = int(month)
    except Exception:
        return []

    try:
        amt = int(float(str(amount).replace(',', '').strip() or 0))
    except Exception:
        amt = 0

    text = str(desc).replace(' ', '')

    if (
        y == 2026
        and amt == 4781810
        and (category == "세탁용역" or "세탁" in text or True)
    ):
        # 2026년 세탁용역 1~2월분이 3월에 4,781,810원으로 일괄 지급된 건은
        # 지급월이 아니라 귀속월 기준으로 1월/2월에 분할 반영합니다.
        return [(1, 1908830), (2, 2872980)]

    if 1 <= m <= 12:
        return [(m, amt)]
    return []

def is_general_material_row(row):
    """예산과목 기준 일반재료비 여부."""
    budget = str(row.get('예산과목', '')).replace(' ', '')
    semok = str(row.get('세목', '')).replace(' ', '')
    return ("일반재료비" in budget) or ("일반재료비" in semok)

def sync_daily_to_master_auto():
    master_data = load_data()
    master_data = ensure_data_integrity(master_data)
    daily = st.session_state.get('daily_expenses', [])
    
    if not daily:
        for r in master_data.get('records', []):
            if r['year'] == 2026:
                r['amount'] = 0.0
                r['status'] = "미지출"
        # [V11/V12] 업로드 데이터가 없어도, 사용자가 제공한 수동 입력분은 유지
        master_data = apply_manual_asset_to_master_data(master_data)
        master_data = apply_manual_laundry_to_master_data(master_data)
        save_data_cloud(master_data)
        st.session_state['data'] = master_data
        return True
    
    sums_map = {}
    # [V37] 같은 업로드 거래가 일반/특수 업로드 또는 반복 업로드로 중복 저장돼도
    # 분석 집계에는 한 번만 반영되도록 거래 단위 중복 제거 키를 사용합니다.
    seen_transactions = set()

    for row in daily:
        desc = str(row.get('적요', '')).strip()
        amt_v = clean_numeric(row.get('집행금액', 0))
        date_raw = str(row.get('집행일자', '')).strip()
        
        year_found = None
        month_found = None

        # [V5] 5대 용역/수수료 전용 업로드는 K열 문서제목에 "2026년 0월"처럼
        # 잘못된 월이 들어오는 경우가 있으므로, 파서가 만든 집행일자(=B열 지급월 반영)를 최우선 사용
        is_special_upload = str(row.get('업로드구분', '')).strip() == '5대용역수수료'
        if is_special_upload and date_raw:
            date_num = re.sub(r'[^0-9]', '', str(date_raw))
            if len(date_num) >= 8:
                year_found = int(date_num[:4])
                month_found = int(date_num[4:6])

        if not year_found or not month_found:
            ym_match = re.search(r'(\d{4})\s*년\s*(\d{1,2})\s*월', desc)
            if ym_match:
                y_tmp = int(ym_match.group(1))
                m_tmp = int(ym_match.group(2))
                # 0월은 무효값이므로 확정하지 않음
                if 1 <= m_tmp <= 12:
                    year_found = y_tmp
                    month_found = m_tmp
        
        if (not year_found or not month_found or not (1 <= int(month_found) <= 12)) and date_raw:
            date_num = re.sub(r'[^0-9]', '', str(date_raw))
            if len(date_num) >= 8:
                year_found = int(date_num[:4])
                month_found = int(date_num[4:6])
        
        if not month_found:
            m_match = re.search(r'(\d{1,2})\s*월', desc)
            if m_match:
                m_tmp = int(m_match.group(1))
                if 1 <= m_tmp <= 12:
                    month_found = m_tmp
                
        if not year_found and month_found:
            year_found = 2026
            
        if not year_found or not month_found or not (1 <= int(month_found) <= 12):
            continue
        
        # [V10] 일반 매핑 전에 반드시 잡아야 하는 예외를 먼저 처리합니다.
        # - 세탁용역 4,781,810원 일괄 지급건: 문서제목에 '세탁'이 없어도 세탁용역으로 강제
        # - 수탁자산취득비성 조달구매/포충기/방화벽 등: 예산과목이 비어도 수탁자산취득비로 강제
        matched_cat = force_mapped_category_for_known_cases(
            desc, row.get('세목', ''), row.get('예산과목', ''), amt_v
        ) or get_mapped_category(desc, row.get('세목', ''), row.get('예산과목', ''))

        if matched_cat:
            normalized_desc = re.sub(r'\s+', '', desc).lower()
            tx_key = (
                int(year_found), int(month_found), matched_cat,
                int(round(float(amt_v))), normalized_desc
            )
            if tx_key in seen_transactions:
                continue
            seen_transactions.add(tx_key)
            if year_found not in sums_map: sums_map[year_found] = {}
            if matched_cat not in sums_map[year_found]: sums_map[year_found][matched_cat] = {}

            # 귀속월 보정 예외 처리
            # 예: 세탁용역 1~2월분이 3월에 일괄 지급된 경우, 항목별 분석에서는 1월/2월로 분할 반영
            accrual_splits = get_accrual_splits_for_special_cases(matched_cat, year_found, month_found, amt_v, desc)
            for accrual_month, accrual_amt in accrual_splits:
                if 1 <= int(accrual_month) <= 12:
                    sums_map[year_found][matched_cat][accrual_month] = sums_map[year_found][matched_cat].get(accrual_month, 0) + accrual_amt

            # 중요: 일반재료비는 전체 금액을 유지하고,
            # 그중 상하수도 요금만 '상하수도' 관리항목에도 별도 집계합니다.
            # 즉, 일반재료비에서 상하수도를 차감하지 않습니다.
            if matched_cat == "일반재료비" and is_water_charge_row(desc, row.get('세목', ''), row.get('예산과목', '')):
                if "상하수도" not in sums_map[year_found]: sums_map[year_found]["상하수도"] = {}
                for accrual_month, accrual_amt in get_accrual_splits_for_special_cases(matched_cat, year_found, month_found, amt_v, desc):
                    if 1 <= int(accrual_month) <= 12:
                        sums_map[year_found]["상하수도"][accrual_month] = sums_map[year_found]["상하수도"].get(accrual_month, 0) + accrual_amt

    # [V11] 수탁자산취득비는 일상경비 업로드 형식이 아니라 사용자가 제공한 표 기준으로 수동 반영
    sums_map = add_manual_asset_to_sums_map(sums_map)
    # [V12] 세탁용역 3월 일괄 지급건은 항목별 분석에서 1월/2월 귀속월 기준으로 수동 반영
    sums_map = add_manual_laundry_to_sums_map(sums_map)
            
    data_changed = False
    for r in master_data.get('records', []):
        if r['year'] == 2026:
            cat, month = r['category'], r['month']
            
            # 수탁자산취득비도 일상경비 동기화 데이터 기준으로 반영
            # 기존에는 신속집행 기본값 보존을 위해 제외했으나, 실제 지출명령 자료 업로드 시 0원으로 남는 문제가 있었음
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
# 엑셀 파일 읽기 엔진: 여러 시트 자동 탐색
# -----------------------------------------------------------------------------
def read_excel_sheets_flexible(uploaded_file):
    """업로드 파일을 모든 시트 기준으로 header=None DataFrame dict로 반환합니다."""
    uploaded_file.seek(0)
    name = uploaded_file.name.lower()
    if name.endswith('.csv'):
        return {"CSV": pd.read_csv(uploaded_file, header=None)}

    xls = pd.ExcelFile(uploaded_file, engine='openpyxl')
    sheets = {}
    for sheet_name in xls.sheet_names:
        try:
            df = pd.read_excel(xls, sheet_name=sheet_name, header=None)
            # 완전 빈 시트 제외
            if not df.dropna(how='all').empty:
                sheets[sheet_name] = df
        except Exception:
            continue
    return sheets

def parse_general_from_uploaded_file(uploaded_file):
    """일반 일상경비 업로드: 모든 시트를 검사해 최초로 인식되는 시트를 사용합니다."""
    sheets = read_excel_sheets_flexible(uploaded_file)
    attempts = []
    for sheet_name, df_raw in sheets.items():
        parsed = parse_expense_excel(df_raw)
        count = len(parsed) if parsed else 0
        attempts.append((sheet_name, count))
        if parsed:
            return sheet_name, parsed, attempts
    return None, None, attempts

def parse_special_from_uploaded_file(uploaded_file):
    """특수 양식 업로드: 모든 시트를 검사해 처리 가능한 시트를 사용합니다."""
    sheets = read_excel_sheets_flexible(uploaded_file)
    attempts = []
    best_sheet, best_parsed = None, None
    for sheet_name, df_raw in sheets.items():
        parsed = parse_special_expense_excel(df_raw)
        count = len(parsed) if parsed else 0
        attempts.append((sheet_name, count))
        if parsed and (best_parsed is None or len(parsed) > len(best_parsed)):
            best_sheet, best_parsed = sheet_name, parsed
    return best_sheet, best_parsed, attempts

# -----------------------------------------------------------------------------
# 엑셀 파싱 엔진 
# -----------------------------------------------------------------------------
def parse_expense_excel(df_raw):
    header_idx = -1; found_cols = {}
    keyword_detect = {
        '집행일자': ['지급일자','지급일','지급명령일자','일자','날짜','집행일','결의일자'], 
        '적요': ['적요','내용','품명','건명'], 
        '집행금액': ['지급명령금액','지급액','지출금액','금액','집행액','지출액','결재금액'],
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
            
    if header_idx == -1:
        # [V10] 헤더 인식 실패 대비: A=지급일자, B=적요, C=지급명령금액 형태의 단순 지출명령 자료 보완
        fallback_rows = []
        for _, row in df_raw.iterrows():
            row_arr = row.values
            if len(row_arr) < 3:
                continue
            date_val = str(row_arr[0]).strip() if pd.notna(row_arr[0]) else ""
            desc = str(row_arr[1]).strip() if pd.notna(row_arr[1]) else ""
            amt_val = clean_numeric(row_arr[2])
            if not desc or "합계" in desc or amt_val <= 0:
                continue
            forced_cat = force_mapped_category_for_known_cases(desc, "", "", amt_val)
            if forced_cat:
                fallback_rows.append({
                    "세목": forced_cat,
                    "집행일자": date_val,
                    "적요": desc,
                    "집행금액": amt_val,
                    "예산과목": forced_cat
                })
        return fallback_rows if fallback_rows else None
        
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
        
        # [V292 절대 명령 반영] I열 값이 일반재료비면 확보
        col_i_val = str(row_arr[8]).replace(" ", "") if len(row_arr) > 8 and pd.notna(row_arr[8]) else ""
        if "일반재료비" in col_i_val:
            budget_subj = "일반재료비"

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

        # 예산과목 컬럼이 없는 지출명령 양식 보완:
        # 적요에 조달구매/포충기/방화벽/보안장비/자동심장충격기 등이 있으면
        # 수탁자산취득비로 저장하여 항목별 분석과 신속집행 대시보드 모두에서 집계되게 합니다.
        forced_cat = force_mapped_category_for_known_cases(desc, semok_str, budget_subj, amt_val)
        if forced_cat == "수탁자산취득비":
            semok_str = "수탁자산취득비"
            budget_subj = "수탁자산취득비"
        elif forced_cat == "세탁용역":
            semok_str = "세탁용역"
            budget_subj = "세탁용역"
        
        new_processed.append({
            "세목": semok_str, 
            "집행일자": date_val, 
            "적요": desc, 
            "집행금액": amt_val,
            "예산과목": budget_subj
        })
        
    return new_processed

def parse_special_expense_excel(df_raw):
    """5대 용역/수수료 전용 양식 파싱

    기준:
    - B열(두 번째 열): 지급월 우선
    - K열(열한 번째 열): 문서제목
    - 문서제목에 '고객편의기기 관리용역' 등이 포함되면 '공청기비데'로 강제 분류

    보완사항:
    - B열 지급월이 '1', '1월', '2026-01-15', '2026.01.', '2026년 1월' 형태여도 월 인식
    - 제목에 '2026년 0월 ...' 같은 잘못된 월이 있어도 B열 지급월을 우선 사용
    - 금액 컬럼명이 없거나 위치가 달라도 행 안의 금액 후보를 최대한 탐색
    """
    def normalize_text(v):
        if pd.isna(v):
            return ""
        s = str(v).strip()
        if s in ["nan", "NaT", "None"]:
            return ""
        return re.sub(r'\s+', '', s)

    def parse_month_from_value(v):
        """지급월 값을 1~12 정수로 변환. 0월은 무효 처리."""
        if pd.isna(v):
            return 0
        s = str(v).strip()
        if not s or s in ["nan", "NaT", "None"]:
            return 0

        # 엑셀 날짜/판다스 Timestamp 대응
        try:
            dt = pd.to_datetime(v, errors='coerce')
            if pd.notna(dt) and 1 <= int(dt.month) <= 12:
                return int(dt.month)
        except Exception:
            pass

        # '2026년 1월', '1월'
        m = re.search(r'(?:20\d{2}\s*년\s*)?(1[0-2]|0?[1-9])\s*월', s)
        if m:
            return int(m.group(1))

        # '2026-01-15', '2026.01', '2026/1'
        m = re.search(r'20\d{2}\D+(1[0-2]|0?[1-9])(?:\D|$)', s)
        if m:
            return int(m.group(1))

        # 숫자만 있는 경우: 1~12만 인정, 0은 무효
        m = re.search(r'^0?([1-9]|1[0-2])(?:\.0)?$', s)
        if m:
            return int(float(s))

        return 0

    def map_special_category(title):
        t = normalize_text(title)
        if not t:
            return ""
        # 5대 특수항목 강제 분류
        if "신용카드" in t and "수수료" in t:
            return "신용카드수수료"
        if "무인경비" in t:
            return "무인경비"
        # 고객편의기기 관리용역 = 공청기/비데 비용
        if (
            "고객편의기기관리용역" in t
            or "고객편의기기" in t
            or "공기청정기" in t
            or "공청기" in t
            or "비데" in t
        ):
            return "공청기비데"
        if "야간경비" in t or "당직용역" in t:
            return "야간경비"
        if "청소용역" in t or "환경용역" in t or "미화용역" in t:
            return "환경용역"
        return ""

    # 금액 컬럼 위치 탐색
    header_idx = -1
    amt_idx = -1
    for i in range(min(30, len(df_raw))):
        row_vals = [normalize_text(v) for v in df_raw.iloc[i]]
        for idx, val in enumerate(row_vals):
            if any(kw in val for kw in ['금액', '집행액', '지출액', '결재금액', '청구액', '지급액']):
                header_idx = i
                amt_idx = idx
                break
        if header_idx != -1:
            break

    # header가 잡히면 그 다음 행부터, 아니면 전체 행 검사
    data_rows = df_raw.iloc[header_idx+1:].copy() if header_idx != -1 else df_raw.copy()
    new_processed = []

    for _, row in data_rows.iterrows():
        row_arr = row.values
        if len(row_arr) <= 10:
            continue

        month_raw = row_arr[1]       # B열 지급월
        title_raw = row_arr[10]      # K열 문서제목
        title_val = str(title_raw).strip() if not pd.isna(title_raw) else ""
        title_no_space = normalize_text(title_raw)

        if not title_no_space or "합계" in title_no_space:
            continue

        # 기름걸레 등 다른 비용이 특수항목으로 섞이는 것 방지
        if "기름" in title_no_space and "걸레" in title_no_space:
            continue

        mapped_cat = map_special_category(title_val)
        if not mapped_cat:
            continue

        # 월은 B열 지급월을 최우선 사용. B열이 비어 있거나 무효일 때만 제목 보조 사용.
        month = parse_month_from_value(month_raw)
        if month == 0:
            month = parse_month_from_value(title_val)
        # [V5] 제목이 "2026년 0월"이고 B열도 비정상인 경우, 행 앞쪽의 다른 월 후보를 보조 탐색
        if month == 0:
            for x in list(row_arr[:10]):
                m_try = parse_month_from_value(x)
                if 1 <= m_try <= 12:
                    month = m_try
                    break
        if month == 0 or not (1 <= month <= 12):
            continue

        # 금액 추출: 금액 헤더 우선, 실패 시 L열 이후, 그래도 실패 시 전체 행에서 후보 탐색
        amt_val = 0.0
        if amt_idx != -1 and len(row_arr) > amt_idx:
            amt_val = clean_numeric(row_arr[amt_idx])

        if amt_val <= 0:
            # 보통 K열 제목 다음 L열 이후에 금액이 있는 경우
            nums = []
            for x in row_arr[11:]:
                n = clean_numeric(x)
                if n > 0:
                    nums.append(n)
            if nums:
                amt_val = max(nums)

        if amt_val <= 0:
            # 금액이 K열 앞쪽에 있는 양식 보완. 월/연도처럼 작은 숫자는 제외.
            nums = []
            for idx, x in enumerate(row_arr):
                if idx in [1, 10]:
                    continue
                n = clean_numeric(x)
                if n >= 1000:  # 2026, 월 숫자 등 오인 방지
                    nums.append(n)
            if nums:
                amt_val = max(nums)

        if amt_val <= 0:
            continue

        date_str = f"2026-{month:02d}-01"
        new_processed.append({
            "세목": mapped_cat,
            "집행일자": date_str,
            "적요": title_val,
            "집행금액": amt_val,
            "예산과목": mapped_cat,
            "업로드구분": "5대용역수수료"
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


def set_subtree_state_v202(node_id, new_state, children_map, states):
    """정량실적 트리의 선택 상태를 하위 행까지 동일하게 적용합니다."""
    states[node_id] = int(new_state)
    for child_id in children_map.get(node_id, []):
        set_subtree_state_v202(child_id, new_state, children_map, states)


def refresh_ancestors_v202(node_id, parent_map, children_map, states):
    """자식 선택 결과를 부모의 미선택/선택/부분선택 상태로 갱신합니다."""
    parent_id = parent_map.get(node_id)
    while parent_id is not None:
        child_states = [int(states.get(child_id, 0)) for child_id in children_map.get(parent_id, [])]
        if child_states and all(value == 1 for value in child_states):
            states[parent_id] = 1
        elif child_states and any(value in (1, 2) for value in child_states):
            states[parent_id] = 2
        else:
            states[parent_id] = 0
        parent_id = parent_map.get(parent_id)


# -----------------------------------------------------------------------------
# 6. 세션 데이터 초기화 
# -----------------------------------------------------------------------------
if st.session_state['quota_exceeded']:
    st.warning("클라우드 일일 사용량을 초과해 로컬 JSON 저장 모드로 전환했습니다. 현재 작업은 이 컴퓨터에 저장되며, 사용량이 초기화된 뒤 다시 클라우드 저장을 사용할 수 있습니다.")

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
_before_integrity_count = len(master_data_raw.get("records", [])) if isinstance(master_data_raw, dict) else 0
master_data_raw = ensure_data_integrity(master_data_raw)
_after_integrity_count = len(master_data_raw.get("records", []))
# 중복 마스터 행이 실제로 정리된 경우 로컬/클라우드에도 즉시 저장해 재발을 방지합니다.
if _after_integrity_count < _before_integrity_count:
    save_data_cloud(master_data_raw)
# [V11/V12] 저장 데이터가 초기화되어도 수동 입력분은 화면 집계에 즉시 반영
master_data_raw = apply_manual_asset_to_master_data(master_data_raw)
master_data_raw = apply_manual_laundry_to_master_data(master_data_raw)
st.session_state['data'] = master_data_raw
df_all = pd.DataFrame(master_data_raw.get("records", []))
if not df_all.empty: df_all["amount"] = pd.to_numeric(df_all["amount"], errors='coerce').fillna(0).astype('float64')

# --- 스타일 가이드 ---
st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;600;700;800;900&display=swap');
        :root {
            --navy-950: #081629;
            --navy-900: #0b1f3a;
            --navy-800: #12345b;
            --blue-600: #2563eb;
            --blue-100: #dbeafe;
            --slate-950: #0f172a;
            --slate-700: #334155;
            --slate-500: #64748b;
            --slate-300: #cbd5e1;
            --slate-200: #e2e8f0;
            --slate-100: #f1f5f9;
            --surface: #ffffff;
            --canvas: #f6f8fb;
            --success: #047857;
            --warning: #b45309;
            --danger: #b91c1c;
        }
        html, body, [class*="css"] {
            font-family: 'Noto Sans KR', "Malgun Gothic", sans-serif !important;
            color: var(--slate-950);
        }
        .stApp { background: var(--canvas); }
        header[data-testid="stHeader"] { background: rgba(246,248,251,.92); }
        .block-container {
            max-width: 1480px !important;
            padding: 1.5rem 2rem 4rem 2rem;
        }

        /* 고정 업무 내비게이션 */
        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, var(--navy-950) 0%, var(--navy-900) 100%);
            border-right: 1px solid rgba(148,163,184,.2);
        }
        section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
            padding-top: .85rem;
        }
        section[data-testid="stSidebar"] h1,
        section[data-testid="stSidebar"] h2,
        section[data-testid="stSidebar"] h3,
        section[data-testid="stSidebar"] p,
        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
            color: #e5edf8 !important;
        }
        section[data-testid="stSidebar"] [data-testid="stButton"] > button {
            width: 100%;
            min-height: 42px;
            justify-content: flex-start;
            border-radius: 10px;
            border: 1px solid transparent;
            background: transparent;
            color: #cbd5e1;
            box-shadow: none;
            padding: .55rem .75rem;
            font-weight: 700;
        }
        section[data-testid="stSidebar"] [data-testid="stButton"] > button:hover {
            background: rgba(255,255,255,.08);
            border-color: rgba(255,255,255,.12);
            color: #ffffff;
        }
        section[data-testid="stSidebar"] [data-testid="stButton"] > button[kind="primary"] {
            background: #ffffff;
            color: var(--navy-900);
            border-color: #ffffff;
            box-shadow: 0 5px 16px rgba(2,6,23,.18);
        }
        section[data-testid="stSidebar"] [data-testid="stExpander"] {
            border-color: rgba(255,255,255,.14);
            background: rgba(255,255,255,.04);
            border-radius: 10px;
        }
        .sidebar-brand {
            padding: 13px 12px 15px 12px;
            margin-bottom: 8px;
            border-bottom: 1px solid rgba(255,255,255,.12);
        }
        .sidebar-brand-kicker {
            color: #93c5fd;
            font-size: .68rem;
            font-weight: 900;
            letter-spacing: .11em;
            text-transform: uppercase;
        }
        .sidebar-brand-title {
            color: #ffffff;
            font-size: 1.05rem;
            font-weight: 900;
            letter-spacing: -.03em;
            margin-top: 4px;
        }
        .sidebar-brand-sub {
            color: #94a3b8;
            font-size: .72rem;
            font-weight: 600;
            margin-top: 3px;
        }
        .sidebar-status {
            display: flex;
            align-items: center;
            gap: 7px;
            margin: 10px 3px 2px 3px;
            padding: 9px 10px;
            border: 1px solid rgba(255,255,255,.12);
            border-radius: 10px;
            color: #dbeafe;
            background: rgba(37,99,235,.12);
            font-size: .72rem;
            font-weight: 700;
        }
        .status-dot {
            width: 7px;
            height: 7px;
            flex: 0 0 7px;
            border-radius: 50%;
            background: #34d399;
            box-shadow: 0 0 0 4px rgba(52,211,153,.12);
        }

        /* 페이지 구조 */
        .page-head {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 20px;
            padding: 6px 0 19px 0;
            margin-bottom: 18px;
            border-bottom: 1px solid var(--slate-200);
        }
        .page-kicker {
            color: var(--blue-600);
            font-size: .72rem;
            font-weight: 900;
            letter-spacing: .08em;
            text-transform: uppercase;
        }
        .page-title {
            margin-top: 4px;
            color: var(--slate-950);
            font-size: 1.82rem;
            line-height: 1.25;
            font-weight: 900;
            letter-spacing: -.045em;
        }
        .page-desc {
            margin-top: 6px;
            color: var(--slate-500);
            font-size: .88rem;
            font-weight: 600;
        }
        .page-badge {
            display: inline-flex;
            align-items: center;
            gap: 7px;
            border: 1px solid #bfdbfe;
            border-radius: 999px;
            padding: 7px 11px;
            background: #eff6ff;
            color: #1d4ed8;
            font-size: .75rem;
            font-weight: 800;
            white-space: nowrap;
        }
        .section-header {
            font-size: 1.12rem;
            font-weight: 900;
            color: var(--slate-950);
            margin: 30px 0 14px 0;
            border-left: 4px solid var(--blue-600);
            padding-left: 10px;
            letter-spacing: -.025em;
        }
        .section-label {
            color: var(--slate-950) !important;
            font-size: 1.12rem !important;
            font-weight: 900 !important;
        }

        /* 카드·데이터·입력 요소 */
        .metric-card {
            min-height: 126px;
            background: var(--surface);
            padding: 18px 19px;
            border-radius: 14px;
            border: 1px solid var(--slate-200);
            border-left: 4px solid var(--blue-600);
            box-shadow: 0 3px 10px rgba(15,23,42,.035);
            margin-bottom: 10px;
        }
        .metric-label {
            color: var(--slate-500);
            font-size: .76rem;
            font-weight: 800;
        }
        .metric-value {
            margin-top: 7px;
            font-size: 1.72rem;
            line-height: 1.2;
            font-weight: 900;
            color: var(--slate-950);
            letter-spacing: -.04em;
        }
        .quick-exec-card-scarlet {
            background: var(--surface);
            border: 1px solid #fecdd3;
            padding: 19px;
            border-radius: 14px;
            border-left: 4px solid #e11d48;
            margin-bottom: 16px;
            box-shadow: 0 3px 10px rgba(15,23,42,.035);
        }
        .timeline-card {
            background: var(--surface);
            border: 1px solid var(--slate-200);
            border-radius: 13px;
            padding: 15px;
            margin-bottom: 10px;
            transition: border-color .18s ease, box-shadow .18s ease;
            position: relative;
            min-height: 210px;
            display: flex;
            flex-direction: column;
        }
        .timeline-card:hover { border-color: #93c5fd; box-shadow: 0 5px 16px rgba(15,23,42,.06); }
        .current-badge {
            position: absolute;
            top: -10px;
            right: 10px;
            background: var(--blue-600);
            color: white;
            padding: 3px 9px;
            border-radius: 999px;
            font-size: .69rem;
            font-weight: 900;
        }
        .theme-blue { border-left: 4px solid #2563eb; }
        .theme-green { border-left: 4px solid #059669; }
        .theme-orange { border-left: 4px solid #d97706; }
        .budget-card-simple {
            background: var(--surface);
            border: 1px solid var(--slate-200);
            padding: 18px;
            border-radius: 14px;
            margin-bottom: 10px;
            border-left: 4px solid #10b981;
        }
        .korean-amount {
            background-color: #eff6ff;
            padding: 9px 13px;
            border-radius: 9px;
            border: 1px solid #bfdbfe;
            color: #1e40af;
            font-weight: 800;
            margin-top: 4px;
            display: block;
            font-size: .95rem;
            text-align: right;
        }
        [data-testid="stVerticalBlockBorderWrapper"] {
            border-color: var(--slate-200);
            border-radius: 14px;
            background: var(--surface);
            box-shadow: 0 3px 10px rgba(15,23,42,.025);
        }
        [data-testid="stDataFrame"],
        [data-testid="stTable"] {
            border: 1px solid var(--slate-200);
            border-radius: 12px;
            overflow: hidden;
            background: var(--surface);
        }
        .stButton > button,
        .stDownloadButton > button {
            min-height: 40px;
            border-radius: 9px;
            font-weight: 800;
            transition: transform .12s ease, box-shadow .12s ease, border-color .12s ease;
        }
        .stButton > button:hover,
        .stDownloadButton > button:hover { transform: translateY(-1px); }
        .stButton > button:focus-visible,
        .stDownloadButton > button:focus-visible {
            outline: 3px solid rgba(37,99,235,.28);
            outline-offset: 2px;
        }
        [data-testid="stSelectbox"] > div > div,
        [data-testid="stNumberInput"] input,
        [data-testid="stTextInput"] input {
            border-radius: 9px;
        }

        /* 앱 홈 */
        .home-hero {
            position: relative;
            overflow: hidden;
            display: grid;
            grid-template-columns: minmax(0, 1.5fr) minmax(280px, .7fr);
            gap: 24px;
            align-items: center;
            padding: 30px 32px;
            border-radius: 18px;
            background: linear-gradient(135deg, #0b1f3a 0%, #12345b 65%, #1d4ed8 140%);
            color: #ffffff;
            box-shadow: 0 12px 30px rgba(15,23,42,.14);
        }
        .home-hero:after {
            content: "";
            position: absolute;
            width: 240px;
            height: 240px;
            border-radius: 50%;
            right: -70px;
            top: -110px;
            background: rgba(96,165,250,.16);
        }
        .home-eyebrow {
            color: #93c5fd;
            font-size: .73rem;
            font-weight: 900;
            letter-spacing: .12em;
        }
        .home-title {
            margin-top: 7px;
            font-size: 2rem;
            line-height: 1.25;
            font-weight: 900;
            letter-spacing: -.045em;
        }
        .home-subtitle {
            max-width: 720px;
            margin-top: 9px;
            color: #cbd5e1;
            font-size: .9rem;
            line-height: 1.65;
            font-weight: 600;
        }
        .home-hero-note {
            position: relative;
            z-index: 1;
            border: 1px solid rgba(255,255,255,.16);
            border-radius: 14px;
            padding: 15px 16px;
            background: rgba(255,255,255,.08);
            backdrop-filter: blur(8px);
        }
        .home-hero-note-label { color: #bfdbfe; font-size: .7rem; font-weight: 900; }
        .home-hero-note-value { margin-top: 5px; color: #fff; font-size: 1rem; font-weight: 900; }
        .home-hero-note-desc { margin-top: 5px; color: #cbd5e1; font-size: .72rem; line-height: 1.5; }
        .home-kpi-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 11px;
            margin: 14px 0 22px 0;
        }
        .home-kpi {
            padding: 15px 16px;
            border: 1px solid var(--slate-200);
            border-radius: 13px;
            background: var(--surface);
        }
        .home-kpi-label { color: var(--slate-500); font-size: .7rem; font-weight: 800; }
        .home-kpi-value { margin-top: 4px; color: var(--slate-950); font-size: 1.15rem; font-weight: 900; letter-spacing: -.03em; }
        .home-card-title { color: var(--slate-950); font-size: 1rem; font-weight: 900; letter-spacing: -.025em; }
        .home-card-desc { min-height: 37px; margin-top: 6px; color: var(--slate-500); font-size: .76rem; line-height: 1.5; font-weight: 600; }
        .home-trust-note {
            margin-top: 18px;
            padding: 13px 15px;
            border: 1px solid #bbf7d0;
            border-radius: 12px;
            background: #f0fdf4;
            color: #166534;
            font-size: .77rem;
            line-height: 1.55;
            font-weight: 700;
        }

        /* v39 역할 기반 예산집행 업무공간 */
        .workspace-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 18px;
            padding: 17px 19px;
            margin-bottom: 14px;
            border: 1px solid var(--slate-200);
            border-radius: 13px;
            background: var(--surface);
            box-shadow: 0 2px 8px rgba(15,23,42,.035);
        }
        .workspace-eyebrow {
            color: var(--blue-600);
            font-size: .68rem;
            font-weight: 900;
            letter-spacing: .1em;
        }
        .workspace-title {
            margin-top: 3px;
            color: var(--slate-950);
            font-size: 1.45rem;
            line-height: 1.25;
            font-weight: 900;
            letter-spacing: -.04em;
        }
        .workspace-subtitle {
            margin-top: 4px;
            color: var(--slate-500);
            font-size: .78rem;
            font-weight: 650;
        }
        .context-chips {
            display: flex;
            flex-wrap: wrap;
            justify-content: flex-end;
            gap: 7px;
        }
        .context-chip {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 6px 9px;
            border: 1px solid var(--slate-200);
            border-radius: 8px;
            background: #f8fafc;
            color: var(--slate-700);
            font-size: .7rem;
            font-weight: 800;
            white-space: nowrap;
        }
        .ops-kpi-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 10px;
            margin: 0 0 15px 0;
        }
        .ops-kpi {
            min-height: 105px;
            padding: 15px 16px;
            border: 1px solid var(--slate-200);
            border-radius: 12px;
            background: var(--surface);
        }
        .ops-kpi-accent { border-top: 3px solid var(--blue-600); }
        .ops-kpi-label {
            color: var(--slate-500);
            font-size: .72rem;
            font-weight: 850;
        }
        .ops-kpi-value {
            margin-top: 7px;
            color: var(--slate-950);
            font-size: 1.35rem;
            line-height: 1.2;
            font-weight: 900;
            letter-spacing: -.035em;
        }
        .ops-kpi-foot {
            margin-top: 6px;
            color: var(--slate-500);
            font-size: .68rem;
            font-weight: 700;
        }
        .ops-panel {
            height: 100%;
            padding: 17px 18px;
            border: 1px solid var(--slate-200);
            border-radius: 13px;
            background: var(--surface);
            box-shadow: 0 2px 8px rgba(15,23,42,.025);
        }
        .ops-panel-title {
            color: var(--slate-950);
            font-size: .96rem;
            font-weight: 900;
        }
        .ops-panel-desc {
            margin-top: 3px;
            color: var(--slate-500);
            font-size: .72rem;
            line-height: 1.45;
            font-weight: 650;
        }
        .queue-list { margin-top: 12px; border-top: 1px solid var(--slate-200); }
        .queue-row {
            display: grid;
            grid-template-columns: 8px minmax(0,1fr) auto;
            align-items: center;
            gap: 10px;
            min-height: 48px;
            border-bottom: 1px solid var(--slate-100);
        }
        .queue-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--blue-600); }
        .queue-dot.warn { background: #d97706; }
        .queue-dot.danger { background: #dc2626; }
        .queue-dot.ok { background: #059669; }
        .queue-label { color: var(--slate-700); font-size: .76rem; font-weight: 800; }
        .queue-value { color: var(--slate-950); font-size: .78rem; font-weight: 900; white-space: nowrap; }
        .pipeline {
            display: grid;
            grid-template-columns: repeat(5, minmax(0,1fr));
            gap: 7px;
            margin-top: 12px;
        }
        .pipeline-step {
            position: relative;
            min-height: 82px;
            padding: 11px 10px;
            border: 1px solid var(--slate-200);
            border-radius: 10px;
            background: #f8fafc;
        }
        .pipeline-step:not(:last-child):after {
            content: "›";
            position: absolute;
            right: -7px;
            top: 28px;
            z-index: 2;
            color: #94a3b8;
            font-size: 1rem;
            font-weight: 900;
        }
        .pipeline-index { color: var(--blue-600); font-size: .62rem; font-weight: 900; letter-spacing: .05em; }
        .pipeline-label { margin-top: 3px; color: var(--slate-950); font-size: .74rem; font-weight: 900; }
        .pipeline-value { margin-top: 5px; color: var(--slate-500); font-size: .66rem; line-height: 1.35; font-weight: 700; }
        .source-note {
            padding: 12px 14px;
            border: 1px solid #bfdbfe;
            border-radius: 10px;
            background: #eff6ff;
            color: #1e3a8a;
            font-size: .74rem;
            line-height: 1.55;
            font-weight: 750;
        }
        .term-note {
            padding: 11px 13px;
            border-left: 3px solid #64748b;
            background: #f8fafc;
            color: #475569;
            font-size: .72rem;
            line-height: 1.55;
            font-weight: 700;
        }
        div[data-testid="stTabs"] button[role="tab"] {
            font-weight: 850;
        }

        @media (max-width: 1100px) {
            .block-container { padding-left: 1.25rem; padding-right: 1.25rem; }
            .home-hero { grid-template-columns: 1fr; }
            .home-kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .page-head { flex-direction: column; }
            .workspace-bar { align-items: flex-start; flex-direction: column; }
            .context-chips { justify-content: flex-start; }
            .ops-kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .pipeline { grid-template-columns: repeat(2, minmax(0,1fr)); }
        }
        @media (max-width: 700px) {
            .block-container { padding: 1rem .85rem 3rem .85rem; }
            .page-title { font-size: 1.45rem; }
            .home-hero { padding: 23px 20px; border-radius: 14px; }
            .home-title { font-size: 1.55rem; }
            .home-kpi-grid { grid-template-columns: 1fr 1fr; gap: 8px; }
            .home-kpi { padding: 12px; }
            .home-kpi-value { font-size: .94rem; }
            .metric-value { font-size: 1.42rem; }
            .ops-kpi-grid { grid-template-columns: 1fr 1fr; gap: 8px; }
            .ops-kpi { min-height: 96px; padding: 12px; }
            .ops-kpi-value { font-size: 1.05rem; }
            .pipeline { grid-template-columns: 1fr; }
            .pipeline-step:not(:last-child):after { display: none; }
        }
    </style>
""", unsafe_allow_html=True)



# -----------------------------------------------------------------------------
# 앱 홈 화면 / 페이지 이동 유틸리티
# -----------------------------------------------------------------------------
PAGE_OVERVIEW = "📊 예산집행 현황"
PAGE_ANALYSIS = "📈 집행항목 분석"
PAGE_MISSING = "⚠️ 미집행 점검"
PAGE_IMPORT = "⇄ 일상경비 자료연계"
PAGE_QUICK = "⚡ 신속집행 관리"
PAGE_QUANT = "▦ 월별 정량실적"

APP_PAGES = [
    PAGE_OVERVIEW,
    PAGE_ANALYSIS,
    PAGE_MISSING,
    PAGE_IMPORT,
    PAGE_QUICK,
    PAGE_QUANT,
]

APP_PAGE_META = {
    PAGE_OVERVIEW: {"icon": "📊", "title": "예산집행 현황", "desc": "지출액 기준 누계·월별 추이·통합 집계"},
    PAGE_ANALYSIS: {"icon": "📈", "title": "집행항목 분석", "desc": "집행관리 항목별 3개년 동월누계 비교"},
    PAGE_MISSING: {"icon": "⚠️", "title": "미집행 점검", "desc": "월별 미집행 확인대상·예외 처리"},
    PAGE_IMPORT: {"icon": "⇄", "title": "일상경비 자료연계", "desc": "엑셀 검증·분류·중복 확인·월별 집계 반영"},
    PAGE_QUICK: {"icon": "⚡", "title": "신속집행 관리", "desc": "지출액 기준 행안부·남양주시 목표 비교"},
    PAGE_QUANT: {"icon": "▦", "title": "월별 정량실적", "desc": "예산계층별 월별 지출액 선택 집계"},
}

APP_NAV_GROUPS = [
    ("현황·분석", [PAGE_OVERVIEW, PAGE_ANALYSIS]),
    ("점검·자료연계", [PAGE_MISSING, PAGE_IMPORT]),
    ("성과·정량관리", [PAGE_QUICK, PAGE_QUANT]),
]

def go_page(page_name: str):
    st.session_state["current_page"] = page_name


def get_daily_quality_snapshot(rows):
    """저장된 일상경비 원천내역의 분류·중복·일자 품질을 화면용으로 계산합니다."""
    mapped_count = 0
    unmapped_count = 0
    invalid_date_count = 0
    duplicate_count = 0
    seen = set()

    for row in rows:
        desc = str(row.get("적요", "")).strip()
        amount = clean_numeric(row.get("집행금액", 0))
        mapped = force_mapped_category_for_known_cases(
            desc,
            row.get("세목", ""),
            row.get("예산과목", ""),
            amount,
        ) or get_mapped_category(desc, row.get("세목", ""), row.get("예산과목", ""))
        if mapped:
            mapped_count += 1
        else:
            unmapped_count += 1

        date_digits = re.sub(r"[^0-9]", "", str(row.get("집행일자", "")))
        valid_date = False
        if len(date_digits) >= 6:
            try:
                valid_date = 1 <= int(date_digits[4:6]) <= 12
            except Exception:
                valid_date = False
        if not valid_date:
            invalid_date_count += 1

        key = (
            str(row.get("집행일자", "")).strip(),
            re.sub(r"\s+", "", desc).lower(),
            int(round(float(amount))),
        )
        if key in seen:
            duplicate_count += 1
        else:
            seen.add(key)

    return {
        "mapped": mapped_count,
        "unmapped": unmapped_count,
        "invalid_date": invalid_date_count,
        "duplicates": duplicate_count,
    }


def render_app_home(df_all):
    """예산담당자가 접속 직후 확인·처리할 일을 보여주는 역할 기반 업무 홈."""
    total_2026 = 0
    latest_month = "-"
    latest_month_num = 0
    master_rows = 0
    yoy_gap = 0
    yoy_rate_text = "-"
    missing_month_total = 0
    df_26_home = pd.DataFrame()

    if df_all is not None and not df_all.empty and "year" in df_all.columns:
        df_26_home = df_all[df_all["year"] == 2026].copy()
        if not df_26_home.empty:
            master_rows = len(df_26_home)
            amount_series = pd.to_numeric(df_26_home.get("amount", 0), errors="coerce").fillna(0)
            total_2026 = float(amount_series.sum())
            if "month" in df_26_home.columns and not df_26_home[amount_series > 0].empty:
                latest_month_num = int(df_26_home.loc[amount_series > 0, "month"].max())
                latest_month = f"{latest_month_num}월"

            compare_month = latest_month_num or min(max(datetime.now().month - 1, 1), 12)
            current_same = float(
                df_all[
                    (df_all["year"] == 2026) & (df_all["month"] <= compare_month)
                ]["amount"].sum()
            )
            prior_same = float(
                df_all[
                    (df_all["year"] == 2025) & (df_all["month"] <= compare_month)
                ]["amount"].sum()
            )
            yoy_gap = current_same - prior_same
            yoy_rate_text = f"{(yoy_gap / prior_same * 100):+.1f}%" if prior_same else "-"

            check_until = min(max(datetime.now().month - 1, 1), 12) if datetime.now().year == 2026 else 12
            for category in CATEGORIES:
                for month in range(1, check_until + 1):
                    month_amount = df_26_home[
                        (df_26_home["category"] == category) & (df_26_home["month"] == month)
                    ]["amount"].sum()
                    if float(month_amount) <= 0:
                        missing_month_total += 1

    daily_rows = st.session_state.get("daily_expenses", [])
    daily_count = len(daily_rows)
    quality = get_daily_quality_snapshot(daily_rows)
    storage_mode = "클라우드·로컬 이중 저장" if db is not None and not st.session_state.get("quota_exceeded") else "로컬 JSON 저장"

    total_quick_target = float(sum(cfg.get("target", 0) for cfg in QUICK_EXEC_CONFIG.values()))
    quick_actual = 0.0
    if not df_26_home.empty:
        for category, cfg in QUICK_EXEC_CONFIG.items():
            category_actual = float(
                df_26_home[
                    (df_26_home["category"] == category) & (df_26_home["month"] <= 6)
                ]["amount"].sum()
            )
            quick_actual += min(category_actual, float(cfg.get("target", 0)))
    city_quick_goal = total_quick_target * QUICK_EXEC_GOAL_RATES["남양주시"]["h1"]
    quick_gap = max(city_quick_goal - quick_actual, 0)
    quick_rate = (quick_actual / total_quick_target * 100) if total_quick_target else 0

    latest_exec_amount = 0.0
    if latest_month_num and not df_26_home.empty:
        latest_exec_amount = float(df_26_home[df_26_home["month"] == latest_month_num]["amount"].sum())

    st.markdown(
        f"""
        <div class="workspace-bar">
            <div>
                <div class="workspace-eyebrow">BUDGET EXECUTION WORKSPACE</div>
                <div class="workspace-title">2026 예산집행 업무 개요</div>
                <div class="workspace-subtitle">정약용 펀그라운드 · 지출액 검증, 월별 집계, 미집행 점검, 신속집행 관리</div>
            </div>
            <div class="context-chips">
                <span class="context-chip">회계연도 2026</span>
                <span class="context-chip">지출액 기준</span>
                <span class="context-chip">표시단위 원</span>
                <span class="context-chip">{storage_mode}</span>
            </div>
        </div>
        <div class="ops-kpi-grid">
            <div class="ops-kpi ops-kpi-accent">
                <div class="ops-kpi-label">누적 집행액</div>
                <div class="ops-kpi-value">{int(total_2026):,}원</div>
                <div class="ops-kpi-foot">지급명령·지출액 기준</div>
            </div>
            <div class="ops-kpi">
                <div class="ops-kpi-label">최근 집행월</div>
                <div class="ops-kpi-value">{latest_month}</div>
                <div class="ops-kpi-foot">{int(latest_exec_amount):,}원 집행</div>
            </div>
            <div class="ops-kpi">
                <div class="ops-kpi-label">전년 동월누계 증감</div>
                <div class="ops-kpi-value">{int(yoy_gap):+,}원</div>
                <div class="ops-kpi-foot">2025년 대비 {yoy_rate_text}</div>
            </div>
            <div class="ops-kpi">
                <div class="ops-kpi-label">일상경비 원천내역</div>
                <div class="ops-kpi-value">{daily_count:,}건</div>
                <div class="ops-kpi-foot">월별 집계 반영 {quality["mapped"]:,}건</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    trend_col, queue_col = st.columns([1.7, 1.0])
    with trend_col:
        with st.container(border=True):
            st.markdown('<div class="ops-panel-title">월별 지출액 추이</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="ops-panel-desc">2025년과 2026년의 월별 지출액을 같은 기준으로 비교합니다.</div>',
                unsafe_allow_html=True,
            )
            if df_all is not None and not df_all.empty:
                trend = (
                    df_all[df_all["year"].isin([2025, 2026])]
                    .groupby(["year", "month"], as_index=False)["amount"]
                    .sum()
                )
                trend["연도"] = trend["year"].astype(str) + "년"
                trend["월"] = trend["month"].astype(int).astype(str) + "월"
                trend_chart = (
                    alt.Chart(trend)
                    .mark_line(point=alt.OverlayMarkDef(size=52), strokeWidth=3)
                    .encode(
                        x=alt.X("month:O", title=None, axis=alt.Axis(labelExpr="datum.value + '월'")),
                        y=alt.Y("amount:Q", title="지출액(원)", axis=alt.Axis(format=",.0f")),
                        color=alt.Color(
                            "연도:N",
                            scale=alt.Scale(domain=["2025년", "2026년"], range=["#94a3b8", "#2563eb"]),
                            legend=alt.Legend(title=None, orient="top"),
                        ),
                        tooltip=[
                            alt.Tooltip("연도:N"),
                            alt.Tooltip("월:N"),
                            alt.Tooltip("amount:Q", title="지출액", format=",.0f"),
                        ],
                    )
                    .properties(height=285)
                )
                st.altair_chart(trend_chart, width="stretch")
            else:
                st.info("표시할 월별 지출액이 없습니다.")

    with queue_col:
        st.markdown(
            f"""
            <div class="ops-panel">
                <div class="ops-panel-title">업무 확인대상</div>
                <div class="ops-panel-desc">오류로 확정하지 않고 담당자가 먼저 확인해야 할 항목입니다.</div>
                <div class="queue-list">
                    <div class="queue-row"><span class="queue-dot {"danger" if missing_month_total else "ok"}"></span><span class="queue-label">미집행 확인대상</span><span class="queue-value">{missing_month_total:,}개월</span></div>
                    <div class="queue-row"><span class="queue-dot {"warn" if quality["unmapped"] else "ok"}"></span><span class="queue-label">미분류 원천내역</span><span class="queue-value">{quality["unmapped"]:,}건</span></div>
                    <div class="queue-row"><span class="queue-dot {"warn" if quality["invalid_date"] else "ok"}"></span><span class="queue-label">일자 확인 필요</span><span class="queue-value">{quality["invalid_date"]:,}건</span></div>
                    <div class="queue-row"><span class="queue-dot {"warn" if quick_gap else "ok"}"></span><span class="queue-label">상반기 신속집행</span><span class="queue-value">{quick_rate:.1f}%</span></div>
                    <div class="queue-row"><span class="queue-dot {"danger" if quality["duplicates"] else "ok"}"></span><span class="queue-label">저장내역 중복 후보</span><span class="queue-value">{quality["duplicates"]:,}건</span></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    action_cols = st.columns(3)
    if action_cols[0].button("일상경비 자료 가져오기", key="home_action_import", width="stretch", type="primary"):
        go_page(PAGE_IMPORT)
        st.rerun()
    if action_cols[1].button("미집행 확인대상 점검", key="home_action_missing", width="stretch"):
        go_page(PAGE_MISSING)
        st.rerun()
    if action_cols[2].button("신속집행 목표 확인", key="home_action_quick", width="stretch"):
        go_page(PAGE_QUICK)
        st.rerun()

    st.markdown('<div class="section-header">자료 반영 구조</div>', unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="ops-panel">
            <div class="ops-panel-title">엑셀 원본부터 화면 집계까지</div>
            <div class="ops-panel-desc">원본 행은 보존하고, 분류·중복검사 결과를 거쳐 월별 집계만 다시 계산합니다.</div>
            <div class="pipeline">
                <div class="pipeline-step"><div class="pipeline-index">STEP 01</div><div class="pipeline-label">엑셀·CSV</div><div class="pipeline-value">전체 시트 자동 탐색</div></div>
                <div class="pipeline-step"><div class="pipeline-index">STEP 02</div><div class="pipeline-label">열 인식</div><div class="pipeline-value">일자·적요·금액·예산과목</div></div>
                <div class="pipeline-step"><div class="pipeline-index">STEP 03</div><div class="pipeline-label">검증·분류</div><div class="pipeline-value">{quality["mapped"]:,}건 분류 · {quality["unmapped"]:,}건 확인</div></div>
                <div class="pipeline-step"><div class="pipeline-index">STEP 04</div><div class="pipeline-label">중복 제거</div><div class="pipeline-value">일자·적요·금액 기준</div></div>
                <div class="pipeline-step"><div class="pipeline-index">STEP 05</div><div class="pipeline-label">월별 집계</div><div class="pipeline-value">마스터 {master_rows:,}행 · 화면 공통 반영</div></div>
            </div>
        </div>
        <div class="term-note" style="margin-top:10px;">
            ‘집행관리 항목’은 앱의 분석용 분류이며 공식 예산세목과 다릅니다.
            공기청정기·비데 관리용역 등은 원본 예산과목을 보존한 채 별도의 관리항목으로만 표시합니다.
        </div>
        """,
        unsafe_allow_html=True,
    )

# -----------------------------------------------------------------------------
# 7. 메인 UI
# -----------------------------------------------------------------------------
if "current_page" not in st.session_state:
    st.session_state["current_page"] = "HOME"

with st.sidebar:
    st.markdown(
        """
        <div class="sidebar-brand">
            <div class="sidebar-brand-kicker">Budget Execution</div>
            <div class="sidebar-brand-title">2026 예산집행관리</div>
            <div class="sidebar-brand-sub">정약용 펀그라운드 · v39 · 지출액 기준</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    is_home = st.session_state.get("current_page") == "HOME"
    if st.button(
        "⌂  업무 개요",
        key="sidebar_nav_HOME",
        width="stretch",
        type="primary" if is_home else "secondary",
    ):
        st.session_state["current_page"] = "HOME"
        st.rerun()

    for group_name, group_pages in APP_NAV_GROUPS:
        st.caption(group_name)
        for nav_value in group_pages:
            nav_label = nav_value
            is_active = st.session_state.get("current_page") == nav_value
            if st.button(
                nav_label,
                key=f"sidebar_nav_{nav_value}",
                width="stretch",
                type="primary" if is_active else "secondary",
            ):
                st.session_state["current_page"] = nav_value
                st.rerun()

    storage_mode = "클라우드 동기화" if db is not None and not st.session_state.get("quota_exceeded") else "로컬 JSON 저장"
    st.markdown(
        f'<div class="sidebar-status"><span class="status-dot"></span><span>{storage_mode}</span></div>',
        unsafe_allow_html=True,
    )
    with st.expander("자료·저장 관리", expanded=False):
        st.caption("현재 집계와 원천내역을 다시 불러오거나 저장합니다.")
        if st.button("현재 집계 저장", key="sidebar_backup", width="stretch"):
            if save_data_cloud(st.session_state["data"]):
                st.success("저장 완료")
        if st.button("저장자료 다시 불러오기", key="sidebar_reload", width="stretch"):
            st.session_state["data"] = load_data()
            st.session_state["daily_expenses"] = load_daily_expenses()
            st.rerun()
        st.caption(f"시스템 ID · {appId}")

if st.session_state.get("current_page") == "HOME":
    render_app_home(df_all)
    st.stop()

current_page = st.session_state.get("current_page", PAGE_OVERVIEW)
current_meta = APP_PAGE_META.get(
    current_page,
    {"icon": "▦", "title": current_page, "desc": "2026 예산집행 업무 화면"},
)
st.markdown(
    f"""
    <div class="page-head">
        <div>
            <div class="page-kicker">2026 예산집행관리 / {current_meta["title"]}</div>
            <div class="page-title">{current_meta["icon"]} {current_meta["title"]}</div>
            <div class="page-desc">{current_meta["desc"]}</div>
        </div>
        <div class="context-chips">
            <span class="context-chip">회계연도 2026</span>
            <span class="context-chip">지출액 기준</span>
            <span class="context-chip">단위 원</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# --- TAB 1: 실적 현황 ---
if current_page == PAGE_OVERVIEW:
    if not df_all.empty:
        df_26 = df_all[df_all["year"] == 2026].copy()
    else:
        df_26 = pd.DataFrame(columns=["year", "month", "category", "amount"])

    val_26 = pd.to_numeric(df_26.get("amount", 0), errors="coerce").fillna(0).sum() if not df_26.empty else 0
    positive_26 = df_26[pd.to_numeric(df_26.get("amount", 0), errors="coerce").fillna(0) > 0].copy() if not df_26.empty else pd.DataFrame()
    latest_exec_month = int(positive_26["month"].max()) if not positive_26.empty else 0
    latest_exec_amount = (
        pd.to_numeric(df_26[df_26["month"] == latest_exec_month]["amount"], errors="coerce").fillna(0).sum()
        if latest_exec_month else 0
    )
    active_category_count = int(positive_26["category"].nunique()) if not positive_26.empty else 0
    daily_rows = st.session_state.get("daily_expenses", [])
    quality = get_daily_quality_snapshot(daily_rows)
    compare_month = latest_exec_month or min(max(datetime.now().month - 1, 1), 12)
    prior_ytd = (
        float(df_all[(df_all["year"] == 2025) & (df_all["month"] <= compare_month)]["amount"].sum())
        if not df_all.empty else 0.0
    )
    current_ytd = (
        float(df_all[(df_all["year"] == 2026) & (df_all["month"] <= compare_month)]["amount"].sum())
        if not df_all.empty else 0.0
    )
    yoy_gap = current_ytd - prior_ytd
    yoy_rate = (yoy_gap / prior_ytd * 100) if prior_ytd else None

    check_until = min(max(datetime.now().month - 1, 1), 12) if datetime.now().year == 2026 else 12
    missing_count = 0
    if not df_26.empty:
        for category in CATEGORIES:
            for month in range(1, check_until + 1):
                amount = df_26[
                    (df_26["category"] == category) & (df_26["month"] == month)
                ]["amount"].sum()
                if float(amount) <= 0:
                    missing_count += 1

    st.markdown(
        f"""
        <div class="ops-kpi-grid">
            <div class="ops-kpi ops-kpi-accent"><div class="ops-kpi-label">누적 집행액</div><div class="ops-kpi-value">{int(val_26):,}원</div><div class="ops-kpi-foot">지출액 기준</div></div>
            <div class="ops-kpi"><div class="ops-kpi-label">최근 월 집행액</div><div class="ops-kpi-value">{int(latest_exec_amount):,}원</div><div class="ops-kpi-foot">{f"{latest_exec_month}월" if latest_exec_month else "집행내역 없음"}</div></div>
            <div class="ops-kpi"><div class="ops-kpi-label">전년 동월누계 증감</div><div class="ops-kpi-value">{int(yoy_gap):+,}원</div><div class="ops-kpi-foot">{f"{yoy_rate:+.1f}%" if yoy_rate is not None else "전년 비교값 없음"} · 1~{compare_month}월</div></div>
            <div class="ops-kpi"><div class="ops-kpi-label">집행 발생 항목</div><div class="ops-kpi-value">{active_category_count} / {len(CATEGORIES)}개</div><div class="ops-kpi-foot">미집행 확인대상 {missing_count}개월</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    trend_col, review_col = st.columns([1.75, 1.0])
    with trend_col:
        with st.container(border=True):
            st.markdown("#### 월별 지출액 추이")
            st.caption("2025년과 2026년의 월별 지출액을 동일한 집행관리 항목 기준으로 비교합니다.")
            if not df_all.empty:
                monthly = (
                    df_all[df_all["year"].isin([2025, 2026])]
                    .groupby(["year", "month"], as_index=False)["amount"]
                    .sum()
                )
                monthly["연도"] = monthly["year"].astype(str) + "년"
                chart = (
                    alt.Chart(monthly)
                    .mark_line(point=True, strokeWidth=3)
                    .encode(
                        x=alt.X("month:O", title=None, axis=alt.Axis(labelExpr="datum.value + '월'")),
                        y=alt.Y("amount:Q", title="지출액(원)", axis=alt.Axis(format=",.0f")),
                        color=alt.Color(
                            "연도:N",
                            scale=alt.Scale(domain=["2025년", "2026년"], range=["#94a3b8", "#2563eb"]),
                            legend=alt.Legend(title=None, orient="top"),
                        ),
                        tooltip=[
                            alt.Tooltip("연도:N"),
                            alt.Tooltip("month:O", title="월"),
                            alt.Tooltip("amount:Q", title="지출액", format=",.0f"),
                        ],
                    )
                    .properties(height=305)
                )
                st.altair_chart(chart, width="stretch")

    with review_col:
        st.markdown(
            f"""
            <div class="ops-panel">
                <div class="ops-panel-title">자료 품질·확인 현황</div>
                <div class="ops-panel-desc">집계 전에 확인할 원천내역과 월별 미집행 상태입니다.</div>
                <div class="queue-list">
                    <div class="queue-row"><span class="queue-dot {"warn" if missing_count else "ok"}"></span><span class="queue-label">미집행 확인대상</span><span class="queue-value">{missing_count}개월</span></div>
                    <div class="queue-row"><span class="queue-dot {"warn" if quality["unmapped"] else "ok"}"></span><span class="queue-label">미분류 원천내역</span><span class="queue-value">{quality["unmapped"]}건</span></div>
                    <div class="queue-row"><span class="queue-dot {"warn" if quality["invalid_date"] else "ok"}"></span><span class="queue-label">일자 확인 필요</span><span class="queue-value">{quality["invalid_date"]}건</span></div>
                    <div class="queue-row"><span class="queue-dot ok"></span><span class="queue-label">분류 완료 원천내역</span><span class="queue-value">{quality["mapped"]}건</span></div>
                    <div class="queue-row"><span class="queue-dot {"danger" if quality["duplicates"] else "ok"}"></span><span class="queue-label">저장내역 중복 후보</span><span class="queue-value">{quality["duplicates"]}건</span></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        nav_a, nav_b = st.columns(2)
        if nav_a.button("자료연계", key="overview_to_import", width="stretch"):
            go_page(PAGE_IMPORT)
            st.rerun()
        if nav_b.button("미집행 점검", key="overview_to_missing", width="stretch"):
            go_page(PAGE_MISSING)
            st.rerun()

    composition_tab, manual_tab, quick_tab = st.tabs(["지출 구성", "지출액 직접 조정", "신속집행 요약"])
    with composition_tab:
        cat_dist = df_26.groupby("category", as_index=False)["amount"].sum() if not df_26.empty else pd.DataFrame()
        if not cat_dist.empty and cat_dist["amount"].sum() > 0:
            cat_dist = cat_dist[cat_dist["amount"] > 0].copy()
            total_dist_amount = float(cat_dist["amount"].sum())
            cat_dist["share"] = cat_dist["amount"] / total_dist_amount * 100
            cat_dist["category_label"] = cat_dist["category"].map(display_category)
            chart_dist = cat_dist.sort_values("amount", ascending=False).head(10)
            bars = (
                alt.Chart(chart_dist)
                .mark_bar(cornerRadiusEnd=5, size=21, color="#2563eb")
                .encode(
                    y=alt.Y("category_label:N", sort="-x", title=None, axis=alt.Axis(labelLimit=240)),
                    x=alt.X("amount:Q", title="지출액(원)", axis=alt.Axis(format=",.0f")),
                    tooltip=[
                        alt.Tooltip("category_label:N", title="집행관리 항목"),
                        alt.Tooltip("amount:Q", title="지출액", format=",.0f"),
                        alt.Tooltip("share:Q", title="비중", format=".1f"),
                    ],
                )
                .properties(height=max(280, len(chart_dist) * 34))
            )
            st.altair_chart(bars, width="stretch")
        else:
            st.info("2026년 지출액이 아직 없습니다.")

    with manual_tab:
        st.markdown("#### 월별 지출액 추가 반영")
        st.caption("2026년 조정분은 원천내역에도 저장되어 자료 재집계 후에도 유지됩니다. 2024·2025년은 비교용 월별 집계에 반영됩니다.")
        iy_col, ic_col, im_col = st.columns([1, 2, 1])
        iy = iy_col.selectbox("회계연도", YEARS, index=2, key="y_reg")
        ic = ic_col.selectbox("집행관리 항목", CATEGORIES, key="c_reg", format_func=display_category)
        im = im_col.selectbox("귀속월", MONTHS, format_func=lambda x: f"{x}월", key="m_reg")
        st.number_input("추가 반영액(원)", min_value=0, step=10000, key="amt_box")
        st.markdown(f'<div class="korean-amount">{number_to_korean(st.session_state.amt_box)}</div>', unsafe_allow_html=True)
        bc1, bc2, bc3, bc4 = st.columns([1, 1, 1, 2])
        bc1.button("+10만", on_click=update_amt, args=(100000,))
        bc2.button("+100만", on_click=update_amt, args=(1000000,))
        bc3.button("입력 초기화", on_click=reset_amt)
        bc4.button("지출액 조정 반영", type="primary", on_click=save_and_register, args=(iy, ic, im), width="stretch")

    with quick_tab:
        st.caption("각 관리대상 항목의 실적은 대상액을 초과하더라도 달성률 계산상 100%까지만 인정합니다.")
        quick_cols = st.columns(3)
        for idx, cat in enumerate(["수탁자산취득비", "일반재료비", "상품매입비"]):
            df_t = df_26[df_26["category"] == cat] if not df_26.empty else pd.DataFrame()
            conf = QUICK_EXEC_CONFIG.get(cat, {"target": 0})
            q1_v = pd.to_numeric(df_t[df_t["month"] <= 3]["amount"], errors="coerce").fillna(0).sum() if not df_t.empty else 0
            h1_v = pd.to_numeric(df_t[df_t["month"] <= 6]["amount"], errors="coerce").fillna(0).sum() if not df_t.empty else 0
            q1_v = min(float(q1_v), float(conf.get("target", 0)))
            h1_v = min(float(h1_v), float(conf.get("target", 0)))
            with quick_cols[idx]:
                st.markdown(render_overview_quick_exec_card(display_category(cat), q1_v, h1_v, conf), unsafe_allow_html=True)

    st.markdown('<div class="section-header">2026 월별 지출액 통합표</div>', unsafe_allow_html=True)
    st.caption("행은 앱의 집행관리 항목이며 공식 예산세목과 다릅니다. 셀을 수정하면 해당 월별 집계값이 변경됩니다.")
    if not df_all.empty:
        df_p = df_26.pivot(index="category", columns="month", values="amount").fillna(0).reindex(index=CATEGORIES, columns=MONTHS, fill_value=0)
        df_p.columns = [f"{m}월" for m in df_p.columns]
        df_d = df_p.map(lambda x: format(int(x), ","))
        df_d.index = [display_category(value) for value in df_d.index]
        df_d.index.name = "집행관리 항목"
        
        ed = st.data_editor(df_d, height=550, key="main_editor_v292")
        
        if st.button("월별 집계 수정사항 저장", type="primary", key="btn_save_tab1_v292"):
            curr = load_data()
            curr = ensure_data_integrity(curr)
            for cat in CATEGORIES:
                for m in MONTHS:
                    display_name = display_category(cat)
                    if display_name in ed.index and f"{m}월" in ed.columns:
                        val = str(ed.loc[display_name, f"{m}월"]).replace(",", "")
                        clean = clean_numeric(val)
                        for r in curr["records"]:
                            if r["year"] == 2026 and r["category"] == cat and r["month"] == m: 
                                r["amount"] = clean
                                r["status"] = "지출" if clean > 0 else "미지출"
                                break
            if save_data_cloud(curr):
                st.session_state['data'] = curr
                st.success("월별 집계 수정사항을 저장했습니다.")
                time.sleep(0.5)
                st.rerun()

# --- TAB 2: 집행관리 항목 분석 ---
if current_page == PAGE_ANALYSIS:
    tool_col, note_col = st.columns([1, 3])
    with tool_col:
        if st.button("원천내역 다시 집계", type="primary", width="stretch", key="analysis_resync"):
            if sync_daily_to_master_auto():
                st.success("원천내역 기준으로 월별 집계를 갱신했습니다.")
                time.sleep(0.5)
                st.rerun()
    with note_col:
        st.markdown(
            '<div class="term-note">화면의 ‘집행관리 항목’은 분석용 분류입니다. '
            '원본의 예산과목·세목은 일상경비 자료연계 화면에서 별도로 확인할 수 있습니다.</div>',
            unsafe_allow_html=True,
        )

    if not df_all.empty:
        df_2026_all = df_all[df_all["year"] == 2026].copy()
        global_months = df_2026_all[df_2026_all["amount"] > 0]["month"].tolist()
        global_latest_month = max(global_months) if global_months else min(max(datetime.now().month - 1, 1), 12)
        comparison_rows = []

        for category in CATEGORIES:
            category_all = df_all[df_all["category"] == category]
            category_2026 = category_all[category_all["year"] == 2026]
            paid_month_list = category_2026[category_2026["amount"] > 0]["month"].tolist()
            recent_month = max(paid_month_list) if paid_month_list else 0
            compare_month = recent_month or global_latest_month
            values = {}
            for year in YEARS:
                values[year] = float(
                    category_all[
                        (category_all["year"] == year) & (category_all["month"] <= compare_month)
                    ]["amount"].sum()
                )
            gap = values[2026] - values[2025]
            rate = (gap / values[2025] * 100) if values[2025] else None
            comparison_rows.append({
                "집행관리 항목": display_category(category),
                "비교기준": f"1~{compare_month}월",
                "2024년 동월누계": int(values[2024]),
                "2025년 동월누계": int(values[2025]),
                "2026년 동월누계": int(values[2026]),
                "전년대비 증감액": int(gap),
                "전년대비 증감률": rate,
                "최근 집행월": f"{recent_month}월" if recent_month else "-",
                "집행월수": int((category_2026["amount"] > 0).sum()),
                "_category": category,
            })

        summary_df = pd.DataFrame(comparison_rows).sort_values("2026년 동월누계", ascending=False)
        total_2026 = int(summary_df["2026년 동월누계"].sum())
        paid_count = int((summary_df["2026년 동월누계"] > 0).sum())
        zero_count = len(CATEGORIES) - paid_count
        max_row = summary_df.iloc[0] if not summary_df.empty else None

        st.markdown(
            f"""
            <div class="ops-kpi-grid">
                <div class="ops-kpi ops-kpi-accent"><div class="ops-kpi-label">2026 동월누계 집행액</div><div class="ops-kpi-value">{total_2026:,}원</div><div class="ops-kpi-foot">항목별 최근 집행월 기준</div></div>
                <div class="ops-kpi"><div class="ops-kpi-label">집행 발생 항목</div><div class="ops-kpi-value">{paid_count}개</div><div class="ops-kpi-foot">전체 {len(CATEGORIES)}개</div></div>
                <div class="ops-kpi"><div class="ops-kpi-label">미집행 항목</div><div class="ops-kpi-value">{zero_count}개</div><div class="ops-kpi-foot">확인 후 예외 처리 가능</div></div>
                <div class="ops-kpi"><div class="ops-kpi-label">최대 집행 항목</div><div class="ops-kpi-value" style="font-size:1rem;">{max_row["집행관리 항목"] if max_row is not None else "-"}</div><div class="ops-kpi-foot">{int(max_row["2026년 동월누계"]) if max_row is not None else 0:,}원</div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("#### 집행관리 항목별 3개년 동월누계")
        st.caption("각 항목의 2026년 최근 집행월까지를 기준으로 2024년·2025년 같은 기간과 비교합니다.")
        summary_display = summary_df.drop(columns=["_category"]).copy()
        summary_display["전년대비 증감률"] = summary_display["전년대비 증감률"].apply(
            lambda value: "-" if pd.isna(value) else f"{value:+.1f}%"
        )
        st.dataframe(
            summary_display,
            width="stretch",
            hide_index=True,
            height=470,
            column_config={
                "2024년 동월누계": st.column_config.NumberColumn(format="%,d원"),
                "2025년 동월누계": st.column_config.NumberColumn(format="%,d원"),
                "2026년 동월누계": st.column_config.NumberColumn(format="%,d원"),
                "전년대비 증감액": st.column_config.NumberColumn(format="%+d원"),
            },
        )
    else:
        st.info("표시할 월별 지출액이 없습니다.")

    st.markdown('<div class="section-header">선택 항목 상세 분석</div>', unsafe_allow_html=True)
    sc = st.selectbox(
        "집행관리 항목",
        CATEGORIES,
        key="analysis_sel_v292",
        format_func=display_category,
    )
    df_c = df_all[df_all["category"] == sc] if not df_all.empty else pd.DataFrame()

    if not df_c.empty:
        if sc in QUICK_EXEC_CONFIG:
            cf = QUICK_EXEC_CONFIG[sc]
            q1_e = min(
                float(df_c[(df_c["year"] == 2026) & (df_c["month"] <= 3)]["amount"].sum()),
                float(cf["target"]),
            )
            h1_e = min(
                float(df_c[(df_c["year"] == 2026) & (df_c["month"] <= 6)]["amount"].sum()),
                float(cf["target"]),
            )
            st.markdown(
                render_quick_goal_comparison_html(display_category(sc), cf["target"], q1_e, h1_e),
                unsafe_allow_html=True,
            )

        v24 = float(df_c[df_c["year"] == 2024]["amount"].sum())
        v25 = float(df_c[df_c["year"] == 2025]["amount"].sum())
        v26 = float(df_c[df_c["year"] == 2026]["amount"].sum())
        st.markdown(
            f"""
            <div class="ops-kpi-grid" style="grid-template-columns:repeat(3,minmax(0,1fr));">
                <div class="ops-kpi"><div class="ops-kpi-label">2024년 지출액</div><div class="ops-kpi-value">{int(v24):,}원</div></div>
                <div class="ops-kpi"><div class="ops-kpi-label">2025년 지출액</div><div class="ops-kpi-value">{int(v25):,}원</div></div>
                <div class="ops-kpi ops-kpi-accent"><div class="ops-kpi-label">2026년 지출액</div><div class="ops-kpi-value">{int(v26):,}원</div><div class="ops-kpi-foot">원천내역 자동 연계 포함</div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        chart_df = df_c.copy()
        chart_df["연도"] = chart_df["year"].astype(str) + "년"
        detail_chart = (
            alt.Chart(chart_df)
            .mark_line(point=True, strokeWidth=2.5)
            .encode(
                x=alt.X("month:O", title=None, axis=alt.Axis(labelExpr="datum.value + '월'")),
                y=alt.Y("amount:Q", title="지출액(원)", axis=alt.Axis(format=",.0f")),
                color=alt.Color("연도:N", legend=alt.Legend(title=None, orient="top")),
                tooltip=[
                    alt.Tooltip("연도:N"),
                    alt.Tooltip("month:O", title="월"),
                    alt.Tooltip("amount:Q", title="지출액", format=",.0f"),
                ],
            )
            .properties(height=300)
        )
        st.altair_chart(detail_chart, width="stretch")

        df_p_c = (
            df_c.pivot(index="month", columns="year", values="amount")
            .fillna(0)
            .reindex(index=MONTHS, columns=YEARS, fill_value=0)
        )
        df_p_c.columns = [f"{year}년" for year in df_p_c.columns]
        df_d_c = df_p_c.map(lambda value: format(int(value), ",")).reset_index()
        df_d_c["월"] = df_d_c["month"].apply(lambda value: f"{value}월")

        st.caption("아래 표는 비교용 월별 집계입니다. 수정 시 선택한 집행관리 항목의 해당 연도·월 값이 변경됩니다.")
        ed_c = st.data_editor(
            df_d_c[["월", "2024년", "2025년", "2026년"]],
            hide_index=True,
            key=f"ed_v292_{sc}",
            height=450,
            disabled=["월"],
        )

        if st.button("선택 항목 월별 집계 저장", type="primary", key=f"btn_save_tab2_v292_{sc}"):
            curr = ensure_data_integrity(load_data())
            for _, row in ed_c.iterrows():
                month_value = int(str(row["월"]).replace("월", ""))
                for year in YEARS:
                    amount_value = clean_numeric(str(row[f"{year}년"]).replace(",", ""))
                    for record in curr["records"]:
                        if record["year"] == year and record["category"] == sc and record["month"] == month_value:
                            record["amount"] = amount_value
                            record["status"] = "지출" if amount_value > 0 else "미지출"
                            break
            if save_data_cloud(curr):
                st.session_state["data"] = curr
                st.success("선택 항목의 월별 집계를 저장했습니다.")
                time.sleep(0.5)
                st.rerun()

# --- TAB 3: 미집행 현황 ---
if current_page == PAGE_MISSING:
    CHECK_YEAR = 2026
    OVERRIDE_FILE = "missing_override_2026.json"
    OVERRIDE_LOG_FILE = "missing_override_log_2026.json"

    def load_missing_overrides():
        """사용자가 월별로 누락 제외 처리한 값을 로컬 JSON에서 불러옵니다."""
        if os.path.exists(OVERRIDE_FILE):
            try:
                with open(OVERRIDE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
        return {}

    def save_missing_overrides(data):
        with open(OVERRIDE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def append_missing_override_log(category, month, action, memo=""):
        """누락 제외/해제 이력을 남깁니다. 회사 PC 단독 사용 기준의 로컬 로그입니다."""
        logs = []
        if os.path.exists(OVERRIDE_LOG_FILE):
            try:
                with open(OVERRIDE_LOG_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                logs = loaded if isinstance(loaded, list) else []
            except Exception:
                logs = []
        logs.append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "year": CHECK_YEAR,
            "category": category,
            "month": int(month),
            "action": action,
            "memo": memo,
        })
        with open(OVERRIDE_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(logs[-300:], f, ensure_ascii=False, indent=2)

    def load_missing_override_logs(limit=30):
        if not os.path.exists(OVERRIDE_LOG_FILE):
            return []
        try:
            with open(OVERRIDE_LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
            return list(reversed(logs[-limit:])) if isinstance(logs, list) else []
        except Exception:
            return []

    def format_month_ranges(months):
        """[1,2,3,5,7,8] -> '1~3월, 5월, 7~8월'"""
        if not months:
            return "-"
        months = sorted(set(int(m) for m in months))
        ranges = []
        start_m = prev_m = months[0]
        for m in months[1:]:
            if m == prev_m + 1:
                prev_m = m
            else:
                ranges.append((start_m, prev_m))
                start_m = prev_m = m
        ranges.append((start_m, prev_m))
        return ", ".join(f"{a}월" if a == b else f"{a}~{b}월" for a, b in ranges)

    def missing_level(count):
        if count >= 9:
            return "장기 미집행", "#be123c", "#fff1f2", "#fecdd3"
        if count >= 5:
            return "주의", "#c2410c", "#fff7ed", "#fed7aa"
        return "확인", "#1d4ed8", "#eff6ff", "#bfdbfe"

    def build_auto_recommendations(category_month_amounts, check_until_month):
        """
        자동추천 기준:
        - 해당 월 금액이 0원이고,
        - 같은 항목에서 이후 1~3개월 안에 실제 지출이 있으면
          '후지급 가능성'으로 누락 제외 후보 추천
        """
        recommendations = {}
        for cat, month_amounts in category_month_amounts.items():
            rec_months = []
            for m in range(1, check_until_month + 1):
                if month_amounts.get(m, 0) > 0:
                    continue
                near_future_paid = any(month_amounts.get(fm, 0) > 0 for fm in range(m + 1, min(check_until_month, m + 3) + 1))
                if near_future_paid:
                    rec_months.append(m)
            if rec_months:
                recommendations[cat] = rec_months
        return recommendations

    st.markdown('<span class="section-label">2026년 월별 미집행 점검</span>', unsafe_allow_html=True)
    now = datetime.now()
    cy, cm = now.year, now.month
    check_until_month = (cm - 1) if cy == CHECK_YEAR else 12
    check_until_month = max(1, min(12, check_until_month))

    st.markdown(
        '<div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:18px; padding:16px 18px; margin-bottom:16px;">'
        '<div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px; flex-wrap:wrap;">'
        '<div>'
        '<div style="font-size:1.05rem; font-weight:950; color:#0f172a;">월별 지출액이 0원인 항목을 확인대상으로 분류합니다.</div>'
        f'<div style="font-size:.86rem; color:#64748b; font-weight:750; margin-top:4px;">점검연도: {CHECK_YEAR}년 · 점검범위: 1~{check_until_month}월 · 후지급·일괄지급 등은 확인대상에서 제외할 수 있습니다.</div>'
        '</div>'
        f'<div style="background:#e0f2fe; color:#075985; border:1px solid #bae6fd; border-radius:999px; padding:8px 13px; font-size:.86rem; font-weight:950; white-space:nowrap;">점검연도 {CHECK_YEAR}</div>'
        '</div>'
        '</div>',
        unsafe_allow_html=True
    )

    if not df_all.empty:
        df_y = df_all[df_all["year"] == CHECK_YEAR].copy()
        df_y["month"] = pd.to_numeric(df_y["month"], errors="coerce").fillna(0).astype(int)
        df_y["amount"] = pd.to_numeric(df_y["amount"], errors="coerce").fillna(0)

        overrides = load_missing_overrides()
        changed = False
        category_month_amounts = {}
        raw_missing_rows = []
        effective_missing_rows = []
        excluded_rows = []

        for cat in CATEGORIES:
            month_amounts = {}
            for m in range(1, check_until_month + 1):
                amt = float(df_y[(df_y["category"] == cat) & (df_y["month"] == m)]["amount"].sum())
                month_amounts[m] = amt
            category_month_amounts[cat] = month_amounts

            raw_missing = [m for m, amt in month_amounts.items() if amt <= 0]
            excluded = [m for m in raw_missing if bool(overrides.get(cat, {}).get(str(m), False))]
            effective_missing = [m for m in raw_missing if m not in excluded]

            if raw_missing:
                raw_missing_rows.append({
                    "year": CHECK_YEAR,
                    "category": cat,
                    "months": raw_missing,
                    "count": len(raw_missing),
                    "month_text": format_month_ranges(raw_missing),
                })
            if excluded:
                excluded_rows.append({
                    "year": CHECK_YEAR,
                    "category": cat,
                    "months": excluded,
                    "count": len(excluded),
                    "month_text": format_month_ranges(excluded),
                })
            if effective_missing:
                effective_missing_rows.append({
                    "year": CHECK_YEAR,
                    "category": cat,
                    "months": effective_missing,
                    "count": len(effective_missing),
                    "month_text": format_month_ranges(effective_missing),
                })

        recommendations = build_auto_recommendations(category_month_amounts, check_until_month)
        rec_total = sum(len(v) for v in recommendations.values())
        raw_total = sum(r["count"] for r in raw_missing_rows)
        excluded_total = sum(r["count"] for r in excluded_rows)
        effective_total = sum(r["count"] for r in effective_missing_rows)

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("점검범위", f"1~{check_until_month}월")
        k2.metric("미집행 확인대상", f"{raw_total}개월")
        k3.metric("확인 제외", f"{excluded_total}개월")
        k4.metric("최종 확인 필요", f"{effective_total}개월")

        st.markdown("### 확인대상 빠른 처리")
        act1, act2, act3 = st.columns([1, 1, 1])
        with act1:
            if st.button(f"후지급 추정 {rec_total}개월 제외", width="stretch", disabled=(rec_total == 0)):
                for cat, months in recommendations.items():
                    overrides.setdefault(cat, {})
                    for m in months:
                        if not overrides[cat].get(str(m), False):
                            overrides[cat][str(m)] = True
                            append_missing_override_log(cat, m, "자동추천 제외", "후지급 가능성 기준")
                save_missing_overrides(overrides)
                st.success("후지급 가능성이 있는 월을 확인대상에서 제외했습니다.")
                st.rerun()
        with act2:
            if st.button("전체 확인대상 제외", width="stretch", disabled=(raw_total == 0)):
                for row in raw_missing_rows:
                    cat = row["category"]
                    overrides.setdefault(cat, {})
                    for m in row["months"]:
                        if not overrides[cat].get(str(m), False):
                            overrides[cat][str(m)] = True
                            append_missing_override_log(cat, m, "전체 제외", "사용자 일괄 처리")
                save_missing_overrides(overrides)
                st.success("전체 미집행 확인대상을 제외 처리했습니다.")
                st.rerun()
        with act3:
            if st.button("확인 제외 설정 초기화", width="stretch"):
                overrides = {}
                save_missing_overrides(overrides)
                append_missing_override_log("전체", 0, "초기화", "제외 설정 전체 초기화")
                st.warning("확인 제외 설정을 초기화했습니다.")
                st.rerun()

        if recommendations:
            rec_text = []
            for cat, months in recommendations.items():
                rec_text.append(f"{cat}: {format_month_ranges(months)}")
            st.info("자동추천 기준: 미집행 월 이후 1~3개월 안에 같은 항목의 지출이 있으면 후지급 가능성으로 표시합니다.  " + " / ".join(rec_text[:6]))

        st.markdown("### 🔎 최종 확인 대상")
        if not effective_missing_rows:
            st.success(f"{CHECK_YEAR}년 기준 최종 확인이 필요한 미집행 항목이 없습니다.")
        else:
            miss_df = pd.DataFrame(effective_missing_rows).sort_values(["count", "category"], ascending=[False, True])
            cols = st.columns(3)
            for i, row in enumerate(miss_df.itertuples(index=False)):
                level_text, fg, bg, border = missing_level(row.count)
                progress_width = min(100, row.count / max(1, check_until_month) * 100)
                with cols[i % 3]:
                    st.markdown(
                        f'<div style="background:#ffffff; border:1px solid {border}; border-left:6px solid {fg}; border-radius:18px; padding:15px 16px; margin-bottom:14px; box-shadow:0 8px 20px rgba(15,23,42,.05); min-height:152px;">'
                        f'<div style="display:flex; justify-content:space-between; align-items:flex-start; gap:8px;">'
                        f'<div style="font-size:1.02rem; font-weight:950; color:#0f172a; line-height:1.25;">{display_category(row.category)}</div>'
                        f'<div style="background:{bg}; color:{fg}; border:1px solid {border}; border-radius:999px; padding:4px 9px; font-size:.74rem; font-weight:950; white-space:nowrap;">{level_text}</div>'
                        f'</div>'
                        f'<div style="margin-top:12px; display:flex; align-items:baseline; gap:6px;">'
                        f'<span style="font-size:1.55rem; font-weight:950; color:{fg};">{row.count}</span>'
                        f'<span style="font-size:.86rem; font-weight:850; color:#64748b;">개월 확인 필요</span>'
                        f'</div>'
                        f'<div style="height:8px; background:#e5e7eb; border-radius:999px; overflow:hidden; margin-top:10px;">'
                        f'<div style="height:100%; width:{progress_width:.1f}%; background:{fg}; border-radius:999px;"></div>'
                        f'</div>'
                        f'<div style="margin-top:12px; font-size:.82rem; color:#334155; font-weight:800; line-height:1.45;">{row.month_text}</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )

        st.markdown("### 월별 확인 제외 조정")
        st.caption("체크한 월은 지출액이 0원이어도 후지급·일괄지급 등 예외로 보고 최종 확인대상에서 제외합니다.")

        if raw_missing_rows:
            raw_df = pd.DataFrame(raw_missing_rows).sort_values(["count", "category"], ascending=[False, True])
            for row in raw_df.itertuples(index=False):
                cat = row.category
                cat_key = re.sub(r"[^0-9a-zA-Z가-힣_]+", "_", str(cat))
                excluded_count = len([m for m in row.months if overrides.get(cat, {}).get(str(m), False)])
                rec_months = set(recommendations.get(cat, []))
                with st.expander(f"{display_category(cat)} · 확인대상 {row.count}개월 · 제외 {excluded_count}개월 · 최종 {row.count - excluded_count}개월", expanded=(row.count - excluded_count > 0)):
                    b1, b2, b3 = st.columns([1, 1, 2])
                    with b1:
                        if st.button("이 항목 전체 제외", key=f"all_ex_{cat_key}", width="stretch"):
                            overrides.setdefault(cat, {})
                            for m in row.months:
                                if not overrides[cat].get(str(m), False):
                                    overrides[cat][str(m)] = True
                                    append_missing_override_log(cat, m, "항목 전체 제외", "사용자 처리")
                            save_missing_overrides(overrides)
                            st.rerun()
                    with b2:
                        if st.button("이 항목 제외 해제", key=f"clear_ex_{cat_key}", width="stretch"):
                            overrides.setdefault(cat, {})
                            for m in row.months:
                                if overrides[cat].get(str(m), False):
                                    overrides[cat][str(m)] = False
                                    append_missing_override_log(cat, m, "항목 제외 해제", "사용자 처리")
                            save_missing_overrides(overrides)
                            st.rerun()
                    with b3:
                        st.markdown(f"**후보월:** {row.month_text}")

                    month_cols = st.columns(min(6, max(1, check_until_month)))
                    for idx, m in enumerate(row.months):
                        with month_cols[idx % len(month_cols)]:
                            old_val = bool(overrides.get(cat, {}).get(str(m), False))
                            label = f"{m}월 제외" + (" · 후지급 추정" if m in rec_months else "")
                            new_val = st.checkbox(label, value=old_val, key=f"miss_override_{cat_key}_{m}")
                            if new_val != old_val:
                                overrides.setdefault(cat, {})[str(m)] = bool(new_val)
                                append_missing_override_log(cat, m, "확인 제외" if new_val else "제외 해제", "월별 체크")
                                save_missing_overrides(overrides)
                                changed = True
            if changed:
                st.toast("월별 확인 제외 설정을 저장했습니다.")
                st.rerun()
        else:
            st.success("미집행 확인대상이 없습니다.")

        st.markdown("### 최종 확인대상 목록")
        if effective_missing_rows:
            table_df = pd.DataFrame(effective_missing_rows)[["year", "category", "month_text", "count"]].copy()
            table_df["category"] = table_df["category"].map(display_category)
            table_df.columns = ["점검연도", "집행관리 항목", "최종 확인월", "확인월수"]
            st.dataframe(table_df, width="stretch", hide_index=True, height=min(460, 42 + len(table_df) * 36))
        else:
            st.info("확인 제외 설정 반영 후 최종 확인대상이 없습니다.")

        with st.expander("최근 확인 제외 변경이력", expanded=False):
            logs = load_missing_override_logs(50)
            if logs:
                log_df = pd.DataFrame(logs)
                log_df["category"] = log_df["category"].map(display_category)
                log_df = log_df.rename(columns={"time":"시간", "year":"연도", "category":"집행관리 항목", "month":"월", "action":"처리", "memo":"메모"})
                st.dataframe(log_df, width="stretch", hide_index=True, height=300)
            else:
                st.caption("아직 변경 로그가 없습니다.")
    else:
        st.info("아직 점검할 데이터가 없습니다.")

# --- TAB 4: 일상경비 원천내역 연계 ---
if current_page == PAGE_IMPORT:
    st.markdown(
        """
        <div class="source-note">
            <b>반영 기준</b> · 파일을 선택해도 바로 저장하지 않습니다.
            인식 시트, 신규·중복 건수, 집행관리 항목 분류 결과를 먼저 검토한 뒤
            <b>검증 결과 반영</b>을 눌러야 원천내역과 월별 집계가 갱신됩니다.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <div class="pipeline">
            <div class="pipeline-step"><div class="pipeline-index">STEP 01</div><div class="pipeline-label">파일 선택</div><div class="pipeline-value">xlsx·csv / 전체 시트</div></div>
            <div class="pipeline-step"><div class="pipeline-index">STEP 02</div><div class="pipeline-label">열·시트 인식</div><div class="pipeline-value">일자·적요·금액·예산과목</div></div>
            <div class="pipeline-step"><div class="pipeline-index">STEP 03</div><div class="pipeline-label">분류 검증</div><div class="pipeline-value">집행관리 항목 매핑</div></div>
            <div class="pipeline-step"><div class="pipeline-index">STEP 04</div><div class="pipeline-label">중복 확인</div><div class="pipeline-value">일자·적요·금액 기준</div></div>
            <div class="pipeline-step"><div class="pipeline-index">STEP 05</div><div class="pipeline-label">월별 반영</div><div class="pipeline-value">확정 후 전체 화면 갱신</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.session_state.get("import_flash"):
        st.success(st.session_state.pop("import_flash"))

    def import_identity(item):
        return (
            str(item.get("집행일자", "")).strip(),
            str(item.get("적요", "")).strip(),
            float(clean_numeric(item.get("집행금액", 0))),
        )

    def build_import_review(rows, existing_rows):
        existing_keys = {import_identity(item) for item in existing_rows}
        review_rows = []
        for row in rows:
            amount = clean_numeric(row.get("집행금액", 0))
            mapped = force_mapped_category_for_known_cases(
                row.get("적요", ""),
                row.get("세목", ""),
                row.get("예산과목", ""),
                amount,
            ) or get_mapped_category(
                row.get("적요", ""),
                row.get("세목", ""),
                row.get("예산과목", ""),
            )
            duplicate = import_identity(row) in existing_keys
            if duplicate:
                status = "기존 중복 · 미추가"
            elif mapped:
                status = "월별 집계 반영"
            else:
                status = "미분류 · 원천내역만 보관"
            review_rows.append({
                "반영상태": status,
                "집행일자": row.get("집행일자", ""),
                "예산 세목(원본)": row.get("세목", ""),
                "예산과목(원본)": row.get("예산과목", ""),
                "집행관리 항목": display_category(mapped) if mapped else "미분류",
                "적요": row.get("적요", ""),
                "지출액": amount,
            })
        return pd.DataFrame(review_rows)

    def render_import_review(uploaded_file, kind, parser, pending_key, last_hash_key):
        if uploaded_file is None:
            return

        file_bytes = uploaded_file.getvalue()
        file_hash = hashlib.md5(file_bytes).hexdigest()
        if st.session_state.get(last_hash_key) == file_hash:
            st.info("현재 선택한 파일은 이 실행 세션에서 이미 반영했습니다. 동일 거래는 다시 추가되지 않습니다.")
            return

        pending = st.session_state.get(pending_key)
        if not pending or pending.get("hash") != file_hash:
            uploaded_file.seek(0)
            with st.spinner("파일 구조와 집행관리 항목을 검증하고 있습니다..."):
                sheet_name, parsed_rows, attempts = parser(uploaded_file)
            pending = {
                "hash": file_hash,
                "file_name": uploaded_file.name,
                "sheet_name": sheet_name,
                "rows": parsed_rows or [],
                "attempts": attempts or [],
            }
            st.session_state[pending_key] = pending

        parsed_rows = pending.get("rows", [])
        attempts = pending.get("attempts", [])
        if not parsed_rows:
            st.error("반영 가능한 행을 찾지 못했습니다. 시트별 인식 결과와 원본 열 제목을 확인해 주세요.")
            if attempts:
                st.dataframe(
                    pd.DataFrame(attempts, columns=["시트명", "인식 건수"]),
                    width="stretch",
                    hide_index=True,
                )
            return

        existing_rows = st.session_state.get("daily_expenses", [])
        review_df = build_import_review(parsed_rows, existing_rows)
        new_count = int((review_df["반영상태"] != "기존 중복 · 미추가").sum())
        duplicate_count = int((review_df["반영상태"] == "기존 중복 · 미추가").sum())
        mapped_count = int((review_df["반영상태"] == "월별 집계 반영").sum())
        unmapped_count = int((review_df["반영상태"] == "미분류 · 원천내역만 보관").sum())
        new_amount = float(
            review_df.loc[review_df["반영상태"] != "기존 중복 · 미추가", "지출액"].sum()
        )

        st.markdown(
            f"""
            <div class="ops-kpi-grid">
                <div class="ops-kpi ops-kpi-accent"><div class="ops-kpi-label">인식 행</div><div class="ops-kpi-value">{len(review_df):,}건</div><div class="ops-kpi-foot">선택 시트 · {pending.get("sheet_name") or "-"}</div></div>
                <div class="ops-kpi"><div class="ops-kpi-label">신규 저장 예정</div><div class="ops-kpi-value">{new_count:,}건</div><div class="ops-kpi-foot">{int(new_amount):,}원</div></div>
                <div class="ops-kpi"><div class="ops-kpi-label">월별 집계 분류</div><div class="ops-kpi-value">{mapped_count:,}건</div><div class="ops-kpi-foot">집행관리 항목 매핑 완료</div></div>
                <div class="ops-kpi"><div class="ops-kpi-label">확인 필요</div><div class="ops-kpi-value">{unmapped_count:,}건</div><div class="ops-kpi-foot">중복 {duplicate_count:,}건 별도 제외</div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if unmapped_count:
            st.warning("미분류 행은 원천내역에는 보관되지만 월별 집계에는 포함되지 않습니다. 반영 전 적요와 예산과목을 확인해 주세요.")
        st.dataframe(
            review_df,
            width="stretch",
            hide_index=True,
            height=min(460, 42 + len(review_df) * 34),
            column_config={"지출액": st.column_config.NumberColumn(format="%,d원")},
        )
        if attempts:
            with st.expander("시트별 인식 결과", expanded=False):
                st.dataframe(
                    pd.DataFrame(attempts, columns=["시트명", "인식 건수"]),
                    width="stretch",
                    hide_index=True,
                )

        if st.button(
            f"검증 결과 반영 · 신규 {new_count:,}건",
            key=f"apply_import_{kind}",
            type="primary",
            width="stretch",
            disabled=(new_count == 0),
        ):
            merged_rows, added_count = merge_expenses(existing_rows, parsed_rows)
            if save_daily_expenses(merged_rows):
                st.session_state["daily_expenses"] = merged_rows
                st.session_state[last_hash_key] = file_hash
                st.session_state.pop(pending_key, None)
                sync_daily_to_master_auto()
                st.session_state["import_flash"] = (
                    f"{pending.get('file_name')} · {pending.get('sheet_name')} 시트에서 "
                    f"신규 {added_count:,}건을 저장하고 월별 집계를 갱신했습니다."
                )
                st.rerun()

    general_tab, special_tab = st.tabs(["일반 일상경비", "5대 정기지출·수수료"])
    with general_tab:
        st.markdown("#### 일반 지출내역 파일")
        st.caption("모든 시트를 확인해 지급일자·적요·지출금액·예산과목 열을 자동 인식합니다. I열 예산과목이 있으면 분류에 우선 사용합니다.")
        general_file = st.file_uploader(
            "일반 지출내역 엑셀·CSV",
            type=["xlsx", "csv"],
            key="daily_up_v292",
        )
        render_import_review(
            general_file,
            "general",
            parse_general_from_uploaded_file,
            "pending_general_import",
            "last_file_hash",
        )

    with special_tab:
        st.markdown("#### 5대 정기지출·수수료 전용 파일")
        st.caption("B열 지급월과 K열 문서제목을 기준으로 신용카드 수수료, 무인경비, 공기청정기·비데, 야간경비, 청소·환경관리용역을 분류합니다.")
        special_file = st.file_uploader(
            "5대 정기지출·수수료 엑셀·CSV",
            type=["xlsx", "csv"],
            key="special_up_v292",
        )
        render_import_review(
            special_file,
            "special",
            parse_special_from_uploaded_file,
            "pending_special_import",
            "last_sp_file_hash",
        )

    st.markdown('<div class="section-header">저장된 일상경비 원천내역</div>', unsafe_allow_html=True)
    daily_data = st.session_state.get("daily_expenses", [])
    if daily_data:
        df_d = pd.DataFrame(daily_data)
        for required_column in ["세목", "집행일자", "적요", "집행금액", "예산과목"]:
            if required_column not in df_d.columns:
                df_d[required_column] = ""
        df_d["세목"] = df_d["세목"].astype(str)
        df_d["집행금액"] = pd.to_numeric(df_d["집행금액"], errors="coerce").fillna(0.0)

        def extract_budget_code(value):
            codes = re.findall(r"(?<!\d)(\d{3}(?:-\d{2})?)(?!\d)", str(value).strip())
            return codes[-1] if codes else str(value).strip()

        df_d = df_d.assign(temp_code=df_d["세목"].apply(extract_budget_code))
        best_names = df_d.groupby("temp_code")["세목"].apply(lambda values: max(values.astype(str), key=len)).to_dict()

        def get_full_semok_name(code, current_best):
            if current_best and "[" in current_best and "]" in current_best:
                return current_best
            if code in BUDGET_MAPPING:
                item_group, item_name = BUDGET_MAPPING[code]
                prefix = code.split("-")[0]
                return f"[{prefix}]{item_group} - [{code}]{item_name}"
            return current_best if current_best else code

        cleaned_semok = df_d["temp_code"].apply(
            lambda code: get_full_semok_name(code, best_names.get(code, ""))
        )
        changed = False
        for idx, _ in df_d.iterrows():
            if daily_data[idx].get("세목", "") != cleaned_semok.iloc[idx]:
                daily_data[idx]["세목"] = cleaned_semok.iloc[idx]
                changed = True
        if changed:
            save_daily_expenses(daily_data)
            st.session_state["daily_expenses"] = daily_data

        df_d = df_d.assign(세목=cleaned_semok).drop(columns=["temp_code"])
        df_d["관리항목키"] = df_d.apply(
            lambda row: force_mapped_category_for_known_cases(
                row.get("적요", ""),
                row.get("세목", ""),
                row.get("예산과목", ""),
                row.get("집행금액", 0),
            ) or get_mapped_category(
                row.get("적요", ""),
                row.get("세목", ""),
                row.get("예산과목", ""),
            ),
            axis=1,
        )
        df_d["집행관리 항목"] = df_d["관리항목키"].apply(
            lambda value: display_category(value) if value else "미분류"
        )
        quality = get_daily_quality_snapshot(daily_data)

        st.markdown(
            f"""
            <div class="ops-kpi-grid">
                <div class="ops-kpi ops-kpi-accent"><div class="ops-kpi-label">원천내역 지출액</div><div class="ops-kpi-value">{int(df_d["집행금액"].sum()):,}원</div><div class="ops-kpi-foot">저장된 전체 행 기준</div></div>
                <div class="ops-kpi"><div class="ops-kpi-label">원천내역 건수</div><div class="ops-kpi-value">{len(df_d):,}건</div><div class="ops-kpi-foot">일반·특수·수동 조정 포함</div></div>
                <div class="ops-kpi"><div class="ops-kpi-label">월별 집계 분류</div><div class="ops-kpi-value">{quality["mapped"]:,}건</div><div class="ops-kpi-foot">집행관리 항목 매핑 완료</div></div>
                <div class="ops-kpi"><div class="ops-kpi-label">미분류 확인</div><div class="ops-kpi-value">{quality["unmapped"]:,}건</div><div class="ops-kpi-foot">일자 확인 {quality["invalid_date"]:,}건</div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        filter_a, filter_b, filter_c, export_col = st.columns([1.4, 1.7, 2.0, 1.2])
        management_options = ["전체"] + sorted(df_d["집행관리 항목"].dropna().unique().tolist())
        management_filter = filter_a.selectbox("집행관리 항목", management_options, key="f_management_v39")
        semok_filter = filter_b.selectbox(
            "예산 세목(원본)",
            ["전체"] + sorted(df_d["세목"].dropna().unique().tolist()),
            key="f1_v292",
        )
        search_text = filter_c.text_input("적요 검색", key="f2_v292")

        disp = df_d.copy()
        if management_filter != "전체":
            disp = disp[disp["집행관리 항목"] == management_filter]
        if semok_filter != "전체":
            disp = disp[disp["세목"] == semok_filter]
        if search_text:
            disp = disp[disp["적요"].astype(str).str.contains(search_text, na=False)]

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            export_df = disp[
                ["집행일자", "세목", "예산과목", "집행관리 항목", "적요", "집행금액"]
            ].copy()
            export_df.columns = [
                "집행일자",
                "예산 세목(원본)",
                "예산과목(원본)",
                "집행관리 항목",
                "적요",
                "지출액",
            ]
            export_df.to_excel(writer, index=False, sheet_name="일상경비원천내역")
        with export_col:
            st.markdown("<div style='margin-top:29px;'></div>", unsafe_allow_html=True)
            st.download_button(
                "조회결과 엑셀",
                data=output.getvalue(),
                file_name=f"일상경비원천내역_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",
            )

        disp = disp.assign(_idx=disp.index, 삭제선택=False)
        disp["지출액표시"] = disp["집행금액"].map(lambda value: f"{int(value):,}")
        table_columns = [
            "삭제선택",
            "집행일자",
            "세목",
            "예산과목",
            "집행관리 항목",
            "적요",
            "지출액표시",
            "_idx",
            "집행금액",
            "관리항목키",
        ]
        edited_df = st.data_editor(
            disp[table_columns],
            height=520,
            hide_index=True,
            column_config={
                "삭제선택": st.column_config.CheckboxColumn("삭제", default=False),
                "집행일자": st.column_config.TextColumn("집행일자", disabled=True),
                "세목": st.column_config.TextColumn("예산 세목(원본)", disabled=True),
                "예산과목": st.column_config.TextColumn("예산과목(원본)", disabled=True),
                "집행관리 항목": st.column_config.TextColumn("집행관리 항목", disabled=True),
                "적요": st.column_config.TextColumn("적요", disabled=True),
                "지출액표시": st.column_config.TextColumn("지출액(원)", disabled=True),
                "_idx": None,
                "집행금액": None,
                "관리항목키": None,
            },
            key="daily_editor_v292",
        )

        delete_col, spacer_col = st.columns([1.5, 5])
        with delete_col:
            if st.button("선택 원천내역 삭제", key="btn_del_sel_v292", width="stretch"):
                to_delete = edited_df[edited_df["삭제선택"] == True]["_idx"].tolist()
                if to_delete:
                    new_daily = [item for idx, item in enumerate(daily_data) if idx not in to_delete]
                    if save_daily_expenses(new_daily):
                        st.session_state["daily_expenses"] = new_daily
                        sync_daily_to_master_auto()
                        st.success(f"{len(to_delete)}건을 삭제하고 월별 집계를 갱신했습니다.")
                        time.sleep(0.5)
                        st.rerun()
                else:
                    st.warning("삭제할 원천내역을 먼저 선택해 주세요.")

        with st.expander("전체 원천내역 삭제", expanded=False):
            confirm_reset = st.checkbox(
                "저장된 일상경비 원천내역 전체 삭제를 확인합니다.",
                key="confirm_daily_reset_v39",
            )
            st.caption("전체 삭제 후 월별 집계도 다시 계산됩니다. 수탁자산취득비·세탁용역의 고정 보정자료는 유지됩니다.")
            if st.button(
                "전체 원천내역 삭제 실행",
                key="btn_del_all_v292",
                disabled=not confirm_reset,
                type="primary",
            ):
                if save_daily_expenses([]):
                    st.session_state["daily_expenses"] = []
                    sync_daily_to_master_auto()
                    st.session_state["import_flash"] = "일상경비 원천내역을 전체 삭제하고 월별 집계를 갱신했습니다."
                    st.rerun()
    else:
        st.info("저장된 일상경비 원천내역이 없습니다. 위에서 파일을 선택하고 검증 결과를 반영해 주세요.")

# --- TAB 5: 신속집행 대시보드 ---
if current_page == PAGE_QUICK:
    st.markdown('<div class="section-header">2026 신속집행 현황</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="source-note"><b>산정 기준</b> · 지급명령·지출액만 실적으로 반영합니다. '
        '원인행위액·계약금액·집행계획액은 실적에 포함하지 않으며, 각 관리대상 항목은 '
        '대상액을 초과해 지출해도 달성률 계산상 100%까지만 인정합니다.</div>',
        unsafe_allow_html=True,
    )
    
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
                
                # [V292 마법의 트릭] master_data에 이미 상하수도가 더해져 있으므로 여기서 또 더하면 안됨!
                df_view.loc[idx, '실제집행액'] = float(actual_val)
            except: pass

    daily_list = st.session_state.get('daily_expenses', [])
    # [V11/V12] 수동 입력분은 상세내역에도 표시되도록 daily view에 병합
    daily_list = list(daily_list) + MANUAL_ASSET_ACQUISITION_ROWS + MANUAL_LAUNDRY_ACCRUAL_ROWS
    df_daily = pd.DataFrame(daily_list) if daily_list else pd.DataFrame(columns=["집행일자", "적요", "집행금액", "세목", "예산과목"])
    if not df_daily.empty:
        df_daily = df_daily.assign(MappedCategory=df_daily.apply(lambda r: force_mapped_category_for_known_cases(r.get('적요',''), r.get('세목',''), r.get('예산과목',''), r.get('집행금액',0)) or get_mapped_category(r.get('적요',''), r.get('세목',''), r.get('예산과목','')), axis=1))

    current_m = datetime.now().month

    summary_list = []
    for cat in CORE_TARGETS:
        if df_view.empty or "세목" not in df_view.columns:
            continue

        sub = df_view[df_view["세목"] == cat].copy()
        t_amt = float(sub["대상액"].max()) if not sub.empty else 0.0
        
        raw_e_amt = float(df_all[(df_all['year'] == 2026) & (df_all['category'] == cat)]['amount'].sum()) if not df_all.empty else 0.0
        e_amt = min(raw_e_amt, t_amt) if t_amt > 0 else raw_e_amt
            
        sub = sub.assign(month_num=sub['월'].apply(lambda x: int(str(x).replace('월',''))))
        plan_to_date = sub[sub['month_num'] <= current_m]["집행예정액"].sum()
        
        summary_list.append({
            "세목": cat, "대상액": t_amt, "누적계획": plan_to_date, "집행액": e_amt,
            "원본지출액": raw_e_amt,
            "총달성률": (e_amt/t_amt*100) if t_amt > 0 else 0, "계획대비달성률": (e_amt/plan_to_date*100) if plan_to_date > 0 else 0
        })
        
    df_summary = pd.DataFrame(summary_list)

    if not df_summary.empty:
        total_target = float(df_summary["대상액"].sum())

        # [V18] 신속집행 대시보드 메인 UI 재구성
        period_choice = st.radio(
            "목표 기준 선택",
            ["상반기", "1분기"],
            index=0,
            horizontal=True,
            key="quick_exec_period_choice_v18"
        )
        if period_choice == "1분기":
            period_months = [1, 2, 3]
            mo_goal_rate = QUICK_EXEC_GOAL_RATES["행안부"]["q1"] * 100
            city_goal_rate = QUICK_EXEC_GOAL_RATES["남양주시"]["q1"] * 100
        else:
            period_months = [1, 2, 3, 4, 5, 6]
            mo_goal_rate = QUICK_EXEC_GOAL_RATES["행안부"]["h1"] * 100
            city_goal_rate = QUICK_EXEC_GOAL_RATES["남양주시"]["h1"] * 100

        def _actual_for_period(cat_name, months):
            if df_all.empty:
                return 0.0
            raw_actual = float(df_all[(df_all['year'] == 2026) & (df_all['category'] == cat_name) & (df_all['month'].isin(months))]['amount'].sum())
            item_target = float(QUICK_EXEC_CONFIG.get(cat_name, {}).get("target", 0))
            return min(raw_actual, item_target) if item_target > 0 else raw_actual

        df_summary["기간집행액"] = df_summary["세목"].apply(lambda c: _actual_for_period(c, period_months))
        total_actual = float(df_summary["기간집행액"].sum())
        total_rate = (total_actual / total_target * 100.0) if total_target > 0 else 0.0
        mo_goal_amt = total_target * (mo_goal_rate / 100.0)
        city_goal_amt = total_target * (city_goal_rate / 100.0)
        mo_gap = max(mo_goal_amt - total_actual, 0)
        city_gap = max(city_goal_amt - total_actual, 0)
        mo_status = "달성" if total_actual >= mo_goal_amt else "미달"
        city_status = "달성" if total_actual >= city_goal_amt else "미달"
        total_bar = min(max(total_rate, 0), 100)

        if total_actual >= city_goal_amt:
            main_msg = f"남양주시 목표까지 달성했습니다. 인정 집행액은 {int(total_actual):,}원, 대상액 대비 {total_rate:.1f}%입니다."
            main_color = "#16a34a"
            main_bg = "#dcfce7"
        elif total_actual >= mo_goal_amt:
            main_msg = f"행안부 목표는 달성했고, 남양주시 목표까지 {int(city_gap):,}원 남았습니다."
            main_color = "#2563eb"
            main_bg = "#dbeafe"
        else:
            main_msg = f"행안부 목표까지 {int(mo_gap):,}원, 남양주시 목표까지 {int(city_gap):,}원 부족합니다."
            main_color = "#dc2626"
            main_bg = "#fee2e2"

        quick_dashboard_html = f"""
<div style="background:#ffffff; border:1px solid #cbd5e1; border-radius:26px; padding:26px; margin:0 0 22px 0; box-shadow:0 18px 38px rgba(15,23,42,.10); font-family:system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
  <div style="display:flex; justify-content:space-between; gap:18px; align-items:flex-start; flex-wrap:wrap;">
    <div>
      <div style="font-size:1.55rem; font-weight:950; color:#0f172a; margin-bottom:6px;">{period_choice} 신속집행 목표 대비 현황</div>
      <div style="font-size:.95rem; color:#475569; font-weight:800;">행안부 목표와 남양주시 목표를 <b>대상액 대비 집행률</b>로 비교합니다.</div>
    </div>
    <div style="background:{main_bg}; color:{main_color}; border-radius:999px; padding:9px 14px; font-weight:950; font-size:.92rem;">{main_msg}</div>
  </div>

  <div style="display:grid; grid-template-columns:1.2fr 1fr 1fr 1fr; gap:12px; margin-top:20px;">
    <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:18px; padding:16px;">
      <div style="font-size:.78rem; color:#64748b; font-weight:900;">인정 집행액</div>
      <div style="font-size:1.8rem; color:#0f172a; font-weight:950; line-height:1.15;">{int(total_actual):,}원</div>
      <div style="font-size:.9rem; color:#2563eb; font-weight:950; margin-top:4px;">대상액 대비 {total_rate:.1f}%</div>
    </div>
    <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:18px; padding:16px;">
      <div style="font-size:.78rem; color:#64748b; font-weight:900;">총 대상액</div>
      <div style="font-size:1.35rem; color:#0f172a; font-weight:950;">{int(total_target):,}원</div>
    </div>
    <div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:18px; padding:16px;">
      <div style="font-size:.78rem; color:#1d4ed8; font-weight:900;">행안부 목표</div>
      <div style="font-size:1.35rem; color:#1d4ed8; font-weight:950;">{mo_goal_rate:.1f}%</div>
      <div style="font-size:.82rem; color:#1e40af; font-weight:900;">{int(mo_goal_amt):,}원 · {mo_status}</div>
    </div>
    <div style="background:#fff1f2; border:1px solid #fecdd3; border-radius:18px; padding:16px;">
      <div style="font-size:.78rem; color:#be123c; font-weight:900;">남양주시 목표</div>
      <div style="font-size:1.35rem; color:#be123c; font-weight:950;">{city_goal_rate:.1f}%</div>
      <div style="font-size:.82rem; color:#9f1239; font-weight:900;">{int(city_goal_amt):,}원 · {city_status}</div>
    </div>
  </div>

  <div style="margin-top:22px; padding-bottom:52px;">
    <div style="display:flex; justify-content:space-between; color:#334155; font-size:.82rem; font-weight:950; margin-bottom:8px;"><span>0%</span><span>대상액 대비 100%</span></div>
    <div style="position:relative; height:34px; background:#e5e7eb; border-radius:999px; overflow:visible;">
      <div style="height:100%; width:{total_bar:.1f}%; background:linear-gradient(90deg,#60a5fa,#22c55e); border-radius:999px;"></div>
      <div style="position:absolute; left:{min(max(total_bar,0),100):.1f}%; top:-8px; transform:translateX(-50%); background:#0f172a; color:white; padding:5px 9px; border-radius:999px; font-size:.78rem; font-weight:950; white-space:nowrap;">현재 {total_rate:.1f}%</div>
      <div style="position:absolute; left:{mo_goal_rate:.1f}%; top:-2px; width:4px; height:46px; background:#2563eb; border-radius:999px;"></div>
      <div style="position:absolute; left:{mo_goal_rate:.1f}%; top:48px; transform:translateX(-50%); color:#1d4ed8; font-size:.76rem; font-weight:950; white-space:nowrap;">행안부 {mo_goal_rate:.1f}%</div>
      <div style="position:absolute; left:{city_goal_rate:.1f}%; top:-2px; width:4px; height:46px; background:#dc2626; border-radius:999px;"></div>
      <div style="position:absolute; left:{city_goal_rate:.1f}%; top:48px; transform:translateX(-50%); color:#be123c; font-size:.76rem; font-weight:950; white-space:nowrap;">남양주시 {city_goal_rate:.1f}%</div>
    </div>
  </div>
</div>
"""
        # 별도 iframe을 만들지 않아 사이드바/콘텐츠 폭과 모바일 레이아웃이
        # 서로 밀려나지 않도록 앱 본문 안에서 직접 렌더링합니다.
        st.markdown(quick_dashboard_html, unsafe_allow_html=True)

        gap_cols = st.columns(2)
        with gap_cols[0]:
            st.markdown(f'''<div style="background:#eff6ff; border-left:6px solid #2563eb; border-radius:16px; padding:15px;"><div style="font-size:.82rem; color:#1d4ed8; font-weight:950;">행안부 {period_choice} 목표 대비</div><div style="font-size:1.25rem; color:#0f172a; font-weight:950;">{'목표 달성' if mo_gap == 0 else f'{int(mo_gap):,}원 부족'}</div></div>''', unsafe_allow_html=True)
        with gap_cols[1]:
            st.markdown(f'''<div style="background:#fff1f2; border-left:6px solid #dc2626; border-radius:16px; padding:15px;"><div style="font-size:.82rem; color:#be123c; font-weight:950;">남양주시 {period_choice} 목표 대비</div><div style="font-size:1.25rem; color:#0f172a; font-weight:950;">{'목표 달성' if city_gap == 0 else f'{int(city_gap):,}원 부족'}</div></div>''', unsafe_allow_html=True)

        st.markdown('<div style="font-size:1.15rem; font-weight:950; color:#1e3a8a; margin:22px 0 8px 0;">관리대상 항목별 목표 대비 현황</div>', unsafe_allow_html=True)

        def _mini_goal_card(cat, target, actual):
            rate = (actual / target * 100.0) if target else 0.0
            width = min(max(rate, 0), 100)
            mo_amt = target * (mo_goal_rate / 100.0)
            city_amt = target * (city_goal_rate / 100.0)
            mo_gap2 = max(mo_amt - actual, 0)
            city_gap2 = max(city_amt - actual, 0)
            conf = CORE_CONFIG.get(cat, {})
            status = '남양주시 달성' if actual >= city_amt else ('행안부 달성' if actual >= mo_amt else '미달')
            status_color = '#059669' if actual >= city_amt else ('#2563eb' if actual >= mo_amt else '#dc2626')
            return f'''<div style="background:#ffffff; border:1px solid #e2e8f0; border-left:9px solid {conf.get('border','#3b82f6')}; border-radius:20px; padding:16px; box-shadow:0 8px 18px rgba(15,23,42,.06);"><div style="display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:10px;"><div style="font-size:1.08rem; color:#0f172a; font-weight:950;">{conf.get('icon','')} {display_category(cat)}</div><div style="font-size:.82rem; color:{status_color}; font-weight:950;">{status}</div></div><div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:10px;"><div style="background:#f8fafc; border-radius:12px; padding:10px;"><div style="font-size:.72rem; color:#64748b; font-weight:900;">인정 집행액</div><div style="font-size:1rem; color:#0f172a; font-weight:950;">{int(actual):,}원</div></div><div style="background:#f8fafc; border-radius:12px; padding:10px;"><div style="font-size:.72rem; color:#64748b; font-weight:900;">집행률</div><div style="font-size:1rem; color:#2563eb; font-weight:950;">{rate:.1f}%</div></div></div><div style="position:relative; height:20px; background:#e5e7eb; border-radius:999px; overflow:hidden;"><div style="height:100%; width:{width:.1f}%; background:linear-gradient(90deg,#93c5fd,#2563eb); border-radius:999px;"></div><div style="position:absolute; left:{mo_goal_rate:.1f}%; top:0; height:100%; width:3px; background:#2563eb;"></div><div style="position:absolute; left:{city_goal_rate:.1f}%; top:0; height:100%; width:3px; background:#dc2626;"></div></div><div style="display:flex; justify-content:space-between; margin-top:8px; font-size:.74rem; color:#475569; font-weight:900;"><span>행안부 부족 {int(mo_gap2):,}원</span><span>남양주시 부족 {int(city_gap2):,}원</span></div></div>'''

        card_cols = st.columns(3)
        for i, row in df_summary.iterrows():
            with card_cols[i % 3]:
                st.markdown(_mini_goal_card(row['세목'], float(row['대상액']), float(row['기간집행액'])), unsafe_allow_html=True)

    for cat in CORE_TARGETS:
        conf = CORE_CONFIG[cat]
        with st.expander(f"{display_category(cat)} · 월별 집행계획 및 지출내역", expanded=False):
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
                        html_timeline = f"""<div class="{card_class}" style="opacity:{opacity};">{badge}<div class="month-label"><span>{m_idx}월</span></div><div class="detail-box" style="font-size:0.75rem; color:#475569; margin:10px 0; min-height:50px;">{conf['details'].get(m_idx, "-")}</div><div class="plan-box">집행계획: {int(p_val):,}</div><div class="actual-box" style="color:#10b981;">지출 {int(a_val):,}원</div></div>"""
                        st.markdown(html_timeline, unsafe_allow_html=True)
            
            st.markdown(f"###### {display_category(cat)} 지출 원천내역")
            if not df_daily.empty:
                if cat == "일반재료비":
                    # 일반재료비는 예산과목 기준으로 모두 포함합니다.
                    # 적요가 상하수도요금이어도 I열 예산과목이 일반재료비이면 제외하지 않습니다.
                    cat_daily = df_daily[
                        (df_daily['MappedCategory'] == '일반재료비') |
                        (df_daily.get('예산과목', '').astype(str).str.replace(' ', '', regex=False).str.contains('일반재료비', na=False))
                    ].copy()
                elif cat == "상하수도":
                    # 상하수도는 일반재료비 중 상하수도 관련 적요/세목/예산과목이 있는 행만 별도 표시합니다.
                    # 일반재료비 전체 집계와 별개로, 상하수도 관리항목의 월별 집계 근거를 보여주기 위한 필터입니다.
                    cat_daily = df_daily[df_daily.apply(lambda r: is_water_charge_row(r.get('적요',''), r.get('세목',''), r.get('예산과목','')), axis=1)].copy()
                else:
                    cat_daily = df_daily[df_daily['MappedCategory'] == cat].copy()

                # 상세내역 표시도 귀속월 보정 예외를 반영합니다.
                # 세탁용역 3월 일괄 지급 4,781,810원은 1월/2월분으로 분할 표시합니다.
                if cat == "세탁용역" and not cat_daily.empty:
                    split_rows = []
                    for _, detail_row in cat_daily.iterrows():
                        detail_desc = str(detail_row.get('적요', ''))
                        detail_amt = clean_numeric(detail_row.get('집행금액', 0))
                        date_raw_detail = str(detail_row.get('집행일자', ''))
                        m_detail = 0
                        date_num_detail = re.sub(r'[^0-9]', '', date_raw_detail)
                        if len(date_num_detail) >= 6:
                            try:
                                m_detail = int(date_num_detail[4:6])
                            except Exception:
                                m_detail = 0
                        for accrual_month, accrual_amt in get_accrual_splits_for_special_cases("세탁용역", 2026, m_detail, detail_amt, detail_desc):
                            new_row = detail_row.copy()
                            if accrual_month != m_detail or int(round(float(accrual_amt))) != int(round(float(detail_amt))):
                                new_row['집행일자'] = f"2026-{int(accrual_month):02d}-01"
                                new_row['집행금액'] = accrual_amt
                                new_row['적요'] = f"{detail_desc} (귀속월 보정: {int(accrual_month)}월분)"
                            split_rows.append(new_row)
                    if split_rows:
                        cat_daily = pd.DataFrame(split_rows)
                
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
                        final_df.columns = ["집행일자", "예산과목(원본)", "적요", "지출액(원)"]
                    else:
                        final_df = disp_df[['집행일자', '적요', '집행금액_str']]
                        final_df.columns = ["집행일자", "적요", "지출액(원)"]
                    
                    st.dataframe(final_df, hide_index=True)
                else:
                    st.info(f"아직 2026년 '{cat}' 항목으로 분류된 일상경비 지출 내역이 없습니다.")
            else:
                st.info("연계된 일상경비 원천내역이 없습니다. 먼저 ‘일상경비 자료연계’에서 파일을 검증·반영해 주세요.")
    
    st.markdown('<div class="section-header">대상액·월별 집행계획액 관리</div>', unsafe_allow_html=True)
    if not df_view.empty and "세목" in df_view.columns:
        edited_df = st.data_editor(
            df_view, 
            hide_index=True, 
            column_config={
                "세목": st.column_config.TextColumn("관리대상 항목", disabled=True), 
                "월": st.column_config.TextColumn(disabled=True), 
                "대상액": st.column_config.NumberColumn("대상액 (원)", format="%,d"), 
                "집행예정액": st.column_config.NumberColumn("월별 집행계획액 (원)", format="%,d"), 
                "실제집행액": st.column_config.NumberColumn("지출액 (자동 연계)", format="%,d", disabled=True)
            }, 
            key="rapid_editor_v292"
        )

        if st.button("대상액·집행계획액 저장", type="primary", key="save_rapid_btn_v292"):
            st.session_state['rapid_df'] = edited_df
            if save_rapid_df(edited_df): 
                st.success("대상액과 월별 집행계획액을 저장했습니다."); time.sleep(0.5); st.rerun()

# --- TAB 6: 정량실적 ---
if current_page == PAGE_QUANT:
    st.markdown('<div class="section-header">예산계층별 월별 정량실적</div>', unsafe_allow_html=True)
    st.caption("선택한 예산계층과 하위 항목의 월별 지출액을 합산합니다. 예산액·예산배정액·지출액은 원본 정량실적 자료의 열을 그대로 사용합니다.")
    c_y, c_m = st.columns([1, 3])
    sel_year = c_y.radio("조회 연도", YEARS, index=2, horizontal=True, key="ry_v292_q")
    sel_months = c_m.multiselect("조회 월 선택", MONTHS, default=[1], format_func=lambda x: f"{x}월", key="rm_v292_q")
    
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
            st.markdown('<div class="sticky-summary">', unsafe_allow_html=True); st.markdown("##### 선택 항목 합계")
            for m in sorted(sel_months):
                col_n = f"{m}월 지출액"; val = dfs_sum_v202(base_id, col_n, master_df, children_map, st.session_state['tree_states'])
                st.markdown(f'<div class="metric-card" style="border-left-color:#10b981; padding:15px;"><div class="metric-label">{m}월 선택 지출액</div><div class="metric-value" style="font-size:1.6rem;">{int(val):,}원</div></div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
        with tree_col:
            for idx, row in master_df.iterrows():
                p_id = parent_map.get(idx)
                if p_id is not None and p_id not in st.session_state.get('tree_expanded', set()): continue
                state = st.session_state['tree_states'].get(idx, 0); icon_chk = "✅" if state == 1 else ("➖" if state == 2 else "⬜")
                has_children = len(children_map.get(idx, [])) > 0; indent_px = row['lvl_sys'] * 12 
                with st.container():
                    cols = st.columns([0.4, 0.4, 4.0, 1.2, 1.2] + [1.2]*len(sel_months))
                    if cols[0].button(icon_chk, key=f"chk_v292_{idx}"):
                        new_st = 0 if state in (1, 2) else 1
                        set_subtree_state_v202(idx, new_st, children_map, st.session_state['tree_states'])
                        refresh_ancestors_v202(idx, parent_map, children_map, st.session_state['tree_states']); st.rerun()
                    if has_children:
                        expanded_set = st.session_state.get('tree_expanded', set())
                        ex_icon = "▼" if idx in expanded_set else "▶"
                        if cols[1].button(ex_icon, key=f"tog_v292_{idx}"):
                            if idx in expanded_set: expanded_set.remove(idx)
                            else: expanded_set.add(idx)
                            st.session_state['tree_expanded'] = expanded_set; st.rerun()
                    label_html = f'<div class="tree-label" style="padding-left: {indent_px}px;">{row["구분"].strip()}</div>'
                    cols[2].markdown(label_html, unsafe_allow_html=True)
                    cols[3].write(f"{int(row['예산액']):,}")
                    cols[4].write(f"{int(row['예산배정']):,}")
    else: st.info("데이터가 없습니다.")
