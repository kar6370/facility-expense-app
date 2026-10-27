"""Source preview and explicit column selection for ERP import review."""
import pandas as pd
import streamlit as st

from transaction_import import analyze_frame, cell, excel_column, inspect_layout


FIELD_LABELS = {"집행일자": "지급일자", "지급월": "지급월", "적요": "적요·문서제목",
                "집행금액": "지출액·지급금액", "세목": "예산 세목(원본)", "예산과목": "예산과목(원본)"}


def review_source_layout(frame, kind, *, year=2026, sheet="", filename="", fingerprint="", mapper=None):
    context = dict(year=year, sheet=sheet, filename=filename, fingerprint=fingerprint, mapper=mapper)
    analysis = analyze_frame(frame, kind, **context)
    prefix = "import_columns_" + kind + fingerprint + sheet
    manual = st.checkbox("원본 열 직접 지정", value=bool(analysis["missing"]), key=prefix+"_manual")
    if manual:
        st.caption("아래 원본을 보고 제목 행과 지급일(또는 지급월)·적요·지출액 열을 선택하세요. 선택 결과는 저장 전 미리보기에만 적용됩니다.")
        preview = frame.head(15).copy()
        preview.columns = [excel_column(i) for i in range(len(preview.columns))]
        preview.insert(0, "원본 행", [int(i)+1 for i in preview.index])
        st.dataframe(preview.astype(str), hide_index=True, width="stretch", height=260)
        heading = st.number_input("열 제목이 있는 행 (없으면 0)", min_value=0, max_value=len(frame),
                                  value=max(0, analysis["header_row"]+1), step=1, key=prefix+"_header")
        if heading:
            _, defaults = inspect_layout(frame.iloc[heading-1:], kind)
            labels = list(frame.iloc[heading-1])
        else:
            defaults, labels = analysis["columns"], []
        mapping = {}
        grid = st.columns(3)
        for i, (field, label) in enumerate(FIELD_LABELS.items()):
            options = [-1] + list(range(len(frame.columns)))
            default = defaults.get(field, -1)
            picked = grid[i % 3].selectbox(
                label, options, index=options.index(default) if default in options else 0,
                format_func=lambda col: "지정 안 함" if col < 0 else f"{excel_column(col)}열 · {cell(labels[col]) if col < len(labels) else ''}",
                key=prefix+"_"+field,
            )
            mapping[field] = None if picked < 0 else picked
        # Preserve automatically recognized identifiers and vendor fields.
        columns = {**defaults, **mapping}
        chosen = [v for k, v in mapping.items() if v is not None]
        if len(chosen) != len(set(chosen)):
            st.error("같은 열을 두 항목에 중복 지정했습니다. 각 원본 열의 의미를 확인해 주세요.")
            return {**analysis, "rows": [], "missing": ["중복 지정된 열"]}
        analysis = analyze_frame(frame, kind, columns=columns, header_row=int(heading)-1, **context)
    if kind == "special" and "지급월" in analysis["columns"] and "집행일자" not in analysis["columns"]:
        selected_year = st.number_input("월만 있는 자료의 회계연도", min_value=2000, max_value=2100,
                                       value=int(year), step=1, key=prefix+"_year")
        if int(selected_year) != int(year):
            analysis = analyze_frame(frame, kind, columns=analysis["columns"],
                                     header_row=analysis["header_row"], **{**context, "year":int(selected_year)})
    if analysis["missing"]:
        st.error("확인이 필요한 열: " + ", ".join(FIELD_LABELS.get(k, k) for k in analysis["missing"]) + ". 원본 열 직접 지정에서 선택해 주세요.")
        return analysis
    mapping_text = " · ".join(f"{FIELD_LABELS[k]}={excel_column(v)}열" for k, v in analysis["columns"].items() if k in FIELD_LABELS)
    st.caption("인식 결과: " + mapping_text)
    if analysis["excluded"]:
        st.info(f"정기지출·세탁용역 대상 {len(analysis['rows']):,}건, 대상 외 {len(analysis['excluded']):,}건입니다. 전체 거래를 가져오려면 일반 일상경비 탭을 사용하세요.")
        with st.expander("대상 외 거래와 제외 사유 확인"):
            st.dataframe(pd.DataFrame(analysis["excluded"]), hide_index=True, width="stretch")
    if not analysis["rows"] and not analysis["excluded"]:
        st.warning("열은 인식했지만 반영할 거래가 없습니다. 원본 시트와 제목 행을 확인해 주세요.")
    return analysis
