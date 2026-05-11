import streamlit as st
import streamlit.components.v1 as components
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
from urllib.parse import quote, unquote
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
# [V11/V12] 저장 데이터가 초기화되어도 수동 입력분은 화면 집계에 즉시 반영
master_data_raw = apply_manual_asset_to_master_data(master_data_raw)
master_data_raw = apply_manual_laundry_to_master_data(master_data_raw)
st.session_state['data'] = master_data_raw
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
# 앱 홈 화면 / 페이지 이동 유틸리티
# -----------------------------------------------------------------------------
APP_PAGES = [
    "📊 실적 현황",
    "📈 항목별 지출 분석",
    "🚨 미집행 누락",
    "📂 일상경비 동기화",
    "🚀 신속집행 대시보드",
    "📂 1~12월 정량실적",
]

APP_PAGE_META = {
    "📊 실적 현황": {"icon": "📊", "title": "실적 현황", "desc": "누적 지출·비중·통합 그리드"},
    "📈 항목별 지출 분석": {"icon": "📈", "title": "항목별 분석", "desc": "3개년 동월누계·월별 추이"},
    "🚨 미집행 누락": {"icon": "🚨", "title": "미집행 누락", "desc": "누락 후보·월별 제외 처리"},
    "📂 일상경비 동기화": {"icon": "📂", "title": "일상경비 동기화", "desc": "엑셀 업로드·자동 반영"},
    "🚀 신속집행 대시보드": {"icon": "🚀", "title": "신속집행", "desc": "행안부·남양주시 목표 비교"},
    "📂 1~12월 정량실적": {"icon": "📋", "title": "정량실적", "desc": "월별 정량실적 관리"},
}

def go_page(page_name: str):
    st.session_state["current_page"] = page_name


def render_phone_home(df_all):
    '''최초 접속용 앱 홈 화면: 순수 HTML 링크로 휴대폰 프레임 내부 메뉴를 렌더링한다.'''
    from urllib.parse import quote

    total_2026 = 0
    active_items = 0
    latest_month = "-"
    if df_all is not None and not df_all.empty and "year" in df_all.columns:
        df_26_home = df_all[df_all["year"] == 2026].copy()
        if not df_26_home.empty:
            amount_series = pd.to_numeric(df_26_home.get("amount", 0), errors="coerce").fillna(0)
            total_2026 = float(amount_series.sum())
            active_items = int(df_26_home[amount_series > 0]["category"].nunique()) if "category" in df_26_home.columns else 0
            if "month" in df_26_home.columns and not df_26_home[amount_series > 0].empty:
                latest_month = f"{int(df_26_home.loc[amount_series > 0, 'month'].max())}월"

    menu_cards = []
    for page in APP_PAGES:
        meta = APP_PAGE_META[page]
        href = "?page=" + quote(page)
        menu_cards.append(
            f'<a class="app-icon-card" href="{href}" target="_self">'
            f'<div class="app-icon-emoji">{meta["icon"]}</div>'
            f'<div class="app-icon-title">{meta["title"]}</div>'
            f'<div class="app-icon-desc">{meta["desc"]}</div>'
            f'</a>'
        )
    menu_html = "".join(menu_cards)

    html = f'''
<style>
.block-container:has(.phone-home-anchor) {{
    padding-top: 1.0rem;
    background: radial-gradient(circle at top, #eff6ff 0%, #f8fafc 48%, #eef2f7 100%);
}}
.phone-home-anchor {{ display:none; }}
.phone-home-wrap {{ max-width: 500px; margin: 0 auto; }}
.phone-home-shell {{
    background: linear-gradient(160deg, #111827 0%, #020617 100%);
    border-radius: 48px;
    padding: 14px;
    box-shadow: 0 30px 70px rgba(15,23,42,0.28);
}}
.phone-home-screen {{
    min-height: 720px;
    background: linear-gradient(180deg, #eff6ff 0%, #f8fafc 54%, #ffffff 100%);
    border-radius: 34px;
    padding: 0 18px 20px 18px;
    border: 1px solid rgba(148,163,184,0.35);
    overflow: hidden;
}}
.phone-notch {{
    width: 120px;
    height: 24px;
    background:#020617;
    border-radius: 0 0 18px 18px;
    margin: 0 auto 18px auto;
}}
.phone-title {{ text-align:center; font-size:1.35rem; font-weight:950; letter-spacing:-0.04em; color:#0f172a; }}
.phone-subtitle {{ text-align:center; font-size:.78rem; font-weight:850; color:#64748b; margin:4px 0 14px 0; }}
.phone-kpi-row {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px; margin-bottom:14px; }}
.phone-kpi {{ background:rgba(255,255,255,0.96); border:1px solid #dbeafe; border-radius:16px; padding:10px 6px; text-align:center; }}
.phone-kpi-label {{font-size:.66rem; color:#64748b; font-weight:850;}}
.phone-kpi-value {{font-size:.80rem; color:#0f172a; font-weight:950; margin-top:2px; word-break:keep-all;}}
.phone-menu-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:10px; }}
.app-icon-card {{
    display:flex;
    flex-direction:column;
    justify-content:center;
    align-items:center;
    min-height:96px;
    border-radius:22px;
    background:rgba(255,255,255,0.98);
    border:1px solid #dbeafe;
    box-shadow:0 10px 22px rgba(37,99,235,0.08);
    text-decoration:none !important;
    color:#0f172a !important;
    transition: all .15s ease;
    padding:10px 8px;
}}
.app-icon-card:hover {{ transform:translateY(-2px); border-color:#2563eb; box-shadow:0 16px 30px rgba(37,99,235,0.18); }}
.app-icon-emoji {{font-size:1.55rem; line-height:1.1; margin-bottom:5px;}}
.app-icon-title {{font-size:.92rem; font-weight:950; line-height:1.25;}}
.app-icon-desc {{font-size:.66rem; font-weight:800; color:#64748b; line-height:1.25; margin-top:4px; text-align:center; word-break:keep-all;}}
.home-caption-box {{ margin:14px 0 0 0; background:#f8fafc; border:1px solid #e2e8f0; border-radius:18px; padding:13px 14px; color:#475569; font-size:.74rem; font-weight:800; line-height:1.55; }}
@media (max-width: 680px) {{
    .phone-home-wrap {{max-width: 390px;}}
    .phone-home-shell {{border-radius: 38px; padding: 10px;}}
    .phone-home-screen {{min-height: 670px; padding: 0 12px 16px 12px; border-radius: 28px;}}
    .phone-title {{font-size:1.12rem;}}
    .phone-kpi-value {{font-size:.68rem;}}
    .app-icon-card {{min-height:86px; border-radius:18px;}}
    .app-icon-desc {{display:none;}}
}}
</style>
<div class="phone-home-anchor"></div>
<div class="phone-home-wrap">
  <div class="phone-home-shell">
    <div class="phone-home-screen">
      <div class="phone-notch"></div>
      <div class="phone-title">🏢 2026 월별 지출관리</div>
      <div class="phone-subtitle">Monthly Expense Control App</div>
      <div class="phone-kpi-row">
        <div class="phone-kpi"><div class="phone-kpi-label">2026 누적</div><div class="phone-kpi-value">{int(total_2026):,}원</div></div>
        <div class="phone-kpi"><div class="phone-kpi-label">집행 항목</div><div class="phone-kpi-value">{active_items}개</div></div>
        <div class="phone-kpi"><div class="phone-kpi-label">최근 집행</div><div class="phone-kpi-value">{latest_month}</div></div>
      </div>
      <div class="phone-menu-grid">
        {menu_html}
      </div>
      <div class="home-caption-box">아이콘을 누르면 해당 업무 화면으로 이동합니다.<br>좌측 사이드바에서도 홈 화면과 각 메뉴로 이동할 수 있습니다.</div>
    </div>
  </div>
</div>
'''
    html = html.strip()
    if hasattr(st, "html"):
        st.html(html)
    else:
        st.markdown(html, unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 7. 메인 UI
# -----------------------------------------------------------------------------
if "current_page" not in st.session_state:
    st.session_state["current_page"] = "HOME"

# 홈 화면 아이콘 링크는 query parameter로 페이지를 전달한다.
try:
    qp_page = st.query_params.get("page", None)
except Exception:
    qp_page = None
if qp_page:
    if isinstance(qp_page, list):
        qp_page = qp_page[0]
    qp_page = unquote(str(qp_page))
    if qp_page in APP_PAGES and st.session_state.get("current_page") != qp_page:
        st.session_state["current_page"] = qp_page

with st.sidebar:
    st.divider()
    st.markdown("### 📱 앱 메뉴")
    if st.button("🏠 앱 홈", key="sidebar_home", use_container_width=True):
        st.session_state["current_page"] = "HOME"
        try:
            st.query_params.clear()
        except Exception:
            pass
        st.rerun()
    for _page in APP_PAGES:
        if st.button(_page, key=f"sidebar_nav_{_page}", use_container_width=True):
            st.session_state["current_page"] = _page
            st.rerun()

if st.session_state.get("current_page") == "HOME":
    render_phone_home(df_all)
    st.stop()

current_page = st.session_state.get("current_page", "📊 실적 현황")
_top_left, _top_right = st.columns([0.78, 0.22])
with _top_left:
    st.title("🏢 2026 월별 지출관리 및 실시간 분석")
    st.caption(f"현재 화면: {current_page}")
with _top_right:
    if st.button("🏠 앱 홈으로", key="top_home_btn", use_container_width=True):
        st.session_state["current_page"] = "HOME"
        try:
            st.query_params.clear()
        except Exception:
            pass
        st.rerun()

# --- TAB 1: 실적 현황 ---
if current_page == "📊 실적 현황":
    if not df_all.empty:
        df_26 = df_all[df_all["year"] == 2026].copy(); val_26 = df_26["amount"].sum()
        st.markdown(f"""<div class="metric-card"><div class="metric-label">🏢 2026년 누적 지출액 (자동 연동 중)</div><div class="metric-value">{format(int(val_26), ",")} <span style="font-size:1rem; color:#94a3b8;">원</span></div></div>""", unsafe_allow_html=True)
    c_s, c_i, c_p = st.columns([0.8, 1.2, 2.1])
    with c_s:
        st.markdown('<b style="font-size:1.1rem; color:#1e3a8a; border-left:5px solid #2563eb; padding-left:10px;">🚀 상반기 신속집행</b>', unsafe_allow_html=True)
        st.markdown('<div style="font-size:0.82rem; color:#64748b; font-weight:700; margin:6px 0 10px 0;">대상액 대비 실적률 기준 · 행안부/남양주시 목표선 표시</div>', unsafe_allow_html=True)
        for cat in ["수탁자산취득비", "일반재료비", "상품매입비"]:
            df_t = df_26[df_26["category"] == cat] if not df_all.empty else pd.DataFrame()
            conf = QUICK_EXEC_CONFIG.get(cat, {"target": 0})
            q1_v = pd.to_numeric(df_t[df_t["month"] <= 3]["amount"], errors='coerce').fillna(0).sum() if not df_t.empty else 0
            h1_v = pd.to_numeric(df_t[df_t["month"] <= 6]["amount"], errors='coerce').fillna(0).sum() if not df_t.empty else 0
            st.markdown(render_overview_quick_exec_card(cat, q1_v, h1_v, conf), unsafe_allow_html=True)
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
        st.markdown('<b style="font-size:1.1rem; color:#1e3a8a; border-left:5px solid #2563eb; padding-left:10px;">📊 지출 비중 상위 항목</b>', unsafe_allow_html=True)
        st.markdown('<div style="font-size:0.82rem; color:#64748b; font-weight:700; margin:6px 0 10px 0;">도넛 차트 대신 금액과 비중이 바로 보이는 가로 막대형으로 표시합니다.</div>', unsafe_allow_html=True)
        cat_dist = df_26.groupby("category", as_index=False)["amount"].sum() if not df_all.empty else pd.DataFrame()
        if not cat_dist.empty and cat_dist["amount"].sum() > 0:
            cat_dist = cat_dist[cat_dist["amount"] > 0].copy()
            total_dist_amount = float(cat_dist["amount"].sum())
            cat_dist["share"] = cat_dist["amount"] / total_dist_amount * 100
            cat_dist = cat_dist.sort_values("amount", ascending=False)

            top_n = 8
            if len(cat_dist) > top_n:
                top_dist = cat_dist.head(top_n).copy()
                etc_amount = float(cat_dist.iloc[top_n:]["amount"].sum())
                etc_share = etc_amount / total_dist_amount * 100 if total_dist_amount else 0
                etc_row = pd.DataFrame([{"category": "기타", "amount": etc_amount, "share": etc_share}])
                chart_dist = pd.concat([top_dist, etc_row], ignore_index=True)
            else:
                chart_dist = cat_dist.copy()

            chart_dist["amount_label"] = chart_dist["amount"].apply(lambda x: f"{int(x):,}원")
            chart_dist["share_label"] = chart_dist["share"].apply(lambda x: f"{x:.1f}%")
            chart_dist["label"] = chart_dist.apply(lambda r: f"{r['amount_label']} · {r['share_label']}", axis=1)
            chart_dist["category_label"] = chart_dist["category"].astype(str)

            base = alt.Chart(chart_dist).encode(
                y=alt.Y("category_label:N", sort="-x", title=None, axis=alt.Axis(labelLimit=220, labelFontSize=12, labelFontWeight="bold")),
                x=alt.X("amount:Q", title=None, axis=alt.Axis(format=",.0f", labelFontSize=11)),
                tooltip=[
                    alt.Tooltip("category_label:N", title="항목"),
                    alt.Tooltip("amount:Q", title="금액", format=",.0f"),
                    alt.Tooltip("share:Q", title="비중", format=".1f")
                ]
            )
            bars = base.mark_bar(cornerRadiusEnd=6, size=22).encode(
                color=alt.Color("category_label:N", legend=None, scale=alt.Scale(scheme="tableau20"))
            )
            labels = base.mark_text(align="left", baseline="middle", dx=6, fontSize=12, fontWeight="bold", color="#0f172a").encode(
                text="label:N"
            )
            fig = (bars + labels).properties(height=max(280, len(chart_dist) * 38))
            st.altair_chart(fig, use_container_width=True)

            table_view = chart_dist[["category", "amount", "share"]].copy()
            table_view.columns = ["항목", "금액", "비중"]
            table_view["금액"] = table_view["금액"].apply(lambda x: f"{int(x):,}원")
            table_view["비중"] = table_view["비중"].apply(lambda x: f"{x:.1f}%")
            st.dataframe(table_view, use_container_width=True, hide_index=True, height=min(360, 42 + len(table_view) * 36))
        else:
            st.info("2026년 지출 데이터가 아직 없습니다.")
            
    st.markdown("---"); st.markdown('<div class="section-header">📅 2026 전체 상세 지출 통합 그리드 (전수 편집 가능)</div>', unsafe_allow_html=True)
    if not df_all.empty:
        df_p = df_26.pivot(index="category", columns="month", values="amount").fillna(0).reindex(index=CATEGORIES, columns=MONTHS, fill_value=0)
        df_p.columns = [f"{m}월" for m in df_p.columns]
        df_d = df_p.map(lambda x: format(int(x), ","))
        
        ed = st.data_editor(df_d, height=550, key="main_editor_v292")
        
        if st.button("💾 통합 그리드 수정 내역 클라우드/로컬 저장", type="primary", key="btn_save_tab1_v292"):
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
if current_page == "📈 항목별 지출 분석":
    st.markdown('<div class="section-header">📈 지능형 분석 및 실시간 연동 (Semantic Sync)</div>', unsafe_allow_html=True)
    if st.button("🔄 지출내역 수동 연동 실행", type="primary"):
        if sync_daily_to_master_auto():
            st.success("✅ 동기화 완료! 실적이 갱신되었습니다."); time.sleep(1); st.rerun()
    
    # [V20] 항목별 지출 분석 상단: 전체 현황 카드 요약
    st.markdown("""
<div style="margin-top:14px; margin-bottom:8px;">
  <div style="font-size:1.15rem; font-weight:950; color:#0f172a;">📊 전체 관리항목 현황</div>
  <div style="font-size:.86rem; color:#64748b; font-weight:750; margin-top:3px;">관리항목별 2026년 집행 현황을 먼저 확인하고, 아래에서 세부 항목을 선택해 월별 내역을 확인합니다.</div>
</div>
""", unsafe_allow_html=True)

    if not df_all.empty:
        df_2026_all = df_all[df_all["year"] == 2026].copy()
        total_2026_all = float(df_2026_all["amount"].sum()) if not df_2026_all.empty else 0.0
        cat_summary_rows = []
        for cat_name in CATEGORIES:
            dcat = df_2026_all[df_2026_all["category"] == cat_name] if not df_2026_all.empty else pd.DataFrame()
            total_cat = float(dcat["amount"].sum()) if not dcat.empty else 0.0
            paid_months = int((dcat["amount"] > 0).sum()) if not dcat.empty else 0
            recent_months = dcat[dcat["amount"] > 0]["month"].tolist() if not dcat.empty else []
            recent_month = max(recent_months) if recent_months else 0
            cat_summary_rows.append({
                "category": cat_name,
                "amount": total_cat,
                "paid_months": paid_months,
                "recent_month": recent_month,
            })
        paid_cats = sum(1 for r in cat_summary_rows if r["amount"] > 0)
        zero_cats = len(CATEGORIES) - paid_cats
        top_row = max(cat_summary_rows, key=lambda r: r["amount"]) if cat_summary_rows else {"category":"-", "amount":0}

        kpi_html = f"""
<div style="display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:12px; margin:10px 0 16px 0;">
  <div style="background:#f8fafc; border:1px solid #dbe7f6; border-radius:18px; padding:14px 16px;">
    <div style="font-size:.78rem; color:#64748b; font-weight:900;">2026 총 집행액</div>
    <div style="font-size:1.28rem; color:#0f172a; font-weight:950; margin-top:5px;">{int(total_2026_all):,}원</div>
  </div>
  <div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:18px; padding:14px 16px;">
    <div style="font-size:.78rem; color:#1d4ed8; font-weight:900;">집행 발생 항목</div>
    <div style="font-size:1.28rem; color:#1e40af; font-weight:950; margin-top:5px;">{paid_cats}개 / {len(CATEGORIES)}개</div>
  </div>
  <div style="background:#fff7ed; border:1px solid #fed7aa; border-radius:18px; padding:14px 16px;">
    <div style="font-size:.78rem; color:#c2410c; font-weight:900;">미집행 항목</div>
    <div style="font-size:1.28rem; color:#9a3412; font-weight:950; margin-top:5px;">{zero_cats}개</div>
  </div>
  <div style="background:#f0fdf4; border:1px solid #bbf7d0; border-radius:18px; padding:14px 16px;">
    <div style="font-size:.78rem; color:#15803d; font-weight:900;">최대 집행 항목</div>
    <div style="font-size:1.0rem; color:#166534; font-weight:950; margin-top:5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{top_row['category']}</div>
    <div style="font-size:.82rem; color:#166534; font-weight:850; margin-top:2px;">{int(top_row['amount']):,}원</div>
  </div>
</div>
"""
        st.markdown(kpi_html, unsafe_allow_html=True)

        # [V22] 관리항목별 요약 카드: 2026년 집행월 기준 동월누계 비교
        comparison_rows = []
        global_2026_months = df_all[(df_all["year"] == 2026) & (df_all["amount"] > 0)]["month"].tolist() if not df_all.empty else []
        global_latest_2026_month = max(global_2026_months) if global_2026_months else 0

        for cat_name in CATEGORIES:
            dcat_all_years = df_all[df_all["category"] == cat_name] if not df_all.empty else pd.DataFrame()
            dcat_2026 = dcat_all_years[dcat_all_years["year"] == 2026] if not dcat_all_years.empty else pd.DataFrame()
            recent_months = dcat_2026[dcat_2026["amount"] > 0]["month"].tolist() if not dcat_2026.empty else []
            recent_month = max(recent_months) if recent_months else 0
            compare_month = recent_month if recent_month else global_latest_2026_month
            if not compare_month:
                compare_month = 12

            v2024_same = float(dcat_all_years[(dcat_all_years["year"] == 2024) & (dcat_all_years["month"] <= compare_month)]["amount"].sum()) if not dcat_all_years.empty else 0.0
            v2025_same = float(dcat_all_years[(dcat_all_years["year"] == 2025) & (dcat_all_years["month"] <= compare_month)]["amount"].sum()) if not dcat_all_years.empty else 0.0
            v2026_same = float(dcat_all_years[(dcat_all_years["year"] == 2026) & (dcat_all_years["month"] <= compare_month)]["amount"].sum()) if not dcat_all_years.empty else 0.0
            paid_months = int((dcat_2026["amount"] > 0).sum()) if not dcat_2026.empty else 0

            yoy_gap = v2026_same - v2025_same
            if v2025_same > 0:
                yoy_rate = (yoy_gap / v2025_same) * 100
                yoy_text = f"전년 동월누계 대비 {yoy_rate:+.1f}%"
            else:
                yoy_text = "전년 동월누계 없음" if v2026_same == 0 else "전년 동월누계 실적 없음"
            comparison_rows.append({
                "category": cat_name,
                "v2024": int(v2024_same),
                "v2025": int(v2025_same),
                "v2026": int(v2026_same),
                "yoy_gap": int(yoy_gap),
                "yoy_text": yoy_text,
                "paid_months": paid_months,
                "recent_month": int(recent_month) if recent_month else 0,
                "compare_month": int(compare_month),
            })

        sorted_rows = sorted(comparison_rows, key=lambda r: r["v2026"], reverse=True)
        st.markdown("""
<div style='margin:4px 0 10px 0; padding:12px 14px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:16px;'>
  <div style='font-size:.95rem; font-weight:950; color:#0f172a;'>관리항목별 3개년 동월누계 비교</div>
  <div style='font-size:.80rem; font-weight:750; color:#64748b; margin-top:3px;'>2026년 각 항목의 최근 집행월 기준으로 2024년·2025년 같은 월까지의 누계와 비교합니다. 예: 2026년 4월까지 집행된 항목은 2024년 1~4월, 2025년 1~4월 누계와 비교합니다.</div>
</div>
""", unsafe_allow_html=True)

        card_html_parts = ["<div style='display:grid; grid-template-columns:repeat(3, minmax(0,1fr)); gap:14px; margin:6px 0 22px 0;'>"]
        for r in sorted_rows:
            status_text = "집행" if r["v2026"] > 0 else "미집행"
            status_bg = "#dcfce7" if r["v2026"] > 0 else "#fee2e2"
            status_color = "#15803d" if r["v2026"] > 0 else "#dc2626"
            gap_color = "#15803d" if r["yoy_gap"] > 0 else ("#dc2626" if r["yoy_gap"] < 0 else "#64748b")
            gap_sign = "+" if r["yoy_gap"] > 0 else ""
            recent_text = f"최근 {r['recent_month']}월" if r["recent_month"] else "최근 집행 없음"
            cm_text = f"1~{r['compare_month']}월 누계"
            card_html_parts.append(f"""
  <div style='background:white; border:1px solid #e2e8f0; border-radius:18px; padding:16px 17px; box-shadow:0 5px 14px rgba(15,23,42,.045);'>
    <div style='display:flex; justify-content:space-between; gap:10px; align-items:flex-start;'>
      <div style='font-size:1.02rem; font-weight:950; color:#0f172a; line-height:1.25;'>{r['category']}</div>
      <div style='background:{status_bg}; color:{status_color}; border-radius:999px; padding:4px 9px; font-size:.74rem; font-weight:950; white-space:nowrap;'>{status_text}</div>
    </div>
    <div style='margin-top:6px; font-size:.73rem; color:#64748b; font-weight:850;'>{cm_text} 기준</div>
    <div style='margin-top:10px; padding:12px 13px; background:#eff6ff; border-radius:14px;'>
      <div style='font-size:.76rem; font-weight:900; color:#1d4ed8;'>2026년 동월누계</div>
      <div style='font-size:1.32rem; font-weight:950; color:#1e3a8a; margin-top:3px;'>{r['v2026']:,}원</div>
    </div>
    <div style='display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:10px;'>
      <div style='background:#f8fafc; border:1px solid #e2e8f0; border-radius:12px; padding:10px;'>
        <div style='font-size:.74rem; font-weight:900; color:#64748b;'>2025년 동월누계</div>
        <div style='font-size:.98rem; font-weight:950; color:#334155; margin-top:3px;'>{r['v2025']:,}원</div>
      </div>
      <div style='background:#f8fafc; border:1px solid #e2e8f0; border-radius:12px; padding:10px;'>
        <div style='font-size:.74rem; font-weight:900; color:#64748b;'>2024년 동월누계</div>
        <div style='font-size:.98rem; font-weight:950; color:#334155; margin-top:3px;'>{r['v2024']:,}원</div>
      </div>
    </div>
    <div style='display:flex; justify-content:space-between; gap:10px; margin-top:11px; color:#64748b; font-size:.78rem; font-weight:850;'>
      <span>{recent_text} · {r['paid_months']}개월 집행</span>
      <span style='color:{gap_color};'>{gap_sign}{r['yoy_gap']:,}원 · {r['yoy_text']}</span>
    </div>
  </div>
""")
        card_html_parts.append("</div>")
        st.markdown("".join(card_html_parts), unsafe_allow_html=True)
    else:
        st.info("아직 표시할 지출 데이터가 없습니다. 일상경비 동기화 또는 수동 등록 후 전체 현황이 표시됩니다.")

    st.markdown("""
<div style="margin-top:4px; padding-top:12px; border-top:1px dashed #cbd5e1;">
  <div style="font-size:1.05rem; font-weight:950; color:#0f172a;">🔎 세부 항목 월별 분석</div>
</div>
""", unsafe_allow_html=True)
    sc = st.selectbox("관리 항목 선택", CATEGORIES, key="analysis_sel_v292")
    df_c = df_all[df_all["category"] == sc] if not df_all.empty else pd.DataFrame()
    
    if not df_c.empty:
        if sc in QUICK_EXEC_CONFIG:
            cf = QUICK_EXEC_CONFIG[sc]
            q1_e = df_c[(df_c["year"] == 2026) & (df_c["month"] <= 3)]["amount"].sum()
            h1_e = df_c[(df_c["year"] == 2026) & (df_c["month"] <= 6)]["amount"].sum()
            st.markdown(render_quick_goal_comparison_html(sc, cf["target"], q1_e, h1_e), unsafe_allow_html=True)
        
        m_cols = st.columns(3); v24, v25, v26 = df_c[df_c['year']==2024]['amount'].sum(), df_c[df_c['year']==2025]['amount'].sum(), df_c[df_c['year']==2026]['amount'].sum()
        m_cols[0].markdown(f'''<div class="metric-card" style="border-left-color: #94a3b8;"><div class="metric-label">📊 2024 실적</div><div class="metric-value">{int(v24):,}<span style="font-size:0.9rem; margin-left:5px; color:#94a3b8;">원</span></div></div>''', unsafe_allow_html=True); m_cols[1].markdown(f'''<div class="metric-card" style="border-left-color: #10b981;"><div class="metric-label">📊 2025 실적</div><div class="metric-value">{int(v25):,}<span style="font-size:0.9rem; margin-left:5px; color:#94a3b8;">원</span></div></div>''', unsafe_allow_html=True); m_cols[2].markdown(f'''<div class="metric-card" style="border-left-color: #3b82f6;"><div class="metric-label">📅 2026 연동 실적</div><div class="metric-value">{int(v26):,}<span style="font-size:0.9rem; margin-left:5px; color:#94a3b8;">원</span></div></div>''', unsafe_allow_html=True)
        
        df_p_c = df_c.pivot(index="month", columns="year", values="amount").fillna(0).reindex(columns=YEARS, fill_value=0); df_p_c.columns = [f"{c}년" for c in df_p_c.columns]; df_d_c = df_p_c.map(lambda x: format(int(x), ",")).reset_index(); df_d_c["월"] = df_d_c["month"].apply(lambda x: f"{x}월")
        
        ed_c = st.data_editor(df_d_c[["월", "2024년", "2025년", "2026년"]], hide_index=True, key=f"ed_v292_{sc}", height=450)
        
        if st.button("💾 분석 데이터 수정 내역 영구 저장", type="primary", key=f"btn_save_tab2_v292_{sc}"):
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
if current_page == "🚨 미집행 누락":
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

    st.markdown('<span class="section-label">🚨 2026년 지출 누락 점검</span>', unsafe_allow_html=True)
    now = datetime.now()
    cy, cm = now.year, now.month
    check_until_month = (cm - 1) if cy == CHECK_YEAR else 12
    check_until_month = max(1, min(12, check_until_month))

    st.markdown(
        '<div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:18px; padding:16px 18px; margin-bottom:16px;">'
        '<div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px; flex-wrap:wrap;">'
        '<div>'
        '<div style="font-size:1.05rem; font-weight:950; color:#0f172a;">2026년 기준 월별 미집행 후보를 사용자가 직접 보정합니다.</div>'
        f'<div style="font-size:.86rem; color:#64748b; font-weight:750; margin-top:4px;">점검년도: {CHECK_YEAR}년 · 점검범위: 1~{check_until_month}월 · 체크한 월은 누락에서 제외됩니다.</div>'
        '</div>'
        f'<div style="background:#e0f2fe; color:#075985; border:1px solid #bae6fd; border-radius:999px; padding:8px 13px; font-size:.86rem; font-weight:950; white-space:nowrap;">점검년도 {CHECK_YEAR}</div>'
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
        k2.metric("누락 후보", f"{raw_total}개월")
        k3.metric("사용자 제외", f"{excluded_total}개월")
        k4.metric("최종 확인 필요", f"{effective_total}개월")

        st.markdown("### 🛠️ 누락 제외 빠른 처리")
        act1, act2, act3 = st.columns([1, 1, 1])
        with act1:
            if st.button(f"🤖 자동추천 {rec_total}개월 제외 적용", use_container_width=True, disabled=(rec_total == 0)):
                for cat, months in recommendations.items():
                    overrides.setdefault(cat, {})
                    for m in months:
                        if not overrides[cat].get(str(m), False):
                            overrides[cat][str(m)] = True
                            append_missing_override_log(cat, m, "자동추천 제외", "후지급 가능성 기준")
                save_missing_overrides(overrides)
                st.success("자동추천 월을 누락 제외 처리했습니다.")
                st.rerun()
        with act2:
            if st.button("✅ 전체 누락 후보 제외", use_container_width=True, disabled=(raw_total == 0)):
                for row in raw_missing_rows:
                    cat = row["category"]
                    overrides.setdefault(cat, {})
                    for m in row["months"]:
                        if not overrides[cat].get(str(m), False):
                            overrides[cat][str(m)] = True
                            append_missing_override_log(cat, m, "전체 제외", "사용자 일괄 처리")
                save_missing_overrides(overrides)
                st.success("전체 누락 후보를 제외 처리했습니다.")
                st.rerun()
        with act3:
            if st.button("↩️ 제외 설정 전체 초기화", use_container_width=True):
                overrides = {}
                save_missing_overrides(overrides)
                append_missing_override_log("전체", 0, "초기화", "제외 설정 전체 초기화")
                st.warning("누락 제외 설정을 초기화했습니다.")
                st.rerun()

        if recommendations:
            rec_text = []
            for cat, months in recommendations.items():
                rec_text.append(f"{cat}: {format_month_ranges(months)}")
            st.info("🤖 자동추천 기준: 미집행 월 이후 1~3개월 안에 같은 항목 지출이 있는 경우 후지급 가능성으로 추천합니다.  " + " / ".join(rec_text[:6]))

        st.markdown("### 🔎 최종 확인 대상")
        if not effective_missing_rows:
            st.success(f"✅ {CHECK_YEAR}년 기준 최종 확인 필요한 미집행 항목이 없습니다.")
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
                        f'<div style="font-size:1.02rem; font-weight:950; color:#0f172a; line-height:1.25;">{row.category}</div>'
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

        st.markdown("### ✅ 월별 누락 제외 조정")
        st.caption("체크된 월은 실제 지출이 0원이어도 ‘후지급·일괄지급 등 예외’로 보고 최종 누락에서 제외합니다.")

        if raw_missing_rows:
            raw_df = pd.DataFrame(raw_missing_rows).sort_values(["count", "category"], ascending=[False, True])
            for row in raw_df.itertuples(index=False):
                cat = row.category
                cat_key = re.sub(r"[^0-9a-zA-Z가-힣_]+", "_", str(cat))
                excluded_count = len([m for m in row.months if overrides.get(cat, {}).get(str(m), False)])
                rec_months = set(recommendations.get(cat, []))
                with st.expander(f"{cat} · 누락 후보 {row.count}개월 · 제외 {excluded_count}개월 · 최종 {row.count - excluded_count}개월", expanded=(row.count - excluded_count > 0)):
                    b1, b2, b3 = st.columns([1, 1, 2])
                    with b1:
                        if st.button("이 항목 전체 제외", key=f"all_ex_{cat_key}", use_container_width=True):
                            overrides.setdefault(cat, {})
                            for m in row.months:
                                if not overrides[cat].get(str(m), False):
                                    overrides[cat][str(m)] = True
                                    append_missing_override_log(cat, m, "항목 전체 제외", "사용자 처리")
                            save_missing_overrides(overrides)
                            st.rerun()
                    with b2:
                        if st.button("이 항목 제외 해제", key=f"clear_ex_{cat_key}", use_container_width=True):
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
                            label = f"{m}월 제외" + (" 🤖" if m in rec_months else "")
                            new_val = st.checkbox(label, value=old_val, key=f"miss_override_{cat_key}_{m}")
                            if new_val != old_val:
                                overrides.setdefault(cat, {})[str(m)] = bool(new_val)
                                append_missing_override_log(cat, m, "누락 제외" if new_val else "제외 해제", "월별 체크")
                                save_missing_overrides(overrides)
                                changed = True
            if changed:
                st.toast("월별 누락 제외 설정을 저장했습니다.")
                st.rerun()
        else:
            st.success("누락 후보가 없습니다.")

        st.markdown("### 📋 최종 누락 목록")
        if effective_missing_rows:
            table_df = pd.DataFrame(effective_missing_rows)[["year", "category", "month_text", "count"]].copy()
            table_df.columns = ["점검년도", "관리항목", "최종 확인월", "확인월수"]
            st.dataframe(table_df, use_container_width=True, hide_index=True, height=min(460, 42 + len(table_df) * 36))
        else:
            st.info("사용자 제외 설정 반영 후 최종 누락 목록이 없습니다.")

        with st.expander("🧾 최근 누락 제외 변경 로그", expanded=False):
            logs = load_missing_override_logs(50)
            if logs:
                log_df = pd.DataFrame(logs)
                log_df = log_df.rename(columns={"time":"시간", "year":"연도", "category":"관리항목", "month":"월", "action":"처리", "memo":"메모"})
                st.dataframe(log_df, use_container_width=True, hide_index=True, height=300)
            else:
                st.caption("아직 변경 로그가 없습니다.")
    else:
        st.info("아직 점검할 데이터가 없습니다.")

# --- TAB 4: 일상경비 지출현황 ---
if current_page == "📂 일상경비 동기화":
    st.markdown('<div class="section-header">📂 일상경비 데이터베이스 관리</div>', unsafe_allow_html=True)
    
    with st.expander("📥 [일반] 일상경비 지출내역 엑셀 업로드 (I열 예산과목 기준 자동 매핑)"):
        f = st.file_uploader("일반 양식 엑셀 파일 선택", type=["xlsx", "csv"], key="daily_up_v292")
        if f:
            file_content = f.read(); file_hash = hashlib.md5(file_content).hexdigest(); f.seek(0)
            if st.session_state['last_file_hash'] != file_hash:
                with st.spinner("일반 엑셀 양식을 분석 중입니다..."):
                    sheet_name, new_processed, attempts = parse_general_from_uploaded_file(f)
                    if new_processed is not None and new_processed:
                        merged_expenses, added_count = merge_expenses(st.session_state.get('daily_expenses', []), new_processed)
                        
                        if save_daily_expenses(merged_expenses):
                            st.session_state['daily_expenses'] = merged_expenses
                            st.session_state['last_file_hash'] = file_hash
                            sync_daily_to_master_auto()
                            st.success(f"✅ 일반 동기화 완료! 반영 시트: {sheet_name} / 새로운 내역 {added_count}건 저장")
                            time.sleep(1.5); st.rerun()
                    else:
                        st.error("❌ 반영할 시트를 찾지 못했습니다. 아래 시트별 인식 결과를 확인해주세요.")
                        if attempts:
                            st.dataframe(pd.DataFrame(attempts, columns=["시트명", "인식 건수"]), use_container_width=True)
                    
    with st.expander("📥 [특수] 5대 용역/수수료 전용 파일 업로드 (B열 지급월, K열 문서제목 기준)"):
        st.info("📌 전용 관리 항목: **신용카드수수료, 무인경비, 공청기비데, 야간경비, 환경(청소)용역**\n\n특수 양식은 B열의 월(Month) 정보와 K열의 제목을 바탕으로 100% 강제 분리되어 기록됩니다.")
        f_sp = st.file_uploader("특수 양식 엑셀 파일 선택", type=["xlsx", "csv"], key="special_up_v292")
        if f_sp:
            sp_file_content = f_sp.read(); sp_file_hash = hashlib.md5(sp_file_content).hexdigest(); f_sp.seek(0)
            if st.session_state.get('last_sp_file_hash') != sp_file_hash:
                with st.spinner("특수 양식을 분석하여 5대 항목을 강제 추출 중입니다..."):
                    sheet_name_sp, new_processed_sp, attempts_sp = parse_special_from_uploaded_file(f_sp)
                    if new_processed_sp is not None and new_processed_sp:
                        merged_expenses_sp, added_count_sp = merge_expenses(st.session_state.get('daily_expenses', []), new_processed_sp)
                        
                        if save_daily_expenses(merged_expenses_sp):
                            st.session_state['daily_expenses'] = merged_expenses_sp
                            st.session_state['last_sp_file_hash'] = sp_file_hash
                            sync_daily_to_master_auto()
                            st.success(f"✅ 5대 특수 항목 완료! 반영 시트: {sheet_name_sp} / {added_count_sp}건 저장")
                            time.sleep(1.5); st.rerun()
                    else:
                        st.error("❌ 처리할 수 있는 특수 항목 데이터를 찾지 못했습니다. 아래 시트별 인식 결과를 확인해주세요.")
                        if attempts_sp:
                            st.dataframe(pd.DataFrame(attempts_sp, columns=["시트명", "인식 건수"]), use_container_width=True)
    
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
        fcat = fc1.selectbox("세목 필터", ["전체"] + sorted(df_d["세목"].unique().tolist()), key="f1_v292")
        sq = fc2.text_input("적요 내용 검색", key="f2_v292")
        
        disp = df_d.copy()
        if fcat != "전체": 
            disp = disp[disp["세목"] == fcat]
                
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
            key="daily_editor_v292"
        )
        
        del_c1, del_c2, del_c3 = st.columns([2, 2, 6])
        with del_c1:
            if st.button("🗑️ 선택 항목 삭제", key="btn_del_sel_v292"):
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
            if st.button("⚠️ 전체 데이터 초기화", key="btn_del_all_v292"):
                if save_daily_expenses([]):
                    st.session_state['daily_expenses'] = []
                    sync_daily_to_master_auto()
                    st.success("✅ 모든 데이터가 완전히 초기화되었습니다.")
                    time.sleep(1.0); st.rerun()
    else:
        st.info("현재 저장된 일상경비 데이터가 없습니다. 엑셀 파일을 업로드해주세요.")

# --- TAB 5: 신속집행 대시보드 ---
if current_page == "🚀 신속집행 대시보드":
    st.markdown('<div class="section-header">📂 2026 상반기 신속집행 실시간 관리 (대상액 대비 실적)</div>', unsafe_allow_html=True)
    
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
        
        e_amt = float(df_all[(df_all['year'] == 2026) & (df_all['category'] == cat)]['amount'].sum()) if not df_all.empty else 0.0
            
        sub = sub.assign(month_num=sub['월'].apply(lambda x: int(str(x).replace('월',''))))
        plan_to_date = sub[sub['month_num'] <= current_m]["집행예정액"].sum()
        
        summary_list.append({
            "세목": cat, "대상액": t_amt, "누적계획": plan_to_date, "집행액": e_amt, 
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
            return float(df_all[(df_all['year'] == 2026) & (df_all['category'] == cat_name) & (df_all['month'].isin(months))]['amount'].sum())

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
            main_msg = f"남양주시 목표까지 달성했습니다. 현재 {int(total_actual):,}원 집행, 대상액 대비 {total_rate:.1f}%입니다."
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
      <div style="font-size:1.55rem; font-weight:950; color:#0f172a; margin-bottom:6px;">🚀 {period_choice} 신속집행 목표 대비 현재 위치</div>
      <div style="font-size:.95rem; color:#475569; font-weight:800;">행안부 목표와 남양주시 목표를 <b>대상액 대비 실적률</b> 기준으로 한 번에 비교합니다.</div>
    </div>
    <div style="background:{main_bg}; color:{main_color}; border-radius:999px; padding:9px 14px; font-weight:950; font-size:.92rem;">{main_msg}</div>
  </div>

  <div style="display:grid; grid-template-columns:1.2fr 1fr 1fr 1fr; gap:12px; margin-top:20px;">
    <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:18px; padding:16px;">
      <div style="font-size:.78rem; color:#64748b; font-weight:900;">현재 집행액</div>
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
        components.html(quick_dashboard_html, height=380, scrolling=False)

        gap_cols = st.columns(2)
        with gap_cols[0]:
            st.markdown(f'''<div style="background:#eff6ff; border-left:6px solid #2563eb; border-radius:16px; padding:15px;"><div style="font-size:.82rem; color:#1d4ed8; font-weight:950;">행안부 {period_choice} 목표 대비</div><div style="font-size:1.25rem; color:#0f172a; font-weight:950;">{'목표 달성' if mo_gap == 0 else f'{int(mo_gap):,}원 부족'}</div></div>''', unsafe_allow_html=True)
        with gap_cols[1]:
            st.markdown(f'''<div style="background:#fff1f2; border-left:6px solid #dc2626; border-radius:16px; padding:15px;"><div style="font-size:.82rem; color:#be123c; font-weight:950;">남양주시 {period_choice} 목표 대비</div><div style="font-size:1.25rem; color:#0f172a; font-weight:950;">{'목표 달성' if city_gap == 0 else f'{int(city_gap):,}원 부족'}</div></div>''', unsafe_allow_html=True)

        st.markdown('<div style="font-size:1.15rem; font-weight:950; color:#1e3a8a; margin:22px 0 8px 0;">📌 세목별 목표 대비 현황</div>', unsafe_allow_html=True)

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
            return f'''<div style="background:#ffffff; border:1px solid #e2e8f0; border-left:9px solid {conf.get('border','#3b82f6')}; border-radius:20px; padding:16px; box-shadow:0 8px 18px rgba(15,23,42,.06);"><div style="display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:10px;"><div style="font-size:1.08rem; color:#0f172a; font-weight:950;">{conf.get('icon','')} {cat}</div><div style="font-size:.82rem; color:{status_color}; font-weight:950;">{status}</div></div><div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:10px;"><div style="background:#f8fafc; border-radius:12px; padding:10px;"><div style="font-size:.72rem; color:#64748b; font-weight:900;">현재 집행액</div><div style="font-size:1rem; color:#0f172a; font-weight:950;">{int(actual):,}원</div></div><div style="background:#f8fafc; border-radius:12px; padding:10px;"><div style="font-size:.72rem; color:#64748b; font-weight:900;">실적률</div><div style="font-size:1rem; color:#2563eb; font-weight:950;">{rate:.1f}%</div></div></div><div style="position:relative; height:20px; background:#e5e7eb; border-radius:999px; overflow:hidden;"><div style="height:100%; width:{width:.1f}%; background:linear-gradient(90deg,#93c5fd,#2563eb); border-radius:999px;"></div><div style="position:absolute; left:{mo_goal_rate:.1f}%; top:0; height:100%; width:3px; background:#2563eb;"></div><div style="position:absolute; left:{city_goal_rate:.1f}%; top:0; height:100%; width:3px; background:#dc2626;"></div></div><div style="display:flex; justify-content:space-between; margin-top:8px; font-size:.74rem; color:#475569; font-weight:900;"><span>행안부 부족 {int(mo_gap2):,}원</span><span>남양주시 부족 {int(city_gap2):,}원</span></div></div>'''

        card_cols = st.columns(3)
        for i, row in df_summary.iterrows():
            with card_cols[i % 3]:
                st.markdown(_mini_goal_card(row['세목'], float(row['대상액']), float(row['기간집행액'])), unsafe_allow_html=True)

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
            key="rapid_editor_v292"
        )

        if st.button("💾 대상액/집행예정액 영구 저장", type="primary", key="save_rapid_btn_v292"):
            st.session_state['rapid_df'] = edited_df
            if save_rapid_df(edited_df): 
                st.success("✅ 저장 성공!"); time.sleep(0.5); st.rerun()

# --- TAB 6: 정량실적 ---
if current_page == "📂 1~12월 정량실적":
    st.markdown('<div class="section-header">📊 1~12월 정량실적 및 무결성 합계</div>', unsafe_allow_html=True)
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