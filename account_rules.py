"""Official source accounts, independent of optional management categories."""
import re
import unicodedata

from account_catalog import ACCOUNT_CATALOG
ACCOUNT_NAMES = {name: code for code, (_, name) in ACCOUNT_CATALOG.items()}
ACCOUNT_NAMES.update({
    '공무직(무기계약)근로자등여비': '202-08',
    '공무직(무기계약직)근로자보수': '101-03',
    '공무직(무기계약직)근로자평가급등': '109-02',
})


def source_text(value):
    if value is None:
        return ''
    text = str(value).strip()
    return '' if text.lower() in ('nan', 'nat', 'none', '<na>', '#cspan', '#rspan') else text


def normalized(value):
    text = unicodedata.normalize('NFKC', source_text(value))
    text = re.sub(r'[‐‑‒–—−﹣]', '-', text)
    text = re.sub(r'[\s\u200b\ufeff]+', '', text)
    # Numeric Excel cells can reach pandas as 215.0 or 20101.0.
    return re.sub(r'^(\d{3}|\d{5})\.0+$', r'\1', text)


def account_result(value):
    text = normalized(value)
    if not text:
        return {'code': '', 'basis': '원본 없음', 'candidates': []}
    codes = {f'{a}-{b}' for a, b in re.findall(r'(?<!\d)(\d{3})[-./](\d{2})(?!\d)', text)}
    for raw in re.findall(r'(?<!\d)(\d{5})(?!\d)', text):
        candidate = raw[:3] + '-' + raw[3:]
        if candidate in ACCOUNT_CATALOG:
            codes.add(candidate)
    codes.update(re.findall(r'(?<!\d)(215|233)(?!\d|[-./]\d)', text))
    if len(codes) > 1:
        return {'code': '', 'basis': '서로 다른 세목코드', 'candidates': sorted(codes)}
    if codes:
        return {'code': next(iter(codes)), 'basis': '원본 세목코드', 'candidates': sorted(codes)}
    found = []
    for name in sorted(ACCOUNT_NAMES, key=len, reverse=True):
        if name == '보수' and text != '보수' and not re.search(r'(?:^|[\]\-:›>])보수(?:$|[\[(])', text):
            continue
        if name in text and not any(name in longer for longer in found):
            found.append(name)
    named_codes = {ACCOUNT_NAMES[name] for name in found}
    if len(named_codes) == 1:
        return {'code': next(iter(named_codes)), 'basis': '원본 세목명', 'candidates': sorted(named_codes)}
    return {'code': '', 'basis': '서로 다른 세목명' if named_codes else '세목코드·명칭 확인 필요', 'candidates': sorted(named_codes)}


def account_code(value):
    return account_result(value)['code']


def is_account_cell(value):
    text = normalized(value)
    result = account_result(value)
    return bool(result['code'] or result['candidates']) and bool(
        re.search(r'\d{3}[-./]\d{2}|[\[【(]\d{3,5}[\]】)]', text)
        or re.fullmatch(r'\d{3,5}(?:\.0+)?', text)
        or any(name in text for name in ACCOUNT_NAMES)
    )


def source_account(row):
    for key in ('원본I열', '예산과목', '세목코드', '예산과목코드', '세목'):
        raw = source_text(row.get(key))
        if not raw:
            continue
        result = account_result(raw)
        if result['code']:
            return result['code'], key, raw
        if result['candidates'] or key == '원본I열':
            return '', key, raw
    return '', '', ''


def account_display(row):
    code, origin, raw = source_account(row)
    result = account_result(raw)
    return {'예산세목 상태': '확인 완료' if code else '확인 필요',
            '인식 세목코드': code,
            '인식 세목명': ACCOUNT_CATALOG.get(code, ('', ''))[1],
            '인식 근거': ('I열' if origin == '원본I열' else origin) + (' · ' + result['basis'] if origin else '원본 세목 없음'),
            '인식 원문': raw}
