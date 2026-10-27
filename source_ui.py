"""Native source dialogs and precise monthly cell selection."""
import pandas as pd
import streamlit as st

from budget_engine import transaction_id
from source_review import quality_details, quality_snapshot, monthly_source_rows


def focus_source(indexes, label, import_page):
    st.session_state["source_filter"] = {"indexes":indexes, "label":label}
    st.session_state["reset_source_controls"] = True
    st.session_state["current_page"] = import_page
    st.session_state["_source_navigation_requested"] = True


@st.dialog("지출내역 확인", width="large")
def show_quality_dialog(rows, mapper, view, import_page):
    details = quality_details(rows, mapper)
    labels = {"accounts":"예산과목 확인 필요", "issues":"일자·금액 확인 필요",
              "duplicates":"저장내역 중복 후보", "all":"전체 분류 현황"}
    if view == "accounts":
        details = [r for r in details if r["확인 사유"] == "예산과목 확인 필요"]
    elif view == "issues":
        details = [r for r in details if r["확인 사유"] in ("지급일 확인 필요", "금액 확인 필요")]
    elif view == "duplicates":
        details = [r for r in details if r["중복후보 그룹"]]
    st.markdown(f"**{labels[view]} · {len(details):,}건**")
    if view == "duplicates":
        st.caption("날짜·내용·금액이 같은 내역입니다. 같은 지출이 두 번 등록됐는지 확인하세요.")
    if details:
        frame = pd.DataFrame(details)
        st.dataframe(frame, hide_index=True, width="stretch", height=420)
        st.download_button("이 확인내역 내려받기", frame.to_csv(index=False).encode("utf-8-sig"),
                           file_name="지출내역_확인.csv", mime="text/csv", key="quality_download")
        st.button("이 거래를 자료연계에서 보기", key="quality_to_import", type="primary",
                  on_click=focus_source, args=([r["원천번호"] for r in details], labels[view], import_page))
        if st.session_state.pop("_source_navigation_requested", False):
            st.rerun(scope="app")
    else:
        st.info("해당 조건에 해당하는 거래가 없습니다.")


def render_quality_panel(rows, mapper, import_page, prefix="quality"):
    stats = quality_snapshot(rows, mapper)
    with st.container(border=True):
        st.markdown("#### 등록자료 확인")
        st.caption(f"예산과목이 확인된 내역 {stats['official_classified']:,}건")
        if stats["account_only"]:
            st.caption(f"인건비 등 관리항목 외 지출 {stats['account_only']:,}건도 원본 예산과목으로 조회됩니다.")
        items = [("accounts", f"예산과목 확인 필요 {stats['unmapped']:,}건"),
                 ("issues", f"일자·금액 확인 {stats['invalid_date']+stats['invalid_amount']:,}건"),
                 ("duplicates", f"중복 후보 {stats['duplicate_groups']:,}그룹 · {stats['duplicates']:,}건"),
                 ("all", "전체 분류 현황 보기")]
        for view, label in items:
            if st.button(label, key=prefix+"_"+view, width="stretch"):
                show_quality_dialog(rows, mapper, view, import_page)


@st.dialog("월별 지출액의 지출내역", width="large")
def show_monthly_source_dialog(contributions, rows, year, category, month, displayed, category_label, import_page):
    details = monthly_source_rows(contributions, year, category, month)
    explained = sum(r["이번 셀 반영액"] for r in details)
    gap = int(displayed) - explained
    st.markdown(f"**{year}년 {month}월 · {category_label}**")
    a, b, c = st.columns(3)
    a.metric("표에 표시된 금액", f"{int(displayed):,}원")
    b.metric("지출내역 반영액 합계", f"{explained:,}원")
    c.metric("확인할 차이", f"{gap:+,}원")
    if gap:
        st.warning("기존 저장값과 확인 가능한 지출내역 사이에 차이가 있습니다. 자료연계에서 원본을 확인하고 다시 집계하세요.")
    if any(row.get("자료구분") == "원장 대조 차액" for row in details):
        st.warning("원장과 맞춘 차액이 포함돼 있습니다. 이 차액은 개별 구매거래가 아닙니다. 신속집행 관리의 수탁자산취득비 원장 대조에서 세부내역을 확인·수정하세요.")
    if details:
        frame = pd.DataFrame(details)
        if '집계 근거' in frame:
            frame['집계 근거']=frame['집계 근거'].replace({'사용자 확정 귀속월':'사용자가 지정한 월','적요의 귀속월':'지출내역에 기재된 월'})
        primary = ["자료구분", "지급일", "적요", "이번 셀 반영액", "원본 파일", "원본 행"]
        st.dataframe(frame[primary].rename(columns={"이번 셀 반영액":"집계금액"}), hide_index=True, width="stretch", height=320)
        with st.expander("날짜·예산과목 등 자세히 보기"):
            st.dataframe(frame.drop(columns=["원천거래키"]).rename(columns={"이번 셀 반영액":"집계금액","원천 지출액":"지출금액"}), hide_index=True, width="stretch")
        st.download_button("이 금액의 지출내역 내려받기", frame.drop(columns=["원천거래키"]).to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"{year}_{month:02d}_지출근거.csv", mime="text/csv", key="cell_source_download")
        keys = {r["원천거래키"] for r in details}
        indexes = []
        for index, row in enumerate(rows):
            try:
                if transaction_id(row) in keys:
                    indexes.append(index)
            except ValueError:
                pass
        if indexes:
            st.button("해당 거래를 자료연계에서 보기", key="cell_source_to_import", type="primary",
                      on_click=focus_source, args=(indexes, f"{year}년 {month}월 {category_label}", import_page))
            if st.session_state.pop("_source_navigation_requested", False):
                st.rerun(scope="app")
    else:
        st.info("해당 월의 세부 지출내역이 없습니다. 지출자료를 등록해 주세요.")


def render_monthly_source_grid(frame, categories, contributions, rows, display_category, import_page):
    config = {col:st.column_config.NumberColumn(col, format="localized") for col in frame.columns}
    event = st.dataframe(frame, key="monthly_source_grid_v452", height=550, width="stretch",
                         on_select="rerun", selection_mode="single-cell", column_config=config)
    cells = event.get("selection", {}).get("cells", [])
    if not cells:
        return
    index, column = cells[0]
    if not 0 <= index < len(categories) or column not in frame.columns:
        return
    month = int(str(column).replace("월", ""))
    category = categories[index]
    token = (category, month)
    requested = st.button(f"선택한 금액 근거 다시 보기 · {display_category(category)} {month}월", key="monthly_reopen_source")
    if requested or st.session_state.get("last_source_cell") != token:
        st.session_state["last_source_cell"] = token
        show_monthly_source_dialog(contributions, rows, 2026, category, month,
                                   frame.iloc[index][column], display_category(category), import_page)
