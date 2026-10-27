"""Plain-language presentation and a shared, read-only management view."""
import copy
import html
import re
from calendar import monthrange
from collections import defaultdict

from account_catalog import ACCOUNT_CATALOG
from budget_engine import build_model, parse_date
from expense_scope_report import csv_data, scoped_accounts, render_scoped_execution


def simple_view():
    import streamlit as st
    return bool(st.session_state.get('app_simple_view',False))


def set_view(simple):
    import streamlit as st
    st.session_state['app_simple_view']=bool(simple)


def _toggle_view():
    import streamlit as st
    set_view(st.session_state['_home_simple_toggle'])


def date_label(value):
    if re.fullmatch(r'\d{4}-\d{2}',str(value)):
        return f'{value[:4]}. {int(value[5:7])}.'
    dt=parse_date(value)
    return f'{dt.year}. {dt.month}. {dt.day}.' if dt else ''


def financial_snapshot(state,config):
    """Use imported snapshots, never a manually edited report or management sum."""
    candidates=[]
    ledger=state.get('ledger')
    if ledger and parse_date(ledger.get('as_of')):
        candidates.append({'as_of':ledger['as_of'],'display_date':ledger['as_of'],'exact_date':True,'priority':1,'filename':ledger.get('filename',''),
            'rows':[{'경비구분':r['scope'],'세목코드':r['code'],'세목명':r['name'],
                '예산현액':r['current_budget'],'예산배정액':r['allocated'],'지출액':r['expense']}
                for r in ledger['lines'].values()]})
    for source in config.get('execution_sources',{}).values():
        if not source.get('verified') or source.get('year')!=state['year'] or not parse_date(source.get('as_of')):continue
        saved=source.get('report_snapshot'); rows=[]
        if saved:
            parsed=scoped_accounts(saved.get('lines',[]),state,source['as_of'])
            rows=[{k:r[k] for k in ('경비구분','세목코드','세목명','예산현액','예산배정액','지출액')} for r in parsed['rows']]
            rows.extend({'경비구분':'구분 확인 필요','세목코드':r['세목코드'],'세목명':r['세목명'],
                         '예산현액':r.get('예산현액'),'예산배정액':r.get('예산배정액'),
                         '지출액':r.get('누적지출액')} for r in parsed['unresolved'])
        else:
            # Old archives froze expenses only. Do not borrow editable budgets.
            amounts=source.get('cumulative',{})
            same=ledger and ledger['as_of']==source['as_of']
            if same:
                ledger_amounts=defaultdict(int)
                for r in ledger['lines'].values():ledger_amounts[r['code']]+=r['expense']
                if dict(ledger_amounts)==amounts:continue
            rows=[{'경비구분':'구분 확인 필요','세목코드':code,'세목명':ACCOUNT_CATALOG.get(code,('',code))[1],
                   '예산현액':None,'예산배정액':None,'지출액':amount} for code,amount in amounts.items()]
        dt=parse_date(source['as_of'])
        exact=source.get('date_precision')=='day' or dt.day!=monthrange(dt.year,dt.month)[1]
        if rows:candidates.append({'as_of':source['as_of'],'display_date':source['as_of'] if exact else source['as_of'][:7],
            'exact_date':exact,'priority':2,'filename':source.get('cumulative_file',''),'rows':rows})
    if not candidates:return {'as_of':'','filename':'','rows':[],'conflict':False,'available':False}
    # An inferred month end must not outrank an actual date in that same month.
    candidates.sort(key=lambda c:(c['as_of'][:7],c['exact_date'],c['as_of'],c['priority']),reverse=True)
    chosen=copy.deepcopy(candidates[0])
    def totals_by_code(rows):
        result=defaultdict(int)
        for r in rows:result[r['세목코드']]+=r['지출액'] or 0
        return dict(result)
    chosen['conflict']=any(c['as_of']==chosen['as_of'] and totals_by_code(c['rows'])!=totals_by_code(chosen['rows']) for c in candidates[1:])
    chosen['available']=True
    return chosen


def financial_totals(rows):
    values={k:sum(r[k] for r in rows) if rows and all(r.get(k) is not None for r in rows) else None
            for k in ('예산현액','예산배정액','지출액')}
    values['예산잔액']=values['예산현액']-values['지출액'] if values['예산현액'] is not None and values['지출액'] is not None else None
    values['집행률']=values['지출액']/values['예산현액']*100 if values['예산현액'] and values['지출액'] is not None else None
    return values


def money(value):return '자료 없음' if value is None else f'{value:,.0f}원'


STYLE='''
<style>
.stApp, [data-testid="stMain"], [data-testid="stMarkdownContainer"], input, textarea, button, select,
[data-testid="stMetricValue"], [data-testid="stMetricLabel"], [data-testid="stCaptionContainer"] {
font-family:"Pretendard","Noto Sans KR","Malgun Gothic","Apple SD Gothic Neo",Arial,sans-serif!important;}
.block-container{padding-top:4.25rem!important;max-width:1440px!important;}
[data-testid="stMain"] p,[data-testid="stMain"] label{line-height:1.6;}
[data-testid="stMain"] h1,[data-testid="stMain"] h2,[data-testid="stMain"] h3{line-height:1.35;color:#10233d;}
[data-testid="stCaptionContainer"] p{font-size:.9rem;line-height:1.6;}
[data-testid="stSidebar"] button[kind="primary"] p{color:#10233d!important;}
.dashboard-hero{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:30px 32px;
background:linear-gradient(115deg,#132c4c,#234f79);border-radius:20px;margin-bottom:20px;color:white;}
.dashboard-hero .eyebrow{font-size:14px;color:#b8d7f0;font-weight:600;margin-bottom:10px;letter-spacing:.04em;}
.dashboard-hero h1{font-size:30px!important;color:white!important;font-weight:750;letter-spacing:-.04em;margin:0;padding:0;}
.dashboard-hero p{font-size:16px;color:#d8e7f2!important;margin:10px 0 0;}
.dashboard-date{align-self:flex-start;white-space:nowrap;font-size:15px;font-weight:600;color:#d8e7f2;}
.page-date{display:flex;align-items:flex-end;flex-direction:column;gap:8px;white-space:nowrap;color:#415873;font-size:15px;font-weight:600;}
.page-date small{font-size:13px;font-weight:500;color:#6b7e93;}
.st-key-home_summary [data-testid="stMetric"],.st-key-simple_summary [data-testid="stMetric"]{
border:1px solid #dce5ee;border-radius:16px;background:#fff;padding:20px 22px;min-height:128px;}
.st-key-home_summary [data-testid="stMetricValue"],.st-key-simple_summary [data-testid="stMetricValue"]{
font-size:clamp(1.25rem,2.1vw,2rem);font-weight:700;letter-spacing:-.035em;color:#122e50;}
.st-key-home_summary [data-testid="stMetricLabel"],.st-key-simple_summary [data-testid="stMetricLabel"]{font-size:15px;color:#51667d;}
.st-key-home_mode{background:#edf4fc;border:1px solid #d2e1f2;border-radius:14px;padding:12px 20px;margin-top:12px;}
[class*="st-key-home_card_"]{background:#fff;border:1px solid #dce5ee;border-radius:16px;padding:20px!important;}
[class*="st-key-home_card_"] button{min-height:48px;font-size:16px;border-color:#bdd0e4;color:#163d66;}
[class*="st-key-home_card_"] button p{font-size:16px!important;font-weight:650;}
.page-head{align-items:center;padding-top:0;}
.page-title{font-size:1.85rem;font-weight:750;letter-spacing:-.035em;}
.page-kicker{font-size:.8rem;letter-spacing:0;font-weight:600;}
.page-desc{font-size:.96rem;font-weight:400;line-height:1.55;}
.st-key-analysis_monthly_adjustment_button button{font-size:1.1rem;min-height:60px;}
@media(max-width:900px){.dashboard-hero{padding:24px;align-items:flex-start;}.dashboard-hero h1{font-size:25px!important;}
.st-key-home_summary [data-testid="stMetricValue"],.st-key-simple_summary [data-testid="stMetricValue"]{font-size:1.4rem;}}
@media(max-width:600px){.block-container{padding:4rem 1rem 3rem!important;}.dashboard-hero{flex-direction:column-reverse;gap:12px;}
.dashboard-date{align-self:flex-end;}.page-head{align-items:flex-start;}.page-date{align-self:flex-end;}}
</style>
'''


def mount_style():
    import streamlit as st
    st.html(STYLE)


def render_summary(snapshot,key='simple_summary'):
    import streamlit as st
    totals=financial_totals(snapshot['rows'])
    with st.container(key=key):
        for col,label in zip(st.columns(4),['예산현액','지출액','예산잔액','집행률']):
            value=totals[label]
            col.metric(label,f'{value:.1f}%' if label=='집행률' and value is not None else money(value))
    st.caption('예산잔액 = 예산현액 − 지출액 · 전체 예산 기준')
    if snapshot.get('conflict'):st.warning('같은 날짜의 집계표 금액이 다릅니다. 보고서에 등록한 자료를 기준으로 표시했습니다. 자료를 확인해 주세요.')
    if snapshot['rows'] and totals['예산현액'] is None:st.info('예산자료를 함께 등록하면 잔액과 집행률도 표시됩니다.')
    return totals


def render_home(snapshot,menu,go_page,owner=False,notice=None):
    import streamlit as st
    st.html('<div class="dashboard-hero"><div><div class="eyebrow">2026 예산집행관리</div>'
        '<h1>정약용 펀그라운드</h1><p>예산과 지출을 확인하고, 필요한 업무로 바로 이동하세요.</p></div>'
        f'<div class="dashboard-date" title="자료 조회기간">{html.escape(date_label(snapshot.get("display_date",snapshot["as_of"])))}</div></div>')
    render_summary(snapshot,'home_summary')
    if notice:notice()
    with st.container(key='home_mode'):
        a,b=st.columns([4,1])
        a.markdown('**간편보기**');a.caption('켜면 모든 메뉴에서 핵심 정보만 표시합니다.')
        st.session_state['_home_simple_toggle']=simple_view()
        b.toggle('간편보기',key='_home_simple_toggle',on_change=_toggle_view,label_visibility='collapsed')
    if not snapshot['available']:st.info('등록된 집행자료가 없습니다. 예산집행 보고서에서 집계표를 등록해 주세요.')
    st.markdown('### 업무 바로가기')
    descriptions={'📊 예산집행 현황':'예산·지출·잔액과 경비별 현황',
        '📈 집행항목 분석':'전년 대비 변동과 월별 지출내역',
        '⚡ 신속집행 관리':'기간별 실적과 목표 달성 여부',
        '📝 예산집행 보고서':'경비별·부기별 집행내역과 보고서',
        '⇄ 일상경비 자료연계':'지출자료 등록과 오류 확인',
        '🧭 편성예산·부기관리':'본예산·추경 등록과 부기 관리'}
    public=[p for p in menu if p not in ('⇄ 일상경비 자료연계','🧭 편성예산·부기관리')]
    extra=['⇄ 일상경비 자료연계']+(['🧭 편성예산·부기관리'] if owner else [])
    for start in range(0,len(public),2):
        for col,page in zip(st.columns(2),public[start:start+2]):
            with col,st.container(key='home_card_'+str(menu.index(page))):
                st.caption(descriptions[page])
                st.button(page,key='v44_home_'+page,width='stretch',on_click=go_page,args=(page,))
    for col,page in zip(st.columns(len(extra)),extra):
        with col,st.container(key='home_card_'+str(menu.index(page))):
            st.caption(descriptions[page])
            st.button(page,key='v44_home_'+page,width='stretch',on_click=go_page,args=(page,))
    st.caption('V45.11')


def render_simple_overview(snapshot):
    import pandas as pd
    import streamlit as st
    render_summary(snapshot)
    if not snapshot['rows']:return
    grouped=[]
    for scope in dict.fromkeys(r['경비구분'] for r in snapshot['rows']):
        values=financial_totals([r for r in snapshot['rows'] if r['경비구분']==scope])
        grouped.append({'경비구분':scope,**{k:values[k] for k in ('예산현액','지출액','예산잔액')}})
    st.subheader('경비별 현황')
    st.dataframe(pd.DataFrame(grouped),hide_index=True,width='stretch',column_config={k:st.column_config.NumberColumn(format='%,d원') for k in ('예산현액','지출액','예산잔액')})
    review=[]
    for r in snapshot['rows']:
        if r['세목코드']=='802-11' or r['예산현액'] is None:continue
        status='예산 초과' if r['지출액']>r['예산현액'] else '미집행' if r['예산현액']>0 and not r['지출액'] else ''
        if status:review.append({'경비구분':r['경비구분'],'세목':r['세목명'],'예산현액':r['예산현액'],'지출액':r['지출액'],'확인할 내용':status})
    st.subheader('먼저 확인할 항목')
    if review:st.dataframe(pd.DataFrame(sorted(review,key=lambda r:(r['확인할 내용']!='예산 초과',-r['예산현액']))),hide_index=True,width='stretch')
    else:st.success('예산 초과 또는 미집행 항목이 없습니다.')


def render_simple_analysis(frame,categories,display_category,render_details,cutoff=12):
    import pandas as pd
    import streamlit as st
    view=st.radio('분석 내용',['주요 변동','미집행 점검'],horizontal=True,key='simple_analysis_view')
    if view=='미집행 점검':
        import json
        from pathlib import Path
        from datetime import datetime
        now=datetime.now();until=max(1,min(cutoff,now.month-1 if now.year==2026 else 12))
        path=Path('missing_override_2026.json')
        try:overrides=json.loads(path.read_text()) if path.exists() else {}
        except (ValueError,OSError):overrides={}
        rows=[]
        for category in categories:
            if category=='수입금':continue
            months=[m for m in range(1,until+1) if frame.loc[(frame['year']==2026)&(frame['category']==category)&(frame['month']==m),'amount'].sum()<=0
                    and not overrides.get(category,{}).get(str(m),False)]
            if months:rows.append({'항목':display_category(category),'확인할 월':', '.join(f'{m}월' for m in months),'개월 수':len(months)})
        st.metric('확인할 항목',f'{len(rows)}개')
        st.caption(f'1~{until}월 중 지출이 없는 월입니다. 일괄지급·후지급 여부를 확인하세요.')
        st.dataframe(pd.DataFrame(rows),hide_index=True,width='stretch')
        return
    st.subheader('전년 대비 주요 변동')
    month=st.selectbox('조회기간',list(range(1,13)),index=max(1,min(cutoff,12))-1,format_func=lambda m:f'1~{m}월',key='simple_analysis_month')
    selected=frame[frame['month']<=month] if not frame.empty else frame
    rows=[]
    for category in categories:
        if category=='수입금':continue
        r=selected[selected['category']==category]
        before=r.loc[r['year']==2025,'amount'].sum();after=r.loc[r['year']==2026,'amount'].sum()
        rows.append({'항목':display_category(category),'전년 지출액':before,'올해 지출액':after,'증감액':after-before})
    data=pd.DataFrame(sorted(rows,key=lambda r:abs(r['증감액']),reverse=True)[:5])
    st.dataframe(data,hide_index=True,width='stretch',column_config={c:st.column_config.NumberColumn(format='%,d원') for c in ('전년 지출액','올해 지출액','증감액')})
    st.caption('같은 기간의 지출을 비교합니다. 항목별 내역이므로 전체 지출액과는 다를 수 있습니다.')
    st.subheader('항목별 월별 내역')
    category=st.selectbox('항목 선택',categories,format_func=display_category,key='simple_analysis_category')
    render_details(frame[frame['category']==category],category,read_only=True)


def render_simple_report(snapshot,state,daily):
    import streamlit as st
    if not snapshot['available']:
        st.info('집계표를 등록하면 경비별 예산·지출·잔액을 볼 수 있습니다.')
        st.button('상세보기로 자료 등록',on_click=set_view,args=(False,),key='simple_report_register')
        return
    lines=[{**r,'누적지출액':r['지출액']} for r in snapshot['rows'] if r['예산현액'] is not None]
    if len(lines)!=len(snapshot['rows']):
        st.warning('이전 저장자료에 경비별 예산이 없습니다. 집계표를 다시 반영해 주세요.')
    if lines:render_scoped_execution(lines,state,daily,snapshot['as_of'],'원',prefix='simple_report_expense',simple=True,allow_leaf=snapshot.get('exact_date',True))
    else:render_summary(snapshot)
    st.download_button('전체 집행현황 내려받기',csv_data(snapshot['rows']),'예산집행현황.csv','text/csv',key='simple_report_download')


def render_simple_import(daily,mapper):
    import streamlit as st
    from source_review import quality_snapshot
    stats=quality_snapshot(daily,mapper)
    cols=st.columns(3)
    cols[0].metric('등록된 지출내역',f'{len(daily):,}건')
    cols[1].metric('예산과목 확인 필요',f'{stats["unmapped"]:,}건')
    cols[2].metric('일자·금액 확인 필요',f'{stats["invalid_date"]+stats["invalid_amount"]:,}건')
    if stats['duplicates']:st.warning(f'중복 여부를 확인할 내역이 {stats["duplicates"]:,}건 있습니다.')
    st.info('지출자료 등록과 수정은 상세보기에서 진행합니다.')
    st.button('상세보기로 자료 등록·확인',type='primary',on_click=set_view,args=(False,),key='simple_import_register')


def render_simple_budget(state):
    import streamlit as st
    import pandas as pd
    model=build_model(state)
    st.metric('등록 편성액',money(model['total']))
    st.dataframe(pd.DataFrame([{'구분':r['stage'],'예산액':r['total'],'증감액':r['delta']} for r in model['timeline']]),hide_index=True,width='stretch')
    st.button('상세보기로 편성자료 관리',on_click=set_view,args=(False,),key='simple_budget_manage')
