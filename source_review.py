"""Source quality evidence; an official account is distinct from a chart label."""
from collections import defaultdict
import re

from account_rules import source_account, account_display
from budget_engine import expense_code, parse_date, transaction_id, won, ValidationError


def quality_details(rows, mapper):
    details, groups, identities = [], defaultdict(list), defaultdict(list)
    for index, row in enumerate(rows):
        try:
            amount = won(row.get("집행금액", 0))
            bad_amount = False
        except (ValidationError, TypeError, ValueError):
            amount, bad_amount = None, True
        code = source_account(row)[0]
        category = mapper(row) if not bad_amount else None
        dt = parse_date(row.get("집행일자"))
        official, source, raw = source_account(row)
        reason = ""
        if bad_amount:
            reason = "금액 확인 필요"
        elif not dt:
            reason = "지급일 확인 필요"
        elif not official:
            reason = "예산과목 확인 필요"
        status = "확인 필요" if reason else "관리항목 연계" if category else "예산과목 확인"
        record = {"원천번호":index, "판정":status, "확인 사유":reason,
                  **account_display(row),
                  "지급일":row.get("집행일자", ""), "적요":row.get("적요", ""), "지출액":amount,
                  "세목코드":code, "예산과목(원본)":raw or row.get("예산과목") or row.get("세목", ""),
                  "과목 인식근거":"I열" if source == "원본I열" or row.get("예산과목원본열") == "I" else source,
                  "관리항목":category or "", "거래ID":row.get("거래ID", ""),
                  "결의번호":row.get("지출결의번호", ""), "지급명령번호":row.get("지급명령번호", ""),
                  "명세번호":row.get("명세번호", ""), "거래처":row.get("거래처", ""),
                  "원본 파일":row.get("원본파일", ""), "원본 시트":row.get("원본시트", ""),
                  "원본 행":row.get("원본행", ""), "중복후보 그룹":"", "중복 판정":""}
        details.append(record)
        try:
            identities[transaction_id(row)].append(index)
        except (ValidationError, TypeError, ValueError):
            pass
        if amount is not None:
            key = (dt.isoformat() if dt else str(row.get("집행일자", "")),
                   re.sub(r"\s+", "", str(row.get("적요", ""))).lower(), amount, code,
                   str(row.get("사업구분", "")), str(row.get("거래처", "")))
            groups[key].append(index)
    parents = list(range(len(rows)))
    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    for indexes in [*groups.values(), *identities.values()]:
        for index in indexes[1:]:
            parents[root(index)] = root(indexes[0])
    combined = defaultdict(list)
    for index in range(len(rows)):
        combined[root(index)].append(index)
    duplicate_groups = 0
    for indexes in combined.values():
        if len(indexes) < 2:
            continue
        duplicate_groups += 1
        try:
            ids = {transaction_id(rows[i]) for i in indexes}
        except (ValidationError, TypeError, ValueError):
            ids = set()
        content = {(details[i]["지급일"], details[i]["적요"], details[i]["지출액"]) for i in indexes}
        if len(ids) == 1:
            note = "동일 식별번호 · 내용 상충 확인" if len(content) > 1 else "동일 식별 거래 반복"
        else:
            note = "별도 식별번호 · 실제 별건인지 확인"
        for index in indexes:
            details[index]["중복후보 그룹"] = f"그룹 {duplicate_groups}"
            details[index]["중복 판정"] = note
    return details


def quality_snapshot(rows, mapper):
    details = quality_details(rows, mapper)
    return {"mapped":sum(bool(r["관리항목"]) for r in details),
            "official_classified":sum(bool(r["세목코드"]) for r in details),
            "account_only":sum(r["판정"] == "예산과목 확인" for r in details),
            "unmapped":sum(r["확인 사유"] == "예산과목 확인 필요" for r in details),
            "invalid_date":sum(r["확인 사유"] == "지급일 확인 필요" for r in details),
            "invalid_amount":sum(r["확인 사유"] == "금액 확인 필요" for r in details),
            "duplicates":sum(bool(r["중복후보 그룹"]) for r in details),
            "duplicate_groups":len({r["중복후보 그룹"] for r in details if r["중복후보 그룹"]})}


def monthly_source_rows(contributions, year, category, month):
    result = []
    for item in contributions:
        if (item["year"], item["category"], item["month"]) != (year, category, month):
            continue
        row = item["row"]
        result.append({"자료구분":item["source_type"], "원천거래키":item["id"],
                       "지급일":row.get("집행일자", ""), "집계월":f"{year}-{month:02d}",
                       "집계 근거":item["month_basis"], "적요":row.get("적요", ""),
                       "원천 지출액":won(row.get("집행금액", 0)), "이번 셀 반영액":item["amount"],
                       "원본 예산과목":row.get("원본I열") or row.get("예산과목") or row.get("세목", ""),
                       "세목코드":expense_code(row),
                       "원본 파일":row.get("원본파일") or "이전 저장자료 · 파일정보 미보존",
                       "원본 시트":row.get("원본시트", ""), "원본 행":row.get("원본행", ""),
                       "지출결의번호":row.get("지출결의번호", ""), "지급명령번호":row.get("지급명령번호", ""),
                       "명세번호":row.get("명세번호", ""),
                       "중복 표시":"일반재료비에도 포함" if item["material_mirror"] else ""})
    return result
