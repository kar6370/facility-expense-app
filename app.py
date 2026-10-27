import streamlit as st
from owner_access import is_owner, render_owner_login, logout as owner_logout
from budget_workspace import render_budget_workspace
from budget_engine import BudgetStore, merge_unique, transaction_id, expense_code, CATEGORY_CODES, won, ValidationError
from transaction_import import parse_frame, review_import, commit_import, cell
from import_ui import review_source_layout
from laundry_review import laundry_review_rows
from account_rules import account_display, source_account
from service_rules import is_laundry_equipment_expense, is_laundry_service_text, is_laundry_supply_expense, special_category, LAUNDRY_MANAGEMENT_CODES, is_elevator_service_text, is_water_charge_text, ELEVATOR_MANAGEMENT_CODES, WATER_MANAGEMENT_CODES
from current_report import render_current_report, render_source_strip, rapid_period_amounts
from expense_integrity import atomic_write_json, management_sums, exclusive_expenditure_frame, management_contributions, sum_contributions
from source_review import quality_snapshot as source_quality_snapshot
from source_ui import render_quality_panel, render_monthly_source_grid
from execution_sync import (ASSET_CODE, ASSET_CATEGORY, PERIODS, preserve_execution_config,
    record_report_import, reference_index, reference_period, reconcile_asset_contributions,
    report_account_label, report_presentation_lines, asset_review)
from execution_ui import render_period_dashboard, render_asset_reconciliation
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
import copy
import html
import inspect
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
    page_title="예산집행관리 V45.9 | 정약용 펀그라운드",
    layout="wide",
    page_icon="🏢",
    initial_sidebar_state="expanded",
)

# -----------------------------------------------------------------------------
# 2. 글로벌 상수, 설정 및 과거 실적 데이터베이스 내장
# -----------------------------------------------------------------------------
CATEGORIES = ["전기요금", "상하수도", "통신요금", "복합기임대", "공청기비데", "상품매입비", "수입금", "자체소수선", "부서업무비", "무인경비", "승강기점검", "신용카드수수료", "환경용역", "세탁용역", "야간경비", "수탁자산취득비", "일반재료비", "전산개발비", "미디어실제습기"]
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
    "전산개발비": "전산개발비",
    "미디어실제습기": "미디어실 제습기",
}


def display_category(value):
    """기존 저장 키를 변경하지 않고 사용자 화면용 명칭을 반환합니다."""
    return CATEGORY_LABELS.get(str(value), str(value))


def render_full_width_chart(chart):
    """설치된 Streamlit이 지원하는 인자로 차트 너비를 지정합니다."""
    if "width" in inspect.signature(st.altair_chart).parameters:
        st.altair_chart(chart, width="stretch")
    else:
        st.altair_chart(chart, use_container_width=True)


def get_report_update_date(config):
    """원천 연계 또는 수기 보정 중 가장 최근 저장일을 보고서 작성기준일로 표시합니다."""
    if not isinstance(config, dict):
        return "-"
    meta = config.get("meta", {}) if isinstance(config.get("meta"), dict) else {}
    source_audit = config.get("source_audit", {}) if isinstance(config.get("source_audit"), dict) else {}
    raw_candidates = [
        config.get("last_updated"),
        meta.get("source_updated_at"),
        source_audit.get("updated_at"),
    ]
    parsed_candidates = []
    for raw_value in raw_candidates:
        if not raw_value:
            continue
        text_value = str(raw_value).strip().replace("Z", "+00:00")
        try:
            parsed_candidates.append(datetime.fromisoformat(text_value))
        except Exception:
            date_match = re.search(r"(20\d{2})[-./년\s]+(\d{1,2})[-./월\s]+(\d{1,2})", text_value)
            if date_match:
                try:
                    parsed_candidates.append(datetime(
                        int(date_match.group(1)),
                        int(date_match.group(2)),
                        int(date_match.group(3)),
                    ))
                except Exception:
                    pass
    if not parsed_candidates:
        return "-"
    latest = max(parsed_candidates, key=lambda value: value.replace(tzinfo=None))
    return f"{latest.year}. {latest.month}. {latest.day}."


def build_three_year_comparison(df_all, compare_month):
    """모든 항목을 하나의 공통 기준월로 맞춘 3개년 동월누계 비교표를 만듭니다."""
    try:
        cutoff_month = min(max(int(compare_month), 1), 12)
    except Exception:
        cutoff_month = 1
    if not isinstance(df_all, pd.DataFrame) or df_all.empty:
        return pd.DataFrame()

    comparison_rows = []
    for category in CATEGORIES:
        category_all = df_all[df_all["category"] == category]
        category_2026 = category_all[
            (category_all["year"] == 2026) & (category_all["month"] <= cutoff_month)
        ]
        values = {
            year: float(
                category_all[
                    (category_all["year"] == year)
                    & (category_all["month"] <= cutoff_month)
                ]["amount"].sum()
            )
            for year in YEARS
        }
        gap = values[2026] - values[2025]
        rate = (gap / values[2025] * 100.0) if values[2025] else None
        if gap > 0:
            direction = "증가"
        elif gap < 0:
            direction = "감소"
        else:
            direction = "동일"
        paid_months = category_2026[category_2026["amount"] > 0]["month"].tolist()
        comparison_rows.append({
            "집행관리 항목": display_category(category),
            "2024년 동월누계": int(round(values[2024])),
            "2025년 동월누계": int(round(values[2025])),
            "2026년 동월누계": int(round(values[2026])),
            "전년대비 증감액": int(round(gap)),
            "전년대비 증감률": rate,
            "변동방향": direction,
            "최근 집행월": f"{max(paid_months)}월" if paid_months else "-",
            "집행월수": int((category_2026["amount"] > 0).sum()),
            "_category": category,
        })
    return pd.DataFrame(comparison_rows)


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
    },
    "전산개발비": {
        "icon": "💻", "color": "#7c3aed", "bg": "#f5f3ff", "border": "#8b5cf6",
        "details": {1: "-", 2: "-", 3: "-", 4: "-", 5: "-", 6: "전산개발 과업 추진 여부 확인"}
    }
}

CORE_TARGETS = list(CORE_CONFIG.keys())

QUICK_EXEC_CONFIG = {
    "수탁자산취득비": {"target": 93464000, "goal_q1": 37385600, "goal_h1": 65424800, "goal_q1_rate": 0.40, "goal_h1_rate": 0.70},
    "일반재료비": {"target": 14300000, "goal_q1": 5720000, "goal_h1": 10010000, "goal_q1_rate": 0.40, "goal_h1_rate": 0.70},
    "상품매입비": {"target": 5450000, "goal_q1": 2180000, "goal_h1": 3815000, "goal_q1_rate": 0.40, "goal_h1_rate": 0.70},
    "전산개발비": {"target": 4900000, "goal_q1": 1960000, "goal_h1": 3430000, "goal_q1_rate": 0.40, "goal_h1_rate": 0.70},
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

# -----------------------------------------------------------------------------
# 통합 예산집행 보고서 기준값
# -----------------------------------------------------------------------------
# 월별 지출관리 데이터는 일부 반복·관리항목을 분석하기 위한 자료이고,
# 아래 보고서 기준값은 전체 사업예산의 예산액·예산현액·예산배정액과
# 공식 예산과목별 누계를 담습니다. 서로 범위가 다르므로 전체 지출액을
# 관리항목 합계로 대체하지 않고, 신속집행 4개 항목만 공통 마스터에서
# 자동 연계합니다.
REPORT_BUSINESS_LABELS = {
    "entrust": "정약용 펀그라운드 운영·관리(수탁)",
    "daily": "정약용 펀그라운드 운영·관리(일상경비)(수탁_일상)",
    "operating": "사업예산(대행사업비반환금 제외)",
    "capital": "자본예산",
    "excluded": "대행사업비반환금(관리 집행률 제외)",
}

REPORT_QUICK_CODE_TO_CATEGORY = {
    "405-12": "수탁자산취득비",
    "206-01": "일반재료비",
    "233": "상품매입비",
    "207-02": "전산개발비",
}

# v40에서 사용하던 수기 기준표입니다. v41은 이 객체를 화면의 월별 계획·
# 전년 비교값을 이관하는 골격으로만 사용하고, 총괄·사업별·예산과목별 값은
# 아래 ERP 원천 기준으로 전부 교체합니다.
REPORT_V40_LEGACY_CONFIG = {
    "meta": {
        "title": "정약용 펀그라운드 예산집행 현황 보고",
        "year": 2026,
        "base_month": 6,
        "annual_goal_rate": 96.0,
        "amount_unit": "천원",
        "source_note": "2026년 6월 예산집행현황 보고 기준값",
    },
    "totals": {
        "예산액": 2428907000,
        "예산현액": 2359620000,
        "예산배정액": 2093483000,
    },
    "business": [
        {
            "사업키": "entrust",
            "사업구분": REPORT_BUSINESS_LABELS["entrust"],
            "예산액": 1764266000,
            "예산현액": 1668670000,
            "예산배정액": 1511976000,
            "누적지출액": 451951993,
        },
        {
            "사업키": "daily",
            "사업구분": REPORT_BUSINESS_LABELS["daily"],
            "예산액": 664641000,
            "예산현액": 690950000,
            "예산배정액": 581507000,
            "누적지출액": 182885151,
        },
    ],
    "monthly": [
        {"월": 1, "집행계획액": 48547480, "지출액": 48547480, "전년동월지출액": 55572382},
        {"월": 2, "집행계획액": 120618317, "지출액": 118932067, "전년동월지출액": 95190578},
        {"월": 3, "집행계획액": 454959460, "지출액": 129614853, "전년동월지출액": 111036655},
        {"월": 4, "집행계획액": 164469100, "지출액": 113372157, "전년동월지출액": 149532871},
        {"월": 5, "집행계획액": 132284000, "지출액": 123479624, "전년동월지출액": 148557668},
        {"월": 6, "집행계획액": 161653000, "지출액": 97702403, "전년동월지출액": 395085484},
        {"월": 7, "집행계획액": 0, "지출액": 0, "전년동월지출액": 0},
        {"월": 8, "집행계획액": 0, "지출액": 0, "전년동월지출액": 0},
        {"월": 9, "집행계획액": 0, "지출액": 0, "전년동월지출액": 0},
        {"월": 10, "집행계획액": 0, "지출액": 0, "전년동월지출액": 0},
        {"월": 11, "집행계획액": 0, "지출액": 0, "전년동월지출액": 0},
        {"월": 12, "집행계획액": 0, "지출액": 0, "전년동월지출액": 0},
    ],
    "lines": [
        {"사업키": "entrust", "세목코드": "101-01", "세목명": "보수", "예산배정액": 356033000, "누적지출액": 157896559},
        {"사업키": "entrust", "세목코드": "101-03", "세목명": "공무직(무기계약)근로자보수", "예산배정액": 66405000, "누적지출액": 25542257},
        {"사업키": "entrust", "세목코드": "101-04", "세목명": "기간제근로자등보수", "예산배정액": 214731000, "누적지출액": 38818897},
        {"사업키": "entrust", "세목코드": "107-03", "세목명": "퇴직급여", "예산배정액": 85961000, "누적지출액": 0},
        {"사업키": "entrust", "세목코드": "109-01", "세목명": "일반직평가급등", "예산배정액": 60441000, "누적지출액": 0},
        {"사업키": "entrust", "세목코드": "109-02", "세목명": "공무직(무기계약)근로자평가급등", "예산배정액": 41192000, "누적지출액": 0},
        {"사업키": "entrust", "세목코드": "201-01", "세목명": "사무관리비", "예산배정액": 11584000, "누적지출액": 8445000},
        {"사업키": "entrust", "세목코드": "201-11", "세목명": "지급수수료", "예산배정액": 286020000, "누적지출액": 103755000},
        {"사업키": "entrust", "세목코드": "201-15", "세목명": "복리후생비", "예산배정액": 85361000, "누적지출액": 36469690},
        {"사업키": "entrust", "세목코드": "201-21", "세목명": "공공요금및제세", "예산배정액": 5561000, "누적지출액": 2118970},
        {"사업키": "entrust", "세목코드": "204-02", "세목명": "직급보조비", "예산배정액": 14040000, "누적지출액": 5560000},
        {"사업키": "entrust", "세목코드": "207-02", "세목명": "전산개발비", "예산배정액": 4900000, "누적지출액": 0},
        {"사업키": "entrust", "세목코드": "214-05", "세목명": "수선유지비", "예산배정액": 101890000, "누적지출액": 34172000},
        {"사업키": "entrust", "세목코드": "304-01", "세목명": "연금부담금", "예산배정액": 20435000, "누적지출액": 7454590},
        {"사업키": "entrust", "세목코드": "304-02", "세목명": "국민건강보험부담금", "예산배정액": 32942000, "누적지출액": 12158340},
        {"사업키": "entrust", "세목코드": "304-03", "세목명": "노인장기요양보험부담금", "예산배정액": 9377000, "누적지출액": 3326525},
        {"사업키": "entrust", "세목코드": "304-05", "세목명": "고용보험부담금", "예산배정액": 21639000, "누적지출액": 3352725},
        {"사업키": "entrust", "세목코드": "405-12", "세목명": "수탁자산취득비", "예산배정액": 93464000, "누적지출액": 8354870},
        {"사업키": "daily", "세목코드": "201-01", "세목명": "사무관리비", "예산배정액": 77320000, "누적지출액": 16529640},
        {"사업키": "daily", "세목코드": "201-02", "세목명": "공공운영비", "예산배정액": 2000000, "누적지출액": 0},
        {"사업키": "daily", "세목코드": "201-03", "세목명": "행사운영비", "예산배정액": 90000000, "누적지출액": 16335050},
        {"사업키": "daily", "세목코드": "201-11", "세목명": "지급수수료", "예산배정액": 67330000, "누적지출액": 24471155},
        {"사업키": "daily", "세목코드": "201-12", "세목명": "교육훈련비", "예산배정액": 8950000, "누적지출액": 3001800},
        {"사업키": "daily", "세목코드": "201-13", "세목명": "임차료", "예산배정액": 5400000, "누적지출액": 945010},
        {"사업키": "daily", "세목코드": "201-14", "세목명": "회의비", "예산배정액": 900000, "누적지출액": 0},
        {"사업키": "daily", "세목코드": "201-15", "세목명": "복리후생비", "예산배정액": 10126000, "누적지출액": 0},
        {"사업키": "daily", "세목코드": "201-21", "세목명": "공공요금및제세", "예산배정액": 33298000, "누적지출액": 16591580},
        {"사업키": "daily", "세목코드": "202-01", "세목명": "국내여비", "예산배정액": 1592118, "누적지출액": 1592118},
        {"사업키": "daily", "세목코드": "202-08", "세목명": "공무직(무기계약직)근로자등여비", "예산배정액": 1680000, "누적지출액": 500000},
        {"사업키": "daily", "세목코드": "204-02", "세목명": "직급보조비", "예산배정액": 14040000, "누적지출액": 13933655},
        {"사업키": "daily", "세목코드": "206-01", "세목명": "일반재료비", "예산배정액": 33040000, "누적지출액": 13700120},
        {"사업키": "daily", "세목코드": "214-05", "세목명": "수선유지비", "예산배정액": 43980000, "누적지출액": 15168860},
        {"사업키": "daily", "세목코드": "215", "세목명": "동력비", "예산배정액": 138138000, "누적지출액": 48879170},
        {"사업키": "daily", "세목코드": "217-01", "세목명": "정원가산업무비", "예산배정액": 510000, "누적지출액": 78660},
        {"사업키": "daily", "세목코드": "217-02", "세목명": "사회복무요원보상금", "예산배정액": 3600000, "누적지출액": 743960},
        {"사업키": "daily", "세목코드": "233", "세목명": "상품매입비", "예산배정액": 15000000, "누적지출액": 7483600},
        {"사업키": "daily", "세목코드": "301-09", "세목명": "행사실비지원금", "예산배정액": 39195000, "누적지출액": 11465470},
        {"사업키": "daily", "세목코드": "802-11", "세목명": "대행사업비반환금", "예산배정액": 109443000, "누적지출액": 0},
    ],
    "notes": {
        "monthly": "월별 집행계획액은 2026년 집행전망액 기준이며, 지출액은 실제 지급 기준입니다.",
        "lines": "예산과목별 집행률은 예산배정액 대비 누적 지출액으로 산정합니다.",
        "asset": "수탁자산취득비는 블라인드·CCTV·포충기·보안장비 등 집행계획을 별도 확인합니다.",
    },
}

# v41 기준 원천자료: 사용자가 제공한 2026년 6월 단월·1~6월 누적 ERP 집행현황.
# 이후 월에는 화면의 "원천자료 연계"에서 같은 양식의 두 파일을 올리면 이 값 전체가
# 다시 생성됩니다. 대행사업비반환금은 원천 합계에는 남기되 관리 집행률에서는 제외합니다.
REPORT_V41_SOURCE_LINE_VALUES = [
    ("101-01", "보수", 356033000, 157896559, 25992529),
    ("101-03", "공무직(무기계약)근로자보수", 66405000, 25542257, 6378142),
    ("101-04", "기간제근로자등보수", 214731000, 38818897, 9059989),
    ("107-03", "퇴직급여", 85961000, 0, 0),
    ("109-01", "일반직평가급등", 60441000, 0, 0),
    ("109-02", "공무직(무기계약)근로자평가급등", 41192000, 0, 0),
    ("201-01", "사무관리비", 88904000, 27718500, 3188920),
    ("201-02", "공공운영비", 2000000, 0, 0),
    ("201-03", "행사운영비", 90000000, 20137620, 5190060),
    ("201-11", "지급수수료", 353350000, 129675955, 26104590),
    ("201-12", "교육훈련비", 8950000, 3107000, 105200),
    ("201-13", "임차료", 5400000, 1493990, 548980),
    ("201-14", "회의비", 900000, 0, 0),
    ("201-15", "복리후생비", 95487000, 36469690, 3449145),
    ("201-21", "공공요금및제세", 38859000, 18931780, 1242860),
    ("202-01", "국내여비", 11040000, 1982118, 390000),
    ("202-08", "공무직(무기계약직)근로자등여비", 1680000, 600000, 100000),
    ("204-02", "직급보조비", 14040000, 6585000, 1025000),
    ("206-01", "일반재료비", 33040000, 16241240, 2541120),
    ("207-02", "전산개발비", 4900000, 0, 0),
    ("214-05", "수선유지비", 145870000, 52230860, 7124350),
    ("215", "동력비", 138138000, 56360180, 7481010),
    ("217-01", "정원가산업무비", 510000, 78660, 0),
    ("217-02", "부서업무비", 3600000, 831960, 88000),
    ("233", "상품매입비", 15000000, 7483600, 1993100),
    ("301-09", "행사실비지원금", 39195000, 11771470, 4011630),
    ("304-01", "연금부담금", 20435000, 7207920, 1332690),
    ("304-02", "국민건강보험부담금등", 32942000, 14478650, 4054260),
    ("304-03", "공무직(무기계약)부담금관련", 9377000, 3845010, 994235),
    ("304-05", "기간제근로자부담금관련", 21639000, 3607940, 988175),
    ("802-11", "대행사업비반환금", 266136311, 266136311, 266136311),
    ("405-12", "수탁자산취득비", 93464000, 42405870, 34984000),
]

# 사용자 제공 '(정약용 펀그라운드) 2026 집행전망액 관리_관리용.pdf' 1쪽
# 합계 행의 월별 계획액(원). 기존 보고서에 없던 하반기만 보완합니다.
REPORT_H2_PLAN_2026 = {
    7: 125994000,
    8: 126216250,
    9: 207126000,
    10: 374516250,
    11: 171584000,
    12: 331531000,
}
REPORT_H2_PLAN_SOURCE = "2026 집행전망액 관리_관리용.pdf · 합계"

# 기존 1~6월 비교값과 동일한 2025년 원본 PDF의 월별 합계(원).
# 왼쪽 연간 채무확정액과 월별 합계는 다르므로 연간 총액을 배분하지 않습니다.
REPORT_PREVIOUS_ACTUAL_2025 = {
    1: 55572382, 2: 95190578, 3: 111036655, 4: 149532871,
    5: 148557668, 6: 395085484, 7: 112275730, 8: 115774713,
    9: 136765158, 10: 111031861, 11: 193813010, 12: 356047272,
}
REPORT_PREVIOUS_ACTUAL_SOURCE = "2025 집행전망액 관리_관리용.pdf · 월별 합계(반환금 포함)"

REPORT_V41_DEFAULT_CONFIG = copy.deepcopy(REPORT_V40_LEGACY_CONFIG)
REPORT_V41_DEFAULT_CONFIG["meta"].update({
    "schema_version": 42,
    "monthly_plan_version": 1,
    "monthly_previous_version": 1,
    "source_mode": "erp_budget_execution",
    "source_note": "6월 집행현황·1~6월 집행현황 ERP 원천자료 연계",
    "source_files": ["6월 집행현황 (1).xlsx", "1~6월 집행현황 (1).xlsx"],
    "source_updated_at": "2026-07-23T15:44:00",
})
REPORT_V41_DEFAULT_CONFIG["totals"] = {
    "예산액": 2428907000,
    "예산현액": 2359620000,
    "예산배정액": 2093483000,
    "누적지출액": 685502726,
    "원천 예산배정액": 2359619311,
    "원천 누적지출액": 951639037,
    "관리대상 예산액": 2093483000,
    "관리대상 예산현액": 2093483000,
    "관리대상 예산배정액": 2093483000,
    "관리대상 누적지출액": 685502726,
    "반환금 예산배정액": 266136311,
    "반환금 지출액": 266136311,
}
REPORT_V41_DEFAULT_CONFIG["business"] = [
    {
        "사업키": "operating",
        "사업구분": REPORT_BUSINESS_LABELS["operating"],
        "예산액": 2000019000,
        "예산현액": 2000019000,
        "예산배정액": 2000019000,
        "누적지출액": 643096856,
    },
    {
        "사업키": "capital",
        "사업구분": REPORT_BUSINESS_LABELS["capital"],
        "예산액": 93464000,
        "예산현액": 93464000,
        "예산배정액": 93464000,
        "누적지출액": 42405870,
    },
]
for _monthly_row in REPORT_V41_DEFAULT_CONFIG["monthly"]:
    _monthly_row["집행계획액 출처"] = "기존 보고 기준값"
    _monthly_row["전년동월지출액"] = REPORT_PREVIOUS_ACTUAL_2025[_monthly_row["월"]]
    _monthly_row["전년동월지출액 출처"] = REPORT_PREVIOUS_ACTUAL_SOURCE
    if _monthly_row["월"] in REPORT_H2_PLAN_2026:
        _monthly_row["집행계획액"] = REPORT_H2_PLAN_2026[_monthly_row["월"]]
        _monthly_row["집행계획액 출처"] = REPORT_H2_PLAN_SOURCE
    if int(_monthly_row.get("월", 0)) == 6:
        _monthly_row["지출액"] = 148367985
REPORT_V41_DEFAULT_CONFIG["lines"] = [
    {
        "사업키": (
            "capital" if code == "405-12"
            else "excluded" if code == "802-11"
            else "operating"
        ),
        "세목코드": code,
        "세목명": name,
        "예산현액": allocation,
        "예산배정액": allocation,
        "원인행위액": cumulative_actual,
        "월지출액": monthly_actual,
        "누적지출액": cumulative_actual,
        "잔액": allocation - cumulative_actual,
        "집행률산정제외": code == "802-11",
        "원천연계": True,
    }
    for code, name, allocation, cumulative_actual, monthly_actual
    in REPORT_V41_SOURCE_LINE_VALUES
]
_v41_commitment_overrides = {
    "201-11": 202927670,
    "214-05": 78229550,
    "405-12": 81979800,
}
for _v41_line in REPORT_V41_DEFAULT_CONFIG["lines"]:
    _v41_code = _v41_line["세목코드"]
    _known_scopes = {r["사업키"] for r in REPORT_V40_LEGACY_CONFIG["lines"] if r["세목코드"] == _v41_code}
    _v41_line["경비구분"] = "운영·일상" if len(_known_scopes) > 1 else "운영경비" if _known_scopes == {"entrust"} else "일상경비" if _known_scopes == {"daily"} else ""
    if _v41_code in _v41_commitment_overrides:
        _v41_line["원인행위액"] = _v41_commitment_overrides[_v41_code]
    if _v41_code == "802-11":
        _v41_line["예산현액"] = 266137000
REPORT_V41_DEFAULT_CONFIG["notes"].update({
    "monthly": "단월 파일은 해당 월 지출액, 누적 파일은 기준월 말 누적 지출액의 원천입니다. 두 합계 차이는 검증표에서 별도로 확인합니다.",
    "lines": "예산과목별 집행률은 누적 원천의 예산배정액 대비 지출액으로 산정하며, 잔액은 예산배정액-지출액입니다.",
    "asset": "수탁자산취득비는 예산배정액·지출액·잔액만 직관적으로 표시합니다.",
})
REPORT_V41_DEFAULT_CONFIG["source_audit"] = {
    "status": "검증 완료",
    "base_month": 6,
    "monthly_file": "6월 집행현황 (1).xlsx",
    "cumulative_file": "1~6월 집행현황 (1).xlsx",
    "leaf_line_count": len(REPORT_V41_SOURCE_LINE_VALUES),
    "monthly_management_actual": 148367985,
    "cumulative_management_actual": 685502726,
    "return_actual": 266136311,
    "updated_at": "2026-07-23T15:44:00",
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
    # Legacy amount-only and fixed month exceptions are no longer used.
    return False


def force_mapped_category_for_known_cases(desc, row_cat="", budget_subj="", amount=0):
    code = expense_code({"세목":row_cat, "예산과목":budget_subj})
    if is_laundry_equipment_expense(desc,row_cat,budget_subj) or is_laundry_supply_expense(desc):
        return None
    if is_known_laundry_accrual_case(desc,amount) and (not code or code in LAUNDRY_MANAGEMENT_CODES) and "세탁" in str(desc)+str(row_cat)+str(budget_subj):
        return "세탁용역"
    if (not code or code == "405-12") and is_asset_acquisition_text(desc,row_cat,budget_subj):
        return "수탁자산취득비"
    return None


# [V11] 수탁자산취득비 수동 반영 데이터
# 이 항목은 사용자가 제공한 지출일자/적요/지급명령금액 기준으로
# 일상경비 동기화 업로드 여부와 무관하게 항목별 지출 분석에 반영합니다.
ASSET_ACQUISITION_PLAN_ROWS = [
    {"세부항목": "CCTV", "규격·수량": "1세트", "계획액": 1050000},
    {"세부항목": "포충기", "규격·수량": "실내 2대·실외 2대", "계획액": 1200000},
    {"세부항목": "블라인드", "규격·수량": "시설 내 설치", "계획액": 4000000},
    {"세부항목": "자동심장충격기(AED)", "규격·수량": "1식", "계획액": 2200000},
    {"세부항목": "차세대 방화벽", "규격·수량": "1식", "계획액": 1094000},
    {"세부항목": "DDoS 보안장비", "규격·수량": "1식", "계획액": 1841000},
    {"세부항목": "웹 방화벽", "규격·수량": "1식", "계획액": 1509000},
]

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

# Laundry totals are reconstructed exclusively from qualifying source titles.
MANUAL_LAUNDRY_ACCRUAL_ROWS = []

def get_manual_laundry_monthly_sums():
    return {}

def add_manual_laundry_to_sums_map(sums_map):
    return sums_map

def apply_manual_laundry_to_master_data(data):
    return data

def apply_manual_asset_to_master_data(data):
    # Initial known amounts are applied by source reconciliation, not a minimum floor.
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
report_ref = db.collection('artifacts').document(appId).collection('public').document('data').collection('facility_data').document('budget_report_v1') if db else None

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
        if prev is None or str(rec.get('updated_at', '')) >= str(prev.get('updated_at', '')):
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
        atomic_write_json('local_master.json', data)
        saved = True
    except: pass
    return saved

def save_and_register(year, cat, mon):
    if int(year) == 2026 and cat == ASSET_CATEGORY and current_execution_index()["monthly"].get(int(mon), {}).get(ASSET_CODE):
        st.warning("수탁자산취득비는 원장 기준금액을 유지합니다. 신속집행 관리의 ‘원장 대조’에서 세부내역을 수정해 주세요.")
        return
    if int(year) == 2026 and cat == "세탁용역":
        st.warning("세탁용역은 지정 제목의 원천거래로만 집계합니다. 일상경비 자료연계에서 원본을 반영해 주세요.")
        return
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

def apply_manual_cell_changes(updates):
    """Save current-year cell deltas as source rows, preserving their evidence."""
    current = ensure_data_integrity(copy.deepcopy(st.session_state["data"]))
    lookup = {(r["year"], r["category"], r["month"]):r for r in current["records"]}
    changes = []
    try:
        for year, category, month, value in updates:
            key = (int(year), category, int(month))
            if key not in lookup:
                raise ValidationError("알 수 없는 연도·항목·월입니다")
            amount = won(value)
            delta = amount - won(lookup[key]["amount"])
            if delta:
                if int(year) == 2026 and category == ASSET_CATEGORY and current_execution_index()["monthly"].get(int(month), {}).get(ASSET_CODE):
                    raise ValidationError("수탁자산취득비는 원장 기준금액과 다르게 저장할 수 없습니다. 신속집행 관리의 원장 대조에서 세부내역을 수정하세요")
                if int(year) == 2026 and category == "세탁용역":
                    raise ValidationError("세탁용역은 지정 제목의 원천거래를 수정·반영해 주세요")
                changes.append((key, amount, delta))
    except (ValidationError, ValueError, TypeError) as exc:
        st.error(f"원 단위 정수 금액을 확인해 주세요: {exc}")
        return False
    if any(key[0] == 2026 for key, _, _ in changes) and not current.get("source_linked"):
        st.error("2026년 조정은 원천자료를 연계한 뒤 저장할 수 있습니다.")
        return False
    if not changes:
        st.info("변경된 금액이 없습니다.")
        return False
    daily = list(st.session_state.get("daily_expenses", []))
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    for (year, category, month), amount, delta in changes:
        if year == 2026:
            daily.append({"거래ID":f"manual:{stamp}:{category}:{month}",
                          "세목":category, "예산과목":category, "관리항목":category,
                          "집행일자":f"{year}-{month:02d}-01", "집행금액":delta,
                          "적요":f"{display_category(category)} 월별 지출액 조정 ({stamp})",
                          "업로드구분":"수동집계조정"})
    if len(daily) != len(st.session_state.get("daily_expenses", [])):
        if not save_daily_expenses(daily):
            st.error("조정 원천내역을 저장하지 못했습니다.")
            return False
        st.session_state["daily_expenses"] = daily
        st.session_state.pop("source_filter", None)
        sync_daily_to_master_auto()
        if st.session_state.get("daily_sync_save_failed"):
            return False
        current = copy.deepcopy(st.session_state["data"])
    for (year, category, month), amount, delta in changes:
        if year != 2026:
            for r in current["records"]:
                if (r["year"], r["category"], r["month"]) == (year, category, month):
                    r["amount"], r["status"] = amount, "지출" if amount > 0 else "미지출"
    if not save_data_cloud(current):
        st.error("조정 후 월별 집계 저장을 완료하지 못했습니다. 원천자료에서 재집계를 확인해 주세요.")
        return False
    st.session_state["data"] = current
    st.toast(f"{len(changes)}개 월별 금액을 조정했습니다.")
    return True


def select_analysis_adjustment(category=None):
    """Open a fresh editor, or discard its draft when closed or switched."""
    st.session_state["analysis_adjustment_category"] = category
    st.session_state["analysis_adjustment_revision"] = st.session_state.get("analysis_adjustment_revision", 0) + 1


def render_analysis_adjustment(frame, category):
    opened = st.session_state.get("analysis_adjustment_category") == category
    st.button(
        "✕ 월별 지출액 조정 닫기" if opened else "✏️ 월별 지출액 조정",
        type="primary", width="stretch", key="analysis_monthly_adjustment_button",
        help="선택한 항목의 2024~2026년, 1~12월 금액을 본문에서 수정합니다.",
        on_click=select_analysis_adjustment, args=(None if opened else category,),
    )
    if not opened:
        st.caption(f"{display_category(category)}의 월별 금액을 수정하려면 위 버튼을 누르세요.")
        return

    with st.container(border=True, key="analysis_monthly_adjustment_panel"):
        st.markdown(f"#### {display_category(category)} · 월별 지출액 조정")
        st.caption("2024~2026년 · 1~12월 · 원 단위. 각 칸에 수정 후 최종 월 합계를 입력하고 아래 저장 버튼을 누르세요. 닫거나 다른 항목을 선택하면 저장하지 않은 변경은 취소됩니다.")
        message = st.session_state.pop("analysis_adjustment_saved", None)
        if message:
            st.success(message)
        disabled = ["월"]
        if not st.session_state["data"].get("source_linked"):
            disabled.append("2026년")
            st.info("2026년 금액은 일상경비 원천자료를 연계한 뒤 조정할 수 있습니다. 2024·2025년은 수정 가능합니다.")
        elif category == "세탁용역":
            disabled.append("2026년")
            st.info("2026년 세탁용역은 지정 제목의 원천거래로 집계합니다. 일상경비 자료연계에서 원천내역을 수정해 주세요.")
        elif category == ASSET_CATEGORY:
            st.info("2026년 수탁자산취득비는 원장 기준금액과 다르게 저장할 수 없습니다. 세부내역은 신속집행 관리의 ‘수탁자산취득비 원장 대조’에서 수정하세요.")
        monthly = frame.pivot(index="month", columns="year", values="amount").reindex(index=MONTHS, columns=YEARS).fillna(0)
        monthly.columns = [f"{year}년" for year in YEARS]
        monthly = monthly.astype("int64").reset_index(drop=True)
        monthly.insert(0, "월", [f"{month}월" for month in MONTHS])
        editor_key = f"analysis_monthly_editor_{category}_{st.session_state.get('analysis_adjustment_revision', 0)}"
        edited = st.data_editor(
            monthly, hide_index=True, width="stretch", height=500, row_height=36,
            key=editor_key, disabled=disabled, num_rows="fixed",
            column_config={
                "월": st.column_config.TextColumn(width="small"),
                **{f"{year}년": st.column_config.NumberColumn(format="%,d원", step=1, required=True) for year in YEARS},
            },
        )
        save_col, cancel_col = st.columns([2, 1])
        if save_col.button("변경한 월별 지출액 저장", type="primary", width="stretch", key="analysis_monthly_adjustment_save"):
            updates = [(year, category, month, edited.iloc[month-1][f"{year}년"]) for month in MONTHS for year in YEARS]
            if apply_manual_cell_changes(updates):
                select_analysis_adjustment(category)
                st.session_state["analysis_adjustment_saved"] = f"{display_category(category)}의 월별 지출액을 저장했습니다. 상세분석에도 반영됐습니다."
                st.rerun()
        cancel_col.button("취소하고 닫기", width="stretch", key="analysis_monthly_adjustment_cancel",
                          on_click=select_analysis_adjustment)


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
        atomic_write_json('local_daily.json', data_to_save)
        saved = True
    except: pass
    return saved

def get_default_rapid_df():
    rows = []
    targets = {"수탁자산취득비": 93464000, "일반재료비": 14300000, "상품매입비": 5450000, "전산개발비": 4900000}
    plans = {
        "수탁자산취득비": {2: 6250000, 3: 6644000, 6: 33219000},
        "일반재료비": {1: 3703000, 2: 3000000, 3: 550000, 4: 550000, 5: 3050000, 6: 550000},
        "상품매입비": {1: 1997000, 3: 1500000, 5: 2000000},
        "전산개발비": {},
    }
    for cat in CORE_TARGETS:
        for m in range(1, 13):
            p_amt = float(plans.get(cat, {}).get(m, 0.0))
            a_amt = p_amt if m == 1 else 0.0
            rows.append({"세목": cat, "월": f"{m}월", "대상액": targets.get(cat, 0) if m == 1 else 0, "집행예정액": p_amt, "실제집행액": a_amt})
    return pd.DataFrame(rows)

def ensure_rapid_df_integrity(df):
    """기존 3개 항목 저장본에 전산개발비 등 새 관리항목을 안전하게 보충합니다."""
    default_df = get_default_rapid_df()
    if not isinstance(df, pd.DataFrame) or df.empty or "세목" not in df.columns:
        return default_df
    current = df.copy()
    for column in ["대상액", "집행예정액", "실제집행액"]:
        if column not in current.columns:
            current[column] = 0.0
        current[column] = pd.to_numeric(current[column], errors="coerce").fillna(0.0)
    current["월"] = current.get("월", "").astype(str).str.extract(r"(\d+)")[0].fillna("0").astype(int).apply(lambda value: f"{value}월")
    existing_keys = set(zip(current["세목"].astype(str), current["월"].astype(str)))
    missing_rows = [
        row.to_dict()
        for _, row in default_df.iterrows()
        if (str(row["세목"]), str(row["월"])) not in existing_keys
    ]
    if missing_rows:
        current = pd.concat([current, pd.DataFrame(missing_rows)], ignore_index=True)
    order_map = {category: idx for idx, category in enumerate(CORE_TARGETS)}
    current["_cat_order"] = current["세목"].map(order_map).fillna(len(order_map))
    current["_month_order"] = pd.to_numeric(current["월"].astype(str).str.extract(r"(\d+)")[0], errors="coerce").fillna(0)
    return current.sort_values(["_cat_order", "_month_order"]).drop(columns=["_cat_order", "_month_order"]).reset_index(drop=True)


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
                        return ensure_rapid_df_integrity(df)
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
                        return ensure_rapid_df_integrity(df)
        except: pass
    return ensure_rapid_df_integrity(get_default_rapid_df())

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
        atomic_write_json('local_rapid.json', data_to_save)
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
        atomic_write_json(f'local_quant_{year}_{month}.json', data_to_save)
        saved = True
    except: pass
    return saved

def merge_report_monthly_edits(existing_rows, edited_rows):
    """계획·전년 비교값의 출처와 직접 입력한 0원을 재실행 후에도 유지합니다."""
    monthly_map = {int(row["월"]): dict(row) for row in existing_rows}
    for edited_row in edited_rows:
        month = int(edited_row["월"])
        if month not in monthly_map:
            continue
        row = monthly_map[month]
        if clean_numeric(edited_row.get("집행계획액", 0)) != clean_numeric(row.get("집행계획액", 0)):
            row["집행계획액 출처"] = "사용자 입력"
        if "전년동월지출액" in edited_row and clean_numeric(edited_row["전년동월지출액"]) != clean_numeric(row.get("전년동월지출액", 0)):
            row["전년동월지출액 출처"] = "사용자 입력"
        for column in ("집행계획액", "지출액", "전년동월지출액"):
            if column in edited_row:
                row[column] = float(clean_numeric(edited_row[column]))
    return [monthly_map[month] for month in sorted(monthly_map)]


def ensure_report_config(config):
    """이전 저장본 일부 필드가 없어도 통합 보고서가 열리도록 기본값과 병합합니다."""
    default = copy.deepcopy(REPORT_V41_DEFAULT_CONFIG)
    if not isinstance(config, dict):
        return default

    # v40의 알려진 오류값(국내여비 배정액=지출액 1,592,118원)이 저장된 경우
    # 해당 수기 기준표만 폐기하고 v41 원천자료 기준으로 자동 이관합니다.
    incoming_config = copy.deepcopy(config)
    legacy_lines = incoming_config.get("lines", [])
    has_v40_domestic_travel_bug = any(
        str(row.get("세목코드", "")).strip() == "202-01"
        and int(round(clean_numeric(row.get("예산배정액", 0)))) == 1592118
        and int(round(clean_numeric(row.get("누적지출액", 0)))) == 1592118
        for row in legacy_lines
        if isinstance(row, dict)
    )
    source_mode = str(incoming_config.get("meta", {}).get("source_mode", "")).strip()
    if has_v40_domestic_travel_bug and source_mode != "erp_budget_execution":
        for legacy_section in ["totals", "business", "monthly", "lines", "source_audit"]:
            incoming_config.pop(legacy_section, None)
        if isinstance(incoming_config.get("meta"), dict):
            incoming_config["meta"].pop("source_note", None)
            incoming_config["meta"]["migration_note"] = "v40 수기 예산과목표를 v41 원천자료 기준으로 교체"

    merged = default
    for section in ["meta", "totals", "notes"]:
        incoming = incoming_config.get(section)
        if isinstance(incoming, dict):
            merged[section].update(incoming)

    business_rows = incoming_config.get("business")
    if isinstance(business_rows, list) and business_rows:
        normalized_business = []
        for idx, row in enumerate(business_rows):
            if not isinstance(row, dict):
                continue
            business_key = str(row.get("사업키", "")).strip()
            if business_key not in REPORT_BUSINESS_LABELS:
                business_key = "entrust" if idx == 0 else "daily"
            normalized_business.append({
                "사업키": business_key,
                "사업구분": str(row.get("사업구분") or REPORT_BUSINESS_LABELS[business_key]),
                "예산액": float(clean_numeric(row.get("예산액", 0))),
                "예산현액": float(clean_numeric(row.get("예산현액", 0))),
                "예산배정액": float(clean_numeric(row.get("예산배정액", 0))),
                "누적지출액": float(clean_numeric(row.get("누적지출액", 0))),
            })
        if normalized_business:
            merged["business"] = normalized_business

    monthly_map = {
        int(row["월"]): dict(row)
        for row in default["monthly"]
        if isinstance(row, dict) and 1 <= int(row.get("월", 0)) <= 12
    }
    incoming_monthly = incoming_config.get("monthly")
    old_plan_version = int(clean_numeric(incoming_config.get("meta", {}).get("monthly_plan_version", 0)))
    old_previous_version = int(clean_numeric(incoming_config.get("meta", {}).get("monthly_previous_version", 0)))
    if isinstance(incoming_monthly, list):
        for row in incoming_monthly:
            if not isinstance(row, dict):
                continue
            try:
                month = int(row.get("월", 0))
            except Exception:
                continue
            if not 1 <= month <= 12:
                continue
            plan = float(clean_numeric(row.get("집행계획액", 0)))
            plan_source = str(row.get("집행계획액 출처") or "").strip()
            missing_plan = row.get("집행계획액") is None or str(row.get("집행계획액")).strip() == ""
            # V45.4까지 하반기 기본값은 0원이었습니다. 기존의 양수 계획과
            # 이관 후 저장한 값(의도적인 0원 포함)은 그대로 보존합니다.
            if month in REPORT_H2_PLAN_2026 and (
                missing_plan or (old_plan_version < 1 and plan == 0 and not plan_source)
            ):
                plan = float(REPORT_H2_PLAN_2026[month])
                plan_source = REPORT_H2_PLAN_SOURCE
            previous = float(clean_numeric(row.get("전년동월지출액", 0)))
            previous_source = str(row.get("전년동월지출액 출처") or "").strip()
            missing_previous = row.get("전년동월지출액") is None or str(row.get("전년동월지출액")).strip() == ""
            if missing_previous or (month >= 7 and old_previous_version < 1 and previous == 0 and not previous_source):
                previous = float(REPORT_PREVIOUS_ACTUAL_2025[month])
                previous_source = REPORT_PREVIOUS_ACTUAL_SOURCE
            monthly_map[month] = {
                "월": month,
                "집행계획액": plan,
                "지출액": float(clean_numeric(row.get("지출액", 0))),
                "전년동월지출액": previous,
                "집행계획액 출처": plan_source or "기존 저장값",
                "전년동월지출액 출처": previous_source or "기존 저장값",
            }
    merged["monthly"] = [monthly_map[month] for month in range(1, 13)]
    merged["meta"]["monthly_plan_version"] = max(old_plan_version, 1)
    merged["meta"]["monthly_previous_version"] = max(old_previous_version, 1)

    line_rows = incoming_config.get("lines")
    if isinstance(line_rows, list) and line_rows:
        normalized_lines = []
        for row in line_rows:
            if not isinstance(row, dict):
                continue
            business_key = str(row.get("사업키", "")).strip()
            if business_key not in REPORT_BUSINESS_LABELS:
                business_key = "daily"
            code = str(row.get("세목코드", "")).strip()
            name = str(row.get("세목명", "")).strip()
            if not code and not name:
                continue
            allocation = float(clean_numeric(row.get("예산배정액", 0)))
            actual = float(clean_numeric(row.get("누적지출액", 0)))
            normalized_lines.append({
                "사업키": business_key,
                "세목코드": code,
                "세목명": name,
                "경비구분": str(row["경비구분"] if "경비구분" in row else next((r.get("경비구분", "") for r in REPORT_V41_DEFAULT_CONFIG["lines"] if r["세목코드"] == code), "")),
                "경비별내역": copy.deepcopy(row.get("경비별내역", [])),
                "예산현액": float(clean_numeric(row.get("예산현액", allocation))),
                "예산배정액": allocation,
                "원인행위액": float(clean_numeric(row.get("원인행위액", actual))),
                "월지출액": float(clean_numeric(row.get("월지출액", 0))),
                "누적지출액": actual,
                "잔액": float(clean_numeric(row.get("잔액", allocation - actual))),
                "집행률산정제외": bool(row.get("집행률산정제외", code == "802-11")),
                "원천연계": bool(row.get("원천연계", False)),
            })
        if normalized_lines:
            merged["lines"] = normalized_lines

    try:
        merged["meta"]["base_month"] = min(max(int(merged["meta"].get("base_month", 6)), 1), 12)
    except Exception:
        merged["meta"]["base_month"] = 6
    merged["meta"]["year"] = 2026
    merged["meta"]["annual_goal_rate"] = min(
        max(float(clean_numeric(merged["meta"].get("annual_goal_rate", 96.0))), 0.0),
        100.0,
    )
    merged["meta"]["amount_unit"] = "천원" if merged["meta"].get("amount_unit") not in ["원", "천원"] else merged["meta"]["amount_unit"]
    merged["meta"]["schema_version"] = max(int(clean_numeric(merged["meta"].get("schema_version", 42))), 42)
    if isinstance(incoming_config.get("source_audit"), dict):
        merged["source_audit"] = copy.deepcopy(incoming_config["source_audit"])
    if incoming_config.get("last_updated"):
        merged["last_updated"] = incoming_config.get("last_updated")
    merged = preserve_execution_config(incoming_config, merged)
    fixed_source = merged.get("execution_sources", {}).get(f"2026-{merged['meta']['base_month']:02d}")
    if fixed_source and fixed_source.get("verified"):
        for line in merged["lines"]:
            if line["세목코드"] == ASSET_CODE and ASSET_CODE in fixed_source.get("cumulative", {}):
                line["누적지출액"] = fixed_source["cumulative"][ASSET_CODE]
                line["월지출액"] = fixed_source.get("monthly", {}).get(ASSET_CODE, line["월지출액"])
                line["잔액"] = line["예산배정액"] - line["누적지출액"]
    return merged


def load_report_config():
    if not st.session_state["quota_exceeded"] and report_ref:
        try:
            doc = report_ref.get(timeout=3.0)
            if doc.exists:
                raw = doc.to_dict()
                return ensure_report_config(raw.get("config", raw))
        except Exception as e:
            check_quota_error(e)

    if os.path.exists("local_report.json"):
        try:
            with open("local_report.json", "r", encoding="utf-8") as f:
                raw = json.load(f)
                return ensure_report_config(raw.get("config", raw))
        except Exception:
            pass
    return ensure_report_config({})


def save_report_config(config):
    safe_config = ensure_report_config(config)
    source = safe_config.get("execution_sources", {}).get(f"2026-{safe_config['meta']['base_month']:02d}")
    proposed = next((r for r in config.get("lines", []) if r.get("세목코드") == ASSET_CODE), None)
    if source and source.get("verified") and proposed and ASSET_CODE in source.get("cumulative", {}):
        if clean_numeric(proposed.get("누적지출액", 0)) != source["cumulative"][ASSET_CODE]:
            st.error("수탁자산취득비 누계는 업로드한 원장 기준금액과 달라질 수 없습니다. 세부내역은 원장 대조에서 수정하거나, ERP 원장을 수정해 다시 반영하세요.")
            return False, safe_config
    safe_config["last_updated"] = datetime.now().isoformat()
    payload = {"config": safe_config, "last_updated": safe_config["last_updated"]}
    saved = False

    if not st.session_state["quota_exceeded"] and report_ref:
        try:
            report_ref.set(payload, timeout=4.0)
            saved = True
        except Exception as e:
            check_quota_error(e)

    try:
        atomic_write_json('local_report.json', payload)
        saved = True
    except Exception:
        pass
    return saved, safe_config


def get_quick_target_map(rapid_df=None):
    target_map = {
        category: float(cfg.get("target", 0))
        for category, cfg in QUICK_EXEC_CONFIG.items()
    }
    if isinstance(rapid_df, pd.DataFrame) and not rapid_df.empty and "세목" in rapid_df.columns:
        temp = rapid_df.copy()
        temp["대상액"] = pd.to_numeric(temp.get("대상액", 0), errors="coerce").fillna(0)
        for category in target_map:
            value = float(temp.loc[temp["세목"] == category, "대상액"].sum())
            if value > 0:
                target_map[category] = value
    return target_map


def get_quick_execution_rows(df_all, rapid_df, base_month, report_lines, execution_index=None):
    target_map = get_quick_target_map(rapid_df)
    budget_map = {}
    report_line_map = {}
    for row in report_lines:
        code = str(row.get("세목코드", "")).strip()
        if code:
            report_line_map[code] = row
        if code in REPORT_QUICK_CODE_TO_CATEGORY:
            budget_map[code] = max(
                budget_map.get(code, 0.0),
                float(clean_numeric(row.get("예산배정액", 0))),
            )

    plan_map = {category: 0.0 for category in target_map}
    if isinstance(rapid_df, pd.DataFrame) and not rapid_df.empty and "세목" in rapid_df.columns:
        temp = rapid_df.copy()
        temp["계획월"] = temp.get("월", "").astype(str).str.extract(r"(\d+)")[0]
        temp["계획월"] = pd.to_numeric(temp["계획월"], errors="coerce").fillna(0).astype(int)
        temp["집행예정액"] = pd.to_numeric(temp.get("집행예정액", 0), errors="coerce").fillna(0)
        for category in plan_map:
            plan_map[category] = float(
                temp[
                    (temp["세목"] == category)
                    & (temp["계획월"] > int(base_month))
                    & (temp["계획월"] <= 12)
                ]["집행예정액"].sum()
            )

    rows = []
    for code, category in REPORT_QUICK_CODE_TO_CATEGORY.items():
        target = float(target_map.get(category, 0))
        raw_q1 = 0.0
        raw_current = 0.0
        if isinstance(df_all, pd.DataFrame) and not df_all.empty:
            mask_base = (
                (df_all.get("year") == 2026)
                & (df_all.get("category") == category)
            )
            raw_q1 = float(pd.to_numeric(
                df_all.loc[mask_base & (df_all.get("month") <= min(base_month, 3)), "amount"],
                errors="coerce",
            ).fillna(0).sum())
            raw_current = float(pd.to_numeric(
                df_all.loc[mask_base & (df_all.get("month") <= int(base_month)), "amount"],
                errors="coerce",
            ).fillna(0).sum())
        source_line = report_line_map.get(code, {})
        raw_h1 = float(df_all.loc[(df_all["year"] == 2026) & (df_all["category"] == category)
                                 & (df_all["month"] <= min(base_month, 6)), "amount"].sum()) if isinstance(df_all, pd.DataFrame) and not df_all.empty else 0.0
        if execution_index:
            for label, end in (("q1", min(base_month, 3)), ("h1", min(base_month, 6)), ("current", base_month)):
                ref = reference_period(execution_index, code, 1, end)
                if ref:
                    if label == "q1": raw_q1 = ref["amount"]
                    elif label == "h1": raw_h1 = ref["amount"]
                    else: raw_current = ref["amount"]
        recognized_q1 = min(max(raw_q1, 0.0), target) if target else 0.0
        recognized_current = min(max(raw_current, 0.0), target) if target else 0.0
        remaining_plan = float(plan_map.get(category, 0))
        forecast = min(recognized_current + remaining_plan, target) if target else 0.0
        rows.append({
            "세목코드": code,
            "집행관리 항목": display_category(category),
            "내부키": category,
            "예산액": float(budget_map.get(code, target)),
            "신속집행 대상액": target,
            "1분기 인정지출액": recognized_q1,
            "상반기 인정지출액": min(max(raw_h1, 0), target) if target else 0,
            "기준월 인정지출액": recognized_current,
            "원천지출액": raw_current,
            "향후 집행계획액": remaining_plan,
            "계획반영 예상액": forecast,
            "대상액 대비 실적률": (recognized_current / target * 100.0) if target else 0.0,
            "계획반영 예상률": (forecast / target * 100.0) if target else 0.0,
            "잔여 대상액": max(target - recognized_current, 0.0),
        })
    return rows


def get_report_period_label(base_month):
    month = int(base_month)
    quarter = ((month - 1) // 3) + 1
    if month in [3, 6, 9, 12]:
        return f"{quarter}분기 누계"
    return f"{quarter}분기 {month}월 누계"


def build_report_snapshot(config, df_all, rapid_df, base_month=None, ledger_state=None):
    cfg = ensure_report_config(config)
    base_month = int(base_month or cfg["meta"].get("base_month", 6))
    base_month = min(max(base_month, 1), 12)
    totals = copy.deepcopy(cfg["totals"])
    business = copy.deepcopy(cfg["business"])
    monthly = [copy.deepcopy(row) for row in cfg["monthly"] if int(row.get("월", 0)) <= base_month]
    lines = copy.deepcopy(cfg["lines"])

    total_budget = float(clean_numeric(totals.get("예산액", 0)))
    total_current = float(clean_numeric(totals.get("예산현액", 0)))
    total_allocated = float(clean_numeric(totals.get("예산배정액", 0)))
    management_budget = float(clean_numeric(
        totals.get("관리대상 예산액", sum(float(clean_numeric(row.get("예산액", 0))) for row in business))
    ))
    management_current = float(clean_numeric(
        totals.get("관리대상 예산현액", sum(float(clean_numeric(row.get("예산현액", 0))) for row in business))
    ))
    total_actual = float(clean_numeric(
        totals.get(
            "관리대상 누적지출액",
            totals.get("누적지출액", sum(float(clean_numeric(row.get("누적지출액", 0))) for row in business)),
        )
    ))
    source_allocated = float(clean_numeric(totals.get("원천 예산배정액", total_allocated)))
    source_actual = float(clean_numeric(totals.get("원천 누적지출액", total_actual)))
    return_actual = float(clean_numeric(totals.get("반환금 지출액", 0)))
    annual_goal_rate = float(clean_numeric(cfg["meta"].get("annual_goal_rate", 96.0)))
    annual_goal_amount = total_allocated * annual_goal_rate / 100.0

    for row in business:
        current = float(clean_numeric(row.get("예산현액", 0)))
        allocated = float(clean_numeric(row.get("예산배정액", 0)))
        actual = float(clean_numeric(row.get("누적지출액", 0)))
        row["예산현액 대비 집행률"] = (actual / current * 100.0) if current else 0.0
        row["예산배정액 대비 집행률"] = (actual / allocated * 100.0) if allocated else 0.0
        row["예산현액 대비 미지출액"] = max(current - actual, 0.0)

    cumulative_actual = 0.0
    for row in monthly:
        plan = float(clean_numeric(row.get("집행계획액", 0)))
        actual = float(clean_numeric(row.get("지출액", 0)))
        previous = float(clean_numeric(row.get("전년동월지출액", 0)))
        cumulative_actual += actual
        row["계획대비 증감"] = actual - plan
        row["전년대비 증감"] = actual - previous
        row["계획대비 집행률"] = (actual / plan * 100.0) if plan else 0.0
        row["누적지출액"] = cumulative_actual

    for row in lines:
        allocation = float(clean_numeric(row.get("예산배정액", 0)))
        actual = float(clean_numeric(row.get("누적지출액", 0)))
        row["잔액"] = allocation - actual
        row["사업구분"] = REPORT_BUSINESS_LABELS.get(row.get("사업키"), row.get("사업키", ""))
        row["집행률"] = (actual / allocation * 100.0) if allocation else 0.0
        if bool(row.get("집행률산정제외")):
            row["상태"] = "관리 집행률 제외"
        elif actual <= 0:
            row["상태"] = "미집행"
        elif row["집행률"] >= 100:
            row["상태"] = "배정액 도달"
        elif row["집행률"] >= 80:
            row["상태"] = "집행률 높음"
        else:
            row["상태"] = "집행 중"

    quick_rows = get_quick_execution_rows(df_all, rapid_df, base_month, lines, reference_index(cfg, ledger_state))
    quick_bases = {}
    for basis_name, exclude_asset in [("수탁자산취득비 포함", False), ("수탁자산취득비 제외", True)]:
        basis_rows = [
            row for row in quick_rows
            if not (exclude_asset and row["세목코드"] == "405-12")
        ]
        target = sum(row["신속집행 대상액"] for row in basis_rows)
        q1_actual = sum(row["1분기 인정지출액"] for row in basis_rows)
        current_actual = sum(row["기준월 인정지출액"] for row in basis_rows)
        forecast = sum(row["계획반영 예상액"] for row in basis_rows)
        goal_rows = []
        for organization, goal_cfg in QUICK_EXEC_GOAL_RATES.items():
            for period_key, period_label in [("q1", "1분기"), ("h1", "상반기")]:
                goal_rate = float(goal_cfg[period_key]) * 100.0
                actual_for_period = q1_actual if period_key == "q1" else sum(row["상반기 인정지출액"] for row in basis_rows)
                goal_amount = target * goal_rate / 100.0
                gap = actual_for_period - goal_amount
                goal_rows.append({
                    "기관": organization,
                    "기간": period_label,
                    "목표율": goal_rate,
                    "목표액": goal_amount,
                    "인정지출액": actual_for_period,
                    "실적률": (actual_for_period / target * 100.0) if target else 0.0,
                    "차이": gap,
                    "결과": "달성" if gap >= 0 else "미달",
                })
        quick_bases[basis_name] = {
            "대상액": target,
            "1분기 인정지출액": q1_actual,
            "기준월 인정지출액": current_actual,
            "계획반영 예상액": forecast,
            "실적률": (current_actual / target * 100.0) if target else 0.0,
            "예상률": (forecast / target * 100.0) if target else 0.0,
            "잔여 대상액": max(target - current_actual, 0.0),
            "목표비교": goal_rows,
        }

    validations = []
    def add_validation(name, expected, actual, tolerance=1.0, note=""):
        difference = float(actual) - float(expected)
        validations.append({
            "검증항목": name,
            "결과": "일치" if abs(difference) <= tolerance else "확인 필요",
            "기준값": float(expected),
            "비교값": float(actual),
            "차이": difference,
            "비고": note,
        })

    add_validation(
        "관리대상 사업별 예산액 합계",
        management_budget,
        sum(float(clean_numeric(row.get("예산액", 0))) for row in business),
    )
    add_validation(
        "관리대상 사업별 예산현액 합계",
        management_current,
        sum(float(clean_numeric(row.get("예산현액", 0))) for row in business),
    )
    add_validation(
        "사업별 예산배정액 합계 = 총괄 예산배정액",
        total_allocated,
        sum(float(clean_numeric(row.get("예산배정액", 0))) for row in business),
    )
    add_validation(
        f"1~{base_month}월 월별 합계 = 관리대상 누적 원천",
        total_actual,
        sum(float(clean_numeric(row.get("지출액", 0))) for row in monthly),
        note="단월 파일과 누적 파일 차이는 자동 보정하지 않습니다.",
    )
    add_validation(
        "관리대상 예산과목별 지출액 합계",
        total_actual,
        sum(
            float(clean_numeric(row.get("누적지출액", 0)))
            for row in lines
            if not bool(row.get("집행률산정제외"))
        ),
        note="관리 집행률 제외 과목은 합계에서 제외합니다.",
    )
    if any(bool(row.get("원천연계")) for row in lines):
        add_validation(
            "원천 예산과목별 지출액 합계",
            source_actual,
            sum(float(clean_numeric(row.get("누적지출액", 0))) for row in lines),
            note="반환금을 포함한 ERP 지출액(E) 원천 합계입니다.",
        )
    quick_over_target = sum(
        1 for row in quick_rows
        if row["기준월 인정지출액"] > row["신속집행 대상액"] + 1
    )
    validations.append({
        "검증항목": "신속집행 인정액 대상액 상한",
        "결과": "일치" if quick_over_target == 0 else "확인 필요",
        "기준값": 0.0,
        "비교값": float(quick_over_target),
        "차이": float(quick_over_target),
        "비고": "각 항목 인정지출액은 대상액의 100%를 넘지 않습니다.",
    })

    return {
        "config": cfg,
        "base_month": base_month,
        "period_label": get_report_period_label(base_month),
        "totals": {
            "예산액": total_budget,
            "예산현액": total_current,
            "예산배정액": total_allocated,
            "누적지출액": total_actual,
            "관리대상 예산액": management_budget,
            "관리대상 예산현액": management_current,
            "원천 예산배정액": source_allocated,
            "원천 누적지출액": source_actual,
            "반환금 지출액": return_actual,
            "집행률": (total_actual / total_allocated * 100.0) if total_allocated else 0.0,
            "원천 전체예산 대비 집행률": (source_actual / total_budget * 100.0) if total_budget else 0.0,
            "예산현액 대비 집행률": (total_actual / management_current * 100.0) if management_current else 0.0,
            "예산현액 대비 미지출액": max(management_current - total_actual, 0.0),
            "집행잔액": total_allocated - total_actual,
            "연간목표율": annual_goal_rate,
            "연간목표액": annual_goal_amount,
            "목표대비 추가필요액": max(annual_goal_amount - total_actual, 0.0),
        },
        "business": business,
        "monthly": monthly,
        "lines": lines,
        "quick_rows": quick_rows,
        "quick_bases": quick_bases,
        "validations": validations,
        "validation_issue_count": sum(1 for row in validations if row["결과"] != "일치"),
    }


def merge_expenses(old_list, new_list):
    return merge_unique(old_list, new_list)

# -----------------------------------------------------------------------------
# ★ [V292 핵심] 공통 매핑 함수 (일반재료비 11.5M, 상하수도 2.3M 완벽 보장)
# -----------------------------------------------------------------------------
def _get_mapped_category_legacy(desc, row_cat, budget_subj=""):
    desc_no_space = str(desc).replace(" ", "").strip()
    row_cat_no_space = str(row_cat).replace(" ", "").strip()
    budget_no_space = str(budget_subj).replace(" ", "").strip()
    laundry_equipment_expense = is_laundry_equipment_expense(desc, row_cat, budget_subj) or is_laundry_supply_expense(desc)

    # [V10] 금액/적요 기반 강제 예외: 세탁용역 귀속월 보정건, 수탁자산취득비성 조달구매
    # amount는 기존 시그니처 호환을 위해 이 함수에서는 직접 받지 않지만,
    # desc/row_cat/budget_subj에 명확한 자산취득 키워드가 있으면 최우선 분류합니다.
    if is_asset_acquisition_text(desc, row_cat, budget_subj):
        return "수탁자산취득비"

    # [V5] 특수 업로드/수동 세목이 이미 관리항목명으로 들어온 경우 최우선 인정
    # 예: 5대 용역 파일에서 "고객편의기기 관리용역"을 "공청기비데"로 변환해 저장한 행
    for cat_name in CATEGORIES:
        if cat_name == "세탁용역":
            continue
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
        if "전산개발비" in budget_no_space: return "전산개발비"
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
    # 세탁용역은 '세탁' 한 단어가 아니라 용역·세탁대금 성격을 확인합니다.
    # 사무관리비의 세탁실 건조기·세탁기 수리비는 여기서 제외됩니다.
    safe_categories = ["환경용역", "상품매입비", "일반재료비", "자체소수선", "부서업무비"]
    for cat_name in safe_categories:
        if cat_name in row_cat_no_space or cat_name in desc_no_space:
            return cat_name
    if is_laundry_service_text(desc, row_cat, budget_subj):
        return "세탁용역"

    regular_service = special_category(desc)
    if regular_service:
        return regular_service

    # 4. 세부 키워드 매핑
    keyword_map = {
        "통신요금": ["통신요금", "통신비", "인터넷", "케이블"],
        "복합기임대": ["복합기임대", "복합기렌탈"], 
        "공청기비데": ["고객편의기기관리용역", "고객편의기기", "공기청정기렌탈", "공기청정기", "공청기", "비데렌탈", "비데"],
        "무인경비": ["무인경비용역"],
        "승강기점검": ["승강기유지보수", "승강기점검"], 
        "신용카드수수료": ["신용카드수수료"], 
        "환경용역": ["청소용역", "미화용역"],
        "야간경비": ["야간경비용역", "당직용역"],
        "상품매입비": ["상품매입", "자판기식음료", "종량제봉투"]
    }
    
    for m_cat, kws in keyword_map.items():
        if any(k in desc_no_space for k in kws):
            return m_cat
            
    return None

def get_mapped_category(desc, row_cat, budget_subj=""):
    code = expense_code({"세목": row_cat, "예산과목": budget_subj})
    direct = {"405-12":"수탁자산취득비", "206-01":"일반재료비", "233":"상품매입비", "207-02":"전산개발비"}
    if is_elevator_service_text(desc, row_cat, budget_subj) and (not code or code in ELEVATOR_MANAGEMENT_CODES):
        return "승강기점검"
    if is_water_charge_text(desc, row_cat, budget_subj) and (not code or code in WATER_MANAGEMENT_CODES):
        return "일반재료비" if code == "206-01" else "상하수도"
    if code in direct:
        return direct[code]
    candidate = "세탁용역" if is_laundry_service_text(desc, row_cat, budget_subj) else _get_mapped_category_legacy(desc, row_cat, budget_subj)
    if candidate == "세탁용역":
        return candidate if is_laundry_service_text(desc) and (not code or code in LAUNDRY_MANAGEMENT_CODES) else None
    if candidate == "상하수도" and not is_water_charge_text(desc, row_cat, budget_subj):
        return None
    if code and candidate in CATEGORY_CODES and CATEGORY_CODES[candidate] != code:
        return None
    return candidate


def get_row_management_category(row):
    if str(row.get("업로드구분", "")).startswith("수동집계조정") and row.get("세목") in CATEGORIES:
        if row["세목"] == "세탁용역" and not is_laundry_service_text(row.get("적요", "")):
            return None
        return row["세목"]
    desc, semok = cell(row.get("적요")), cell(row.get("세목"))
    budget = cell(row.get("원본I열")) or cell(row.get("예산과목"))
    official, origin, _ = source_account(row)
    if origin == "원본I열" and not official:
        return None
    if official:
        budget = official
    return force_mapped_category_for_known_cases(desc, semok, budget, row.get("집행금액", 0)) or get_mapped_category(desc, semok, budget)


def is_water_charge_row(desc, row_cat="", budget_subj=""):
    return is_water_charge_text(desc, row_cat, budget_subj)


def raw_management_evidence():
    state, _ = BudgetStore().load(2026)
    return management_contributions(st.session_state.get("daily_expenses", []),
        MANUAL_ASSET_ACQUISITION_ROWS, MANUAL_LAUNDRY_ACCRUAL_ROWS,
        get_row_management_category,
        lambda row: is_water_charge_row(row.get("적요", ""), row.get("세목", ""), row.get("예산과목", "")),
        state.get("accruals", {}), state.get("accrual_revisions", {}))


def current_execution_index():
    state, _ = BudgetStore().load(2026)
    return reference_index(st.session_state.get("report_config", {}), state)


def current_management_evidence():
    return reconcile_asset_contributions(raw_management_evidence(), st.session_state.get("report_config", {}), current_execution_index())


def save_execution_settings(config):
    saved, safe = save_report_config(config)
    if saved:
        st.session_state["report_config"] = safe
        st.session_state.pop("initial_sync_done", None)
        st.toast("원장 대조·설정을 저장했습니다.")
    else:
        st.error("원장 대조·설정을 저장하지 못했습니다.")
    return saved


def get_accrual_splits_for_special_cases(category, year, month, amount, desc):
    """Use the selected month; a coincidentally equal amount is not evidence."""
    try:
        m, amt = int(month), won(amount)
    except (ValueError, TypeError, ValidationError):
        return []
    return [(m, amt)] if 1 <= m <= 12 else []


def is_general_material_row(row):
    """예산과목 기준 일반재료비 여부."""
    budget = str(row.get('예산과목', '')).replace(' ', '')
    semok = str(row.get('세목', '')).replace(' ', '')
    return ("일반재료비" in budget) or ("일반재료비" in semok)

def sync_daily_to_master_auto():
    st.session_state["daily_sync_save_failed"] = False
    master = ensure_data_integrity(load_data())
    daily = st.session_state.get("daily_expenses", [])
    if not daily and os.path.exists("local_master.json") and not master.get("source_linked"):
        # Preserve unrelated historical manual totals, but never keep an
        # unbacked current-year laundry amount under the new source-only rule.
        changed = False
        for record in master.get("records", []):
            if record["year"] == 2026 and record["category"] == "세탁용역" and record["amount"]:
                record.update(amount=0, status="미지출")
                changed = True
        if changed:
            if not save_data_cloud(master):
                st.session_state["daily_sync_save_failed"] = True
                st.error("세탁용역 재집계 결과를 저장하지 못했습니다.")
                return False
            st.session_state["data"] = master
        return changed
    evidence = current_management_evidence()
    values = sum_contributions(evidence)
    water_overlap = {m:sum(v["amount"] for v in evidence if v["year"] == 2026 and v["month"] == m and v["category"] == "상하수도" and v["material_mirror"]) for m in MONTHS}
    changed = not master.get("source_linked", False)
    master["source_linked"] = True
    for r in master.get("records", []):
        if r["year"] == 2026:
            if r["category"] == "상하수도" and r.get("materials_overlap") != water_overlap[r["month"]]:
                r["materials_overlap"] = water_overlap[r["month"]]
                changed = True
            value = values.get(2026, {}).get(r["category"], {}).get(r["month"], 0)
            if clean_numeric(r["amount"]) != value:
                r["amount"] = value
                r["status"] = "지출" if value > 0 else "환불·여입" if value < 0 else "미지출"
                changed = True
    if changed:
        if not save_data_cloud(master):
            st.session_state["daily_sync_save_failed"] = True
            st.error("원천 재집계 결과를 저장하지 못했습니다. 기존 저장자료를 유지합니다.")
            return False
        st.session_state["data"] = master
    return changed

# -----------------------------------------------------------------------------
# 엑셀 파일 읽기 엔진: 여러 시트 자동 탐색
# -----------------------------------------------------------------------------
def read_excel_sheets_flexible(uploaded_file):
    """업로드 파일을 모든 시트 기준으로 header=None DataFrame dict로 반환합니다."""
    uploaded_file.seek(0)
    name = uploaded_file.name.lower()
    if name.endswith('.csv'):
        return {"CSV": pd.read_csv(uploaded_file, header=None)}

    # 일부 ERP 엑셀은 실제 셀이 수백 행 있어도 worksheet dimension을 A1로
    # 잘못 기록합니다. pandas는 이 경우 첫 셀만 읽으므로 openpyxl의
    # reset_dimensions()로 실제 사용범위를 다시 탐색합니다.
    try:
        from openpyxl import load_workbook
        uploaded_file.seek(0)
        wb = load_workbook(uploaded_file, read_only=True, data_only=True)
        repaired = {}
        for ws in wb.worksheets:
            ws.reset_dimensions()
            rows = list(ws.iter_rows(values_only=True))
            if rows:
                width = max(len(row) for row in rows)
                normalized = [list(row) + [None] * (width - len(row)) for row in rows]
                df = pd.DataFrame(normalized)
                if not df.dropna(how="all").empty:
                    repaired[ws.title] = df
        wb.close()
        if repaired:
            return repaired
    except Exception:
        uploaded_file.seek(0)

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


REPORT_PERFORMANCE_EXCLUDED_CODES = {"802-11"}


def parse_erp_budget_amount(value):
    """ERP 집행현황의 쉼표·△ 표기 금액을 숫자로 변환합니다."""
    if pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else 0.0
    text_value = str(value).strip()
    if not text_value or text_value in ["nan", "NaT", "None"]:
        return 0.0
    is_negative = "△" in text_value or (
        text_value.startswith("(") and text_value.endswith(")")
    )
    cleaned = re.sub(r"[^0-9.\-]", "", text_value)
    if not cleaned:
        return 0.0
    try:
        numeric = float(cleaned)
    except Exception:
        return 0.0
    return -abs(numeric) if is_negative else numeric


def normalize_erp_label(value):
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", "", str(value).replace("\u3000", " ")).strip()


def parse_budget_execution_sheet(df_raw):
    """ERP '예산집행현황' 2단 헤더를 읽어 합계와 최하위 예산과목을 추출합니다."""
    if not isinstance(df_raw, pd.DataFrame) or df_raw.empty or df_raw.shape[1] < 8:
        return None

    header_idx = -1
    for row_idx in range(min(15, len(df_raw))):
        first_cell = normalize_erp_label(df_raw.iloc[row_idx, 0])
        if "사업구분" in first_cell and "세목" in first_cell:
            header_idx = row_idx
            break
    if header_idx < 1:
        return None

    top_idx = header_idx - 1
    top_headers = [normalize_erp_label(value) for value in df_raw.iloc[top_idx].tolist()]
    sub_headers = [normalize_erp_label(value) for value in df_raw.iloc[header_idx].tolist()]

    def first_index(values, predicate):
        for column_idx, value in enumerate(values):
            if predicate(value):
                return column_idx
        return -1

    original_idx = first_index(sub_headers, lambda value: value == "당초")
    current_idx = first_index(sub_headers, lambda value: value == "계")
    allocated_idx = first_index(
        top_headers,
        lambda value: "예산배정" in value and ("(B)" in value or value.endswith("B")),
    )
    commitment_idx = first_index(sub_headers, lambda value: "원인행위(C)" in value)
    actual_idx = first_index(top_headers, lambda value: "지출액" in value)
    required = {
        "당초": original_idx,
        "예산현액": current_idx,
        "예산배정액": allocated_idx,
        "원인행위액": commitment_idx,
        "지출액": actual_idx,
    }
    if any(column_idx < 0 for column_idx in required.values()):
        return None

    def row_values(row):
        return {
            "예산액": parse_erp_budget_amount(row.iloc[original_idx]),
            "예산현액": parse_erp_budget_amount(row.iloc[current_idx]),
            "예산배정액": parse_erp_budget_amount(row.iloc[allocated_idx]),
            "원인행위액": parse_erp_budget_amount(row.iloc[commitment_idx]),
            "지출액": parse_erp_budget_amount(row.iloc[actual_idx]),
        }

    summary_parts = {}
    selected_lines = {}
    current_scope = ""
    for source_row_idx in range(header_idx + 1, len(df_raw)):
        row = df_raw.iloc[source_row_idx]
        raw_label = "" if pd.isna(row.iloc[0]) else str(row.iloc[0])
        label = raw_label.replace("\u3000", " ").strip()
        if not label:
            continue
        if not re.match(r"^\d", label) and "운영" in label and "관리" in label:
            current_scope = "일상경비" if "일상" in label else "운영경비"
        if label in ["합계", "사업예산", "자본예산"]:
            summary_parts[("" if label == "합계" else current_scope, label)] = {
                "구분": label,
                **row_values(row),
                "원천행": source_row_idx + 1,
            }
            continue

        match = re.match(r"^(\d+(?:-\d+)?)\s+(.+)$", label)
        if not match:
            continue
        code, name = match.group(1).strip(), match.group(2).strip()
        if code not in BUDGET_MAPPING:
            continue
        prefix = raw_label[: len(raw_label) - len(raw_label.lstrip(" \t\u3000"))]
        depth = sum(2 if char == "\u3000" else 1 for char in prefix)
        values = row_values(row)
        candidate = {
            "세목코드": code,
            "세목명": name,
            "경비구분": current_scope,
            **values,
            "잔액": values["예산배정액"] - values["지출액"],
            "원천행": source_row_idx + 1,
            "_depth": depth,
        }
        line_key = (current_scope, code)
        previous = selected_lines.get(line_key)
        if previous is None or depth >= previous.get("_depth", -1):
            selected_lines[line_key] = candidate

    summary = {}
    for label in ("합계", "사업예산", "자본예산"):
        parts = [value for (scope, name), value in summary_parts.items() if name == label]
        if ("", label) in summary_parts:
            parts = [summary_parts[("", label)]]
        if parts:
            summary[label] = {"구분": label, **{field: sum(r[field] for r in parts) for field in
                ("예산액", "예산현액", "예산배정액", "원인행위액", "지출액")}}
    if "합계" not in summary or len(selected_lines) < 10:
        return None

    lines = []
    for code in BUDGET_MAPPING:
        parts = [r for (scope, account), r in selected_lines.items() if account == code and scope]
        if not parts and ("", code) in selected_lines:
            parts = [selected_lines[("", code)]]
        if parts:
            clean_row = dict(parts[0])
            for field in ("예산액", "예산현액", "예산배정액", "원인행위액", "지출액", "잔액"):
                clean_row[field] = sum(r[field] for r in parts)
            scopes = {r["경비구분"] for r in parts if r["경비구분"]}
            clean_row["경비구분"] = next(iter(scopes)) if len(scopes) == 1 else "운영·일상" if scopes else ""
            clean_row["경비별내역"] = [{k: v for k, v in r.items() if k != "_depth"} for r in parts if r["경비구분"]]
            clean_row.pop("_depth", None)
            lines.append(clean_row)
    return {
        "header_row": header_idx + 1,
        "column_map": required,
        "summary": summary,
        "lines": lines,
        "line_count": len(lines),
    }


def infer_budget_report_period(file_name):
    normalized_name = re.sub(r"\s+", "", str(file_name))
    cumulative_match = re.search(r"1[~～\-](1[0-2]|[1-9])월", normalized_name)
    if cumulative_match:
        return "누적", int(cumulative_match.group(1))
    month_matches = re.findall(r"(1[0-2]|[1-9])월", normalized_name)
    if month_matches:
        return "단월", int(month_matches[-1])
    return "미확인", 0


def parse_budget_execution_report_file(uploaded_file):
    """여러 시트를 검사해 ERP 예산집행현황으로 인식되는 최적 시트를 반환합니다."""
    try:
        file_bytes = uploaded_file.getvalue()
    except Exception:
        uploaded_file.seek(0)
        file_bytes = uploaded_file.read()
        uploaded_file.seek(0)
    sheets = read_excel_sheets_flexible(uploaded_file)
    candidates = []
    for sheet_name, df_raw in sheets.items():
        parsed = parse_budget_execution_sheet(df_raw)
        if parsed:
            candidates.append((sheet_name, parsed))
    if not candidates:
        return {
            "ok": False,
            "file_name": getattr(uploaded_file, "name", "업로드 파일"),
            "error": "예산액·예산배정·지출액 2단 헤더를 인식하지 못했습니다.",
        }
    sheet_name, parsed = max(candidates, key=lambda item: item[1]["line_count"])
    period_kind, base_month = infer_budget_report_period(getattr(uploaded_file, "name", ""))
    parsed.update({
        "ok": True,
        "file_name": getattr(uploaded_file, "name", "업로드 파일"),
        "sheet_name": sheet_name,
        "period_kind": period_kind,
        "base_month": base_month,
        "fingerprint": hashlib.sha256(file_bytes).hexdigest()[:12],
    })
    return parsed


def subtract_report_values(left, right):
    result = {}
    for key in ["예산액", "예산현액", "예산배정액", "원인행위액", "지출액"]:
        result[key] = float(clean_numeric(left.get(key, 0))) - float(clean_numeric(right.get(key, 0)))
    return result


def prepare_budget_report_source_import(uploaded_files, current_config):
    """단월·누적 두 원천을 분류하고, 저장 전 미리보기·정합성 검증·신규 설정을 만듭니다."""
    files = list(uploaded_files or [])
    if len(files) != 2:
        return {
            "ok": False,
            "errors": ["단월 집행현황과 1~기준월 누적 집행현황, 총 2개 파일이 필요합니다."],
        }

    parsed_files = [parse_budget_execution_report_file(uploaded_file) for uploaded_file in files]
    errors = [
        f"{item.get('file_name', '파일')}: {item.get('error', '인식 실패')}"
        for item in parsed_files
        if not item.get("ok")
    ]
    if errors:
        return {"ok": False, "errors": errors, "parsed_files": parsed_files}

    cumulative_candidates = [item for item in parsed_files if item.get("period_kind") == "누적"]
    monthly_candidates = [item for item in parsed_files if item.get("period_kind") == "단월"]
    if len(cumulative_candidates) == 1 and len(monthly_candidates) == 1:
        cumulative = cumulative_candidates[0]
        monthly = monthly_candidates[0]
    else:
        sorted_by_actual = sorted(
            parsed_files,
            key=lambda item: float(clean_numeric(item["summary"]["합계"].get("지출액", 0))),
            reverse=True,
        )
        cumulative, monthly = sorted_by_actual[0], sorted_by_actual[1]

    base_month = int(cumulative.get("base_month") or monthly.get("base_month") or 0)
    if not 1 <= base_month <= 12:
        return {
            "ok": False,
            "errors": ["파일명에서 기준월을 확인할 수 없습니다. 예: '7월 집행현황', '1~7월 집행현황'"],
            "parsed_files": parsed_files,
        }

    cumulative_lines = {row["세목코드"]: row for row in cumulative["lines"]}
    monthly_lines = {row["세목코드"]: row for row in monthly["lines"]}
    required_summary = ["합계", "사업예산", "자본예산"]
    if any(key not in cumulative["summary"] for key in required_summary):
        return {"ok": False, "errors": ["누적 파일에서 합계·사업예산·자본예산 행을 모두 찾지 못했습니다."]}
    if "802-11" not in cumulative_lines or "405-12" not in cumulative_lines:
        return {"ok": False, "errors": ["누적 파일에서 802-11 또는 405-12 필수 행을 찾지 못했습니다."]}

    source_total = cumulative["summary"]["합계"]
    source_month_total = monthly["summary"]["합계"]
    return_line = cumulative_lines["802-11"]
    monthly_return_line = monthly_lines.get("802-11", {})
    management_total = subtract_report_values(source_total, return_line)
    monthly_management_total = subtract_report_values(source_month_total, monthly_return_line)

    operating_source = cumulative["summary"]["사업예산"]
    operating_management = subtract_report_values(operating_source, return_line)
    capital_source = cumulative["summary"]["자본예산"]
    business_rows = [
        {
            "사업키": "operating",
            "사업구분": REPORT_BUSINESS_LABELS["operating"],
            "예산액": operating_management["예산액"],
            "예산현액": operating_management["예산현액"],
            "예산배정액": operating_management["예산배정액"],
            "누적지출액": operating_management["지출액"],
        },
        {
            "사업키": "capital",
            "사업구분": REPORT_BUSINESS_LABELS["capital"],
            "예산액": capital_source["예산액"],
            "예산현액": capital_source["예산현액"],
            "예산배정액": capital_source["예산배정액"],
            "누적지출액": capital_source["지출액"],
        },
    ]

    line_rows = []
    for cumulative_row in cumulative["lines"]:
        code = cumulative_row["세목코드"]
        monthly_row = monthly_lines.get(code, {})
        allocation = float(clean_numeric(cumulative_row.get("예산배정액", 0)))
        actual = float(clean_numeric(cumulative_row.get("지출액", 0)))
        line_rows.append({
            "사업키": (
                "capital" if code == "405-12"
                else "excluded" if code in REPORT_PERFORMANCE_EXCLUDED_CODES
                else "operating"
            ),
            "세목코드": code,
            "세목명": cumulative_row.get("세목명", BUDGET_MAPPING.get(code, ("", code))[1]),
            "경비구분": cumulative_row.get("경비구분", ""),
            "경비별내역": copy.deepcopy(cumulative_row.get("경비별내역", [])),
            "예산현액": float(clean_numeric(cumulative_row.get("예산현액", allocation))),
            "예산배정액": allocation,
            "원인행위액": float(clean_numeric(cumulative_row.get("원인행위액", 0))),
            "월지출액": float(clean_numeric(monthly_row.get("지출액", 0))),
            "누적지출액": actual,
            "잔액": allocation - actual,
            "집행률산정제외": code in REPORT_PERFORMANCE_EXCLUDED_CODES,
            "원천연계": True,
        })

    cfg = ensure_report_config(current_config)
    new_config = copy.deepcopy(cfg)
    now_text = datetime.now().isoformat(timespec="seconds")
    new_config["meta"].update({
        "schema_version": 42,
        "source_mode": "erp_budget_execution",
        "base_month": base_month,
        "source_note": f"{monthly['file_name']} + {cumulative['file_name']} 자동 연계",
        "source_files": [monthly["file_name"], cumulative["file_name"]],
        "source_updated_at": now_text,
    })
    new_config["totals"].update({
        "예산액": source_total["예산액"],
        "예산현액": source_total["예산현액"],
        "예산배정액": management_total["예산배정액"],
        "누적지출액": management_total["지출액"],
        "원천 예산배정액": source_total["예산배정액"],
        "원천 누적지출액": source_total["지출액"],
        "관리대상 예산액": management_total["예산액"],
        "관리대상 예산현액": management_total["예산현액"],
        "관리대상 예산배정액": management_total["예산배정액"],
        "관리대상 누적지출액": management_total["지출액"],
        "반환금 예산배정액": return_line["예산배정액"],
        "반환금 지출액": return_line["지출액"],
    })
    new_config["business"] = business_rows
    new_config["lines"] = line_rows
    monthly_config = {int(row.get("월", 0)): dict(row) for row in new_config["monthly"]}
    if base_month not in monthly_config:
        monthly_config[base_month] = {
            "월": base_month,
            "집행계획액": 0.0,
            "지출액": 0.0,
            "전년동월지출액": 0.0,
        }
    monthly_config[base_month]["지출액"] = monthly_management_total["지출액"]
    new_config["monthly"] = [
        monthly_config.get(month, {
            "월": month,
            "집행계획액": 0.0,
            "지출액": 0.0,
            "전년동월지출액": 0.0,
        })
        for month in range(1, 13)
    ]
    new_config["notes"].update({
        "monthly": "단월 파일은 해당 월 지출액, 누적 파일은 기준월 말 누적 지출액의 원천입니다. 두 합계 차이는 검증표에서 별도로 확인합니다.",
        "lines": "예산과목별 집행률은 누적 원천의 예산배정액 대비 지출액으로 산정하며, 잔액은 예산배정액-지출액입니다.",
        "asset": "수탁자산취득비는 예산배정액·지출액·잔액만 직관적으로 표시합니다.",
    })

    checks = []

    def add_check(name, expected, actual, note="", tolerance=1.0):
        difference = float(actual) - float(expected)
        checks.append({
            "검증항목": name,
            "결과": "일치" if abs(difference) <= tolerance else "확인 필요",
            "기준값": float(expected),
            "비교값": float(actual),
            "차이": difference,
            "비고": note,
        })

    add_check(
        "단월·누적 기준월 일치",
        base_month,
        int(monthly.get("base_month") or base_month),
        "파일명 기준",
        tolerance=0.0,
    )
    add_check("전체 예산액 일치", source_total["예산액"], source_month_total["예산액"], "두 파일의 당초예산")
    add_check("예산현액 일치", source_total["예산현액"], source_month_total["예산현액"], "두 파일의 예산액 계")
    add_check("원천 예산배정액 일치", source_total["예산배정액"], source_month_total["예산배정액"], "두 파일의 예산배정(B)")
    add_check(
        "예산과목 수 일치",
        cumulative["line_count"],
        monthly["line_count"],
        "최하위 예산과목",
        tolerance=0.0,
    )
    allocation_gap = sum(
        abs(
            float(clean_numeric(row.get("예산배정액", 0)))
            - float(clean_numeric(monthly_lines.get(code, {}).get("예산배정액", 0)))
        )
        for code, row in cumulative_lines.items()
    )
    add_check("예산과목별 배정액 일치", 0, allocation_gap, "단월·누적 파일 전 과목")
    for source_label, parsed in (("단월", monthly), ("누적", cumulative)):
        for field in ("예산현액", "예산배정액", "지출액"):
            add_check(f"{source_label} 원본 합계와 세목 합계 · {field}", parsed["summary"]["합계"][field],
                      sum(r[field] for r in parsed["lines"]), "운영경비·일상경비 합산")
    cumulative_less_than_month = sum(
        1 for code, row in cumulative_lines.items()
        if float(clean_numeric(row.get("지출액", 0))) + 1
        < float(clean_numeric(monthly_lines.get(code, {}).get("지출액", 0)))
    )
    add_check(
        "누적 지출액이 단월 이상",
        0,
        cumulative_less_than_month,
        "역전된 예산과목 수",
        tolerance=0.0,
    )
    prior_month_sum = sum(
        float(clean_numeric(row.get("지출액", 0)))
        for row in new_config["monthly"]
        if int(row.get("월", 0)) < base_month
    )
    implied_month_actual = management_total["지출액"] - prior_month_sum
    add_check(
        "월별 합계와 누적 원천 연결",
        implied_month_actual,
        monthly_management_total["지출액"],
        "차이는 이전 월 자료의 누락·보정 가능성으로 자동 수정하지 않습니다.",
    )

    # 마지막 월별 연결 검사는 과거 월 자료의 누락·보정 여부를 알려 주는
    # 참고 검증입니다. 그 외 기준월·예산·배정·과목·누적 역전 검사는
    # 원천을 잘못 고른 경우에 해당하므로 통과해야만 저장할 수 있습니다.
    can_apply = all(row["결과"] == "일치" for row in checks[:-1])
    new_config["source_audit"] = {
        "status": "검증 완료" if can_apply else "반영 차단",
        "base_month": base_month,
        "monthly_file": monthly["file_name"],
        "cumulative_file": cumulative["file_name"],
        "monthly_sheet": monthly["sheet_name"],
        "cumulative_sheet": cumulative["sheet_name"],
        "monthly_fingerprint": monthly["fingerprint"],
        "cumulative_fingerprint": cumulative["fingerprint"],
        "leaf_line_count": cumulative["line_count"],
        "monthly_management_actual": monthly_management_total["지출액"],
        "cumulative_management_actual": management_total["지출액"],
        "source_total_actual": source_total["지출액"],
        "return_actual": return_line["지출액"],
        "can_apply": can_apply,
        "checks": checks,
        "updated_at": now_text,
    }

    key_rows = []
    for code in ["202-01", "204-02", "405-12"]:
        row = next((item for item in line_rows if item["세목코드"] == code), None)
        if row:
            key_rows.append({
                "세목코드": code,
                "세목명": row["세목명"],
                "예산배정액": row["예산배정액"],
                "누적 지출액": row["누적지출액"],
                "잔액": row["잔액"],
                "집행률": (
                    row["누적지출액"] / row["예산배정액"] * 100.0
                    if row["예산배정액"] else 0.0
                ),
            })
    if can_apply:
        new_config, history_only = record_report_import(cfg, new_config)
    else:
        history_only = False
    return {
        "ok": True,
        "config": new_config,
        "history_only": history_only,
        "monthly": monthly,
        "cumulative": cumulative,
        "checks": checks,
        "key_rows": key_rows,
        "can_apply": can_apply,
        "base_month": base_month,
        "monthly_management_actual": monthly_management_total["지출액"],
        "cumulative_management_actual": management_total["지출액"],
        "source_total_actual": source_total["지출액"],
        "return_actual": return_line["지출액"],
    }

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
    return parse_frame(df_raw, mapper=force_mapped_category_for_known_cases)


def parse_special_expense_excel(df_raw):
    return parse_frame(df_raw, "special")


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
    else:
        st.session_state['rapid_df'] = ensure_rapid_df_integrity(st.session_state['rapid_df'])

if 'daily_expenses' not in st.session_state: st.session_state['daily_expenses'] = load_daily_expenses()
if 'report_config' not in st.session_state: st.session_state['report_config'] = load_report_config()
else: st.session_state['report_config'] = ensure_report_config(st.session_state['report_config'])
if 'tree_expanded' not in st.session_state or st.session_state['tree_expanded'] is None: 
    st.session_state['tree_expanded'] = set()
if 'tree_states' not in st.session_state: st.session_state['tree_states'] = {}
if 'last_file_hash' not in st.session_state: st.session_state['last_file_hash'] = None
if 'last_sp_file_hash' not in st.session_state: st.session_state['last_sp_file_hash'] = None

if st.session_state.get("initial_sync_done") != "V45.2":
    sync_daily_to_master_auto()
    st.session_state["initial_sync_done"] = "V45.2"

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
_execution_index = current_execution_index()
_asset_synced = False
for _record in master_data_raw.get("records", []):
    if _record["year"] == 2026 and _record["category"] == ASSET_CATEGORY:
        _reference = _execution_index["monthly"].get(_record["month"], {}).get(ASSET_CODE)
        if _reference and _record["amount"] != _reference["amount"]:
            _record.update(amount=_reference["amount"], status="지출" if _reference["amount"] > 0 else "환불·여입" if _reference["amount"] < 0 else "미지출")
            _asset_synced = True
if _asset_synced and not save_data_cloud(master_data_raw):
    st.warning("원장 기준금액은 화면에 반영했지만 월별 집계 저장을 완료하지 못했습니다. 저장 상태를 확인하세요.")
st.session_state['data'] = master_data_raw
df_all = pd.DataFrame(master_data_raw.get("records", []))
if not df_all.empty: df_all["amount"] = pd.to_numeric(df_all["amount"], errors='coerce').fillna(0).astype('float64')

# --- 스타일 가이드 ---
st.html("""
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
        .theme-purple { border-left: 4px solid #7c3aed; }
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
        .st-key-analysis_monthly_adjustment_button button {
            min-height: 62px;
            padding: 14px 20px;
            background: #2563eb;
            border: 1px solid #1d4ed8;
            color: #fff;
            box-shadow: 0 4px 12px rgba(37,99,235,.20);
        }
        .st-key-analysis_monthly_adjustment_button button:hover {
            background: #1d4ed8;
            color: #fff;
        }
        .st-key-analysis_monthly_adjustment_button button p {
            font-size: 1.1rem;
            font-weight: 800;
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

        /* v40 통합 예산집행 보고서 */
        .report-update-strip {
            display: flex;
            justify-content: flex-end;
            align-items: baseline;
            gap: 8px;
            min-height: 24px;
            margin: -2px 3px 6px;
            color: #64748b;
            font-size: .69rem;
            font-weight: 800;
        }
        .report-update-strip strong {
            color: #0f172a;
            font-size: .78rem;
            font-weight: 950;
        }
        .report-hero {
            position: relative;
            overflow: hidden;
            padding: 21px 22px;
            margin-bottom: 14px;
            border: 1px solid #c7d2fe;
            border-radius: 15px;
            background: linear-gradient(135deg, #ffffff 0%, #eef2ff 100%);
            box-shadow: 0 3px 12px rgba(15,23,42,.04);
        }
        .report-hero:after {
            content: "";
            position: absolute;
            right: -55px;
            top: -75px;
            width: 190px;
            height: 190px;
            border-radius: 50%;
            background: rgba(37,99,235,.08);
        }
        .report-hero-grid {
            position: relative;
            z-index: 1;
            display: grid;
            grid-template-columns: 1.35fr repeat(3, minmax(150px, .65fr));
            gap: 14px;
            align-items: stretch;
        }
        .report-hero-title {
            color: #0f172a;
            font-size: 1.18rem;
            font-weight: 950;
            letter-spacing: -.035em;
        }
        .report-hero-desc {
            margin-top: 6px;
            color: #475569;
            font-size: .76rem;
            line-height: 1.55;
            font-weight: 700;
        }
        .report-stat {
            padding: 12px 13px;
            border: 1px solid rgba(148,163,184,.28);
            border-radius: 11px;
            background: rgba(255,255,255,.78);
        }
        .report-stat-label {
            color: #64748b;
            font-size: .68rem;
            font-weight: 850;
        }
        .report-stat-value {
            margin-top: 4px;
            color: #0f172a;
            font-size: 1.05rem;
            line-height: 1.25;
            font-weight: 950;
            letter-spacing: -.025em;
        }
        .report-kpi-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(0,1fr));
            gap: 9px;
            margin: 10px 0 16px;
        }
        .report-kpi {
            min-height: 106px;
            padding: 14px 15px;
            border: 1px solid var(--slate-200);
            border-radius: 12px;
            background: #ffffff;
        }
        .report-kpi.primary { border-top: 3px solid #2563eb; }
        .report-kpi.goal { border-top: 3px solid #7c3aed; background: #faf5ff; }
        .report-kpi-label { color: #64748b; font-size: .69rem; font-weight: 850; }
        .report-kpi-value { margin-top: 7px; color: #0f172a; font-size: 1.18rem; font-weight: 950; letter-spacing: -.035em; }
        .report-kpi-foot { margin-top: 5px; color: #64748b; font-size: .65rem; font-weight: 700; line-height: 1.4; }
        .goal-rail-wrap {
            padding: 16px 18px 38px;
            margin-bottom: 15px;
            border: 1px solid var(--slate-200);
            border-radius: 13px;
            background: #ffffff;
        }
        .goal-rail {
            position: relative;
            height: 20px;
            margin-top: 14px;
            border-radius: 999px;
            background: #e2e8f0;
        }
        .goal-rail-fill {
            height: 100%;
            border-radius: 999px;
            background: linear-gradient(90deg,#60a5fa,#2563eb);
        }
        .goal-rail-marker {
            position: absolute;
            top: -6px;
            width: 3px;
            height: 32px;
            border-radius: 999px;
            background: #7c3aed;
        }
        .goal-rail-label {
            position: absolute;
            top: 28px;
            transform: translateX(-50%);
            color: #6d28d9;
            font-size: .68rem;
            font-weight: 900;
            white-space: nowrap;
        }
        .report-paper {
            padding: 28px 30px;
            border: 1px solid #d7dde7;
            border-radius: 12px;
            background: #ffffff;
            box-shadow: 0 6px 22px rgba(15,23,42,.055);
            color: #111827;
        }
        .report-paper-title {
            padding-bottom: 13px;
            margin-bottom: 7px;
            border-bottom: 2px solid #1e3a8a;
            font-size: 1.35rem;
            font-weight: 950;
            letter-spacing: -.035em;
        }
        .report-paper-meta {
            margin: 0 0 16px;
            color: #64748b;
            font-size: .72rem;
            font-weight: 800;
            text-align: right;
        }
        .report-paper-section {
            margin: 18px 0 8px;
            color: #1e3a8a;
            font-size: .96rem;
            font-weight: 950;
        }
        .report-paper-line {
            padding: 4px 0;
            color: #334155;
            font-size: .82rem;
            line-height: 1.55;
            font-weight: 650;
        }
        .report-status {
            display: inline-flex;
            align-items: center;
            padding: 4px 8px;
            border-radius: 999px;
            font-size: .68rem;
            font-weight: 900;
        }
        .report-status.ok { color: #047857; background: #d1fae5; }
        .report-status.warn { color: #b45309; background: #fef3c7; }
        .variance-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
        }
        .variance-panel {
            padding: 13px 14px;
            border: 1px solid var(--slate-200);
            border-radius: 12px;
            background: #ffffff;
        }
        .variance-title {
            margin-bottom: 8px;
            color: #334155;
            font-size: .74rem;
            font-weight: 950;
        }
        .variance-row {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            gap: 10px;
            padding: 7px 0;
            border-top: 1px solid #f1f5f9;
            color: #475569;
            font-size: .7rem;
            font-weight: 750;
        }
        .variance-row:first-of-type { border-top: 0; }
        .variance-amount { color: #0f172a; font-weight: 950; white-space: nowrap; }
        .variance-amount.up { color: #b91c1c; }
        .variance-amount.down { color: #1d4ed8; }

        @media (max-width: 1100px) {
            .block-container { padding-left: 1.25rem; padding-right: 1.25rem; }
            .home-hero { grid-template-columns: 1fr; }
            .home-kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .page-head { flex-direction: column; }
            .workspace-bar { align-items: flex-start; flex-direction: column; }
            .context-chips { justify-content: flex-start; }
            .ops-kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .pipeline { grid-template-columns: repeat(2, minmax(0,1fr)); }
            .report-hero-grid { grid-template-columns: repeat(2, minmax(0,1fr)); }
            .report-kpi-grid { grid-template-columns: repeat(3, minmax(0,1fr)); }
            .variance-grid { grid-template-columns: 1fr; }
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
            .report-hero-grid { grid-template-columns: 1fr; }
            .report-kpi-grid { grid-template-columns: 1fr 1fr; }
            .report-paper { padding: 20px 17px; }
        }
    </style>
""")



# -----------------------------------------------------------------------------
# 앱 홈 화면 / 페이지 이동 유틸리티
# -----------------------------------------------------------------------------
def render_budget_control_page():
    render_budget_workspace(st.session_state.get("daily_expenses", []))


PAGE_BUDGET_CONTROL = "🧭 편성예산·부기관리"
PAGE_OVERVIEW = "📊 예산집행 현황"
PAGE_ANALYSIS = "📈 집행항목 분석"
PAGE_MISSING = "⚠️ 미집행 점검"
PAGE_IMPORT = "⇄ 일상경비 자료연계"
PAGE_QUICK = "⚡ 신속집행 관리"
PAGE_REPORT = "📝 예산집행 보고서"

APP_PAGES = [
    PAGE_OVERVIEW,
    PAGE_ANALYSIS,
    PAGE_IMPORT,
    PAGE_QUICK,
    PAGE_REPORT,
]

APP_PAGE_META = {
    PAGE_BUDGET_CONTROL: {"icon": "🧭", "title": "편성예산·부기관리", "desc": "본예산·추경 변동, 지출액, 편성잔액과 최종 부기 분류"},
    PAGE_OVERVIEW: {"icon": "📊", "title": "예산집행 현황", "desc": "지출액 기준 누계·월별 추이·통합 집계"},
    PAGE_ANALYSIS: {"icon": "📈", "title": "집행항목 분석", "desc": "집행관리 항목별 3개년 동월누계 비교"},
    PAGE_MISSING: {"icon": "⚠️", "title": "미집행 점검", "desc": "월별 미집행 확인대상·예외 처리"},
    PAGE_IMPORT: {"icon": "⇄", "title": "일상경비 자료연계", "desc": "엑셀 검증·분류·중복 확인·월별 집계 반영"},
    PAGE_QUICK: {"icon": "⚡", "title": "신속집행 관리", "desc": "지출액 기준 행안부·남양주시 목표 비교"},
    PAGE_REPORT: {"icon": "📝", "title": "예산집행 보고서", "desc": "총괄·사업·월별·예산과목·신속집행 통합 보고"},
}

APP_NAV_GROUPS = [
    ("현황·분석", [PAGE_BUDGET_CONTROL, PAGE_OVERVIEW, PAGE_ANALYSIS]),
    ("자료연계", [PAGE_IMPORT]),
    ("보고·성과관리", [PAGE_REPORT, PAGE_QUICK]),
]

def go_page(page_name: str):
    st.session_state["current_page"] = "HOME" if page_name == PAGE_BUDGET_CONTROL and not is_owner() else page_name


def get_daily_quality_snapshot(rows):
    return source_quality_snapshot(rows, get_row_management_category)


def report_amount(value, unit="천원"):
    divisor = 1000.0 if unit == "천원" else 1.0
    suffix = "천원" if unit == "천원" else "원"
    return f"{float(clean_numeric(value)) / divisor:,.0f}{suffix}"


def report_signed_amount(value, unit="천원"):
    numeric = float(clean_numeric(value))
    if numeric > 0:
        return f"+{report_amount(numeric, unit)}"
    if numeric < 0:
        return f"△{report_amount(abs(numeric), unit)}"
    return report_amount(0, unit)


def build_report_narrative(snapshot, unit="천원"):
    totals = snapshot["totals"]
    monthly = snapshot["monthly"]
    business = snapshot["business"]
    quick_include = snapshot["quick_bases"]["수탁자산취득비 포함"]
    quick_exclude = snapshot["quick_bases"]["수탁자산취득비 제외"]
    cumulative_plan = sum(float(clean_numeric(row.get("집행계획액", 0))) for row in monthly)
    cumulative_monthly_actual = sum(float(clean_numeric(row.get("지출액", 0))) for row in monthly)
    cumulative_previous = sum(float(clean_numeric(row.get("전년동월지출액", 0))) for row in monthly)
    current_month = monthly[-1] if monthly else {"월": snapshot["base_month"], "집행계획액": 0, "지출액": 0, "전년동월지출액": 0}
    low_lines = sorted(
        [
            row for row in report_presentation_lines(snapshot["lines"])
            if float(clean_numeric(row.get("예산배정액", 0))) > 0
            and not bool(row.get("집행률산정제외"))
        ],
        key=lambda row: float(clean_numeric(row.get("집행률", 0))),
    )[:5]
    top_actual_lines = sorted(
        [
            row for row in report_presentation_lines(snapshot["lines"])
            if float(clean_numeric(row.get("누적지출액", 0))) > 0
            and not bool(row.get("집행률산정제외"))
        ],
        key=lambda row: float(clean_numeric(row.get("누적지출액", 0))),
        reverse=True,
    )[:4]
    validation_gap = cumulative_monthly_actual - totals["누적지출액"]

    sections = [
        {
            "title": "1. 총괄",
            "lines": [
                f"ERP 전체 예산액 {report_amount(totals['예산액'], unit)}, 예산현액 {report_amount(totals['예산현액'], unit)}",
                f"관리대상 예산배정액 {report_amount(totals['예산배정액'], unit)}, {snapshot['base_month']}월 말 누적 지출액 {report_amount(totals['누적지출액'], unit)}, 배정액 대비 집행률 {totals['집행률']:.2f}%",
                f"원천 총지출액 {report_amount(totals['원천 누적지출액'], unit)} 중 대행사업비반환금 {report_amount(totals['반환금 지출액'], unit)}은 관리 집행률에서 제외",
                f"연간 관리목표 {totals['연간목표율']:.1f}%({report_amount(totals['연간목표액'], unit)}) 대비 추가 필요액 {report_amount(totals['목표대비 추가필요액'], unit)}",
                f"예산배정액 대비 집행잔액 {report_amount(totals['집행잔액'], unit)}",
            ],
        },
        {
            "title": "2. 사업별 집행현황",
            "lines": [
                f"{row['사업구분']}: 누적 지출액 {report_amount(row['누적지출액'], unit)}, 예산현액 대비 {row['예산현액 대비 집행률']:.2f}%, 예산배정액 대비 {row['예산배정액 대비 집행률']:.2f}%"
                for row in business
            ],
        },
        {
            "title": f"3. 월별 집행현황({snapshot['period_label']})",
            "lines": [
                f"1~{snapshot['base_month']}월 누적 계획액 {report_amount(cumulative_plan, unit)}, 월별 지출액 합계 {report_amount(cumulative_monthly_actual, unit)}, 계획 대비 {report_signed_amount(cumulative_monthly_actual - cumulative_plan, unit)}",
                f"2025년 동기 누계 {report_amount(cumulative_previous, unit)}, 전년 대비 {report_signed_amount(cumulative_monthly_actual - cumulative_previous, unit)}",
                f"{snapshot['base_month']}월 단월 계획액 {report_amount(current_month.get('집행계획액', 0), unit)}, 지출액 {report_amount(current_month.get('지출액', 0), unit)}, 전년동월 {report_amount(current_month.get('전년동월지출액', 0), unit)}",
                *(["전년 비교 기본자료는 2025년 PDF의 월별 합계이며 반환금이 포함됩니다. 금년 관리대상 집계는 반환금 제외 기준이므로 포함범위를 함께 확인합니다."]
                  if any(row.get("전년동월지출액 출처") == REPORT_PREVIOUS_ACTUAL_SOURCE for row in monthly) else []),
                "주요 지출 예산과목: " + ", ".join(f"{report_account_label(row)} {report_amount(row['누적지출액'], unit)}" for row in top_actual_lines),
            ],
        },
        {
            "title": "4. 신속집행 목표 달성현황",
            "lines": [
                f"수탁자산취득비 포함 기준: 대상액 {report_amount(quick_include['대상액'], unit)}, 인정 지출액 {report_amount(quick_include['기준월 인정지출액'], unit)}, 실적률 {quick_include['실적률']:.2f}%, 잔여 대상액 {report_amount(quick_include['잔여 대상액'], unit)}",
                f"수탁자산취득비 제외 기준: 대상액 {report_amount(quick_exclude['대상액'], unit)}, 인정 지출액 {report_amount(quick_exclude['기준월 인정지출액'], unit)}, 실적률 {quick_exclude['실적률']:.2f}%, 잔여 대상액 {report_amount(quick_exclude['잔여 대상액'], unit)}",
                "조회기간별 목표율은 신속집행 메뉴의 등록 기준을 확인",
                "상품매입비 등 대상액을 초과한 지출은 신속집행 인정액에서 항목별 100%까지만 반영",
            ],
        },
        {
            "title": "5. 주요 관리사항",
            "lines": [
                "집행률 하위 확인대상: " + ", ".join(f"{report_account_label(row)}({row['집행률']:.1f}%)" for row in low_lines),
                snapshot["config"]["notes"].get("asset", ""),
                "집행액은 지출액(E) 기준이며, 예산과목별 집행률은 예산배정액(B) 대비 지출액으로 산정",
            ],
        },
    ]
    if abs(validation_gap) > 1:
        sections.append({
            "title": "6. 자료 검증사항",
            "lines": [
                f"월별 지출액 합계와 누적 원천 지출액 간 {report_signed_amount(validation_gap, unit)} 차이 확인 필요",
                "차이는 자동 보정하지 않았으며, 단월·누적 원천을 각각 보존하여 확인 가능",
            ],
        })
    selected = snapshot.get("selected_quick")
    if "selected_quick" in snapshot and not selected:
        sections[3] = {"title": "4. 신속집행·기간별 집행현황", "lines": ["선택 기간이 실적 기준월 이후이므로 해당 기간 실적을 산정하지 않았습니다."]}
    if selected:
        first = selected[0]
        label = f"{first['조회기간']} · {first['기간시작월']}~{first['기간종료월']}월"
        lines_by_code = {row["세목코드"]: row for row in snapshot["lines"]}
        sections[3] = {"title": f"4. 신속집행·기간별 집행현황({label})", "lines": [
            f"{report_account_label(lines_by_code.get(row['세목코드'], {'세목명': row['집행관리 항목']}))}: 지출액 {report_amount(row['지출액'], unit)}, 인정 지출액 {report_amount(row['인정 지출액'], unit)} · {row['기준']}"
            for row in selected
        ]}
        for row in selected:
            if row.get("월별합계 차이") not in (None, 0):
                sections[3]["lines"].append(f"{row['집행관리 항목']} 누계와 월별 원장 합계 차이 {report_signed_amount(row['월별합계 차이'], unit)} 확인 필요")
    return sections


def build_report_text(snapshot, unit="천원"):
    title = snapshot["config"]["meta"].get("title", "예산집행 현황 보고")
    update_date = get_report_update_date(snapshot["config"])
    lines = [
        f"■ {title}({snapshot['base_month']}월)",
        f"  ○ 작성기준일: {update_date}",
        f"  ○ 기준: 2026년 {snapshot['period_label']} / 지출액 기준 / 단위 {unit}",
        "",
    ]
    for section in build_report_narrative(snapshot, unit):
        lines.append(section["title"])
        for line in section["lines"]:
            if line:
                lines.append(f"  ○ {line}")
        lines.append("")
    return "\n".join(lines).rstrip()


def report_table_html(headers, rows):
    head_html = "".join(f"<th>{html.escape(str(header))}</th>" for header in headers)
    body_html = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>"


def build_report_download_html(snapshot, unit="천원"):
    totals = snapshot["totals"]
    update_date = get_report_update_date(snapshot["config"])
    narrative = build_report_narrative(snapshot, unit)
    narrative_html = "".join(
        f"<h3>{html.escape(section['title'])}</h3><ul>"
        + "".join(f"<li>{html.escape(line)}</li>" for line in section["lines"] if line)
        + "</ul>"
        for section in narrative
    )
    total_table = report_table_html(
        ["구분", f"금액({unit})", "비고"],
        [
            ["ERP 전체 예산액", report_amount(totals["예산액"], unit), "원천 합계"],
            ["ERP 전체 예산현액", report_amount(totals["예산현액"], unit), "경정 반영 원천 합계"],
            ["관리대상 예산배정액", report_amount(totals["예산배정액"], unit), "대행사업비반환금 제외"],
            ["관리대상 누적 지출액", report_amount(totals["누적지출액"], unit), f"{snapshot['base_month']}월 말"],
            ["관리대상 집행잔액", report_amount(totals["집행잔액"], unit), "예산배정액-지출액"],
            ["ERP 원천 총지출액", report_amount(totals["원천 누적지출액"], unit), "반환금 포함"],
            ["대행사업비반환금 지출액", report_amount(totals["반환금 지출액"], unit), "관리 집행률 제외"],
            ["연간 관리목표", report_amount(totals["연간목표액"], unit), f"{totals['연간목표율']:.1f}%"],
        ],
    )
    business_table = report_table_html(
        ["사업구분", "예산현액", "예산배정액", "누적 지출액", "현액 대비", "배정액 대비"],
        [
            [
                row["사업구분"],
                report_amount(row["예산현액"], unit),
                report_amount(row["예산배정액"], unit),
                report_amount(row["누적지출액"], unit),
                f"{row['예산현액 대비 집행률']:.2f}%",
                f"{row['예산배정액 대비 집행률']:.2f}%",
            ]
            for row in snapshot["business"]
        ],
    )
    monthly_table = report_table_html(
        ["월", "집행계획액", "지출액", "전년동월", "계획대비", "전년대비", "누적 지출액"],
        [
            [
                f"{int(row['월'])}월",
                report_amount(row["집행계획액"], unit),
                report_amount(row["지출액"], unit),
                report_amount(row["전년동월지출액"], unit),
                report_signed_amount(row["계획대비 증감"], unit),
                report_signed_amount(row["전년대비 증감"], unit),
                report_amount(row["누적지출액"], unit),
            ]
            for row in snapshot["monthly"]
        ],
    )
    export_quick = report_quick_export_rows(snapshot)
    quick_table = report_table_html(
        ["항목", "대상액", "지출액", "인정 지출액", "기준"],
        [[row["항목"], report_amount(row["대상액"], unit), report_amount(row["지출액"], unit),
          report_amount(row["인정 지출액"], unit), row["기준"]] for row in export_quick],
    )
    document_title = f"{snapshot['config']['meta'].get('title', '예산집행 현황 보고')}({snapshot['base_month']}월)"
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>{html.escape(document_title)}</title>
<style>
body{{font-family:'Malgun Gothic',sans-serif;color:#111827;margin:34px;line-height:1.55}}
h1{{font-size:22px;border-bottom:3px solid #1e3a8a;padding-bottom:12px}}
h2{{font-size:17px;color:#1e3a8a;margin-top:26px}}h3{{font-size:15px;color:#1e3a8a;margin:18px 0 4px}}
table{{width:100%;border-collapse:collapse;margin:10px 0 22px;font-size:12px}}
th,td{{border:1px solid #cbd5e1;padding:7px 8px;text-align:right}}th{{background:#eff6ff;color:#1e3a8a}}
th:first-child,td:first-child{{text-align:left}}ul{{margin-top:5px}}li{{margin:3px 0;font-size:13px}}
.meta{{color:#475569;font-size:12px}}
</style></head><body>
<h1>{html.escape(document_title)}</h1>
<div class="meta">작성기준일 {html.escape(update_date)} · 2026년 {html.escape(snapshot['period_label'])} · 지출액 기준 · 단위 {html.escape(unit)}</div>
<h2>총괄</h2>{total_table}
<h2>사업별 집행현황</h2>{business_table}
<h2>월별 집행현황</h2>{monthly_table}
<h2>신속집행</h2>{quick_table}
<h2>자동 보고문</h2>{narrative_html}
</body></html>"""


def report_quick_export_rows(snapshot):
    lines = {row["세목코드"]: row for row in snapshot["lines"]}
    result = []
    for row in snapshot["selected_quick"] if "selected_quick" in snapshot else snapshot["quick_rows"]:
        code = row["세목코드"]
        result.append({"항목": report_account_label(lines.get(code, {"세목명": row["집행관리 항목"]})),
            "대상액": row.get("대상액", row.get("신속집행 대상액", 0)),
            "지출액": row.get("지출액", row.get("원천지출액", 0)),
            "인정 지출액": row.get("인정 지출액", row.get("기준월 인정지출액", 0)),
            "기준": row.get("기준", "보고 기준월 누계"),
            "조회기간": row.get("조회기간", "연간 누계"),
            "기간": f"{row.get('기간시작월', 1)}~{row.get('기간종료월', snapshot['base_month'])}월"})
    return result


def build_report_excel_bytes(snapshot):
    output = io.BytesIO()
    totals = snapshot["totals"]
    update_date = get_report_update_date(snapshot["config"])
    summary_df = pd.DataFrame([
        {"구분": "작성기준일", "금액": None, "비고": update_date},
        {"구분": "ERP 전체 예산액", "금액": totals["예산액"], "비고": "원천 합계"},
        {"구분": "ERP 전체 예산현액", "금액": totals["예산현액"], "비고": "경정 반영 원천 합계"},
        {"구분": "관리대상 예산배정액", "금액": totals["예산배정액"], "비고": "반환금 제외"},
        {"구분": "관리대상 누적 지출액", "금액": totals["누적지출액"], "비고": f"{snapshot['base_month']}월 말"},
        {"구분": "관리대상 집행잔액", "금액": totals["집행잔액"], "비고": "예산배정액-지출액"},
        {"구분": "ERP 원천 총지출액", "금액": totals["원천 누적지출액"], "비고": "반환금 포함"},
        {"구분": "대행사업비반환금 지출액", "금액": totals["반환금 지출액"], "비고": "관리 집행률 제외"},
        {"구분": "예산배정액 대비 집행률", "금액": totals["집행률"] / 100.0, "비고": "비율"},
        {"구분": "연간 관리목표액", "금액": totals["연간목표액"], "비고": f"{totals['연간목표율']:.1f}%"},
        {"구분": "목표 대비 추가 필요액", "금액": totals["목표대비 추가필요액"], "비고": "연간 관리목표 기준"},
    ])
    business_df = pd.DataFrame(snapshot["business"])
    monthly_df = pd.DataFrame(snapshot["monthly"])
    lines_df = pd.DataFrame([{"항목": report_account_label(row),
        **{key: row.get(key) for key in ("예산현액", "예산배정액", "누적지출액", "잔액", "집행률")}}
        for row in report_presentation_lines(snapshot["lines"])])
    quick_df = pd.DataFrame(report_quick_export_rows(snapshot))
    validation_df = pd.DataFrame(snapshot["validations"])
    report_text_df = pd.DataFrame({"자동 보고문": build_report_text(snapshot, "천원").splitlines()})

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="총괄", index=False)
        business_df.to_excel(writer, sheet_name="사업별", index=False)
        monthly_df.to_excel(writer, sheet_name="월별", index=False)
        lines_df.to_excel(writer, sheet_name="예산과목별", index=False)
        quick_df.to_excel(writer, sheet_name="신속집행", index=False)
        validation_df.to_excel(writer, sheet_name="검증", index=False)
        report_text_df.to_excel(writer, sheet_name="자동보고문", index=False)

        from openpyxl.styles import Font, PatternFill, Alignment
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            for cell in worksheet[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="1E3A8A")
                cell.alignment = Alignment(horizontal="center")
            for column_cells in worksheet.columns:
                letter = column_cells[0].column_letter
                max_length = min(max(len(str(cell.value or "")) for cell in column_cells) + 3, 46)
                worksheet.column_dimensions[letter].width = max(max_length, 11)
            for row in worksheet.iter_rows(min_row=2):
                for cell in row:
                    if isinstance(cell.value, (int, float)):
                        cell.number_format = "#,##0.00" if isinstance(cell.value, float) and not float(cell.value).is_integer() else "#,##0"
    return output.getvalue()


def get_asset_acquisition_detail_rows():
    candidates = list(st.session_state.get("daily_expenses", [])) + copy.deepcopy(MANUAL_ASSET_ACQUISITION_ROWS)
    rows = []
    seen = set()
    for row in candidates:
        desc = str(row.get("적요", "")).strip()
        amount = float(clean_numeric(row.get("집행금액", 0)))
        category = force_mapped_category_for_known_cases(
            desc,
            row.get("세목", ""),
            row.get("예산과목", ""),
            amount,
        ) or get_mapped_category(desc, row.get("세목", ""), row.get("예산과목", ""))
        if category != "수탁자산취득비":
            continue
        date_value = str(row.get("집행일자", "")).strip()
        if re.search(r"\d{4}", date_value) and "2026" not in date_value:
            continue
        key = (date_value, re.sub(r"\s+", "", desc).lower(), int(round(amount)))
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "집행일자": date_value,
            "적요": desc,
            "지출액": amount,
            "자료구분": str(row.get("업로드구분", "원천내역") or "원천내역"),
        })
    return sorted(rows, key=lambda row: str(row.get("집행일자", "")))


def render_asset_acquisition_drilldown(allocated_amount, recognized_actual, unit="천원", context_label=""):
    with st.expander("수탁자산취득비 · 원장 대조 및 세부내역 수정", expanded=False):
        render_asset_reconciliation(st.session_state.get("report_config", {}), current_execution_index(),
            raw_management_evidence(), prefix="report_line_asset", save_config=save_execution_settings)


def render_asset_simple_summary(allocated_amount, actual_amount, unit="천원"):
    """예산과목별 화면은 요약을 먼저 보여주고, 거래내역은 클릭해 펼칩니다."""
    allocated = float(clean_numeric(allocated_amount))
    actual = float(clean_numeric(actual_amount))
    remaining = allocated - actual
    execution_rate = (actual / allocated * 100.0) if allocated else 0.0
    with st.container(border=True):
        st.markdown("#### 405-12 수탁자산취득비")
        asset_cols = st.columns(4)
        asset_cols[0].metric("예산배정액", report_amount(allocated, unit))
        asset_cols[1].metric("누적 지출액", report_amount(actual, unit))
        asset_cols[2].metric("집행잔액", report_signed_amount(remaining, unit) if remaining < 0 else report_amount(remaining, unit))
        asset_cols[3].metric("집행률", f"{execution_rate:.2f}%")
        st.progress(min(max(execution_rate / 100.0, 0.0), 1.0))
        st.caption("누적 원천의 예산배정액(B) 대비 지출액(E)이며, 집행잔액은 B-E로 계산합니다.")
        render_asset_acquisition_drilldown(
            allocated,
            actual,
            unit,
            "예산과목별",
        )


def render_report_page(df_all):
    cfg = ensure_report_config(st.session_state.get("report_config", {}))
    meta = cfg["meta"]
    st.info("이 보고서는 2026년 단월·누적 집행현황을 별도로 반영한 자료입니다. ‘원천자료 연계’에서 보고서 기준월을 갱신하세요. 최신 통제원장과 기준기간·포함범위를 대조한 뒤 사용해 주세요.")
    st.caption("기존 6월 기준값이 포함될 수 있습니다. 아래 작성기준일은 저장일이며, 실적의 조회종료일은 보고기간으로 확인합니다.")
    report_update_date = get_report_update_date(cfg)
    st.html(f'<div class="report-update-strip"><span>자료 작성기준일</span><strong>{html.escape(report_update_date)}</strong></div>')
    filter_cols = st.columns([1.0, 1.0, 1.15, 3.2])
    base_month = filter_cols[0].selectbox(
        "보고 기준월",
        [int(meta.get("base_month", 6))],
        index=0,
        format_func=lambda value: f"{value}월",
        key="report_view_base_month",
        disabled=True,
    )
    unit = filter_cols[1].selectbox(
        "표시단위",
        ["천원", "원"],
        index=0 if meta.get("amount_unit", "천원") == "천원" else 1,
        key="report_view_unit",
    )
    filter_cols[2].text_input("보고기간", value=get_report_period_label(base_month), disabled=True)
    filter_cols[3].text_input(
        "보고 기준자료",
        value=str(meta.get("source_note", "저장된 보고 기준값")),
        disabled=True,
    )

    rapid_df = st.session_state.get("rapid_df", get_default_rapid_df())
    ledger_state, _ = BudgetStore().load(2026)
    snapshot = build_report_snapshot(cfg, df_all, rapid_df, base_month, ledger_state)
    totals = snapshot["totals"]
    issue_count = snapshot["validation_issue_count"]
    issue_badge = (
        f'<span class="report-status warn">검증 필요 {issue_count}건</span>'
        if issue_count else
        '<span class="report-status ok">검증 일치</span>'
    )
    st.html(f"""
        <div class="report-hero">
            <div class="report-hero-grid">
                <div>
                    <div class="report-hero-title">통합 예산집행 보고</div>
                    <div class="report-hero-desc">
                        단월·누적 ERP 원천을 검증한 뒤 총괄·예산과목·신속집행에 같은 지출액을 반영합니다.<br>
                        대행사업비반환금은 원천 합계에 보존하고 관리 집행률에서는 분리합니다. {issue_badge}
                    </div>
                </div>
                <div class="report-stat"><div class="report-stat-label">ERP 전체 예산액</div><div class="report-stat-value">{report_amount(totals['예산액'], unit)}</div></div>
                <div class="report-stat"><div class="report-stat-label">{base_month}월 말 관리대상 지출액</div><div class="report-stat-value">{report_amount(totals['누적지출액'], unit)}</div></div>
                <div class="report-stat"><div class="report-stat-label">배정액 대비 집행률</div><div class="report-stat-value">{totals['집행률']:.2f}%</div></div>
            </div>
        </div>
        """)

    overview_tab, lines_tab, quick_tab, report_tab, source_tab, settings_tab = st.tabs([
        "현황",
        "예산과목별",
        "신속집행",
        "자동 보고서",
        "원천자료 연계",
        "기준값·검증",
    ])

    with overview_tab:
        st.html(f"""
            <div class="report-kpi-grid">
                <div class="report-kpi primary"><div class="report-kpi-label">관리대상 예산현액</div><div class="report-kpi-value">{report_amount(totals['관리대상 예산현액'], unit)}</div><div class="report-kpi-foot">대행사업비반환금 제외</div></div>
                <div class="report-kpi"><div class="report-kpi-label">관리대상 예산배정액</div><div class="report-kpi-value">{report_amount(totals['예산배정액'], unit)}</div><div class="report-kpi-foot">집행률 산정 기준</div></div>
                <div class="report-kpi"><div class="report-kpi-label">관리대상 누적 지출액</div><div class="report-kpi-value">{report_amount(totals['누적지출액'], unit)}</div><div class="report-kpi-foot">배정액 대비 {totals['집행률']:.2f}%</div></div>
                <div class="report-kpi"><div class="report-kpi-label">집행잔액</div><div class="report-kpi-value">{report_amount(totals['집행잔액'], unit)}</div><div class="report-kpi-foot">예산배정액 - 지출액</div></div>
                <div class="report-kpi goal"><div class="report-kpi-label">반환금 지출액(참고)</div><div class="report-kpi-value">{report_amount(totals['반환금 지출액'], unit)}</div><div class="report-kpi-foot">원천 총지출액 {report_amount(totals['원천 누적지출액'], unit)}</div></div>
            </div>
            <div class="goal-rail-wrap">
                <div style="display:flex;justify-content:space-between;gap:12px;font-size:.76rem;font-weight:900;color:#334155;">
                    <span>현재 관리대상 배정액 대비 {totals['집행률']:.2f}%</span>
                    <span>연간 관리목표 {totals['연간목표율']:.1f}%</span>
                </div>
                <div class="goal-rail">
                    <div class="goal-rail-fill" style="width:{min(max(totals['집행률'], 0), 100):.2f}%"></div>
                    <div class="goal-rail-marker" style="left:{min(max(totals['연간목표율'], 0), 100):.2f}%"></div>
                    <div class="goal-rail-label" style="left:{min(max(totals['연간목표율'], 0), 100):.2f}%">{totals['연간목표율']:.1f}% 목표</div>
                </div>
            </div>
            """)

        business_display = pd.DataFrame(snapshot["business"])[[
            "사업구분", "예산액", "예산현액", "예산배정액", "누적지출액",
            "예산현액 대비 집행률", "예산배정액 대비 집행률", "예산현액 대비 미지출액",
        ]].copy()
        for column in ["예산액", "예산현액", "예산배정액", "누적지출액", "예산현액 대비 미지출액"]:
            business_display[column] = business_display[column].apply(lambda value: report_amount(value, unit))
        for column in ["예산현액 대비 집행률", "예산배정액 대비 집행률"]:
            business_display[column] = business_display[column].apply(lambda value: f"{float(value):.2f}%")
        st.html('<div class="section-header">관리대상 예산구분별 집행현황</div>')
        st.dataframe(business_display, hide_index=True, width="stretch")

        monthly_df = pd.DataFrame(snapshot["monthly"])
        left_chart, right_table = st.columns([1.25, 1.0])
        with left_chart:
            st.html('<div class="section-header">월별 계획·지출·전년동월 비교</div>')
            chart_source = monthly_df[["월", "집행계획액", "지출액", "전년동월지출액"]].melt(
                id_vars=["월"],
                var_name="구분",
                value_name="금액",
            )
            chart_source["월표시"] = chart_source["월"].astype(int).astype(str) + "월"
            chart = (
                alt.Chart(chart_source)
                .mark_bar()
                .encode(
                    x=alt.X("월:O", title=None, axis=alt.Axis(labelExpr="datum.value + '월'")),
                    xOffset="구분:N",
                    y=alt.Y("금액:Q", title="금액(원)", axis=alt.Axis(format=",.0f")),
                    color=alt.Color(
                        "구분:N",
                        scale=alt.Scale(
                            domain=["집행계획액", "지출액", "전년동월지출액"],
                            range=["#cbd5e1", "#2563eb", "#7c3aed"],
                        ),
                        legend=alt.Legend(title=None, orient="top"),
                    ),
                    tooltip=[
                        alt.Tooltip("월표시:N", title="월"),
                        alt.Tooltip("구분:N"),
                        alt.Tooltip("금액:Q", format=",.0f"),
                    ],
                )
                .properties(height=320)
            )
            render_full_width_chart(chart)
        with right_table:
            st.html('<div class="section-header">월별 집행표</div>')
            monthly_display = monthly_df[[
                "월", "집행계획액", "지출액", "전년동월지출액", "계획대비 증감", "전년대비 증감", "누적지출액"
            ]].copy()
            monthly_display["월"] = monthly_display["월"].apply(lambda value: f"{int(value)}월")
            for column in ["집행계획액", "지출액", "전년동월지출액", "누적지출액"]:
                monthly_display[column] = monthly_display[column].apply(lambda value: report_amount(value, unit))
            for column in ["계획대비 증감", "전년대비 증감"]:
                monthly_display[column] = monthly_display[column].apply(lambda value: report_signed_amount(value, unit))
            st.dataframe(monthly_display, hide_index=True, width="stretch", height=356)
            st.caption("7~12월 기본 계획액은 2026년 집행전망액 PDF의 월별 합계입니다. 계획액 수정과 출처 확인은 ‘기준값·검증’에서 할 수 있습니다.")
            st.caption("전년동월 기본값은 기존 1~6월과 같은 2025년 PDF의 월별 합계입니다. 직접 수정한 값은 유지하며 출처는 ‘기준값·검증’에서 확인할 수 있습니다. 전년 기본자료는 반환금 포함, 금년 관리대상 집계는 반환금 제외 기준입니다.")

    with lines_tab:
        line_df = pd.DataFrame(snapshot["lines"])
        line_filter_cols = st.columns([1.1, 1.0, 1.8])
        available_business_labels = list(dict.fromkeys(
            str(value) for value in line_df.get("사업구분", pd.Series(dtype=str)).dropna().tolist()
        ))
        selected_business = line_filter_cols[0].selectbox(
            "예산구분",
            ["전체"] + available_business_labels,
            key="report_line_business_filter",
        )
        selected_status = line_filter_cols[1].selectbox(
            "집행상태",
            ["전체", "미집행", "집행 중", "집행률 높음", "배정액 도달", "관리 집행률 제외"],
            key="report_line_status_filter",
        )
        line_search = line_filter_cols[2].text_input(
            "세목코드·세목명 검색",
            placeholder="예: 201-21 또는 공공요금",
            key="report_line_search",
        )
        filtered_lines = line_df.copy()
        if selected_business != "전체":
            filtered_lines = filtered_lines[filtered_lines["사업구분"] == selected_business]
        if selected_status != "전체":
            filtered_lines = filtered_lines[filtered_lines["상태"] == selected_status]
        if line_search.strip():
            pattern = re.escape(line_search.strip())
            filtered_lines = filtered_lines[
                filtered_lines["세목코드"].astype(str).str.contains(pattern, case=False, na=False)
                | filtered_lines["세목명"].astype(str).str.contains(pattern, case=False, na=False)
            ]
        display_lines = filtered_lines[[
            "사업구분", "세목코드", "세목명", "예산배정액", "누적지출액", "잔액", "집행률", "상태"
        ]].copy()
        display_lines["예산배정액"] = display_lines["예산배정액"].apply(lambda value: report_amount(value, unit))
        display_lines["누적지출액"] = display_lines["누적지출액"].apply(lambda value: report_amount(value, unit))
        display_lines["잔액"] = display_lines["잔액"].apply(
            lambda value: report_signed_amount(value, unit) if float(clean_numeric(value)) < 0 else report_amount(value, unit)
        )
        display_lines["집행률"] = display_lines["집행률"].apply(lambda value: f"{float(value):.2f}%")
        display_lines = display_lines.rename(columns={"누적지출액": "누적 지출액", "잔액": "집행잔액"})
        st.dataframe(display_lines, hide_index=True, width="stretch", height=570)
        st.caption("집행률 = 누적 원천 지출액(E) ÷ 예산배정액(B), 집행잔액 = B-E. 대행사업비반환금은 관리 집행률에서 제외합니다.")
        asset_line = next(
            (row for row in snapshot["lines"] if row.get("세목코드") == "405-12"),
            {"예산배정액": 93464000, "누적지출액": 0},
        )
        render_asset_simple_summary(
            asset_line.get("예산배정액", 0),
            asset_line.get("누적지출액", 0),
            unit,
        )

    with quick_tab:
        snapshot["selected_quick"] = render_period_dashboard(
            df_all, rapid_df, current_execution_index(), cfg, get_quick_target_map(rapid_df), QUICK_EXEC_GOAL_RATES,
            prefix="report_quick", save_config=save_execution_settings, default_month=base_month)
        render_asset_reconciliation(cfg, current_execution_index(), raw_management_evidence(),
                                   prefix="report_asset", save_config=save_execution_settings)
        if st.button("신속집행 관리에서 대상액·계획 수정", key="report_go_quick", width="stretch"):
            go_page(PAGE_QUICK)
            st.rerun()

    with report_tab:
        narrative = build_report_narrative(snapshot, unit)
        report_sections_html = "".join(
            f'<div class="report-paper-section">{html.escape(section["title"])}</div>'
            + "".join(f'<div class="report-paper-line">○ {html.escape(line)}</div>' for line in section["lines"] if line)
            for section in narrative
        )
        st.html(f"""
            <div class="report-paper">
                <div class="report-paper-title">{html.escape(meta.get('title', '예산집행 현황 보고'))}({base_month}월)</div>
                <div class="report-paper-meta">작성기준일: {html.escape(report_update_date)}</div>
                <div class="report-paper-line">○ 보고기준: 2026년 {html.escape(snapshot['period_label'])} · 지출액 기준 · 단위 {html.escape(unit)}</div>
                {report_sections_html}
            </div>
            """)
        report_html_bytes = build_report_download_html(snapshot, unit).encode("utf-8")
        report_text_value = build_report_text(snapshot, unit)
        report_excel_bytes = build_report_excel_bytes(snapshot)
        file_base = f"정약용_펀그라운드_2026_예산집행현황_{base_month}월"
        download_cols = st.columns(4)
        download_cols[0].download_button(
            "HTML 보고서",
            data=report_html_bytes,
            file_name=f"{file_base}.html",
            mime="text/html",
            width="stretch",
        )
        download_cols[1].download_button(
            "Word 보고서",
            data=report_html_bytes,
            file_name=f"{file_base}.doc",
            mime="application/msword",
            width="stretch",
        )
        download_cols[2].download_button(
            "Excel 보고서",
            data=report_excel_bytes,
            file_name=f"{file_base}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )
        download_cols[3].download_button(
            "텍스트 보고서",
            data=report_text_value.encode("utf-8"),
            file_name=f"{file_base}.txt",
            mime="text/plain",
            width="stretch",
        )
        with st.expander("복사용 보고문", expanded=False):
            st.session_state["report_copy_text"] = report_text_value
            st.text_area("보고문", height=420, key="report_copy_text")

    with source_tab:
        st.html("""
            <div class="source-note">
                ERP에서 내려받은 <b>해당 월 단월 집행현황</b>과 <b>1~해당 월 누적 집행현황</b>을 함께 올리세요.
                파일 선택만으로 저장하지 않으며, 시트·헤더·예산과목·합계를 먼저 검증한 뒤
                ‘검증 결과 반영’을 눌러야 보고서가 최신화됩니다.
            </div>
            """)
        with st.expander("ERP 원장 다운로드·파일명 안내", expanded=True):
            st.markdown("ERP 메뉴 검색에서 **지출예산통제원장 집계표** 또는 **지출통제원장집계표**를 찾으세요. 회계연도 2026년, 정약용 펀그라운드의 운영경비·일상경비 전체를 같은 조건으로 선택하고 Excel로 내려받습니다.")
            st.dataframe(pd.DataFrame([
                {"구분": "단월", "ERP 조회기간 예시": "2026-09-01 ~ 2026-09-30", "저장할 파일명 예시": "9월 집행현황.xlsx", "용도": "9월 세목별 지출액"},
                {"구분": "누적", "ERP 조회기간 예시": "2026-01-01 ~ 2026-09-30", "저장할 파일명 예시": "1~9월 집행현황.xlsx", "용도": "9월 말 세목별 누적 지출액"},
            ]), hide_index=True, width="stretch")
            st.caption("예산현액(계)·예산배정액(B)·원인행위(C)·지출액(E), 합계와 사업예산·자본예산 행을 포함하고 금액은 원 단위로 내려받으세요. 두 파일의 사업 범위와 기준월은 같아야 합니다. 실제 실적은 지출액(E)을 사용합니다.")
            st.caption("자동 파일명에 조회기간이 없으면 위 예시처럼 이름을 바꾼 뒤 두 파일을 함께 선택하세요. 7월·8월 등 과거 월도 같은 방식으로 추가할 수 있으며 최신 보고서의 기준월은 유지됩니다.")
        source_audit = cfg.get("source_audit", {})
        if source_audit:
            current_source_cols = st.columns(4)
            current_source_cols[0].metric("현재 기준월", f"{int(source_audit.get('base_month', base_month))}월")
            current_source_cols[1].metric("연계 예산과목", f"{int(source_audit.get('leaf_line_count', len(cfg['lines'])))}개")
            current_source_cols[2].metric(
                "관리대상 누적 지출액",
                report_amount(source_audit.get("cumulative_management_actual", totals["누적지출액"]), unit),
            )
            current_source_cols[3].metric("원천 검증상태", str(source_audit.get("status", "저장됨")))

        uploaded_report_files = st.file_uploader(
            "예산집행현황 원천 2개 선택",
            type=["xlsx", "xls"],
            accept_multiple_files=True,
            help="예: 7월 집행현황.xlsx + 1~7월 집행현황.xlsx",
            key="budget_report_source_files",
        )
        if uploaded_report_files:
            with st.spinner("원천자료의 시트·열·합계를 검증하고 있습니다."):
                prepared_import = prepare_budget_report_source_import(
                    uploaded_report_files,
                    cfg,
                )
            if not prepared_import.get("ok"):
                for error_message in prepared_import.get("errors", ["원천자료를 인식하지 못했습니다."]):
                    st.error(error_message)
            else:
                monthly_source = prepared_import["monthly"]
                cumulative_source = prepared_import["cumulative"]
                st.html('<div class="section-header">1. 파일 인식 결과</div>')
                source_file_df = pd.DataFrame([
                    {
                        "구분": "단월",
                        "파일명": monthly_source["file_name"],
                        "인식 시트": monthly_source["sheet_name"],
                        "기준월": f"{prepared_import['base_month']}월",
                        "예산과목": monthly_source["line_count"],
                    },
                    {
                        "구분": "누적",
                        "파일명": cumulative_source["file_name"],
                        "인식 시트": cumulative_source["sheet_name"],
                        "기준월": f"1~{prepared_import['base_month']}월",
                        "예산과목": cumulative_source["line_count"],
                    },
                ])
                st.dataframe(source_file_df, hide_index=True, width="stretch")

                source_total_cols = st.columns(4)
                source_total_cols[0].metric(
                    f"{prepared_import['base_month']}월 단월 지출액",
                    report_amount(prepared_import["monthly_management_actual"], unit),
                )
                source_total_cols[1].metric(
                    f"1~{prepared_import['base_month']}월 관리대상 지출액",
                    report_amount(prepared_import["cumulative_management_actual"], unit),
                )
                source_total_cols[2].metric(
                    "ERP 원천 총지출액",
                    report_amount(prepared_import["source_total_actual"], unit),
                )
                source_total_cols[3].metric(
                    "반환금 지출액(분리)",
                    report_amount(prepared_import["return_actual"], unit),
                )

                st.html('<div class="section-header">2. 전 과목 정합성 검사</div>')
                source_checks = pd.DataFrame(prepared_import["checks"]).copy()
                for column in ["기준값", "비교값", "차이"]:
                    source_checks[column] = source_checks[column].apply(lambda value: f"{float(value):,.0f}")
                st.dataframe(source_checks, hide_index=True, width="stretch")
                if any(row.get("결과") != "일치" for row in prepared_import["checks"]):
                    st.warning(
                        "검증표의 ‘확인 필요’ 항목을 살펴보세요. ‘월별 합계와 누적 원천 연결’ "
                        "차이는 숫자를 숨겨 맞추지 않고 검증사항으로 남기며, 그 밖의 원천 정합성 "
                        "불일치는 잘못된 파일 반영을 막기 위해 저장을 차단합니다."
                    )

                st.html('<div class="section-header">3. 오류 제기 과목 재검증</div>')
                key_display = pd.DataFrame(prepared_import["key_rows"]).copy()
                for column in ["예산배정액", "누적 지출액", "잔액"]:
                    key_display[column] = key_display[column].apply(lambda value: report_amount(value, unit))
                key_display["집행률"] = key_display["집행률"].apply(lambda value: f"{float(value):.2f}%")
                st.dataframe(key_display, hide_index=True, width="stretch")

                if prepared_import.get("history_only"):
                    st.info("과거 월 원장은 월별 대조자료로 추가합니다. 최신 보고서 기준월과 누계는 유지합니다.")
                if not prepared_import.get("can_apply", False):
                    st.error(
                        "기준월·예산·배정액·예산과목 또는 누적 역전 검사에 불일치가 있어 반영을 차단했습니다. "
                        "같은 기준월에서 내려받은 단월·누적 파일인지 확인해 다시 선택하세요."
                    )
                elif st.button(
                    "검증 결과를 예산집행 보고서에 반영",
                    type="primary",
                    width="stretch",
                    key="apply_budget_report_source_import",
                ):
                    saved, safe_config = save_report_config(prepared_import["config"])
                    if saved:
                        st.session_state["report_config"] = safe_config
                        st.session_state.pop("initial_sync_done", None)
                        for editor_key in list(st.session_state):
                            if editor_key.startswith("report_edit_") or editor_key in ("report_business_editor", "report_monthly_editor", "report_line_editor"):
                                st.session_state.pop(editor_key, None)
                        if prepared_import.get("history_only"):
                            st.success(
                                f"{prepared_import['base_month']}월 원장을 월별 대조자료로 추가했습니다. "
                                "해당 월 집행현황·신속집행에 반영하며 최신 보고서 기준월과 누계는 유지합니다."
                            )
                        else:
                            st.success(
                                f"{prepared_import['base_month']}월 단월·누적 원천을 반영했습니다. "
                                "총괄·예산과목별·신속집행·다운로드 보고서가 함께 갱신됩니다."
                            )
                        time.sleep(0.5)
                        st.rerun()
                    else:
                        st.error("원천자료 검증은 완료했지만 보고 기준값을 저장하지 못했습니다.")
        else:
            st.info(
                "파일 2개를 한 번에 선택하세요. 파일명과 지출액 크기를 함께 확인해 단월·누적을 자동 구분합니다."
            )

    with settings_tab:
        st.html("""
            <div class="source-note">
                원칙적으로 ‘원천자료 연계’에서 단월·누적 파일을 검증 후 반영합니다.
                아래 편집기는 예외적인 수기 보정용이며, 다음 원천자료 반영 시 원천값으로 다시 갱신됩니다.
            </div>
            """)
        settings_meta_cols = st.columns([2.2, 1.0, 1.0])
        edit_title = settings_meta_cols[0].text_input("보고서 제목", value=str(meta.get("title", "")), key="report_edit_title")
        edit_goal_rate = settings_meta_cols[1].number_input(
            "연간 관리목표율(%)",
            min_value=0.0,
            max_value=100.0,
            value=float(meta.get("annual_goal_rate", 96.0)),
            step=0.1,
            key="report_edit_goal_rate",
        )
        edit_default_month = settings_meta_cols[2].selectbox(
            "기본 기준월",
            list(range(1, 13)),
            index=int(meta.get("base_month", 6)) - 1,
            format_func=lambda value: f"{value}월",
            key="report_edit_default_month",
        )
        edit_source_note = st.text_input("보고 기준자료 설명", value=str(meta.get("source_note", "")), key="report_edit_source_note")

        st.html('<div class="section-header">총괄 기준값</div>')
        total_cols = st.columns(3)
        edit_total_budget = total_cols[0].number_input("전체 예산액(ERP 원천·원)", min_value=0, value=int(cfg["totals"]["예산액"]), step=1000, key="report_edit_total_budget")
        edit_total_current = total_cols[1].number_input("전체 예산현액(ERP 원천·원)", min_value=0, value=int(cfg["totals"]["예산현액"]), step=1000, key="report_edit_total_current")
        edit_total_allocated = total_cols[2].number_input("관리대상 예산배정액(원)", min_value=0, value=int(cfg["totals"]["예산배정액"]), step=1000, key="report_edit_total_allocated")

        st.html('<div class="section-header">사업별 기준값</div>')
        business_editor = st.data_editor(
            pd.DataFrame(cfg["business"])[["사업키", "사업구분", "예산액", "예산현액", "예산배정액", "누적지출액"]],
            hide_index=True,
            width="stretch",
            disabled=["사업키"],
            column_config={
                "사업키": st.column_config.TextColumn("내부키"),
                "예산액": st.column_config.NumberColumn(format="%,d"),
                "예산현액": st.column_config.NumberColumn(format="%,d"),
                "예산배정액": st.column_config.NumberColumn(format="%,d"),
                "누적지출액": st.column_config.NumberColumn(format="%,d"),
            },
            key="report_business_editor",
        )

        st.html('<div class="section-header">월별 계획·지출·전년동월 기준값</div>')
        monthly_editor = st.data_editor(
            pd.DataFrame(cfg["monthly"])[["월", "집행계획액", "지출액", "전년동월지출액", "집행계획액 출처", "전년동월지출액 출처"]],
            hide_index=True,
            width="stretch",
            disabled=["월", "집행계획액 출처", "전년동월지출액 출처"],
            column_config={
                "월": st.column_config.NumberColumn(format="%d월"),
                "집행계획액": st.column_config.NumberColumn(format="%,d"),
                "지출액": st.column_config.NumberColumn(format="%,d"),
                "전년동월지출액": st.column_config.NumberColumn(format="%,d"),
            },
            key="report_monthly_editor",
        )

        with st.expander("예산과목별 기준값 편집", expanded=False):
            line_editor = st.data_editor(
                pd.DataFrame(cfg["lines"])[["사업키", "경비구분", "세목코드", "세목명", "예산배정액", "누적지출액"]],
                hide_index=True,
                width="stretch",
                column_config={
                    "사업키": st.column_config.SelectboxColumn(options=list(REPORT_BUSINESS_LABELS.keys())),
                    "경비구분": st.column_config.SelectboxColumn(options=["운영경비", "일상경비", "운영·일상", ""]),
                    "예산배정액": st.column_config.NumberColumn(format="%,d"),
                    "누적지출액": st.column_config.NumberColumn(format="%,d"),
                },
                key="report_line_editor",
            )
        edit_monthly_note = st.text_area("월별 집행 주석", value=str(cfg["notes"].get("monthly", "")), key="report_edit_monthly_note")
        edit_line_note = st.text_area("예산과목 집행 주석", value=str(cfg["notes"].get("lines", "")), key="report_edit_line_note")
        edit_asset_note = st.text_area("수탁자산취득비 관리 주석", value=str(cfg["notes"].get("asset", "")), key="report_edit_asset_note")

        if st.button("보고 기준값 저장", type="primary", key="report_save_config", width="stretch"):
            updated_config = copy.deepcopy(cfg)
            updated_config["meta"].update({
                "title": edit_title,
                "year": 2026,
                "base_month": int(edit_default_month),
                "annual_goal_rate": float(edit_goal_rate),
                "amount_unit": unit,
                "source_note": edit_source_note,
            })
            updated_config["totals"].update({
                "예산액": float(edit_total_budget),
                "예산현액": float(edit_total_current),
                "예산배정액": float(edit_total_allocated),
            })
            updated_config["business"] = business_editor.to_dict("records")
            updated_config["monthly"] = merge_report_monthly_edits(cfg["monthly"], monthly_editor.to_dict("records"))
            existing_line_map = {
                str(row.get("세목코드", "")).strip(): dict(row)
                for row in cfg["lines"]
            }
            edited_line_rows = []
            for edited_row in line_editor.to_dict("records"):
                code = str(edited_row.get("세목코드", "")).strip()
                merged_line = existing_line_map.get(code, {})
                merged_line.update(edited_row)
                allocation = float(clean_numeric(merged_line.get("예산배정액", 0)))
                actual = float(clean_numeric(merged_line.get("누적지출액", 0)))
                merged_line["잔액"] = allocation - actual
                edited_line_rows.append(merged_line)
            updated_config["lines"] = edited_line_rows
            updated_config["notes"].update({
                "monthly": edit_monthly_note,
                "lines": edit_line_note,
                "asset": edit_asset_note,
            })
            saved, safe_config = save_report_config(updated_config)
            if saved:
                st.session_state["report_config"] = safe_config
                st.success("보고 기준값을 저장했습니다.")
                time.sleep(0.4)
                st.rerun()
            else:
                st.error("보고 기준값을 저장하지 못했습니다.")

        st.html('<div class="section-header">자료 정합성 검증</div>')
        validation_df = pd.DataFrame(snapshot["validations"]).copy()
        for column in ["기준값", "비교값", "차이"]:
            validation_df[column] = validation_df[column].apply(lambda value: report_signed_amount(value, unit) if column == "차이" else report_amount(value, unit))
        st.dataframe(validation_df, hide_index=True, width="stretch")
        st.caption("확인 필요 차이는 원천값을 임의로 맞추지 않고 그대로 보존합니다. 단월 합계와 누적 합계는 각각의 파일을 기준으로 합니다.")

        source_map_df = pd.DataFrame([
            {"화면": "월별 지출관리·집행항목 분석", "원천": "일상경비 엑셀·CSV", "반영": "분류·중복 제거 후 관리항목별 월 집계"},
            {"화면": "통합 예산집행 보고", "원천": "단월 집행현황 + 1~기준월 누적 집행현황", "반영": "헤더·합계·전 과목 검증 후 총괄·월별·예산과목 자동 갱신"},
            {"화면": "신속집행", "원천": "대상액·계획 저장값 + 누적 예산과목 원천", "반영": "4개 항목 지출액 자동 연계·대상액 100% 상한"},
        ])
        st.dataframe(source_map_df, hide_index=True, width="stretch")


def render_app_home(df_all):
    st.markdown("### 정약용 펀그라운드 예산집행관리")
    st.caption("필요한 업무를 선택하세요. 예산·지출 현황과 보고서를 한곳에서 확인합니다.")
    st.html("<style>.st-key-v44_phone{border:7px solid #132641!important;border-radius:34px!important;padding:24px!important;background:#fff;max-width:100%;}.st-key-v44_phone button{min-height:78px;white-space:normal;}</style>")
    _left, center, _right = st.columns([1, 2, 1])
    with center, st.container(border=True, key="v44_phone"):
        st.markdown("**예산집행 업무**")
        st.caption("정약용 펀그라운드")
        state, _rev = BudgetStore().load(2026)
        ledger = state.get("ledger")
        if ledger:
            st.metric("공식 지출액", f"{ledger['total']['expense']:,}원")
            st.caption(f"2026. 1. 1. ~ {ledger['as_of']} 통제원장 기준")
        else:
            st.info("예산집행 보고서의 원천자료 연계에서 집행현황을 등록할 수 있습니다.")
        menu = [
            ("🧭 편성예산·부기관리", PAGE_BUDGET_CONTROL),
            ("⇄ 일상경비 자료연계", PAGE_IMPORT),
            ("📈 집행항목 분석", PAGE_ANALYSIS),
            ("📊 예산집행 현황", PAGE_OVERVIEW),
            ("⚡ 신속집행 관리", PAGE_QUICK),
            ("📝 예산집행 보고서", PAGE_REPORT),
        ]
        menu = [(label, page) for label, page in menu if page != PAGE_BUDGET_CONTROL or is_owner()]
        for index in range(0, len(menu), 2):
            for col, (label, page) in zip(st.columns(2), menu[index:index+2]):
                col.button(label, key="v44_home_"+page, width="stretch", on_click=go_page, args=(page,))
        st.caption("V45.9 · 원장 대조 · 거래 변경검토 · 부기 분할")

# -----------------------------------------------------------------------------
# 7. 메인 UI
# -----------------------------------------------------------------------------
if "current_page" not in st.session_state:
    st.session_state["current_page"] = "HOME"
if st.session_state["current_page"] == PAGE_BUDGET_CONTROL and not is_owner():
    st.session_state["current_page"] = "HOME"

with st.sidebar:
    st.html("""
            <div class="sidebar-brand">
                <div class="sidebar-brand-kicker">Budget Execution</div>
                <div class="sidebar-brand-title">2026 예산집행관리</div>
                <div class="sidebar-brand-sub">정약용 펀그라운드 · V45.9 원장 대조·부기관리 · 지출액 기준</div>
            </div>
        """)

    is_home = st.session_state.get("current_page") == "HOME"
    if st.button(
        "⌂  앱 홈",
        key="sidebar_nav_HOME",
        width="stretch",
        type="primary" if is_home else "secondary",
    ):
        st.session_state["current_page"] = "HOME"
        st.rerun()

    for group_name, group_pages in APP_NAV_GROUPS:
        st.caption(group_name)
        for nav_value in group_pages:
            if nav_value == PAGE_BUDGET_CONTROL and not is_owner():
                continue
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

    if is_owner():
        st.button("관리자 접속 종료", key="owner_logout", on_click=owner_logout, width="stretch")

    storage_mode = "클라우드 동기화" if db is not None and not st.session_state.get("quota_exceeded") else "로컬 JSON 저장"
    st.html(f'<div class="sidebar-status"><span class="status-dot"></span><span>{storage_mode}</span></div>')
    with st.expander("자료·저장 관리", expanded=False):
        st.caption("현재 집계와 원천내역을 다시 불러오거나 저장합니다.")
        if st.button("현재 집계 저장", key="sidebar_backup", width="stretch"):
            if save_data_cloud(st.session_state["data"]):
                st.success("저장 완료")
        if st.button("저장자료 다시 불러오기", key="sidebar_reload", width="stretch"):
            st.session_state["data"] = load_data()
            st.session_state["daily_expenses"] = load_daily_expenses()
            st.session_state["rapid_df"] = load_rapid_df()
            st.session_state["report_config"] = load_report_config()
            st.session_state.pop("initial_sync_done", None)
            st.rerun()
        st.caption(f"시스템 ID · {appId}")

if st.query_params.get("owner") == "1" and not is_owner():
    render_owner_login()
    st.stop()

if st.session_state.get("current_page") == "HOME":
    render_app_home(exclusive_expenditure_frame(df_all))
    st.stop()

current_page = st.session_state.get("current_page", PAGE_OVERVIEW)
if current_page == PAGE_MISSING:
    st.session_state["current_page"] = PAGE_ANALYSIS
    st.session_state["analysis_section"] = "⚠️ 미집행 점검"
    current_page = PAGE_ANALYSIS
if current_page == "▦ 월별 정량실적":
    st.session_state["current_page"] = PAGE_ANALYSIS
    current_page = PAGE_ANALYSIS
current_meta = APP_PAGE_META.get(
    current_page,
    {"icon": "▦", "title": current_page, "desc": "2026 예산집행 업무 화면"},
)
page_unit_label = (
    ensure_report_config(st.session_state.get("report_config", {}))["meta"].get("amount_unit", "천원")
    if current_page == PAGE_REPORT
    else "원"
)
st.html(f"""
    <div class="page-head">
        <div>
            <div class="page-kicker">2026 예산집행관리 / {current_meta["title"]}</div>
            <div class="page-title">{current_meta["icon"]} {current_meta["title"]}</div>
            <div class="page-desc">{current_meta["desc"]}</div>
        </div>
        <div class="context-chips">
            <span class="context-chip">회계연도 2026</span>
            <span class="context-chip">지출액 기준</span>
            <span class="context-chip">단위 {page_unit_label}</span>
        </div>
    </div>
    """)

if current_page not in (PAGE_BUDGET_CONTROL, PAGE_REPORT):
    render_source_strip(current_page)

# --- 통합 예산집행 보고서 ---
if current_page == PAGE_BUDGET_CONTROL:
    render_budget_control_page()
    st.stop()

if current_page == PAGE_REPORT:
    report_mode = st.radio("보고서 기준", ["기존 단월·비교 보고", "현재 통제원장 자동 보고"], horizontal=True, key="v45_report_mode")
    if report_mode == "현재 통제원장 자동 보고":
        render_current_report(st.session_state.get("daily_expenses", []))
    else:
        render_report_page(df_all)
    st.stop()

# --- TAB 1: 실적 현황 ---
if current_page == PAGE_OVERVIEW:
    _, settings_col = st.columns([8, 1])
    overview_settings = settings_col.popover("⚙ 설정", help="지출액 추가 반영·월별 집계 조정", width="stretch")
    full_management_26 = df_all[df_all["year"] == 2026].copy() if not df_all.empty else pd.DataFrame()
    df_all = exclusive_expenditure_frame(df_all)
    st.caption("관리항목 합계: 일반재료비에 포함된 상하수도 중복분과 수입금을 제외합니다. ERP 공식 지출액은 예산집행 보고서에서 확인하세요.")
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

    st.html(f"""
        <div class="ops-kpi-grid">
            <div class="ops-kpi ops-kpi-accent"><div class="ops-kpi-label">누적 집행액</div><div class="ops-kpi-value">{int(val_26):,}원</div><div class="ops-kpi-foot">지출액 기준</div></div>
            <div class="ops-kpi"><div class="ops-kpi-label">최근 월 집행액</div><div class="ops-kpi-value">{int(latest_exec_amount):,}원</div><div class="ops-kpi-foot">{f"{latest_exec_month}월" if latest_exec_month else "집행내역 없음"}</div></div>
            <div class="ops-kpi"><div class="ops-kpi-label">전년 동월누계 증감</div><div class="ops-kpi-value">{int(yoy_gap):+,}원</div><div class="ops-kpi-foot">{f"{yoy_rate:+.1f}%" if yoy_rate is not None else "전년 비교값 없음"} · 1~{compare_month}월</div></div>
            <div class="ops-kpi"><div class="ops-kpi-label">집행 발생 항목</div><div class="ops-kpi-value">{active_category_count} / {len(CATEGORIES)}개</div><div class="ops-kpi-foot">2026년 지출 발생 기준</div></div>
        </div>
        """)

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
                render_full_width_chart(chart)

    with review_col:
        render_quality_panel(daily_rows, get_row_management_category, PAGE_IMPORT, prefix="overview_quality")

    composition_tab, quick_tab = st.tabs(["지출 구성", "신속집행 요약"])
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
            render_full_width_chart(bars)
        else:
            st.info("2026년 지출액이 아직 없습니다.")

    with overview_settings:
        st.markdown("#### 월별 지출액 추가 반영")
        st.caption("2026년 조정분은 원천내역에도 저장되어 자료 재집계 후에도 유지됩니다. 2024·2025년은 비교용 월별 집계에 반영됩니다.")
        iy_col, ic_col, im_col = st.columns([1, 2, 1])
        iy = iy_col.selectbox("회계연도", YEARS, index=2, key="y_reg")
        ic = ic_col.selectbox("집행관리 항목", CATEGORIES, key="c_reg", format_func=display_category)
        im = im_col.selectbox("귀속월", MONTHS, format_func=lambda x: f"{x}월", key="m_reg")
        st.number_input("추가 반영액(원)", min_value=0, step=10000, key="amt_box")
        st.html(f'<div class="korean-amount">{number_to_korean(st.session_state.amt_box)}</div>')
        bc1, bc2, bc3, bc4 = st.columns([1, 1, 1, 2])
        bc1.button("+10만", on_click=update_amt, args=(100000,))
        bc2.button("+100만", on_click=update_amt, args=(1000000,))
        bc3.button("입력 초기화", on_click=reset_amt)
        bc4.button("지출액 조정 반영", type="primary", on_click=save_and_register, args=(iy, ic, im), width="stretch")

    with quick_tab:
        render_period_dashboard(df_all, st.session_state["rapid_df"], current_execution_index(),
            st.session_state["report_config"], get_quick_target_map(st.session_state["rapid_df"]), QUICK_EXEC_GOAL_RATES,
            prefix="overview_quick", save_config=save_execution_settings)

    asset_index, asset_contributions = current_execution_index(), raw_management_evidence()
    asset_needs = [m for m in MONTHS if asset_review(st.session_state["report_config"], asset_index, asset_contributions, m)["needs_review"]]
    if asset_needs:
        st.warning("수탁자산취득비는 원장 금액으로 동기화했으며, 세부내역 확인이 필요합니다: " + ", ".join(f"{m}월" for m in asset_needs))
        st.button("수탁자산취득비 원장 대조·수정으로 이동", key="overview_asset_review", type="primary",
                  on_click=go_page, args=(PAGE_QUICK,))
    with st.expander("수탁자산취득비 · 원장 기준금액 확인 및 세부내역 수정", expanded=False):
        render_asset_reconciliation(st.session_state["report_config"], asset_index, asset_contributions,
                                   prefix="overview_asset", save_config=save_execution_settings)

    st.html('<div class="section-header">2026 월별 지출액 통합표</div>')
    st.caption("금액 셀을 선택하면 해당 월에 반영된 원천거래와 합계가 열립니다. 지출액 조정은 오른쪽 위 ⚙ 설정에서 처리합니다.")
    if not full_management_26.empty:
        df_p = full_management_26.pivot(index="category", columns="month", values="amount").fillna(0).reindex(index=CATEGORIES, columns=MONTHS, fill_value=0)
        df_p.columns = [f"{m}월" for m in df_p.columns]
        source_table = df_p.copy()
        source_table.index = [display_category(value) for value in source_table.index]
        source_table.index.name = "집행관리 항목"
        render_monthly_source_grid(source_table, CATEGORIES, current_management_evidence(), daily_rows, display_category, PAGE_IMPORT)
        with overview_settings:
            with st.expander("월별 지출액 조정", expanded=False):
                st.caption("변경분은 수동 조정 원천내역에 보존됩니다. 다시 집계해도 유지됩니다.")
                ed = st.data_editor(source_table, height=400, key="monthly_adjustment_v452")
                if st.button("월별 지출액 조정 저장", key="save_monthly_adjustment_v452", type="primary", disabled=not st.session_state["data"].get("source_linked")):
                    updates = [(2026, cat, month, ed.loc[display_category(cat), f"{month}월"]) for cat in CATEGORIES for month in MONTHS]
                    if apply_manual_cell_changes(updates):
                        st.rerun()

# --- 집행항목 분석 안에서 미집행 점검으로 바로 전환 ---
if current_page == PAGE_ANALYSIS:
    st.markdown("### 집행항목 분석 · ⚠️ 미집행 점검")
    analysis_section = st.radio("분석·점검 화면", ["집행항목 분석", "⚠️ 미집행 점검"],
                                horizontal=True, key="analysis_section")
    st.caption("월별 미집행 확인과 제외 처리는 ‘⚠️ 미집행 점검’을 선택하세요.")

# --- TAB 2: 집행관리 항목 분석 ---
if current_page == PAGE_ANALYSIS and analysis_section == "집행항목 분석":
    _, settings_col = st.columns([8, 1])
    analysis_settings = settings_col.popover("⚙ 설정", help="원천내역 기준으로 집계 다시 계산", width="stretch")
    tool_col, note_col = st.columns([1, 3])
    with analysis_settings:
        if st.button("원천내역 다시 집계", type="primary", width="stretch", key="analysis_resync"):
            if sync_daily_to_master_auto():
                st.success("원천내역 기준으로 월별 집계를 갱신했습니다.")
                time.sleep(0.5)
                st.rerun()
    with note_col:
        st.html('<div class="term-note">화면의 ‘집행관리 항목’은 분석용 분류입니다. '
            '원본의 예산과목·세목은 일상경비 자료연계 화면에서 별도로 확인할 수 있습니다.</div>')

    analysis_compare_month = min(max(datetime.now().month - 1, 1), 12)
    summary_df = pd.DataFrame()
    if not df_all.empty:
        df_2026_all = df_all[df_all["year"] == 2026].copy()
        global_months = df_2026_all[df_2026_all["amount"] > 0]["month"].tolist()
        global_latest_month = max(global_months) if global_months else analysis_compare_month

        analysis_controls = st.columns([1.0, 1.6, 2.4])
        analysis_compare_month = analysis_controls[0].selectbox(
            "동월누계 기준월",
            MONTHS,
            index=max(min(int(global_latest_month), 12), 1) - 1,
            format_func=lambda value: f"1~{value}월",
            key="analysis_compare_month_v42",
        )
        chart_focus = analysis_controls[1].selectbox(
            "차트 표시기준",
            ["2026 집행액 상위 8개", "전년 대비 변동 큰 8개", "전체 항목"],
            key="analysis_chart_focus_v42",
        )
        analysis_controls[2].markdown(
            '<div class="term-note">모든 항목을 동일한 기준월로 맞춰 비교합니다. '
            '일반재료비와 상하수도처럼 중복 관리되는 항목이 있어 단순 합계는 KPI로 표시하지 않습니다.</div>',
            unsafe_allow_html=True,
        )

        summary_df = build_three_year_comparison(df_all, analysis_compare_month)
        summary_df = summary_df.sort_values("2026년 동월누계", ascending=False).reset_index(drop=True)
        paid_count = int((summary_df["2026년 동월누계"] > 0).sum())
        increase_count = int((summary_df["전년대비 증감액"] > 0).sum())
        decrease_count = int((summary_df["전년대비 증감액"] < 0).sum())
        max_variance_row = (
            summary_df.loc[summary_df["전년대비 증감액"].abs().idxmax()]
            if not summary_df.empty else None
        )

        st.html(f"""
            <div class="ops-kpi-grid">
                <div class="ops-kpi ops-kpi-accent"><div class="ops-kpi-label">비교기간</div><div class="ops-kpi-value">1~{analysis_compare_month}월</div><div class="ops-kpi-foot">2024·2025·2026 동일 기준</div></div>
                <div class="ops-kpi"><div class="ops-kpi-label">2026 집행 발생</div><div class="ops-kpi-value">{paid_count}개</div><div class="ops-kpi-foot">전체 {len(CATEGORIES)}개 집행관리 항목</div></div>
                <div class="ops-kpi"><div class="ops-kpi-label">전년 대비 변동</div><div class="ops-kpi-value">↑ {increase_count} · ↓ {decrease_count}</div><div class="ops-kpi-foot">증가·감소 항목 수</div></div>
                <div class="ops-kpi"><div class="ops-kpi-label">최대 변동 항목</div><div class="ops-kpi-value" style="font-size:1rem;">{html.escape(str(max_variance_row["집행관리 항목"])) if max_variance_row is not None else "-"}</div><div class="ops-kpi-foot">{int(max_variance_row["전년대비 증감액"]) if max_variance_row is not None else 0:+,}원</div></div>
            </div>
            """)

        if chart_focus == "전년 대비 변동 큰 8개":
            chart_rows = (
                summary_df.assign(_abs_gap=summary_df["전년대비 증감액"].abs())
                .nlargest(8, "_abs_gap")
                .drop(columns=["_abs_gap"])
            )
        elif chart_focus == "전체 항목":
            chart_rows = summary_df.copy()
        else:
            chart_rows = summary_df.nlargest(8, "2026년 동월누계")

        chart_source = chart_rows[[
            "집행관리 항목", "2024년 동월누계", "2025년 동월누계", "2026년 동월누계"
        ]].melt(
            id_vars=["집행관리 항목"],
            var_name="연도",
            value_name="동월누계",
        )
        chart_source["연도"] = chart_source["연도"].str.replace(" 동월누계", "", regex=False)
        category_order = chart_rows["집행관리 항목"].tolist()
        comparison_chart = (
            alt.Chart(chart_source)
            .mark_bar(cornerRadiusEnd=3)
            .encode(
                y=alt.Y(
                    "집행관리 항목:N",
                    title=None,
                    sort=category_order,
                    axis=alt.Axis(labelLimit=190),
                ),
                yOffset="연도:N",
                x=alt.X("동월누계:Q", title="동월누계 지출액(원)", axis=alt.Axis(format=",.0f")),
                color=alt.Color(
                    "연도:N",
                    scale=alt.Scale(
                        domain=["2024년", "2025년", "2026년"],
                        range=["#cbd5e1", "#94a3b8", "#2563eb"],
                    ),
                    legend=alt.Legend(title=None, orient="top"),
                ),
                tooltip=[
                    alt.Tooltip("집행관리 항목:N"),
                    alt.Tooltip("연도:N"),
                    alt.Tooltip("동월누계:Q", format=",.0f", title="지출액"),
                ],
            )
            .properties(height=max(330, min(len(chart_rows) * 43, 760)))
        )
        st.markdown("#### 3개년 동월누계 비교")
        st.caption(f"2024년·2025년·2026년 모두 1~{analysis_compare_month}월 누계로 비교합니다.")
        render_full_width_chart(comparison_chart)

        increase_rows = summary_df[summary_df["전년대비 증감액"] > 0].nlargest(4, "전년대비 증감액")
        decrease_rows = summary_df[summary_df["전년대비 증감액"] < 0].nsmallest(4, "전년대비 증감액")

        def variance_panel(title, rows, amount_class):
            row_html = "".join(
                f'<div class="variance-row"><span>{html.escape(str(row["집행관리 항목"]))}</span>'
                f'<span class="variance-amount {amount_class}">{int(row["전년대비 증감액"]):+,}원</span></div>'
                for _, row in rows.iterrows()
            )
            if not row_html:
                row_html = '<div class="variance-row"><span>해당 항목 없음</span><span class="variance-amount">-</span></div>'
            return f'<div class="variance-panel"><div class="variance-title">{html.escape(title)}</div>{row_html}</div>'

        st.markdown(
            '<div class="variance-grid">'
            + variance_panel("전년 대비 증가 상위", increase_rows, "up")
            + variance_panel("전년 대비 감소 상위", decrease_rows, "down")
            + '</div>',
            unsafe_allow_html=True,
        )

        with st.expander(f"전체 {len(summary_df)}개 항목 비교표 보기", expanded=False):
            summary_display = summary_df.drop(columns=["_category"]).copy()
            summary_display["전년대비 증감률"] = summary_display["전년대비 증감률"].apply(
                lambda value: "-" if pd.isna(value) else f"{value:+.1f}%"
            )
            st.dataframe(
                summary_display,
                width="stretch",
                hide_index=True,
                height=540,
                column_config={
                    "2024년 동월누계": st.column_config.NumberColumn(format="%,d원"),
                    "2025년 동월누계": st.column_config.NumberColumn(format="%,d원"),
                    "2026년 동월누계": st.column_config.NumberColumn(format="%,d원"),
                    "전년대비 증감액": st.column_config.NumberColumn(format="%+d원"),
                },
            )
    else:
        st.info("표시할 월별 지출액이 없습니다.")

    st.html('<div class="section-header">선택 항목 상세 분석</div>')
    sc = st.selectbox(
        "집행관리 항목",
        CATEGORIES,
        key="analysis_sel_v292",
        format_func=display_category,
        on_change=select_analysis_adjustment,
    )
    df_c = df_all[df_all["category"] == sc] if not df_all.empty else pd.DataFrame()

    if not df_c.empty:
        render_analysis_adjustment(df_c, sc)
        if sc in QUICK_EXEC_CONFIG:
            render_period_dashboard(df_all, st.session_state["rapid_df"], current_execution_index(),
                st.session_state["report_config"], get_quick_target_map(st.session_state["rapid_df"]), QUICK_EXEC_GOAL_RATES,
                prefix="analysis_quick", save_config=save_execution_settings, categories=[sc])

        df_c_period = df_c[df_c["month"] <= analysis_compare_month].copy()
        v24 = float(df_c_period[df_c_period["year"] == 2024]["amount"].sum())
        v25 = float(df_c_period[df_c_period["year"] == 2025]["amount"].sum())
        v26 = float(df_c_period[df_c_period["year"] == 2026]["amount"].sum())
        st.html(f"""
            <div class="ops-kpi-grid" style="grid-template-columns:repeat(3,minmax(0,1fr));">
                <div class="ops-kpi"><div class="ops-kpi-label">2024년 1~{analysis_compare_month}월</div><div class="ops-kpi-value">{int(v24):,}원</div></div>
                <div class="ops-kpi"><div class="ops-kpi-label">2025년 1~{analysis_compare_month}월</div><div class="ops-kpi-value">{int(v25):,}원</div></div>
                <div class="ops-kpi ops-kpi-accent"><div class="ops-kpi-label">2026년 1~{analysis_compare_month}월</div><div class="ops-kpi-value">{int(v26):,}원</div><div class="ops-kpi-foot">원천내역 자동 연계 포함</div></div>
            </div>
            """)

        chart_df = df_c_period.copy()
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
        render_full_width_chart(detail_chart)

# --- TAB 3: 미집행 현황 ---
if current_page == PAGE_ANALYSIS and analysis_section == "⚠️ 미집행 점검":
    _, settings_col = st.columns([8, 1])
    missing_settings = settings_col.popover("⚙ 설정", help="월별 확인 제외 조정·일괄 처리", width="stretch")
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
        atomic_write_json(OVERRIDE_FILE, data)

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
        atomic_write_json(OVERRIDE_LOG_FILE, logs[-300:])

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

    st.html('<span class="section-label">2026년 월별 미집행 점검</span>')
    now = datetime.now()
    cy, cm = now.year, now.month
    check_until_month = (cm - 1) if cy == CHECK_YEAR else 12
    check_until_month = max(1, min(12, check_until_month))

    st.html('<div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:18px; padding:16px 18px; margin-bottom:16px;">'
        '<div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px; flex-wrap:wrap;">'
        '<div>'
        '<div style="font-size:1.05rem; font-weight:950; color:#0f172a;">월별 지출액이 0원인 항목을 확인대상으로 분류합니다.</div>'
        f'<div style="font-size:.86rem; color:#64748b; font-weight:750; margin-top:4px;">점검연도: {CHECK_YEAR}년 · 점검범위: 1~{check_until_month}월 · 후지급·일괄지급 등은 확인대상에서 제외할 수 있습니다.</div>'
        '</div>'
        f'<div style="background:#e0f2fe; color:#075985; border:1px solid #bae6fd; border-radius:999px; padding:8px 13px; font-size:.86rem; font-weight:950; white-space:nowrap;">점검연도 {CHECK_YEAR}</div>'
        '</div>'
        '</div>')

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
        k2.metric("지출 확인 항목", f"{len(raw_missing_rows)}개")
        k3.metric("확인 제외 적용 항목", f"{len(excluded_rows)}개")
        k4.metric("최종 확인 필요 항목", f"{len(effective_missing_rows)}개")

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
                    st.html(f'<div style="background:#ffffff; border:1px solid {border}; border-left:6px solid {fg}; border-radius:18px; padding:15px 16px; margin-bottom:14px; box-shadow:0 8px 20px rgba(15,23,42,.05); min-height:152px;">'
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
                        f'</div>')

        with missing_settings:
            st.markdown("### 월별 확인 제외 조정")
            st.caption("체크한 월은 지출액이 0원이어도 후지급·일괄지급 등 예외로 보고 최종 확인대상에서 제외합니다.")

            if raw_missing_rows:
                raw_df = pd.DataFrame(raw_missing_rows).sort_values(["count", "category"], ascending=[False, True])
                for row in raw_df.itertuples(index=False):
                    cat = row.category
                    cat_key = re.sub(r"[^0-9a-zA-Z가-힣_]+", "_", str(cat))
                    excluded_count = len([m for m in row.months if overrides.get(cat, {}).get(str(m), False)])
                    rec_months = set(recommendations.get(cat, []))
                    with st.expander(f"{display_category(cat)} · 확인대상 {row.count}개월 · 제외 {excluded_count}개월 · 최종 {row.count - excluded_count}개월", expanded=False):
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
    st.session_state.pop("_source_navigation_requested", None)
    st.html("""
        <div class="source-note">
            <b>반영 기준</b> · 파일을 선택해도 바로 저장하지 않습니다.
            인식 시트, 신규·중복 건수, 집행관리 항목 분류 결과를 먼저 검토한 뒤
            <b>검증 결과 반영</b>을 눌러야 원천내역과 월별 집계가 갱신됩니다.
        </div>
        """)
    st.html("""
        <div class="pipeline">
            <div class="pipeline-step"><div class="pipeline-index">STEP 01</div><div class="pipeline-label">파일 선택</div><div class="pipeline-value">xlsx·csv / 전체 시트</div></div>
            <div class="pipeline-step"><div class="pipeline-index">STEP 02</div><div class="pipeline-label">열·시트 인식</div><div class="pipeline-value">일자·적요·금액·예산과목</div></div>
            <div class="pipeline-step"><div class="pipeline-index">STEP 03</div><div class="pipeline-label">분류 검증</div><div class="pipeline-value">집행관리 항목 매핑</div></div>
            <div class="pipeline-step"><div class="pipeline-index">STEP 04</div><div class="pipeline-label">중복 확인</div><div class="pipeline-value">일자·적요·금액 기준</div></div>
            <div class="pipeline-step"><div class="pipeline-index">STEP 05</div><div class="pipeline-label">월별 반영</div><div class="pipeline-value">확정 후 전체 화면 갱신</div></div>
        </div>
        """)

    if st.session_state.get("import_flash"):
        st.success(st.session_state.pop("import_flash"))

    def render_import_review(uploaded_file, kind, parser, pending_key, last_hash_key):
        if uploaded_file is None:
            return
        raw = uploaded_file.getvalue()
        file_hash = hashlib.sha256(raw).hexdigest()
        sheets = read_excel_sheets_flexible(uploaded_file)
        if not sheets:
            st.error("값이 있는 시트가 없습니다.")
            return
        sheet = st.selectbox("반영할 원본 시트", list(sheets), key="v45_sheet_"+kind)
        analysis = review_source_layout(sheets[sheet], kind, filename=uploaded_file.name, sheet=sheet,
                                        fingerprint=file_hash, mapper=force_mapped_category_for_known_cases)
        parsed_rows = analysis["rows"]
        if not parsed_rows:
            return
        existing_rows = st.session_state.get("daily_expenses", [])
        review = review_import(existing_rows, parsed_rows)
        counts = pd.Series([x["status"] for x in review]).value_counts().to_dict()
        cols = st.columns(5)
        for col,label in zip(cols,["신규","분류정보 보완","기존 중복","수정 후보","파일 내부 중복"]):
            col.metric(label, f"{counts.get(label,0):,}건")
        details = [{"상태":x["status"], "원본 행":x["row"].get("원본행"),
                    "지급일/월":x["row"].get("집행일자"), "일자 근거":x["row"].get("일자근거", "지급일자"),
                    **account_display(x["row"]),
                    "예산과목 원본":x["row"].get("원본I열") or x["row"].get("예산과목", ""),
                    "과목 원본 열":x["row"].get("예산과목원본열", "열 제목 기준"),
                    "집행관리 항목":get_row_management_category(x["row"]) or "관리항목 해당 없음",
                    "적요":x["row"].get("적요",""),
                    "기존 지출액":x["before"].get("집행금액") if x["before"] else None,
                    "새 지출액":x["row"].get("집행금액"), "검증사항":x["row"].get("검증오류", ""),
                    "결의/명령번호":x["row"].get("지출결의번호",x["row"].get("지급명령번호","")),
                    "명세번호":x["row"].get("명세번호", "")} for x in review]
        st.dataframe(pd.DataFrame(details), hide_index=True, width="stretch")
        changes = [x for x in review if x["status"] == "수정 후보"]
        selected=[]
        if changes:
            labels={x["id"]:f"{x['row'].get('적요','')} · {x['before']['집행금액']} → {x['row']['집행금액']}원" for x in changes}
            selected=st.multiselect("기존 거래를 교체할 수정건", list(labels), format_func=labels.get, key="v45_changes_"+kind+file_hash+sheet)
            st.caption("선택한 수정건만 이전값을 남기고 교체합니다. 저장된 부기 분류는 원천 변경 시 재확인 대상이 됩니다.")
        fallback=sum(x["id"].startswith("row:") for x in review)
        if fallback:
            st.caption(f"{fallback}행은 완전한 거래 식별번호가 없어 날짜·적요·금액 등으로 대조합니다. 실제 별건이 모두 같은 경우 원본에 거래ID/명세번호를 추가해 주세요.")
        if counts.get("분류정보 보완", 0):
            st.caption("분류정보 보완은 같은 원천거래의 I열·세목·식별정보를 갱신합니다. 금액·지급일·적요와 기존 거래키는 유지하고 변경 전 정보를 보관합니다. 기존 부기 분류는 재확인할 수 있습니다.")
        if counts.get("기존 거래 대조 필요", 0):
            st.error("기존 거래와 내용이 같지만 식별정보가 부족하거나 중복되어 자동 대조하지 못했습니다. 결의번호·명세번호·원본 파일/시트/행을 확인해 주세요. 이 상태에서는 중복 추가를 차단합니다.")
            with st.expander("대조가 필요한 기존 거래 상세", expanded=True):
                candidates = [{"업로드 적요": x["row"].get("적요", ""),
                    "기존 거래키": transaction_id(r), "기존 지급일": r.get("집행일자", ""),
                    "기존 금액": r.get("집행금액", ""), "기존 세목": expense_code(r),
                    "결의번호": r.get("지출결의번호", ""), "명세번호": r.get("명세번호", ""),
                    "원본 파일": r.get("원본파일", ""), "원본 시트": r.get("원본시트", ""),
                    "원본 행": r.get("원본행", "")} for x in review for r in x.get("match_candidates", [])]
                st.dataframe(pd.DataFrame(candidates), hide_index=True, width="stretch")
        invalid=any(x["status"] in ("오류 · 반영 제외","파일 내부 충돌","기존 거래 대조 필요") for x in review)
        if invalid:
            st.error("오류 또는 같은 식별번호의 상충 행이 있습니다. 원본을 수정한 뒤 반영해 주세요.")
        delta=sum(x["delta"] for x in review if x["status"] == "신규" or x["status"] == "수정 후보" and x["id"] in selected)
        st.caption(f"반영 시 원천 지출액 증감 {delta:+,}원 · {sheet} 시트 기준")
        if st.button(f"검증 결과 반영 · 신규 {counts.get('신규',0)}건 / 보완 {counts.get('분류정보 보완',0)}건 / 수정 {len(selected)}건",
                     type="primary", key=f"apply_import_{kind}",
                     disabled=invalid or not (counts.get("신규",0) or counts.get("분류정보 보완",0) or selected)):
            merged, applied=commit_import(existing_rows,review,selected)
            if save_daily_expenses(merged):
                st.session_state["daily_expenses"]=merged
                st.session_state.pop("source_filter", None)
                st.session_state.pop("initial_sync_done",None)
                sync_daily_to_master_auto()
                st.session_state["import_flash"]=f"신규 {applied.get('added',0)}건·분류정보 보완 {applied.get('refreshed',0)}건·수정 {applied.get('replaced',0)}건을 저장했습니다."
                st.rerun()

    general_tab, special_tab = st.tabs(["일반 일상경비", "정기지출·공과금"])
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
        st.markdown("#### 정기지출·공과금 파일")
        st.caption("지급명령 엑셀의 열 제목을 자동 인식하며 기존 B열 지급월·K열 문서제목 양식도 지원합니다. 기존 5대 정기지출과 세탁용역·승강기 유지관리·상하수도요금을 함께 가져옵니다. 건조기·세탁기 수리비는 세탁용역에서 제외합니다.")
        special_file = st.file_uploader(
            "정기지출·공과금 엑셀·CSV",
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

    st.html('<div class="section-header">저장된 일상경비 원천내역</div>')
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

        df_d = df_d.assign(temp_code=df_d.apply(lambda row: expense_code(row) or extract_budget_code(row.get("세목", "")), axis=1))
        best_names = df_d.groupby("temp_code")["세목"].apply(lambda values: max(values.astype(str), key=len)).to_dict()

        def get_full_semok_name(code, current_best):
            if code in BUDGET_MAPPING:
                item_group, item_name = BUDGET_MAPPING[code]
                prefix = code.split("-")[0]
                return f"[{prefix}]{item_group} - [{code}]{item_name}"
            return current_best if current_best else code

        cleaned_semok = df_d["temp_code"].apply(
            lambda code: get_full_semok_name(code, best_names.get(code, ""))
        )
        df_d = df_d.assign(세목=cleaned_semok).drop(columns=["temp_code"])
        df_d["관리항목키"] = df_d.apply(get_row_management_category, axis=1)
        df_d["집행관리 항목"] = df_d["관리항목키"].apply(
            lambda value: display_category(value) if value else "관리항목 해당 없음"
        )
        for field in ("예산세목 상태", "인식 세목코드", "인식 근거"):
            df_d[field] = [account_display(row)[field] for row in daily_data]
        quality = get_daily_quality_snapshot(daily_data)

        st.html(f"""
            <div class="ops-kpi-grid">
                <div class="ops-kpi ops-kpi-accent"><div class="ops-kpi-label">원천내역 지출액</div><div class="ops-kpi-value">{int(df_d["집행금액"].sum()):,}원</div><div class="ops-kpi-foot">저장된 전체 행 기준</div></div>
                <div class="ops-kpi"><div class="ops-kpi-label">원천내역 건수</div><div class="ops-kpi-value">{len(df_d):,}건</div><div class="ops-kpi-foot">일반·특수·수동 조정 포함</div></div>
                <div class="ops-kpi"><div class="ops-kpi-label">월별 집계 분류</div><div class="ops-kpi-value">{quality["mapped"]:,}건</div><div class="ops-kpi-foot">집행관리 항목 매핑 완료</div></div>
                <div class="ops-kpi"><div class="ops-kpi-label">예산과목 확인 필요</div><div class="ops-kpi-value">{quality["unmapped"]:,}건</div><div class="ops-kpi-foot">일자 확인 {quality["invalid_date"]:,}건</div></div>
            </div>
            """)

        render_quality_panel(daily_data, get_row_management_category, PAGE_IMPORT, prefix="import_quality")

        with st.expander("세탁용역 집계 확인 · 반영·제외 사유", expanded=False):
            laundry_state, _ = BudgetStore().load(2026)
            laundry_rows = laundry_review_rows(
                daily_data,
                get_row_management_category,
                laundry_state.get("accruals", {}), laundry_state.get("accrual_revisions", {}),
            )
            if laundry_rows:
                included = [r for r in laundry_rows if r["판정"] == "집계 반영"]
                left, middle, right = st.columns(3)
                left.metric("원천에서 확인된 세탁용역", f"{sum(r['지출액'] for r in included):,}원", f"{len(included)}건", delta_color="off")
                middle.metric("제외 거래", f"{sum(r['판정'].startswith('집계 제외') for r in laundry_rows)}건")
                right.metric("확인할 거래", f"{sum(r['판정'].startswith('확인 필요') for r in laundry_rows)}건")
                st.dataframe(pd.DataFrame(laundry_rows), hide_index=True, width="stretch")
            else:
                st.info("저장된 원천내역에서 세탁 관련 거래를 찾지 못했습니다. 지급명령 엑셀을 위 정기지출·세탁용역 탭에 올려 주세요.")
            st.caption("원본 제목이 ‘2026년 ○월 정약용 펀그라운드 세탁용역’인 지출만 반영합니다. 세탁기 수리·기름걸레·제목 불일치·원천 없는 고정 보정액은 제외합니다.")
            if st.button("저장된 원천내역으로 월별 집계 갱신", key="laundry_recalculate_v451"):
                sync_daily_to_master_auto()
                if not st.session_state.get("daily_sync_save_failed"):
                    st.session_state["import_flash"] = "저장된 원천내역의 최신 분류로 월별 집계를 다시 계산했습니다."
                    st.rerun()

        if st.session_state.pop("reset_source_controls", False):
            st.session_state["f_management_v39"] = "전체"
            st.session_state["f1_v292"] = "전체"
            st.session_state["f2_v292"] = ""
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
        source_filter = st.session_state.get("source_filter")
        if source_filter:
            st.info(f"원천내역 확인: {source_filter['label']}")
            if st.button("원천내역 전체로 돌아가기", key="clear_source_filter_v452"):
                st.session_state.pop("source_filter", None)
                st.rerun()
            disp = disp[disp.index.isin(source_filter["indexes"])]
        if management_filter != "전체":
            disp = disp[disp["집행관리 항목"] == management_filter]
        if semok_filter != "전체":
            disp = disp[disp["세목"] == semok_filter]
        if search_text:
            disp = disp[disp["적요"].astype(str).str.contains(search_text, na=False)]

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            export_df = disp[
                ["집행일자", "세목", "예산과목", "집행관리 항목", "적요", "집행금액", "예산세목 상태", "인식 세목코드", "인식 근거"]
            ].copy()
            export_df.columns = [
                "집행일자",
                "예산 세목(원본)",
                "예산과목(원본)",
                "집행관리 항목",
                "적요",
                "지출액", "예산세목 상태", "인식 세목코드", "인식 근거",
            ]
            export_df.to_excel(writer, index=False, sheet_name="일상경비원천내역")
        with export_col:
            st.html("<div style='margin-top:29px;'></div>")
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
            "예산세목 상태", "인식 세목코드", "인식 근거",
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
                "예산세목 상태": st.column_config.TextColumn("예산세목 상태", disabled=True),
                "인식 세목코드": st.column_config.TextColumn("인식 세목코드", disabled=True),
                "인식 근거": st.column_config.TextColumn("인식 근거", disabled=True),
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
                        st.session_state.pop("source_filter", None)
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
            st.caption("전체 삭제 후 월별 집계도 다시 계산됩니다. 세탁용역은 원천거래 없이 보정액을 생성하지 않습니다. 기존 수탁자산취득비 확인자료는 유지됩니다.")
            if st.button(
                "전체 원천내역 삭제 실행",
                key="btn_del_all_v292",
                disabled=not confirm_reset,
                type="primary",
            ):
                if save_daily_expenses([]):
                    st.session_state["daily_expenses"] = []
                    st.session_state.pop("source_filter", None)
                    sync_daily_to_master_auto()
                    st.session_state["import_flash"] = "일상경비 원천내역을 전체 삭제하고 월별 집계를 갱신했습니다."
                    st.rerun()
    else:
        st.info("저장된 일상경비 원천내역이 없습니다. 위에서 파일을 선택하고 검증 결과를 반영해 주세요.")

# --- 신속집행 관리: 공통 원장·기간 계산 ---
if current_page == PAGE_QUICK:
    st.subheader("2026 신속집행·하반기 집행 관리")
    cfg = ensure_report_config(st.session_state.get("report_config", {}))
    index = current_execution_index()
    plans = ensure_rapid_df_integrity(st.session_state["rapid_df"])
    render_period_dashboard(df_all, plans, index, cfg, get_quick_target_map(plans), QUICK_EXEC_GOAL_RATES,
                            prefix="quick", save_config=save_execution_settings)
    render_asset_reconciliation(cfg, index, raw_management_evidence(), prefix="quick_asset", save_config=save_execution_settings)

    st.subheader("대상액·1~12월 집행계획")
    targets = get_quick_target_map(plans)
    target_editor = st.data_editor(pd.DataFrame([{"세목": cat, "연간 대상액": targets[cat]} for cat in CORE_TARGETS]),
        hide_index=True, disabled=["세목"], width="stretch", key="rapid_targets_v456",
        column_config={"연간 대상액": st.column_config.NumberColumn(min_value=0, format="%,d원", required=True)})
    plan_view = plans[["세목", "월", "집행예정액"]].copy()
    plan_view["월별 지출액"] = [float(df_all[(df_all["year"] == 2026) & (df_all["category"] == row["세목"])
        & (df_all["month"] == int(str(row["월"]).replace("월", "")))]["amount"].sum()) for row in plan_view.to_dict("records")]
    edited_plans = st.data_editor(plan_view, hide_index=True, disabled=["세목", "월", "월별 지출액"],
        width="stretch", height=450, key="rapid_editor_v292",
        column_config={"집행예정액": st.column_config.NumberColumn("월별 집행계획액", format="%,d원", min_value=0),
                       "월별 지출액": st.column_config.NumberColumn(format="%,d원")})
    if st.button("대상액·집행계획액 저장", type="primary", key="save_rapid_btn_v292"):
        try:
            target_values = {row["세목"]: won(row["연간 대상액"]) for row in target_editor.to_dict("records")}
            saved_rows = []
            for row in edited_plans.to_dict("records"):
                plan = won(row["집행예정액"])
                if plan < 0 or target_values[row["세목"]] < 0:
                    raise ValidationError("대상액과 계획액은 0원 이상이어야 합니다.")
                saved_rows.append({"세목": row["세목"], "월": row["월"], "집행예정액": plan,
                                   "대상액": target_values[row["세목"]] if row["월"] == "1월" else 0,
                                   "실제집행액": row["월별 지출액"]})
            updated = ensure_rapid_df_integrity(pd.DataFrame(saved_rows))
            if save_rapid_df(updated):
                st.session_state["rapid_df"] = updated
                st.toast("대상액과 1~12월 계획액을 저장했습니다.")
                st.rerun()
            else:
                st.error("저장하지 못했습니다.")
        except (ValidationError, ValueError, TypeError) as exc:
            st.error(str(exc))
    with st.expander("기존 자산 구매계획 참고"):
        st.dataframe(pd.DataFrame(ASSET_ACQUISITION_PLAN_ROWS), hide_index=True, width="stretch")
        st.caption("구매계획은 실제 지출에 더하지 않습니다. 세부 지출내역은 위 원장 대조에서 작성하세요.")
