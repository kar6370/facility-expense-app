"""Compare manual changes with immutable ledger references before saving."""
from collections import defaultdict
from calendar import monthrange

from account_catalog import ACCOUNT_CATALOG
from budget_engine import CATEGORY_CODES, ValidationError, expense_code, parse_date, transaction_id, won
from execution_sync import token

CANCELLED = {'취소','취소완료','삭제','미지급','미집행','반려'}


def transaction_totals(daily, year):
    totals=defaultdict(int); seen=set(); invalid=[]
    for row in daily:
        if row.get('거래상태') in CANCELLED:continue
        code=expense_code(row); dt=parse_date(row.get('집행일자'))
        try:
            identity=transaction_id(row); amount=won(row.get('집행금액'))
        except (ValidationError,ValueError,TypeError):
            invalid.append((dt.month if dt and dt.year==year else None,code));continue
        if dt and dt.year!=year:continue
        if not dt or not code:
            invalid.append((dt.month if dt else None,code))
            if dt and identity not in seen:totals[dt.month,'']+=amount;seen.add(identity)
            continue
        if identity in seen:continue
        seen.add(identity);totals[dt.month,code]+=amount
    return totals,invalid


def account_review(daily,index,changes):
    """Compare whole accounts, not one subcategory against an account total."""
    totals,invalid=transaction_totals(daily,index['year']);result=[];references=[]
    for (month,code),delta in sorted(changes.items()):
        ref=index.get('monthly',{}).get(month,{}).get(code)
        before=totals[month,code]; after=before+delta
        uncertain=any((m is None or m==month) and (not c or c==code) for m,c in invalid)
        conflicts=any(c['month']==month and c['code']==code and c['kind']=='monthly' for c in index.get('conflicts',[]))
        partial=bool(ref and parse_date(ref['as_of']) and parse_date(ref['as_of']).day<monthrange(index['year'],month)[1])
        status=('세목 확인 필요' if not code else '원장자료 없음' if not ref else
                '자료 확인 필요' if uncertain or partial or conflicts else
                '금액 일치' if after==ref['amount'] else '차액 확인 필요')
        result.append({'월':f'{month}월','세목':ACCOUNT_CATALOG.get(code,('',code or '미확인'))[1],
            '원장 지출액':ref['amount'] if ref else None,'변경 전 자료 합계':before,
            '입력 반영 후 합계':after,'차액':after-ref['amount'] if ref else None,'확인 상태':status})
        references.append(ref.get('token') if ref else None)
    return result,references


def review_adjustments(updates,records,daily,index,evidence=None):
    lookup={(int(r['year']),r['category'],int(r['month'])):won(r['amount']) for r in records}
    changes=[];deltas=defaultdict(int)
    seen=set()
    for year,category,month,value in updates:
        key=(int(year),category,int(month))
        if key not in lookup or key in seen:raise ValidationError('연도·항목·월을 확인해 주세요.')
        if value is None or isinstance(value,str) and not value.strip():raise ValidationError('빈 금액은 저장할 수 없습니다. 0원은 숫자 0을 입력하세요.')
        seen.add(key);amount=won(value);delta=amount-lookup[key]
        if not delta:continue
        if evidence is None:
            code=CATEGORY_CODES.get(category,'')
        else:
            matching=[r for r in evidence if r['year']==key[0] and r['category']==category and not r['row'].get('예산세목확인필요')]
            this_month=[r for r in matching if r['month']==key[2]]
            codes={expense_code(r['row']) for r in (this_month or matching)}-{''}
            code=next(iter(codes)) if len(codes)==1 else ''
        changes.append({'year':key[0],'category':category,'month':key[2],'before':lookup[key],'after':amount,'delta':delta,'code':code})
        if key[0]==index['year']:
            deltas[key[2],code]+=delta
    rows,refs=account_review(daily,index,deltas)
    return {'changes':changes,'rows':rows,'token':token([changes,rows,refs]),
            'needs_confirmation':any(r['확인 상태']!='금액 일치' for r in rows)}


def saved_adjustment_issues(daily,index):
    affected={}
    for row in daily:
        dt=parse_date(row.get('집행일자'))
        if row.get('업로드구분')=='수동집계조정' and dt and dt.year==index['year']:
            affected[dt.month,expense_code(row)]=0
    rows,_=account_review(daily,index,affected)
    return [r for r in rows if r['확인 상태']!='금액 일치']


def render_adjustment_review(review,prefix):
    import pandas as pd
    import streamlit as st
    if not review['rows']:return review['token']
    st.caption('같은 세목의 지출내역을 모두 합산해 원장과 비교합니다. 예산·지출·잔액 요약은 원장 금액을 유지합니다.')
    st.dataframe(pd.DataFrame(review['rows']),hide_index=True,width='stretch',
        column_config={k:st.column_config.NumberColumn(format='%,d원') for k in
                       ['원장 지출액','변경 전 자료 합계','입력 반영 후 합계','차액']})
    if review['needs_confirmation']:
        st.warning('입력한 금액이 원장과 다르거나 확인할 자료가 부족합니다. 차액을 확인한 뒤 저장해 주세요.')
        checked=st.checkbox('차이와 확인할 내용을 확인했습니다. 입력한 금액을 별도로 저장합니다.',key=prefix+'_ack_'+review['token'])
        return review['token'] if checked else None
    st.success('입력 반영 후 합계가 원장 지출액과 일치합니다.')
    return review['token']


def render_saved_adjustment_warning(daily,index):
    import pandas as pd
    import streamlit as st
    rows=saved_adjustment_issues(daily,index)
    if rows:
        st.warning(f'직접 입력한 내역 중 {len(rows)}개 월·세목은 금액 확인이 필요합니다. 예산·지출·잔액 요약은 원장 금액입니다.')
        with st.expander('확인할 금액 보기'):
            st.dataframe(pd.DataFrame(rows),hide_index=True,width='stretch')
    return rows


def review_report_changes(before,after,index):
    """Plans and prior-year comparisons remain editable without a spending alert."""
    audit=before.get('source_audit',{})
    month=int(audit.get('base_month') or before.get('meta',{}).get('base_month',1))
    source=before.get('execution_sources',{}).get(f"{index['year']}-{month:02d}",{})
    snapshot=source.get('report_snapshot',{})
    rows=[]
    def compare(label,name,old,new,reference):
        if won(old)==won(new):return
        value=won(new)
        rows.append({'구분':label,'항목':name,'기존금액':won(old),'입력금액':value,
            '원장 기준금액':reference,'차액':value-reference if reference is not None else None,
            '확인 상태':'원장자료 없음' if reference is None else '금액 일치' if value==reference else '차액 확인 필요'})
    for field in ('예산액','예산현액','예산배정액'):
        compare('총괄',field,before.get('totals',{}).get(field,0),after.get('totals',{}).get(field,0),snapshot.get('totals',{}).get(field))
    for section,key,fields in [('lines','세목코드',('예산배정액','누적지출액')),
                               ('business','사업키',('예산액','예산현액','예산배정액','누적지출액'))]:
        old={str(r[key]):r for r in before.get(section,[])}
        new={str(r[key]):r for r in after.get(section,[])}
        frozen={str(r[key]):r for r in snapshot.get(section,[])}
        for identity in set(old)|set(new):
            for field in fields:
                reference=frozen.get(identity,{}).get(field)
                if section=='lines' and field=='누적지출액':reference=source.get('cumulative',{}).get(identity,reference)
                name=(new.get(identity) or old[identity]).get('세목명',identity)
                compare('세목별' if section=='lines' else '예산구분별',name+' · '+field,old.get(identity,{}).get(field,0),new.get(identity,{}).get(field,0),reference)
    old={r['월']:r for r in before.get('monthly',[])}
    codes={r['세목코드'] for r in before.get('lines',[]) if r['세목코드']!='802-11'}
    for r in after.get('monthly',[]):
        refs=index.get('monthly',{}).get(int(r['월']),{})
        reference=sum(refs[c]['amount'] for c in codes) if codes and codes.issubset(refs) else None
        compare('월별',f"{r['월']}월 지출액",old.get(r['월'],{}).get('지출액',0),r.get('지출액',0),reference)
    rows.sort(key=lambda r:(r['구분'],r['항목']))
    return {'rows':rows,'needs_confirmation':any(r['확인 상태']!='금액 일치' for r in rows),
            'token':token([rows,source,index])}


def render_report_change_review(review):
    import pandas as pd
    import streamlit as st
    if not review['rows']:return review['token']
    st.dataframe(pd.DataFrame(review['rows']),hide_index=True,width='stretch')
    if review['needs_confirmation']:
        st.warning('입력한 금액이 원장과 다르거나 대조할 자료가 없습니다. 원장 금액을 확인한 뒤 저장해 주세요. 홈과 간편보기의 금액은 원장 기준을 유지합니다.')
        return review['token'] if st.checkbox('차이를 확인했습니다. 이 보고서에 입력금액을 별도로 저장합니다.',key='report_edit_ack_'+review['token']) else None
    st.success('변경한 금액이 원장과 일치합니다.')
    return review['token']
