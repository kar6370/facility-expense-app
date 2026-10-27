"""Read-only execution views separated by expense scope and actual budget leaf."""
import copy
import csv
import io
from calendar import monthrange

from budget_engine import build_model, parse_date, reconciliation, won

SCOPES = ('일상경비', '운영경비')


def report_scope_date(config):
    audit=config.get('source_audit',{})
    explicit=parse_date(audit.get('as_of'))
    if explicit:return explicit.isoformat()
    year=int(config['meta'].get('year',2026))
    month=int(audit.get('base_month') or config['meta'].get('base_month',1))
    return f'{year}-{month:02d}-{monthrange(year,month)[1]:02d}'


def valid_parts(row, parts):
    if not isinstance(parts,list) or not parts or any(not isinstance(p,dict) or p.get('경비구분') not in SCOPES for p in parts):
        return False
    try:
        return all(sum(won(p.get(part, 0)) for p in parts) == won(row.get(total, 0))
                   for part, total in [('예산배정액','예산배정액'),('지출액','누적지출액'),('예산현액','예산현액')]
                   if total in row)
    except (ValueError, TypeError):
        return False


def scoped_accounts(lines, state=None, as_of=None):
    """Use saved per-scope source rows; never call business-budget type a scope.

    Old totals can only be expanded from a same-date ledger with exact sums.
    Unresolved rows remain visible separately and never enter either branch.
    """
    state = state or {}
    ledger = state.get('ledger_snapshots', {}).get(as_of)
    if as_of and (state.get('ledger') or {}).get('as_of') == as_of:
        ledger = state['ledger']
    result, unresolved = [], []
    for original in lines:
        row = copy.deepcopy(original)
        code = str(row.get('세목코드',''))
        parts = row.get('경비별내역', [])
        source = '보고서 경비별 원천'
        if parts and not valid_parts(row, parts):
            unresolved.append({**row, '확인 사유':'경비별 원천과 현재 합계가 다릅니다. 원천자료를 다시 확인하세요.'})
            continue
        if not parts:
            scope = row.get('경비구분') or {'entrust':'운영경비','daily':'일상경비'}.get(row.get('사업키'))
            if scope in SCOPES:
                parts = [{'경비구분':scope, '예산현액':row.get('예산현액',row.get('예산배정액',0)),
                          '예산배정액':row.get('예산배정액',0),'지출액':row.get('누적지출액',0)}]
                source = '저장된 경비 구분'
            elif ledger:
                candidates = [{'경비구분':v['scope'],'예산현액':v['current_budget'],
                               '예산배정액':v['allocated'],'지출액':v['expense']}
                              for v in ledger['lines'].values() if v['code']==code]
                if valid_parts(row,candidates):
                    parts=candidates;source='같은 기준일 원장과 금액 일치'
        if not parts:
            unresolved.append({**row,'확인 사유':'경비별 금액 근거가 없습니다. 운영·일상 구분이 있는 단월·누적 원장을 다시 반영하세요.'})
            continue
        for part in parts:
            current = won(part.get('예산현액',part.get('예산배정액',0)))
            allocation, actual = won(part.get('예산배정액',0)), won(part.get('지출액',0))
            result.append({'경비구분':part['경비구분'],'세목코드':code,'세목명':row.get('세목명',code),
                           '예산현액':current,'예산배정액':allocation,'지출액':actual,
                           '예산잔액':current-actual,'배정잔액':allocation-actual,
                           '예산 대비 집행률(%)':actual/current*100 if current else None,
                           '상태':'예산 초과' if actual>current else '미집행' if actual==0 else '집행 중',
                           '근거':source,'원천행':part.get('원천행'),
                           '반환금':code=='802-11' or bool(row.get('집행률산정제외'))})
    # A scope and account remain independent even if the account name is equal.
    grouped={}
    for row in result:
        key=(row['경비구분'],row['세목코드'])
        if key not in grouped:
            grouped[key]=row
        else:
            current=grouped[key]
            for field in ('예산현액','예산배정액','지출액','예산잔액','배정잔액'):
                current[field]+=row[field]
            current['예산 대비 집행률(%)']=current['지출액']/current['예산현액']*100 if current['예산현액'] else None
            current['상태']='예산 초과' if current['지출액']>current['예산현액'] else '미집행' if current['지출액']==0 else '집행 중'
    return {'rows':list(grouped.values()),'unresolved':unresolved}


def leaf_execution(state, daily, account, as_of):
    """Explain exactly this scope/account/cutoff, without spreading ledger totals."""
    scope,code=account['경비구분'],account['세목코드']
    model=build_model(state)
    leaves=[x for x in model['leaves'].values() if x['scope']==scope and x['code']==code]
    result={'rows':[], 'transactions':[], 'budget_stage':model['timeline'][-1]['stage'] if model['timeline'] else '',
            'registered_budget':sum(x['amount'] for x in leaves), 'official':account['지출액'],
            'missing_budget':not leaves, 'as_of':as_of, 'complete':False}
    if any(account.get(k) is None for k in ('예산현액','예산배정액','지출액')):
        result['source_error']=True
        return result
    result['budget_gap']=result['registered_budget']-account['예산현액']
    end=parse_date(as_of)
    if not end or end.year!=state['year']:
        result['date_error']=True
        return result
    context=copy.deepcopy(state)
    key=scope+'|'+code
    # Temporary source snapshot for period reconciliation; never saved to SQLite.
    context['ledger']={'as_of':as_of,'period_start':f"{state['year']}-01-01",
        'lines':{key:{'scope':scope,'code':code,'name':account['세목명'],'expense':account['지출액'],
                      'allocated':account['예산배정액'],'current_budget':account['예산현액']}}}
    data=reconciliation(context,daily)
    summary=next((r for r in data['rows'] if r['key']==key),{})
    txs=[t for t in data['transactions'] if t['eligible'] and t['scope']==scope and t['code']==code]
    # Unknown scope, date, or invalid amount can still belong to this account.
    # They prevent claiming that an unassigned leaf has a confirmed zero expense.
    ambiguous=[t for t in data['transactions'] if (t.get('code')==code or not t.get('code'))
               and (not t.get('scope') or t.get('scope')==scope)
               and t['status'] not in ('분류완료','다른 회계연도','통제원장 기준일 이후','취소·미지급 확인')
               and not t['eligible']]
    complete=bool(leaves and summary.get('complete') and result['budget_gap']==0 and not ambiguous)
    classified_ids={a['leaf'] for t in txs for a in t['allocations']}
    result.update(complete=complete,linked=summary.get('linked',0),classified=summary.get('classified',0),
                  gap=account['지출액']-summary.get('linked',0),pending_count=summary.get('pending_count',0),
                  ambiguous_count=len(ambiguous))
    for leaf in leaves:
        spent=data['leaf_spent'].get(leaf['id'],0)
        known=complete or leaf['id'] in classified_ids
        result['rows'].append({'경비구분':scope,'세목코드':code,'세목명':account['세목명'],
            '부기':leaf['label'],'편성예산':leaf['amount'],'확인된 지출액':spent if known else None,
            '확정 잔액':leaf['amount']-spent if complete else None,
            '확인 상태':'추경 원본 확인 필요' if leaf['amount']<0 else '대조 완료' if complete else '원장·분류 대조 필요',
            '원본 행':leaf['row']})
    result['transactions']=[{'지급일':t['date'],'경비구분':scope,'세목명':account['세목명'],
        '적요':t['row'].get('적요',''),'지출액':t['amount'],'분류 상태':t['status'],
        '원본파일':t['row'].get('원본파일','기존 저장자료'),'원본 행':t['row'].get('원본행')}
        for t in txs]
    return result


def csv_data(rows):
    if not rows:return b''
    text=io.StringIO();writer=csv.DictWriter(text,fieldnames=list(rows[0]));writer.writeheader()
    for row in rows:
        writer.writerow({k:"'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for k,v in row.items()})
    return text.getvalue().encode('utf-8-sig')


def amount(value, unit='원'):
    return '확인 필요' if value is None else f"{value/(1000 if unit=='천원' else 1):,.0f}{unit}"


def render_leaf_details(state,daily,account,as_of,unit='원',prefix='report_scope'):
    import pandas as pd
    import streamlit as st
    detail=leaf_execution(state,daily,account,as_of)
    st.markdown(f"**{account['경비구분']} · {account['세목명']} — 부기별 예산·지출·잔액**")
    if detail.get('source_error'):
        st.info('선택 경비·세목의 원장 기준금액이 없습니다. 지출통제원장 집계표를 등록해야 예산·지출·잔액을 대조할 수 있습니다.')
        return
    if detail['missing_budget']:
        st.info('등록된 부기별 편성자료가 없습니다. 본예산·추경 명세와 거래의 부기 분류가 등록되면 여기에 표시됩니다. 원장 집계표의 세목 총액은 위 표에서 확인할 수 있습니다.')
        return
    if detail.get('date_error'):
        st.warning('원장 조회종료일을 확인해야 부기별 지출을 대조할 수 있습니다.')
        return
    st.caption(f"편성예산: 등록된 {detail['budget_stage']} 기준 · 지출: {state['year']}-01-01 ~ {as_of}")
    if not detail['complete']:
        st.warning('부기별 지출 분류와 원장 대조가 완료되지 않았습니다. 확인된 지출만 표시하며 확정 잔액은 확인 필요로 남깁니다.')
        st.caption(f"등록 부기예산−ERP 예산현액 {amount(detail['budget_gap'],unit)} · 원장 지출−거래 합계 {amount(detail['gap'],unit)} · 미분류 {detail['pending_count']}건 · 경비·일자 확인 {detail['ambiguous_count']}건")
    frame=pd.DataFrame(detail['rows'])
    for col in ('편성예산','확인된 지출액','확정 잔액'):
        frame[col]=[amount(r[col],unit) for r in detail['rows']]
    st.dataframe(frame[['부기','편성예산','확인된 지출액','확정 잔액','확인 상태']],hide_index=True,width='stretch',height=min(500,max(160,36*len(frame)+40)))
    st.download_button('선택 세목의 부기 내역 CSV',csv_data(detail['rows']),f"{account['경비구분']}_{account['세목코드']}_부기.csv",'text/csv',key=prefix+'_leaf_csv')
    with st.expander('같은 경비·세목의 지출 내역 확인'):
        if detail['transactions']:
            st.dataframe(pd.DataFrame(detail['transactions']),hide_index=True,width='stretch')
        else:st.info('이 조회기간에 연결된 거래 상세자료가 없습니다.')


def render_scoped_execution(lines,state,daily,as_of,unit='천원',prefix='report_expense',simple=False,allow_leaf=True):
    import pandas as pd
    import streamlit as st
    st.markdown('**경비를 선택하면 해당 예산·지출·잔액만 표시합니다.**')
    controls=st.columns([3,1])
    scope=controls[0].segmented_control('경비 구분',list(SCOPES)+['전체'],default='일상경비',key=prefix+'_scope') or '일상경비'
    default_date=parse_date(as_of)
    if default_date and not simple:
        as_of=controls[1].date_input('부기 지출 대조 기준일',value=default_date,
            min_value=default_date.replace(month=1,day=1),max_value=default_date.replace(month=12,day=31),
            key=prefix+'_asof_'+as_of,format='YYYY-MM-DD',
            help='원장의 실제 조회 종료일과 같게 선택하세요. 기본값은 보고 기준월말입니다. 세목 금액은 업로드 원장값을 유지하고 선택일까지의 거래와 대조합니다.').isoformat()
    parsed=scoped_accounts(lines,state,as_of)
    if parsed['unresolved']:
        st.warning(f"경비별 금액 근거가 없는 과목 {len(parsed['unresolved'])}개가 있습니다. 아래 합계에는 경비 구분이 확인된 항목만 포함합니다. 자료 등록에서 경비 구분이 포함된 단월·누적 파일을 다시 반영해 주세요.")
    if simple:
        include_returns,status,search=True,'전체',''
    else:
        filters=st.columns([1,1,2])
        include_returns=filters[0].checkbox('대행사업비반환금 포함',value=False,key=prefix+'_returns')
        status=filters[1].selectbox('집행상태',['전체','미집행','집행 중','예산 초과'],key=prefix+'_status')
        search=filters[2].text_input('세목코드·세목명 검색',key=prefix+'_search',placeholder='예: 지급수수료, 사무관리비')
    rows=[r for r in parsed['rows'] if (scope=='전체' or r['경비구분']==scope) and (include_returns or not r['반환금'])
          and (status=='전체' or r['상태']==status) and (not search.strip() or search.strip().lower() in (r['세목코드']+' '+r['세목명']).lower())]
    for col,label in zip(st.columns(4),['예산현액','지출액','예산잔액','배정잔액']):
        col.metric(f'{scope} {label}',amount(sum(r[label] for r in rows),unit) if rows else '자료 없음')
    st.caption('예산잔액 = 예산현액 − 지출액 · 배정잔액 = 예산배정액 − 지출액')
    if rows:
        frame=pd.DataFrame(rows)
        for col in ['예산현액','예산배정액','지출액','예산잔액','배정잔액']:
            frame[col]=frame[col].map(lambda v:amount(v,unit))
        frame['예산 대비 집행률(%)']=[f"{r['예산 대비 집행률(%)']:.2f}%" if r['예산 대비 집행률(%)'] is not None else '산정 불가' for r in rows]
        columns=['경비구분','세목명','예산현액','지출액','예산잔액'] if simple else ['경비구분','세목코드','세목명','예산현액','예산배정액','지출액','예산잔액','배정잔액','예산 대비 집행률(%)']
        st.dataframe(frame[columns],hide_index=True,width='stretch',height=min(570,max(160,36*len(rows)+40)))
        st.download_button('선택 경비의 예산·지출·잔액 CSV',csv_data(rows),f'{scope}_예산집행_{as_of}.csv','text/csv',key=prefix+'_csv')
        options={r['경비구분']+'|'+r['세목코드']:r for r in rows}
        # Scope/filter changes must never keep the previous branch's selection.
        key=prefix+'_account_'+scope
        if st.session_state.get(key) not in options:st.session_state.pop(key,None)
        selected=st.selectbox('부기별 내역을 볼 세목',list(options),format_func=lambda k:options[k]['경비구분']+' · '+options[k]['세목명'],key=key)
        if allow_leaf:
            render_leaf_details(state,daily,options[selected],as_of,unit,prefix+'_detail_'+selected)
        else:
            st.info('등록자료에 정확한 조회 종료일이 없습니다. 부기별 내역은 상세보기에서 날짜를 확인한 뒤 볼 수 있습니다.')
    else:st.info('선택한 경비·조건에 맞는 원장 내역이 없습니다.')
    if parsed['unresolved']:
        with st.expander('경비 구분 확인이 필요한 기존 금액'):
            st.dataframe(pd.DataFrame([{'세목명':r.get('세목명'),'예산배정액(원)':r.get('예산배정액'),'지출액(원)':r.get('누적지출액'),'확인 사유':r['확인 사유']} for r in parsed['unresolved']]),hide_index=True,width='stretch')
