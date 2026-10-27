"""Shared period controls and asset reconciliation in management and reports."""
import copy
from datetime import datetime

import pandas as pd
import streamlit as st

from budget_engine import ValidationError
from execution_sync import (ASSET_CODE, ASSET_CATEGORY, PERIODS, asset_review,
                            quick_period_rows, reference_period, save_asset_review,
                            token, validate_detail_rows)


def money(value):
    return "자료 없음" if value is None else f"{int(value):,}원"


def render_period_dashboard(frame, plan_frame, index, config, targets, rates, *, prefix,
                            save_config, default_month=None, categories=None, simple=False):
    default_month = int(default_month or (index["as_of"][5:7] if index["as_of"] else datetime.now().month))
    a, b = st.columns([3, 1])
    choices = list(PERIODS)
    default_period = "연간 누계"
    period = a.selectbox("집행실적 조회기간", choices, index=choices.index(default_period), key=prefix+"_period")
    cutoff = b.selectbox("실적 기준월", list(range(1, 13)), index=max(1, min(default_month, 12))-1,
                         format_func=lambda m: f"{m}월", key=prefix+"_cutoff")
    start, end = PERIODS[period]
    end = min(cutoff, end)
    if start > end:
        st.info("선택한 기간이 실적 기준월 이후입니다. 기준월을 변경하세요.")
        return []
    st.caption(f"{index['year']}년 {start}~{end}월 지출액을 비교합니다.")
    rows = quick_period_rows(frame, plan_frame, index, period, cutoff, targets)
    if categories:
        rows = [r for r in rows if r["집행관리 항목"] in categories]
    target = sum(r["대상액"] for r in rows)
    actual = sum(r["인정 지출액"] for r in rows)
    provisional = any(not r["원장확인"] for r in rows)
    cols = st.columns(3)
    cols[0].metric("대상액", money(target))
    cols[1].metric("인정 지출액" + (" · 확인 필요" if provisional else ""), money(actual))
    cols[2].metric("대상액 대비 집행률", f"{actual / target * 100:.2f}%" if target else "산정 불가")
    if provisional:
        pending = ", ".join(r["집행관리 항목"] for r in rows if not r["원장확인"])
        st.warning(f"{pending}: 선택 기간을 확정할 원장이 부족하여 관리내역 잠정값을 표시합니다. "
                   "보고서의 자료 등록에서 단월·누적 원장을 추가하세요.")
    discrepancies = [r for r in rows if r["월별합계 차이"] not in (None, 0)]
    if discrepancies:
        st.warning("누계 원장과 월별 원장의 합계가 다릅니다. 기간 지출액은 누계 원장을 따르며 차액은 등록자료 확인이 필요합니다.")
    display = pd.DataFrame(rows).drop(columns=["세목코드", "원장확인", "기간시작월", "기간종료월"])
    if simple:display=display[['집행관리 항목','대상액','지출액','인정 지출액']]
    st.dataframe(display, hide_index=True, width="stretch",
                 column_config={c: st.column_config.NumberColumn(format="%,d원") for c in
                                ("대상액", "기간 계획액", "지출액", "인정 지출액", "월별합계 차이")})
    st.caption("인정 지출액은 항목별 대상액까지 계산합니다. 계약·원인행위액은 지출액에 포함하지 않습니다.")
    saved_goals = config.get("quick_goals", {}).get(period)
    period_key = {"1분기": "q1", "상반기": "h1"}.get(period)
    defaults = {org: spec[period_key]*100 if period_key else None for org, spec in rates.items()}
    goals = saved_goals if saved_goals is not None else defaults
    goal_rows = []
    for organization, goal in goals.items():
        amount = target * float(goal)/100 if goal is not None else None
        goal_rows.append({"기관": organization, "기간": period, "목표율(%)": goal,
                          "목표액": amount, "인정 지출액": actual,
                          "차이": actual-amount if amount is not None else None,
                          "판정": "목표율 미등록" if amount is None else
                          ("잠정 " if provisional else "") + ("달성" if actual >= amount else "미달")})
    st.markdown("**등록 목표율과 비교**")
    st.dataframe(pd.DataFrame(goal_rows), hide_index=True, width="stretch",
                 column_config={c: st.column_config.NumberColumn(format="%,d원") for c in ("목표액", "인정 지출액", "차이")})
    if simple:return rows
    with st.expander(f"{period} 목표율 설정"):
        st.caption("기존 1분기·상반기 목표율은 이전 저장 기준입니다. 공문으로 확인한 해당 기간 목표율을 입력하세요. 미등록 기간에는 목표 달성을 판정하지 않습니다.")
        edited = {}
        for org in rates:
            current = goals.get(org)
            enabled = st.checkbox(f"{org} {period} 목표율 등록", value=current is not None, key=prefix+"_goal_enabled_"+org+period)
            value = st.number_input(f"{org} 목표율(%)", min_value=0.0, max_value=100.0,
                                    value=float(current or 0), step=0.1, disabled=not enabled,
                                    key=prefix+"_goal_"+org+period)
            edited[org] = value if enabled else None
        if st.button("이 기간 목표율 저장", key=prefix+"_save_goals"):
            updated = copy.deepcopy(config)
            updated.setdefault("quick_goals", {})[period] = edited
            if save_config(updated):
                st.rerun()
    return rows


def render_asset_reconciliation(config, index, raw_contributions, *, prefix, save_config):
    reviews = [asset_review(config, index, raw_contributions, m) for m in range(1, 13)]
    needs = [r for r in reviews if r["needs_review"]]
    with st.container(border=True):
        st.markdown(f"#### 수탁자산취득비 원장 대조 · 확인 필요 {len(needs)}개월")
        st.caption("보고서에 반영한 단월 원장을 우선 사용합니다. 단월 원장이 없으면 전월 말과 해당 월 누계 원장을 차감합니다. 근거가 없는 월은 금액을 추정하지 않습니다.")
        if needs:
            st.warning("원장 기준금액과 세부내역이 다르거나 재확인이 필요합니다: " + ", ".join(f"{r['month']}월" for r in needs))
        table = []
        for review in reviews:
            ref = review["reference"]
            table.append({"월": f"{review['month']}월", "원장 기준금액": ref["amount"] if ref else None,
                          "업로드 세부내역": review["raw_amount"], "작성한 세부내역": review["detail_amount"],
                          "확인할 차액": review["gap"],
                          "상태": "근거 원장 필요" if not ref else "대조 확정" if review["confirmed"] else
                          "재확인 필요" if review["stale"] else "차액 확인 필요" if review["gap"] else "금액 일치",
                          "기준일": ref["as_of"] if ref else "", "근거": ref["file"] if ref else ""})
        st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="%,d원") for c in
                                    ("원장 기준금액", "업로드 세부내역", "작성한 세부내역", "확인할 차액")})
        latest = max(index["cumulative"], default=0)
        if latest:
            cumulative = reference_period(index, ASSET_CODE, 1, latest)
            if cumulative:
                explained = sum(r["reference"]["amount"] if r["reference"] else r["raw_amount"] for r in reviews[:latest])
                a, b, c = st.columns(3)
                a.metric(f"1~{latest}월 원장 누계", money(cumulative["amount"]))
                b.metric("월별로 배분된 금액", money(explained))
                c.metric("월 구분 확인 필요", money(cumulative["amount"]-explained))
                if cumulative["amount"] != explained:
                    st.warning("누계와 월별 합계 차액은 임의로 특정 월에 넣지 않았습니다. 과거 단월 원장이나 연속 월말 누계 원장을 추가해 월을 확인하세요.")
        default = needs[0]["month"] if needs else max((r["month"] for r in reviews if r["reference"]), default=1)
        month = st.selectbox("세부내역 확인·수정할 월", list(range(1, 13)), index=default-1,
                             format_func=lambda m: f"{m}월", key=prefix+"_month")
        review = reviews[month-1]
        reference = review["reference"]
        st.markdown(f"**{month}월 기준금액: {money(reference['amount'] if reference else None)}**")
        st.caption("지급일·세부내역·지출액을 추가·수정·삭제할 수 있습니다. 정확한 지급일을 모르면 비워 두세요. 원장과 금액이 다르면 임시 저장만 가능하며, 현황 금액은 원장 기준을 유지합니다.")
        if review["stale"]:
            st.warning("원장이 바뀌어 이전 확인이 해제되었습니다. 새 기준금액으로 내역을 다시 확인하세요.")
        conflicts = [c for c in index["conflicts"] if c["code"] == ASSET_CODE and c["month"] == month]
        if conflicts:
            st.warning("이 기간의 원장 간 금액이 다릅니다. 위에 표시한 보고서 우선 기준과 다른 원장을 확인하세요.")
            st.dataframe(pd.DataFrame([{"종류": c["kind"], "기준파일": c["chosen"]["file"], "기준금액": c["chosen"]["amount"],
                                       "대조파일": alt["file"], "대조금액": alt["amount"]}
                                      for c in conflicts for alt in c["alternatives"]]), hide_index=True)
        identity = token([reference["token"] if reference else "missing", review["saved"]])
        editor_key = f"{prefix}_details_{month}_{identity}"
        frame = pd.DataFrame(review["rows"], columns=["지급일", "세부내역", "지출액", "근거"])
        edited = st.data_editor(frame, num_rows="dynamic", hide_index=True, width="stretch", key=editor_key,
                                column_config={"지급일": st.column_config.TextColumn(help="YYYY-MM-DD, 모르면 공란"),
                                               "세부내역": st.column_config.TextColumn(required=True),
                                               "지출액": st.column_config.NumberColumn(format="%,d원", required=True, step=1)})
        valid, cleaned = True, []
        try:
            cleaned = validate_detail_rows(edited.astype(object).where(pd.notna(edited), None).to_dict("records"), index["year"], month)
        except (ValidationError, ValueError, TypeError) as exc:
            valid = False
            st.error(str(exc))
        total = sum(r["지출액"] for r in cleaned)
        a, b = st.columns(2)
        a.metric("수정내역 합계", money(total))
        b.metric("기준금액과 차액", money(reference["amount"]-total if reference else None))
        note = st.text_input("확인·수정 메모", value=review["saved"].get("note", "") if review["saved"] else "", key=prefix+"_note_"+str(month)+identity)
        matched = valid and reference is not None and total == reference["amount"]
        acknowledged = st.checkbox("원장 기준금액과 세부내역을 대조했습니다" + (" · 원장 간 차이도 확인했습니다" if conflicts else ""),
                                    key=prefix+"_ack_"+str(month)+identity, disabled=not matched)
        left, right = st.columns(2)
        if left.button("작성내역 임시 저장", key=prefix+"_save_draft", disabled=not valid, width="stretch"):
            if save_config(save_asset_review(config, index, month, cleaned, note=note)):
                st.rerun()
        if right.button("원장과 일치하는 내역 확정", type="primary", key=prefix+"_confirm",
                        disabled=not matched or not acknowledged, width="stretch"):
            if save_config(save_asset_review(config, index, month, cleaned, confirmed=True, note=note)):
                st.rerun()
        if not reference:
            st.info("이 월의 기준 원장이 없어 확정할 수 없습니다. 예산집행 보고서 → 자료 등록에서 단월·누적 원장을 반영하세요.")
        elif not matched:
            st.info("차액을 확인하고 내역을 수정하면 확정 버튼이 활성화됩니다. 기준금액 자체를 고치려면 ERP 원장을 수정해 다시 반영하세요.")
        with st.expander("업로드된 원래 세부내역 확인"):
            st.dataframe(pd.DataFrame(review["raw_rows"], columns=["지급일", "세부내역", "지출액", "근거"]), hide_index=True, width="stretch")
