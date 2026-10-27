"""One current ledger snapshot for report, drilldown and downloadable values."""
from calendar import monthrange
from datetime import date
import csv
import html
import io
from collections import defaultdict

from budget_engine import BudgetStore, parse_date, reconciliation
from execution_sync import report_account_label
from expense_scope_report import render_leaf_details


def money(value):
    return "자료 없음" if value is None else f"{int(value):,}원"


def snapshots(state):
    result = dict(state.get("ledger_snapshots", {}))
    if state.get("ledger"):
        result[state["ledger"]["as_of"]] = state["ledger"]
    return result


def monthly_evidence(state, scope="전체", exclude_returns=False):
    """Consecutive cumulative month ends are needed; missing evidence stays None."""
    ledger = state.get("ledger")
    if not ledger:
        return []
    end = parse_date(ledger["as_of"])
    history = snapshots(state)
    rows=[]
    for month in range(1,end.month+1):
        stop = end if month == end.month else date(end.year,month,monthrange(end.year,month)[1])
        prior = date(end.year,month-1,monthrange(end.year,month-1)[1]) if month>1 else None
        cur = history.get(stop.isoformat())
        prev = history.get(prior.isoformat()) if prior else None
        ready = bool(cur) and (month==1 or bool(prev))
        def total(doc):
            return sum(r["expense"] for r in doc["lines"].values() if (scope=="전체" or r["scope"]==scope) and (not exclude_returns or "대행사업비반환금" not in r["name"])) if doc else 0
        amount=total(cur)-total(prev) if ready else None
        rows.append({"월":month, "조회종료일":stop.isoformat(), "원장 누계 증감액":amount,
                     "근거": "연속 누계 원장 차감" if ready else "전월말·해당월 원장 필요"})
    return rows


def rapid_period_amounts(state, month):
    end=date(state["year"],month,monthrange(state["year"],month)[1])
    doc=snapshots(state).get(end.isoformat())
    if not doc:return None
    amounts=defaultdict(int)
    for line in doc["lines"].values():amounts[line["code"]]+=line["expense"]
    return dict(amounts)


def report_data(state, daily, scope="전체", exclude_returns=False):
    data=reconciliation(state,daily)
    lines=[r for r in data["rows"] if (scope=="전체" or r["scope"]==scope) and (not exclude_returns or "대행사업비반환금" not in r["name"])]
    present=bool(state.get("ledger"))
    summary={k:sum(r[k] or 0 for r in lines) if present else None for k in ("current","allocated","official")}
    summary["budget"]=sum(r["budget"] for r in lines)
    summary["remaining"]=summary["allocated"]-summary["official"] if present else None
    ledger=state.get("ledger") or {}
    return {"state":state,"data":data,"lines":lines,"summary":summary,"scope":scope,
            "exclude_returns":exclude_returns,"as_of":ledger.get("as_of","미등록"),
            "updated_at":ledger.get("imported_at","미등록"),"filename":ledger.get("filename","미등록")}


def report_rows(report):
    return [{"사업구분":r["scope"],"세목코드":r["code"],"세목명":r["name"],
             "등록 편성액":r["budget"],"예산현액":r["current"],"예산배정액":r["allocated"],
             "지출액":r["official"],"집행잔액":r["allocated"]-r["official"] if r["official"] is not None else None,
             "집행률(%)":round(r["official"]/r["allocated"]*100,4) if r["allocated"] else None,
             "미분류 건수":r["pending_count"],"미분류 금액(절대값)":r["pending_abs"],"ERP·거래 차액":r["unlinked"]} for r in report["lines"]]


def csv_bytes(rows):
    if not rows:return b""
    buf=io.StringIO();writer=csv.DictWriter(buf,fieldnames=list(rows[0]));writer.writeheader()
    for row in rows:
        # CSV formulas from user-controlled descriptions must remain plain text.
        writer.writerow({k:("'"+v if isinstance(v,str) and v.startswith(("=","+","-","@","\t","\r")) else v) for k,v in row.items()})
    return buf.getvalue().encode("utf-8-sig")


def report_export_rows(report):
    return [{"항목": report_account_label({"scope": row["사업구분"], "세목명": row["세목명"]}),
             **{k: v for k, v in row.items() if k not in ("사업구분", "세목코드", "세목명")}}
            for row in report_rows(report)]


def report_html(report):
    esc=lambda x:html.escape(str(x))
    cols=report_export_rows(report)
    header="".join(f"<th>{esc(k)}</th>" for k in cols[0]) if cols else ""
    body="".join("<tr>"+"".join(f"<td>{esc('자료 없음' if v is None else format(v,',') if isinstance(v,(int,float)) else v)}</td>" for v in row.values())+"</tr>" for row in cols)
    s=report["summary"]
    basis="대행사업비반환금 제외" if report["exclude_returns"] else "ERP 전체"
    cards="".join(f"<div><small>{name}</small><b>{money(s[k])}</b></div>" for name,k in [("예산배정액","allocated"),("지출액","official"),("집행잔액","remaining")])
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><title>{report['state']['year']} 예산집행 보고서</title>
<style>body{{font-family:Arial,'Malgun Gothic',sans-serif;margin:36px;color:#18304b}}h1{{font-size:26px}}.meta{{text-align:right;color:#52657b}}.cards{{display:flex;gap:18px;margin:26px 0}}.cards div{{border:1px solid #d5e0ea;border-radius:12px;padding:18px;flex:1}}b{{display:block;font-size:22px;margin-top:8px}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{border-bottom:1px solid #dde5ed;padding:9px;text-align:right}}th{{background:#edf3f8}}td:first-child,td:nth-child(3){{text-align:left}}small{{color:#567}}@media print{{body{{margin:10mm}}thead{{display:table-header-group}}tr{{break-inside:avoid}}}}</style>
<div class="meta">실적 조회종료일 {esc(report['as_of'])}<br>자료 반영일 {esc(report['updated_at'])}</div>
<h1>{report['state']['year']} 정약용 펀그라운드 예산집행 보고서</h1><p>{esc(report['scope'])} · {basis} · 원 단위</p>
<div class="cards">{cards}</div><p>집행잔액 = 예산배정액 − 지출액. 부기 분류자료는 지출액을 설명하며 추가 합산하지 않습니다.</p>
<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>
<p>원천: {esc(report['filename'])} · {esc(report['as_of'])} 누계. 과거 월별 원장이 없는 기간은 월별 지출액으로 추정하지 않습니다.</p></html>'''.encode("utf-8")


def report_excel_bytes(report):
    """Use the existing application's Excel export stack and the same snapshot."""
    import pandas as pd
    output=io.BytesIO()
    rows=report_export_rows(report)
    frame=pd.DataFrame(rows)
    for c in ("항목",):
        if c in frame:
            frame[c]=frame[c].map(lambda x:"'"+x if isinstance(x,str) and x.startswith(("=","+","-","@")) else x)
    with pd.ExcelWriter(output,engine="openpyxl") as writer:
        frame.to_excel(writer,index=False,sheet_name="예산과목별")
        pd.DataFrame([{"회계연도":report["state"]["year"],"조회종료일":report["as_of"],"자료반영일":report["updated_at"],
                       "사업구분":report["scope"],"반환금": "제외" if report["exclude_returns"] else "포함",
                       "예산배정액":report["summary"]["allocated"],"지출액":report["summary"]["official"],
                       "집행잔액":report["summary"]["remaining"]}]).to_excel(writer,index=False,sheet_name="기준과총괄")
        for ws in writer.book.worksheets:
            ws.freeze_panes="A2";ws.auto_filter.ref=ws.dimensions
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width=24 if col[0].value=="세목명" else 20
            for row in ws.iter_rows(min_row=2):
                for item in row:
                    if isinstance(item.value,(int,float)):item.number_format='#,##0.####;[Red]-#,##0.####'
    return output.getvalue()


def render_source_strip(page):
    import streamlit as st
    state,_=BudgetStore().load(2026)
    ledger=state.get("ledger")
    if not ledger:return
    st.caption('아래는 관리항목별 지출내역입니다. 전체 금액은 예산집행 보고서에서 확인하세요.')


def render_current_report(daily):
    import streamlit as st
    import pandas as pd
    year=st.number_input("보고 회계연도",2000,2100,2026,step=1,key="v45_report_year")
    state,revision=BudgetStore().load(year)
    if not state.get("ledger"):
        st.info("통제원장 등록자료가 없습니다. 기존 단월·비교 보고의 자료 등록에서 집행현황을 등록할 수 있습니다.")
        return
    a,b=st.columns([1,1])
    scope=a.segmented_control("보고 사업구분",["전체","운영경비","일상경비"],default="전체",key="v45_report_scope") or "전체"
    exclude=b.checkbox("관리 집행률: 대행사업비반환금 제외",value=False,key="v45_report_returns")
    report=report_data(state,daily,scope,exclude);summary=report["summary"]
    from management_view import date_label
    st.html(f'<div class="page-date">{html.escape(date_label(report["as_of"]))}</div>')
    st.subheader("현재 예산집행 현황")
    cols=st.columns(4)
    for col,label,key in zip(cols,["예산배정액","지출액","집행잔액"],["allocated","official","remaining"]):col.metric(label,money(summary[key]))
    percent=summary["official"]/summary["allocated"]*100 if summary["allocated"] else None
    cols[3].metric("배정액 대비 집행률",f"{percent:.2f}%" if percent is not None else "산정 불가")
    st.caption("집행잔액 = 예산배정액 − 지출액")
    detail,month,download=st.tabs(["예산과목별","월별 근거","보고서 내려받기"])
    with detail:
        rows=report_rows(report)
        selected=st.dataframe(pd.DataFrame(rows),hide_index=True,width="stretch",on_select="rerun",selection_mode="single-row",key="v45_report_grid",
             column_order=["사업구분","세목코드","세목명","예산배정액","지출액","집행잔액","집행률(%)"])
        st.caption("행을 선택하면 같은 사업구분·세목의 거래 원천을 펼칩니다.")
        if selected.selection.rows:
            index=selected.selection.rows[0]
            if index<len(report["lines"]):
                r=report["lines"][index]
                st.markdown(f"**{r['scope']} · {r['code']} {r['name']}**")
                txs=[t for t in report["data"]["transactions"] if t["eligible"] and t["scope"]==r["scope"] and t["code"]==r["code"]]
                st.caption(f"거래 확인액 {money(r['linked'])} · 원장과의 차액 {money(r['unlinked'])}")
                st.dataframe(pd.DataFrame([{"지급일":t['date'],"적요":t['row'].get('적요'),"지출액":t['amount'],"분류":t['status'],"원본파일":t['row'].get('원본파일','기존 저장자료'),"원본 행":t['row'].get('원본행')} for t in txs]),hide_index=True,width="stretch")
                render_leaf_details(state,daily,{'경비구분':r['scope'],'세목코드':r['code'],'세목명':r['name'],
                    '예산현액':r['current'],'예산배정액':r['allocated'],'지출액':r['official']},
                    report['as_of'],prefix='current_report_leaf_'+r['scope']+'_'+r['code'])
    with month:
        st.info("월별 금액을 보려면 각 월말 집계표가 필요합니다. 등록되지 않은 월은 자료 없음으로 표시합니다.")
        st.dataframe(pd.DataFrame(monthly_evidence(state,scope,exclude)),hide_index=True,width="stretch")
        st.caption("통제원장 원본과 조회종료일은 반영할 때마다 보존됩니다. 현재 원장보다 과거 자료를 추가하는 절차는 사용안내를 확인하세요.")
    with download:
        st.caption(f"화면과 동일한 사업구분·반환금 포함기준 · 저장 버전 {revision}")
        st.download_button("보고서 HTML",report_html(report),f"budget_report_{year}_{report['as_of']}.html","text/html",key="v45_report_html")
        st.download_button("보고서 Excel",report_excel_bytes(report),f"budget_report_{year}_{report['as_of']}.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",key="v45_report_xlsx")
        st.download_button("Word 호환 문서(.doc)",report_html(report),f"budget_report_{year}_{report['as_of']}.doc","application/msword",key="v45_report_doc")
        st.download_button("예산과목별 CSV",csv_bytes(report_export_rows(report)),f"budget_report_{year}_{report['as_of']}.csv","text/csv",key="v45_report_csv")
