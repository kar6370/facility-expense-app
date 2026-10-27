"""Native Streamlit workspace for the verified V44 reconciliation engine."""
import copy
import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
from owner_access import require_owner

from budget_engine import (
    BudgetStore, ValidationError, ConflictError, SCOPES, STAGES, MODES,
    apply_source, build_model, classify, empty_state, inspect_workbook,
    parse_budget, parse_ledger, reconciliation, recommendations, won, migrate_legacy_classifications, expense_code, prepare_amendment,
)


def money(value):
    return "자료 없음" if value is None or pd.isna(value) else f"{int(value):,}원"


def rate(value, total):
    return "—" if value is None or not total else f"{value/total*100:.1f}%"


@st.cache_data(show_spinner=False, max_entries=16)
def preview(raw, kind, sheet, column, scale, mode):
    return parse_ledger(raw, sheet, scale) if kind == "ledger" else parse_budget(raw, sheet, column, scale, mode)


def commit(store, state, revision, action, source=None):
    require_owner()
    try:
        store.save(state, revision, action, source)
    except (ValidationError, ConflictError, OSError) as exc:
        st.error(str(exc))
        return
    st.toast("저장했습니다.")
    # History/backup restores can also change attribution months.
    st.session_state.pop("initial_sync_done", None)
    # A save may start during a full app run (including form submissions).
    # Full reruns are valid in both contexts and refresh dependent reports.
    st.rerun()


def table(rows, monetary=(), **kwargs):
    frame = pd.DataFrame(rows)
    st.dataframe(frame, hide_index=True, width="stretch",
                 column_config={c:st.column_config.NumberColumn(format="%,d원") for c in monetary if c in frame}, **kwargs)


def upload_panel(store, state, revision):
    st.subheader("원천자료 반영")
    st.caption("파일·시트·금액 단위·자료 성격을 확인한 다음 반영합니다. 각 차수와 통제원장은 독립적으로 저장합니다.")
    with st.expander("ERP에서 어떤 파일을 내려받나요? · 본예산·1회추경·2회추경·통제원장", expanded=True):
        st.markdown("ERP 메뉴 검색에서 아래 **서식명**을 찾은 뒤 Excel로 내려받으세요. 기관별 상위 메뉴 이름은 다를 수 있습니다.")
        table([
            {"이 앱의 업로드칸": "본예산", "ERP 서식명": "세출예산사업명세서", "조회 조건": f"{state['year']}년 · 본예산 · 정약용 펀그라운드 · 운영경비와 일상경비 전체", "포함할 내용": "목·세목·마지막 부기와 산출기초, 당해연도 예산액"},
            {"이 앱의 업로드칸": "제1회 추경", "ERP 서식명": "세출예산사업명세서", "조회 조건": f"{state['year']}년 · 제1회 추가경정예산 · 같은 부서·사업 범위", "포함할 내용": "세목의 경정 총액·직전 예산·비교증감, 변경 부기의 증감액·산출기초"},
            {"이 앱의 업로드칸": "제2회 추경", "ERP 서식명": "세출예산사업명세서", "조회 조건": f"{state['year']}년 · 제2회 추가경정예산 · 같은 부서·사업 범위", "포함할 내용": "세목의 경정 총액·직전 예산·비교증감, 변경 부기의 증감액·산출기초"},
            {"이 앱의 업로드칸": "지출통제원장", "ERP 서식명": "지출예산통제원장 집계표 / 지출통제원장집계표", "조회 조건": f"{state['year']}-01-01부터 확인할 종료일까지 누계 · 동일 사업 범위", "포함할 내용": "예산현액(계)·예산배정액(B)·지출액(E), 운영·일상경비와 합계"},
        ])
        st.caption("실제 사용한 파일 예: 세출예산사업명세서_20260902143923.xlsx / 지출통제원장집계표_20260902144023(1).xlsx")
        st.markdown("본예산 → 제1회 추경 → 제2회 추경 순서로 반영하세요. 첨부 ERP 추경처럼 **세목 행은 경정 총액, 아래 부기는 증감액**인 파일은 **자동 인식**을 선택합니다. 같은 부기의 감액·증액 행을 합산하고, 나오지 않은 부기는 유지합니다. ‘전년도예산액’이라는 열 제목이 있어도 실제 직전 차수와 금액을 대조합니다. 일반 본예산의 전년도 대비 비교증감은 추경 증감액이 아닙니다.")
        st.caption("예산명세서는 보통 천원, 통제원장은 원 단위입니다. 반드시 파일에 표시된 단위를 확인하세요. 이 화면의 원장은 1월 1일부터 누계이며, 보고서의 자료 등록에서는 단월·누적 두 파일을 함께 받습니다.")
    labels = STAGES + ["지출통제원장"]
    selected = st.segmented_control("자료 종류", labels, default=labels[0], key="v44_source_kind")
    selected = selected or labels[0]
    kind = "ledger" if selected == "지출통제원장" else "budget"
    # Each stage has a persistent independent uploader key.
    uploaded = st.file_uploader(f"{selected} 업로드", type=["xlsx"], key=f"v44_upload_{state['year']}_{selected}")
    current = state.get("ledger") if kind == "ledger" else state["stages"].get(selected)
    if current:
        st.caption(f"현재 등록: {current.get('filename')} · {current.get('imported_at')} · {current.get('sheet')}")
        original = store.original(current["fingerprint"])
        if original:
            st.download_button("현재 등록 원본 받기", original[1], original[0], key="v44_original")
    if uploaded is None:
        return
    raw = uploaded.getvalue()
    try:
        options = inspect_workbook(raw, kind)
    except ValidationError as exc:
        st.error(str(exc)); return
    sheet = st.selectbox("읽을 시트", [x["sheet"] for x in options], key=f"v44_sheet_{selected}")
    info = next(x for x in options if x["sheet"] == sheet)
    a, b = st.columns(2)
    scale = a.selectbox("금액 단위", [1000, 1], index=1 if kind == "ledger" else 0,
                        format_func=lambda x: "천원 → 원으로 환산" if x == 1000 else "원", key=f"v44_scale_{selected}")
    mode = "auto"
    if kind == "budget" and selected != "본예산":
        mode = b.selectbox("추경자료 성격", list(MODES), format_func=MODES.get, key=f"v44_mode_{selected}")
        st.caption({"auto": "세목 총액과 부기 증감액이 섞인 ERP 양식을 인식하고 직전 차수와 대조합니다.",
                    "full": "파일에 없는 종전 부기는 0원으로 처리합니다. 전체명세에만 사용하세요.",
                    "partial": "파일에 나온 부기만 경정액으로 교체하고 나머지는 유지합니다. 삭제 항목은 0원 행이 필요합니다.",
                    "delta": "선택한 증감액을 종전 예산에 더합니다. 전년도 대비 비교증감은 사용할 수 없습니다."}[mode])
    column = None
    if kind == "budget":
        columns = info["columns"]
        preferred = next((k for k, v in columns.items() if v == "예산액"), next(iter(columns)))
        column = st.selectbox("반영할 금액 열", list(columns), index=list(columns).index(preferred),
                              format_func=lambda x: f"{x+1}열 · {columns[x]}", key=f"v44_col_{selected}")
    end = None
    history_only = False
    if kind == "ledger":
        end = st.date_input("누계 조회종료일", value=date(state["year"], 12, 31) if state["year"] != date.today().year else date.today(),
                            min_value=date(state["year"], 1, 1), max_value=date(state["year"], 12, 31), key="v44_asof")
        st.caption("파일 생성일과 자료의 조회종료일은 다를 수 있습니다. ERP에서 조회한 종료일을 입력하세요.")
        history_only = st.checkbox("과거 월말 대조자료로만 추가", key="v45_history_ledger")
        if history_only:
            st.caption("현재 보고서의 최신 원장은 유지합니다. 월별 근거를 계산할 과거 누계만 보관합니다.")
    try:
        doc = preview(raw, kind, sheet, column, scale, mode)
    except (ValidationError, ValueError) as exc:
        st.error(str(exc)); return
    if kind == "budget":
        if doc.get("layout") == "erp_mixed_delta":
            doc = prepare_amendment(state, selected, doc)
            st.info("ERP 추경 혼합 양식: 세목은 추경 후 총액, 부기는 증감액으로 인식했습니다. 같은 부기의 여러 행은 부호를 유지해 합산합니다.")
            st.caption("원본의 ‘전년도예산액’ 열을 직전 차수 등록금액과 직접 대조합니다. 파일에 없는 부기는 그대로 유지합니다.")
            if doc.get("changes"):
                previous_state = copy.deepcopy(state)
                previous_state["stages"] = {s: state["stages"][s] for s in STAGES[:STAGES.index(selected)]}
                previous_leaves = build_model(previous_state)["leaves"]
                mappings = dict(doc["mapping"])
                with st.expander("이름이 바뀐 부기·새 부기의 연결 확인"):
                    st.caption("같은 경비·세목 안에서만 연결할 수 있습니다. 신규 부기는 ‘새 부기로 추가’를 유지하세요. 반환금 명칭 변경은 유일한 기존 반환금 부기를 제안합니다.")
                    for change in doc["changes"]:
                        if change["source_id"] in previous_leaves or not change["delta"]:
                            continue
                        candidates = [change["source_id"]] + [k for k,x in previous_leaves.items() if x["scope"] == change["scope"] and x["code"] == change["code"] and not x["historical"]]
                        mappings[change["source_id"]] = st.selectbox(
                            f"{change['scope']} · {change['code']} · {change['source_label']}", candidates,
                            index=candidates.index(change["target_id"]),
                            format_func=lambda k, source_id=change["source_id"]: "새 부기로 추가" if k == source_id else previous_leaves[k]["label"],
                            key=f"v459_map_{doc['fingerprint']}_{change['source_id']}")
                doc = prepare_amendment(state, selected, doc, mappings)
                st.markdown("**직전 차수 대비 부기별 증감**")
                table([{"경비": c["scope"], "세목": c["code"], "부기": c["label"], "직전 편성액": c["before"],
                        "이번 증감": c["delta"], "반영 후": c["after"], "연결": c["match"],
                        "원본 행": ", ".join(map(str,c["rows"]))} for c in doc["changes"]], ("직전 편성액", "이번 증감", "반영 후"))
                with st.expander("합산 전 원본 부기 행·산출기초"):
                    table([{"경비":x["scope"],"세목":x["code"],"부기":x["label"],"원본 행":c["row"],"증감액":c["amount"],"산출기초":c["basis"]}
                           for x in doc["source_leaves"].values() for c in x.get("components",[])], ("증감액",))
            if doc.get("review_items"):
                st.warning("원본대로 반영하면 아래 부기가 음수입니다. 원본의 부호·산출기초를 확인해야 하며, 잠정 반영해도 음수를 0원으로 바꾸지 않습니다. 해당 부기는 분류 확정 대상에서 제외합니다.")
                table([{"경비":x["scope"],"세목":x["code"],"부기":x["label"],"반영 후":x["amount"]} for x in doc["review_items"]], ("반영 후",))
                doc["review_acknowledged"] = st.checkbox("원본 확인 필요 부기를 확인했으며, 원본 금액 그대로 잠정 반영합니다.", key=f"v459_review_{selected}_{revision}_{doc['fingerprint']}_{json.dumps(doc['mapping'],ensure_ascii=False)}")
        c1, c2, c3 = st.columns(3)
        if doc.get("layout") == "erp_mixed_delta":
            c1.metric("직전 차수 총액", money(doc["previous_amount"]))
            c2.metric("이번 추경 증감", money(doc["total"]))
            c3.metric("추경 후 총액", money(doc["source_total"]))
            st.caption(f"변경 세목 {len(doc['semoks'])}개 · 합산 후 변경 부기 {len(doc['leaves'])}개")
        else:
            c1.metric("인식 부기 합계", money(doc["total"]))
            c2.metric("세목", f"{len(doc['semoks'])}개")
            c3.metric("마지막 부기", f"{len(doc['leaves'])}개")
        with st.expander("인식한 마지막 부기 확인"):
            table([{"구분": x["scope"], "세목": x["code"], "부기": x["label"], "금액": x["amount"], "원본 행": x["row"]} for x in doc["leaves"].values()], ("금액",))
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("ERP 예산현액", money(doc["total"].get("current_budget")))
        c2.metric("지출액(E)", money(doc["total"].get("expense")))
        c3.metric("세목", f"{len(doc['lines'])}개")
    for error in doc["errors"][:12]:
        st.error(error)
    check_label = f"{state['year']}년 {selected}의 시트·단위·합계를 확인했습니다."
    if kind == "ledger":
        check_label = f"{state['year']}년 1월 1일~{end} 누계자료이며 단월 자료가 아님을 확인했습니다."
    mapping_signature = hashlib.sha256(json.dumps(doc.get("mapping", {}), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    checked = st.checkbox(check_label, key=f"v44_checked_{selected}_{doc['fingerprint']}_{sheet}_{column}_{scale}_{mode}_{end}_{revision}_{mapping_signature}")
    if st.button(f"{selected} 검증 후 반영", type="primary", disabled=bool(doc["errors"]) or not checked or bool(doc.get("review_items") and not doc.get("review_acknowledged")), key="v44_source_commit"):
        try:
            new = apply_source(state, selected, doc, uploaded.name, as_of=end, period_start=date(state["year"], 1, 1), history_only=history_only)
        except ValidationError as exc:
            st.error(str(exc)); return
        commit(store, new, revision, f"{selected} 반영 · {uploaded.name}", (uploaded.name, raw))


def overview(state, data):
    model, rows = data["model"], data["rows"]
    if not model["leaves"]:
        st.info("‘원천자료’에서 본예산을 반영하면 예산과목과 마지막 부기가 표시됩니다.")
        return
    scope = st.segmented_control("사업구분", ["전체"]+SCOPES, default="전체", key="v44_scope") or "전체"
    rows = [x for x in rows if scope == "전체" or x["scope"] == scope]
    for col, branch in zip(st.columns(2), SCOPES):
        branch_rows = [r for r in data["rows"] if r["scope"] == branch]
        with col, st.container(border=True):
            st.markdown(f"**{branch}**")
            expense = sum(r["official"] or 0 for r in branch_rows) if state["ledger"] else None
            current = sum(r["current"] or 0 for r in branch_rows) if state["ledger"] else None
            st.metric("지출액 / ERP 예산현액", money(expense), f"예산 {money(current)} · {rate(expense, current)}")
            if current:
                st.progress(min(max(expense/current, 0), 1))
            st.caption(f"연계 미분류 {money(sum(r['unclassified'] for r in branch_rows))} · ERP 미연계 차액 {money(sum(r['unlinked'] or 0 for r in branch_rows))}")
    query = st.text_input("과목명·코드 검색", key="v44_search", placeholder="예: 국내여비, 사무관리비, 201-01")
    if query:
        rows = [r for r in rows if query.lower() in (r["name"]+r["code"]).lower()]
    focus = st.segmented_control("확인 범위", ["전체", "대조 필요", "예산 초과"], default="전체", key="v45_focus") or "전체"
    if focus == "대조 필요":
        rows = [r for r in rows if not r["complete"] or r["gap"]]
    if focus == "예산 초과":
        rows = [r for r in rows if r["official"] is not None and r["official"] > (r["allocated"] or 0)]
    grid = [{"구분":r["scope"], "세목":f"{r['code']} {r['name']}", "최종 편성액":r["budget"],
             "지출액":r["official"], "편성잔액":r["budget"]-r["official"] if r["official"] is not None else None,
             "확인 상태": "추경 원본 확인 필요" if r.get("budget_review") else f"미분류 {r['pending_count']}건" if r["pending_count"] else "원장 대조 필요" if not r["complete"] else "대조 완료"} for r in rows]
    table(grid, ("최종 편성액", "지출액", "편성잔액"), height=min(440, max(160, len(grid)*36+40)))
    st.caption("편성잔액 = 등록한 최종 편성액 − 지출액. 상세 화면에서 ERP 예산현액·배정액과의 차이를 확인합니다.")
    if not rows:
        return
    r = st.selectbox("세목 상세 보기", rows, format_func=lambda x: f"{x['scope']} · {x['code']} {x['name']}", key="v44_detail")
    st.markdown(f"#### {r['code']} {r['name']}")
    if r["gap"]:
        st.warning(f"등록 편성액과 ERP 예산현액이 {r['gap']:+,}원 다릅니다. 아래 부기 잔액은 등록 편성자료에 따른 잠정값입니다.")
    amounts=st.columns(4)
    amounts[0].metric("예산배정액", money(r["allocated"]))
    amounts[1].metric("지출액", money(r["official"]))
    amounts[2].metric("집행잔액", money(r["allocated"]-r["official"]) if r["official"] is not None else "자료 없음")
    amounts[3].metric("집행률", rate(r["official"],r["allocated"]))
    if not r["complete"]:
        st.info(f"미분류 {r['pending_count']}건 · 미분류 금액(절대값) {money(r['pending_abs'])} · ERP·거래 차액 {money(r['unlinked'])}. 부기 잔액은 잠정값입니다.")
    explain = [{"구분": "통제원장 지출액", "금액": r["official"]},
               {"구분": "연계 거래 합계", "금액": r["linked"]},
               {"구분": "부기 분류 완료", "금액": r["classified"]},
               {"구분": "연계자료 내 미분류", "금액": r["unclassified"]},
               {"구분": "ERP 지출 − 연계 거래", "금액": r["unlinked"]}]
    with st.expander("금액이 어떻게 연결되는지 보기"):
        table(explain, ("금액",))
        st.caption("미설명 지출액 = 연계자료 내 미분류 + ERP 지출과 연계 거래의 차액. 음수 차액도 감추지 않습니다.")
    leaf_rows = []
    for leaf in model["leaves"].values():
        if leaf["scope"] != r["scope"] or leaf["code"] != r["code"]:
            continue
        spent = data["leaf_spent"].get(leaf["id"], 0)
        item = {"마지막 부기": leaf["label"], "등록 편성액": leaf["amount"], "분류 완료액": spent,
                "잠정 잔액": leaf["amount"]-spent, "상태": "원본 확인 필요 · 감액 후 음수" if leaf["amount"] < 0 else "현행명세에서 삭제" if leaf["historical"] else "예산 초과" if spent > leaf["amount"] else "",
                "원본 행": leaf["row"]}
        prev = 0
        for stage in model["timeline"]:
            val = stage["values"].get(leaf["id"], 0)
            item[stage["stage"]] = val
            if stage["stage"] != "본예산":
                item[stage["stage"]+" 증감"] = val-prev
            prev = val
        leaf_rows.append(item)
    monetary = [c for c in (list(leaf_rows[0]) if leaf_rows else []) if c not in ("마지막 부기", "상태", "원본 행")]
    table(leaf_rows, monetary)
    timeline = [{"차수": t["stage"], "편성액": sum(v for k, v in t["values"].items() if model["leaves"][k]["scope"] == r["scope"] and model["leaves"][k]["code"] == r["code"])} for t in model["timeline"]]
    if len(timeline) > 1:
        st.bar_chart(pd.DataFrame(timeline), x="차수", y="편성액", color="#2563eb", height=200)
    csv = pd.DataFrame(leaf_rows).to_csv(index=False).encode("utf-8-sig")
    st.download_button("현재 세목·부기 내역 CSV", csv, f"budget_{state['year']}_{r['code']}.csv", "text/csv", key="v44_csv")


def classify_panel(store, state, revision, data):
    model = data["model"]
    st.subheader("부기 분류작업")
    if not model["leaves"]:
        st.info("본예산부터 등록해 주세요."); return
    txs = data["transactions"]
    counts = pd.Series([x["status"] for x in txs]).value_counts().to_dict() if txs else {}
    st.caption(" · ".join(f"{s} {n}건" for s, n in counts.items()) or "아직 연계 거래가 없습니다.")
    st.caption("ERP 지출액에는 지급일을 사용합니다. 1~2월분을 3월에 지급한 건도 공식 누계에서는 3월 지급입니다.")
    filter_status = st.selectbox("작업대상", ["미완료", "분류완료", "전체"], key="v44_pending_filter")
    query = st.text_input("지출 적요 검색", key="v44_tx_search")
    filtered = [t for t in txs if (filter_status == "전체" or (t["status"] == "분류완료") == (filter_status == "분류완료")) and query.lower() in str(t["row"].get("적요", "")).lower()]
    if not filtered:
        st.info("선택 조건의 거래가 없습니다. ERP 미연계 차액이 남아 있으면 지출 상세자료가 더 필요합니다."); return
    tx = st.selectbox("분류할 거래", filtered,
                      format_func=lambda t: f"[{t['status']}] {t.get('date', '')} · {t['row'].get('적요', '')} · {money(t['amount'])}", key="v44_tx")
    if tx["amount"] is None or tx["status"] in ("지급일 확인", "다른 회계연도", "통제원장 기준일 이후", "취소·미지급 확인"):
        st.warning(f"{tx['status']}: 현재 원장과 대사할 수 없습니다. 원천자료 또는 회계연도·조회종료일을 확인하세요.")
        return
    txid = tx["id"]
    st.markdown(f"**{tx['row'].get('적요', '')}**")
    st.caption(f"지급일 {tx['date']} · 원거래 {money(tx['amount'])} · 원본 세목 {tx['row'].get('세목', '')}")
    col1, col2 = st.columns(2)
    scope = col1.selectbox("사업구분 확인", [""]+SCOPES, index=([""]+SCOPES).index(tx.get("scope", "")), key="v44_scope_"+txid,
                           format_func=lambda x: x or "선택 필요")
    codes = sorted({x["code"] for x in model["leaves"].values() if x["scope"] == scope})
    locked_code = expense_code(tx["row"])
    if locked_code:
        codes = [locked_code] if locked_code in codes else []
        st.caption("원천에 공식 세목이 있으므로 그 세목의 부기만 선택할 수 있습니다.")
    code = col2.selectbox("공식 예산세목", [""]+codes, index=([""]+codes).index(tx["code"]) if tx.get("code") in codes else 0,
                          key="v44_code_"+txid+scope, format_func=lambda x: (x+" "+model["semoks"].get(scope+"|"+x, {}).get("name", "")) if x else "선택 필요")
    if not scope or not code:
        st.info("사업구분과 세목을 선택하면 그 안에 편성된 마지막 부기만 표시됩니다."); return
    candidates = sorted([x for x in model["leaves"].values() if x["scope"] == scope and x["code"] == code], key=lambda x: x["label"])
    ids = {x["label"] + (" [현행명세 삭제]" if x["historical"] else ""): x["id"] for x in candidates}
    reverse = {v: k for k, v in ids.items()}
    suggestions = recommendations(state, tx, candidates)
    if suggestions:
        st.caption("분류 참고: " + " / ".join(reverse[k]+" ("+reason+")" for _, k, reason in suggestions))
    existing = tx["allocations"]
    editor_rows = [{"부기": reverse.get(a["leaf"], ""), "금액(원)": a["amount"]} for a in existing] or [{"부기": "", "금액(원)": tx["amount"]}]
    with st.form("v44_assignment_"+txid+scope+code):
        edited = st.data_editor(pd.DataFrame(editor_rows), num_rows="dynamic", hide_index=True, width="stretch",
                                column_config={"부기": st.column_config.SelectboxColumn("마지막 부기", options=[""]+list(ids), required=True, width="large"),
                                               "금액(원)": st.column_config.NumberColumn(step=1, required=True, format="localized")},
                                key=f"v44_split_{revision}_{txid}_{scope}_{code}")
        st.caption("여러 부기로 나눌 때는 행을 추가하세요. 분할 합계가 원거래 금액과 같아야 저장됩니다. 환불·여입은 음수로 분류합니다.")
        reason = st.text_input("분류 근거 / 사업구분·세목 수정 사유", value=state["assignments"].get(txid, {}).get("reason", ""))
        submitted = st.form_submit_button("분류 확정 저장", type="primary")
    if submitted:
        try:
            allocations = [{"leaf": ids.get(str(x["부기"]), ""), "amount": x["금액(원)"]} for x in edited.to_dict("records")]
            new = classify(state, tx, scope, code, allocations, reason)
        except (ValidationError, ValueError) as exc:
            st.error(str(exc))
        else:
            commit(store, new, revision, "부기 분류 · "+str(tx["row"].get("적요", ""))[:120])
    if txid in state["assignments"] and st.button("분류 해제", key="v44_unclassify_"+txid):
        new = copy.deepcopy(state)
        del new["assignments"][txid]
        commit(store, new, revision, "부기 분류 해제 · "+str(tx["row"].get("적요", ""))[:120])
    with st.expander("월별 분석의 집계월 조정"):
        st.caption("ERP 지급일과 지출액은 유지하고, 집행항목 분석에서 사용할 집계월만 정합니다. 현재 기존 분석화면은 2026년 자료에 적용됩니다.")
        default = state.get("accruals", {}).get(txid, [{"month": int(tx["date"][5:7]), "amount": tx["amount"]}])
        with st.form("v44_accrual_"+txid):
            frame = st.data_editor(pd.DataFrame(default).rename(columns={"month": "집계월", "amount": "금액(원)"}),
                                   num_rows="dynamic", hide_index=True,
                                   column_config={"집계월": st.column_config.NumberColumn(min_value=1, max_value=12, step=1, required=True),
                                                  "금액(원)": st.column_config.NumberColumn(step=1, required=True)})
            submit = st.form_submit_button("집계월 분할 저장", disabled=state["year"] != 2026)
        if submit:
            try:
                values = [{"month": won(x["집계월"]), "amount": won(x["금액(원)"])} for x in frame.to_dict("records")]
                if not values or sum(x["amount"] for x in values) != tx["amount"] or any(not 1 <= x["month"] <= 12 or x["amount"]*tx["amount"] < 0 for x in values):
                    raise ValidationError("집계월은 1~12월이며 분할 합계와 부호가 원거래와 같아야 합니다.")
            except ValidationError as exc:
                st.error(str(exc))
            else:
                new = copy.deepcopy(state)
                new.setdefault("accruals", {})[txid] = values
                new.setdefault("accrual_revisions", {})[txid] = tx["row"].get("원천수정번호", 0)
                commit(store, new, revision, "집계월 조정 · "+str(tx["row"].get("적요", ""))[:120])


def history_panel(store, state, revision, daily):
    st.subheader("변경 이력과 복원")
    history = store.history(state["year"])
    table([{"버전": r, "시각": t, "변경 내용": a} for r, t, a in history])
    if history:
        target = st.selectbox("확인할 저장 버전", [h[0] for h in history], key="v44_history_version")
        past = store.version(state["year"], target)
        model = build_model(past)
        st.caption(f"선택 버전 {target}: 등록 편성액 {money(model['total'])} · 분류 기록 {len(past['assignments'])}건")
        checked = st.checkbox("위 버전의 예산·통제원장·분류 상태로 되돌립니다. 현재 상태는 변경 이력에 남습니다.", key=f"v44_restore_check_{target}")
        if st.button("선택 버전으로 복원", disabled=not checked or target == revision, key="v44_undo"):
            commit(store, past, revision, f"버전 {target} 상태로 복원")
    st.download_button("예산·분류·원본 통합 백업(JSON)", store.export(state["year"]), f"budget_backup_{state['year']}_r{revision}.json", "application/json", key="v44_backup")
    raw = st.file_uploader("통합 백업 가져오기", type=["json"], key="v44_backup_upload")
    if raw:
        checked = st.checkbox("현재 회계연도 자료를 백업 내용으로 복원합니다.", key="v44_import_backup_check")
        if st.button("백업 복원", disabled=not checked, key="v44_import_backup"):
            try:
                store.restore(raw.getvalue(), state["year"], revision)
            except (ValidationError, ConflictError, ValueError) as exc:
                st.error(str(exc))
            else:
                st.session_state.pop("initial_sync_done", None)
                st.rerun()
    with st.expander("V39·V43 기존 부기 분류 가져오기"):
        st.caption("본예산 원본을 먼저 등록하세요. 사업구분·세목·전체 부기 경로가 일치하는 분류만 옮기며, 상위 부기에서 멈춘 기록은 재확인 대상으로 남깁니다.")
        legacy_file = st.file_uploader("budget_execution_workspace.json 또는 local_budget_control.json", type=["json"], key="v44_legacy_import")
        if legacy_file:
            try:
                new, imported, pending = migrate_legacy_classifications(state, json.loads(legacy_file.getvalue()), daily)
                st.caption(f"정확히 일치 {len(imported)}건 · 재확인 {len(pending)}건")
                table(imported, ("금액",))
                if st.button("일치하는 기존 분류 반영", disabled=not imported and not pending, key="v44_legacy_commit"):
                    commit(store, new, revision, "기존 부기 분류 이전", (legacy_file.name, legacy_file.getvalue()))
            except (ValidationError, ValueError, TypeError) as exc:
                st.error(str(exc))


@st.fragment
def render_budget_workspace(daily):
    require_owner()
    store = BudgetStore()
    year = st.number_input("회계연도", min_value=2000, max_value=2100, value=2026, step=1, key="v44_fiscal_year")
    state, revision = store.load(year)
    data = reconciliation(state, daily)
    model = data["model"]
    if model.get("review_leaves"):
        st.warning(f"원본 추경에서 감액 후 음수인 부기 {len(model['review_leaves'])}개가 있습니다. 세목 총액은 원본과 대조했지만 해당 부기의 배분은 원본 확인이 필요합니다.")
        with st.expander("원본 확인 필요 부기", expanded=True):
            table([{"경비":x["scope"],"세목":x["code"],"부기":x["label"],"편성액":x["amount"]} for x in model["review_leaves"]], ("편성액",))
    ledger = state.get("ledger")
    st.caption(f"등록 편성액: 마지막으로 반영한 예산명세서 기준 · 지출액: {ledger['period_start']+' ~ '+ledger['as_of'] if ledger else '통제원장 미등록'} · 저장 버전 {revision}")
    cards = st.columns(4)
    cards[0].metric("등록 편성액", money(model["total"]) if model["leaves"] else "미등록")
    cards[1].metric("ERP 예산현액", money(ledger["total"]["current_budget"]) if ledger else "미등록")
    cards[2].metric("지출액", money(ledger["total"]["expense"]) if ledger else "미등록")
    issues=data["issues"]
    cards[3].metric("확인 대기", f"{issues['pending_count']+issues['unscoped_count']}건", f"ERP·거래 차액 절대합 {money(issues['unlinked_abs'])}" if ledger else "통제원장 미등록")
    with st.container(border=True):
        st.markdown("**지출 내역 확인 진행**")
        st.progress(issues["complete_count"]/issues["semok_count"] if issues["semok_count"] else 0)
        st.caption(f"세목 {issues['complete_count']}/{issues['semok_count']}개 대조 완료 · 미분류 절대금액 {money(issues['pending_abs'])} · 사업구분·일자 등 확인 {issues['unscoped_count']}건")
        if issues["all_complete"]: st.success("모든 세목의 지출액이 분류한 거래와 일치합니다.")
    if ledger and model["leaves"]:
        gap = model["total"]-ledger["total"]["current_budget"]
        if gap:
            st.warning(f"편성자료와 통제원장 예산현액 차이 {gap:+,}원. 추경·이월·변경(경비)과 자료 기준을 확인해야 합니다.")
            with st.expander("통제원장 예산 변동 근거"):
                t = ledger["total"]
                table([{"당초": t.get("original"), "경정": t.get("adjustment"), "이월": t.get("carryover"),
                        "변경": t.get("transfer"), "변경(경비)": t.get("expense_transfer"), "예산현액": t.get("current_budget"),
                        "예산배정액": t.get("allocated")}], ("당초", "경정", "이월", "변경", "변경(경비)", "예산현액", "예산배정액"))
    if not revision:
        legacy = [p.name for p in Path(".").glob("*.json") if "budget" in p.name or "workspace" in p.name]
        if legacy:
            st.info("이전 예산 저장파일이 있습니다: "+", ".join(legacy)+". 일상경비 자료는 자동 연계됩니다. 기존 부기 분류의 이전 방법은 사용안내를 확인해 주세요.")
    view = st.segmented_control("작업 화면", ["예산현황", "부기 분류", "원천자료", "변경 이력"], default="예산현황", key="v44_view") or "예산현황"
    if view == "원천자료":
        upload_panel(store, state, revision)
    elif view == "부기 분류":
        classify_panel(store, state, revision, data)
    elif view == "변경 이력":
        history_panel(store, state, revision, daily)
    else:
        overview(state, data)
