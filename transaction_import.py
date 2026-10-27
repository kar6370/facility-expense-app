"""Traceable ERP detail parsing and explicit revision review, with no UI dependency."""
import copy
import re
from datetime import date, datetime
from collections import Counter, defaultdict

from budget_engine import ValidationError, norm, won, parse_date, transaction_id, transaction_signature
from service_rules import special_category
from account_rules import is_account_cell, account_code, account_display, source_account


def cell(value):
    if value is None or str(value).strip() in ("nan", "NaT", "None", "#cspan", "#rspan"):
        return ""
    return str(value).strip()


HEADERS = {
    "지급월": ["지급월", "지급년월", "지급연월", "지출월", "집행월"],
    "집행일자": ["지급일자", "지급일", "지급명령일자", "지급명령일", "지출일자", "지출일", "집행일자", "집행일", "날짜", "일자"],
    "적요": ["적요", "문서제목", "문서명", "제목", "결의제목", "건명", "품명", "내용"],
    "집행금액": ["지급명령금액", "지급명령액", "지급금액", "지출금액", "지출액", "집행금액", "집행액", "지급액", "결재금액", "금액"],
    "예산과목": ["예산과목", "예산항목", "과목명", "예산정보", "예산과목명", "예산목세목", "계정과목"],
    "세목": ["세목코드", "예산세목", "세목", "세목명", "예산과목코드"],
    "사업구분": ["사업구분", "경비구분", "세부사업명", "세부사업"],
    "거래ID": ["거래ID", "transaction_id"],
    "지출결의번호": ["지출결의번호", "결의번호"],
    "지급명령번호": ["지급명령번호"],
    "문서번호": ["문서번호"],
    "명세번호": ["명세번호", "명세순번", "상세순번", "내역순번"],
    "거래처": ["거래처명", "거래처", "채주명", "채주"],
    "거래상태": ["거래상태", "지급상태", "처리상태"],
    "회계연도": ["회계연도", "회계년도"],
}


def header_map(values):
    vals = [norm(cell(v)) for v in values]
    mapped = {}
    for key, aliases in HEADERS.items():
        # Exact matches before annotated headings; do not mistake balance or contract for payment.
        for alias in aliases:
            hits = [i for i, v in enumerate(vals) if v == norm(alias)]
            if not hits:
                hits = [i for i, v in enumerate(vals) if v.startswith(norm(alias)+"(")]
            if hits:
                mapped[key] = hits[0]; break
    return mapped


def payment_month(value):
    """Numeric 2 means February, never Unix nanoseconds/January 1970."""
    if isinstance(value, (date, datetime)):
        return value.month
    s = cell(value)
    if re.fullmatch(r"\d{1,2}(?:\.0+)?", s):
        n = int(float(s))
        return n if 1 <= n <= 12 else 0
    packed = re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])(?:\.0+)?", s)
    if packed:
        return int(packed[1])
    m = re.search(r"(?:20\d{2}\s*년\s*)?(\d{1,2})\s*월", s)
    if not m:
        m = re.search(r"20\d{2}[./-]\s*(\d{1,2})(?:\D|$)", s)
    return int(m[1]) if m and 1 <= int(m[1]) <= 12 else 0


def excel_column(index):
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _records(frame):
    return frame.values.tolist() if hasattr(frame, "values") else frame


def _header_candidates(records):
    """Single headings and two-level ERP headings, without guessing money values."""
    for index, values in enumerate(records[:100]):
        yield index, header_map(values)
        if index == 0:
            continue
        upper, combined, last_parent = records[index-1], [], ""
        for col, value in enumerate(values):
            parent = cell(upper[col]) if col < len(upper) else ""
            if parent:
                last_parent = parent
            child = cell(value)
            if norm(child) in ("일자", "일", "금액", "액", "번호", "월") and last_parent:
                combined.append(last_parent + child)
            else:
                combined.append(child or parent)
        yield index, header_map(combined)


def inspect_layout(frame, kind="general"):
    records = _records(frame)
    best, score = (-1, {}), -1
    for index, cols in _header_candidates(records):
        # Do not take a title row containing just a word such as 'amount'.
        required = {"적요", "집행금액"}
        has_time = "집행일자" in cols or kind == "special" and "지급월" in cols
        current = 20 * len(required.intersection(cols)) + 20 * has_time + len(cols)
        if current > score:
            best, score = (index, cols), current
        if required.issubset(cols) and has_time:
            return index, cols
    index, cols = best
    # The original B/K layout may have a nonstandard title header. It still
    # needs an explicitly named payment amount column, never the largest value.
    if kind == "special" and "집행금액" in cols:
        sample = records[index+1:index+11]
        if any(len(r) > 10 and special_category(r[10]) for r in sample):
            cols = dict(cols)
            cols.setdefault("적요", 10)
            if "집행일자" not in cols and "지급월" not in cols:
                cols["지급월"] = 1
    if kind == "general" and score < 40 and any(len(r) >= 3 and parse_date(r[0]) for r in records):
        return -1, {"집행일자": 0, "적요": 1, "집행금액": 2}
    return index, cols


def payment_date(value):
    dt = parse_date(value)
    if dt:
        return dt
    # Only called for a column identified as the payment date.
    if isinstance(value, (int, float)) and 20000 <= value <= 80000:
        from openpyxl.utils.datetime import from_excel
        return from_excel(value).date()
    return None


def analyze_frame(frame, kind="general", *, year=2026, sheet="", filename="", fingerprint="",
                  mapper=None, columns=None, header_row=None):
    records = _records(frame)
    detected_row, detected = inspect_layout(frame, kind)
    header = detected_row if header_row is None else int(header_row)
    explicit_columns = columns is not None
    columns = dict(detected) if columns is None else {k: int(v) for k, v in columns.items() if v is not None}
    # A numeric amount or supplier reference can coincidentally equal a code.
    # Do not reinterpret an explicitly identified non-account column as I.
    i_owned_elsewhere = any(index == 8 for field, index in columns.items() if field not in ("예산과목", "세목"))
    i_evidence = any(len(r) > 8 and is_account_cell(r[8])
                     and not re.fullmatch(r"\d{3,5}(?:\.0+)?", cell(r[8]))
                     for r in records[max(0, header+1):])
    if kind == "general" and not explicit_columns and not i_owned_elsewhere and i_evidence:
        columns["예산과목"] = 8
    missing = [key for key in ("적요", "집행금액") if key not in columns]
    if "집행일자" not in columns and (kind != "special" or "지급월" not in columns):
        missing.append("지급일자 또는 지급월" if kind == "special" else "집행일자")
    result = {"rows": [], "columns": columns, "header_row": header, "missing": missing,
              "excluded": [], "candidate_count": 0}
    if missing:
        return result
    if header < -1 or header >= len(records):
        result["missing"] = ["열 제목 행"]
        return result
    for position in range(header + 1, len(records)):
        values = records[position]
        get = lambda col: values[col] if col is not None and 0 <= col < len(values) else None
        fields = {k: cell(get(i)) for k, i in columns.items()}
        desc = fields.get("적요", "")
        if not desc or norm(desc) in ("합계", "총계", "소계", "총합계", "적요", "문서제목"):
            continue
        # Repeated ERP page headers are not transactions.
        if {"적요", "집행금액"}.issubset(header_map(values)):
            continue
        result["candidate_count"] += 1
        original_row = position + 1
        if hasattr(frame, "index"):
            try:
                original_row = int(frame.index[position]) + 1
            except (ValueError, TypeError):
                pass
        row = {**fields, "적요": desc, "원본파일": filename, "원본시트": sheet,
               "원본행": original_row, "원본해시": fingerprint}
        if columns.get("예산과목") == 8 or columns.get("세목") == 8:
            row["원본I열"] = cell(get(8))
            row["예산과목원본열"] = "I"
            row["예산과목코드"] = account_code(get(8))
        category = special_category(desc) if kind == "special" else ""
        if kind == "special" and not category:
            result["excluded"].append({"원본 행": original_row, "적요": desc,
                                       "제외 사유": "정기지출·세탁용역 대상 외 거래", "원본 금액": fields.get("집행금액", "")})
            continue
        errors = []
        raw_amount = get(columns.get("집행금액"))
        try:
            if not cell(raw_amount):
                raise ValidationError("지출액이 비어 있습니다")
            row["집행금액"] = won(raw_amount)
        except ValidationError as exc:
            row["집행금액"] = cell(raw_amount)
            errors.append(str(exc))
        date_raw = get(columns.get("집행일자"))
        dt = payment_date(date_raw)
        if kind == "special":
            month_raw = get(columns.get("지급월"))
            month = payment_month(month_raw)
            if cell(date_raw) and not dt:
                errors.append("선택한 지급일자를 날짜로 읽을 수 없습니다")
            if cell(month_raw) and not month:
                errors.append("지급월을 1~12월로 확인해 주세요")
            if not month and not cell(month_raw):
                month = dt.month if dt else payment_month(desc)
            if not month:
                errors.append("지급일자 또는 지급월을 확인해 주세요")
            source_year = (re.search(r"(20\d{2})", cell(month_raw))
                           or re.search(r"(20\d{2})", fields.get("회계연도", "")))
            y = int(source_year[1]) if source_year else dt.year if dt else int(year)
            if dt and (dt.year != y or dt.month != month):
                errors.append("지급일자와 지급월이 다릅니다")
            row["집행일자"] = dt.isoformat() if dt else f"{y}-{month:02d}-01" if month else ""
            row["일자근거"] = "지급일자" if dt else "지급월(일자미상)"
            row["지급월"] = cell(month_raw)
            row["업로드구분"] = "5대용역수수료"
            row["관리항목"] = category
            row.setdefault("세목", "")
            row.setdefault("예산과목", "")
            if not row["세목"] and not row["예산과목"]:
                row["예산세목확인필요"] = True
        else:
            if not dt:
                errors.append("지급일자 확인 필요(결의일자를 지급일로 대신 사용하지 않음)")
            row["집행일자"] = dt.isoformat() if dt else fields.get("집행일자", "")
            b = cell(get(1))
            if not row.get("세목") and re.fullmatch(r"\d{3}(?:-\d{2})?", b):
                row["세목"] = b
            row.setdefault("세목", row.get("예산과목", ""))
            row.setdefault("예산과목", "")
            if mapper and not errors:
                mapped = mapper(desc, row["세목"], row["예산과목"], row["집행금액"])
                if mapped:
                    row["관리항목"] = mapped
        if errors:
            row["검증오류"] = "; ".join(dict.fromkeys(errors))
        row.update(account_display(row))
        if source_account(row)[0]:
            row.pop("예산세목확인필요", None)
        result["rows"].append(row)
    return result


def parse_frame(frame, kind="general", **kwargs):
    return analyze_frame(frame, kind, **kwargs)["rows"]


SOURCE_FIELDS = ("원본I열", "예산과목", "세목", "예산과목코드", "세목코드", "예산과목원본열",
                 "원본파일", "원본시트", "원본행", "원본해시", "거래ID", "transaction_id",
                 "지출결의번호", "지급명령번호", "문서번호", "명세번호", "상세순번")


def _payment_key(row):
    dt = parse_date(row.get("집행일자"))
    return (dt.isoformat() if dt else cell(row.get("집행일자")), norm(row.get("적요")),
            won(row.get("집행금액", 0)), norm(row.get("사업구분")),
            norm(row.get("거래처", row.get("채주", ""))), cell(row.get("거래상태")),
            cell(row.get("일자근거")))


def _identity_conflict(a, b):
    for keys in (("거래ID", "transaction_id"), ("지출결의번호", "지급명령번호", "문서번호"), ("명세번호", "상세순번")):
        av = next((cell(a.get(k)) for k in keys if cell(a.get(k))), "")
        bv = next((cell(b.get(k)) for k in keys if cell(b.get(k))), "")
        if av and bv and av != bv:
            return True
    return False


def _same_source(a, b):
    physical = all(cell(a.get(k)) and cell(a.get(k)) == cell(b.get(k)) for k in ("원본파일", "원본시트", "원본행"))
    erp = any(cell(a.get(k)) and cell(a.get(k)) == cell(b.get(k)) for k in ("지출결의번호", "지급명령번호", "문서번호"))
    return physical or erp


def _source_refresh(before, row):
    has_official = bool(source_account(row)[0])
    return has_official and _payment_key(before) == _payment_key(row) and any(
        cell(row.get(k)) and cell(row.get(k)) != cell(before.get(k))
        for k in SOURCE_FIELDS if not k.startswith("원본") or k == "원본I열" or not cell(before.get(k)))


def review_import(existing, incoming):
    """No mutation. Stable-ID revisions must be explicitly selected at commit."""
    old = {transaction_id(r): r for r in existing}
    old_counts = Counter(transaction_id(r) for r in existing)
    payments, incoming_counts = defaultdict(list), Counter()
    for row in existing:
        try:
            payments[_payment_key(row)].append(row)
        except (ValidationError, ValueError, TypeError):
            pass
    for row in incoming:
        if not row.get("검증오류"):
            try:
                incoming_counts[_payment_key(row)] += 1
            except (ValidationError, ValueError, TypeError):
                pass
    items, seen = [], {}
    signatures = {}
    for row in incoming:
        if row.get("검증오류"):
            items.append({"status":"오류 · 반영 제외", "row":row, "id":"", "before":None, "delta":0}); continue
        try:
            tid, signature = transaction_id(row), transaction_signature(row)
        except ValidationError as exc:
            items.append({"status":"오류 · 반영 제외", "row":{**row,"검증오류":str(exc)}, "id":"", "before":None, "delta":0}); continue
        before = old.get(tid)
        ambiguous, matches = False, []
        if old_counts[tid] > 1:
            matches = [r for r in existing if transaction_id(r) == tid]
            ambiguous = True
        if before is None:
            matches = [r for r in payments[_payment_key(row)] if not _identity_conflict(r, row)]
            if matches:
                if len(matches) == 1 and incoming_counts[_payment_key(row)] == 1 and _same_source(matches[0], row):
                    before, tid = matches[0], transaction_id(matches[0])
                else:
                    ambiguous = True
        if ambiguous or old_counts[tid] > 1:
            status = "기존 거래 대조 필요"
        elif tid in seen:
            status = "파일 내부 중복" if signatures[tid] == signature else "파일 내부 충돌"
            if status == "파일 내부 충돌":
                items[seen[tid]]["status"] = status
        elif before:
            status = "분류정보 보완" if _source_refresh(before, row) else "기존 중복" if transaction_signature(before) == signature else "수정 후보"
        else:
            status = "신규"
        item = {"status":status, "row":row, "id":tid, "before":before,
                "match_candidates": matches if ambiguous else [],
                "delta":won(row["집행금액"])-(won(before["집행금액"]) if before else 0)}
        if tid not in seen: seen[tid], signatures[tid] = len(items), signature
        items.append(item)
    return items


def commit_import(existing, review, replace_ids=()):
    if any(x["status"] in ("오류 · 반영 제외", "파일 내부 충돌", "기존 거래 대조 필요") for x in review):
        raise ValidationError("금액·지급월 오류 또는 동일 식별번호의 상충 행을 해결한 뒤 반영해 주세요.")
    result = copy.deepcopy(existing)
    indexes = {transaction_id(r): i for i,r in enumerate(result)}
    count = Counter()
    for x in review:
        row = copy.deepcopy(x["row"])
        if x["status"] == "신규" and x["id"] not in indexes:
            indexes[x["id"]] = len(result); result.append(row); count["added"] += 1
        elif x["status"] == "분류정보 보완":
            before = result[indexes[x["id"]]]
            updated = copy.deepcopy(before)
            updated.update({k: row[k] for k in SOURCE_FIELDS if cell(row.get(k))})
            updated["원천연계키"] = x["id"]
            updated.update(account_display(updated))
            if source_account(updated)[0]:
                updated.pop("예산세목확인필요", None)
            updated["원천수정번호"] = before.get("원천수정번호", 0) + 1
            updated["수정이력"] = before.get("수정이력", []) + [{"수정시각": datetime.now().isoformat(timespec="seconds"),
                "사유": "I열·예산세목 원천정보 보완(금액 유지)",
                "이전값": {k: v for k, v in before.items() if k != "수정이력"}}]
            result[indexes[x["id"]]] = updated
            count["refreshed"] += 1
        elif x["status"] == "수정 후보" and x["id"] in replace_ids:
            before = result[indexes[x["id"]]]
            row["원천연계키"] = x["id"]
            row["원천수정번호"] = before.get("원천수정번호", 0) + 1
            row["수정이력"] = before.get("수정이력", []) + [{"수정시각":datetime.now().isoformat(timespec="seconds"),
                "이전값":{k:v for k,v in before.items() if k != "수정이력"}}]
            result[indexes[x["id"]]] = row; count["replaced"] += 1
    return sorted(result, key=lambda r:str(r.get("집행일자", "")), reverse=True), dict(count)
