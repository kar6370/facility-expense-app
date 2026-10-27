"""Explain laundry inclusion and exclusion using the same monthly calculator."""
import re

from budget_engine import expense_code, parse_date, transaction_id, won, ValidationError
from expense_integrity import management_sums
from service_rules import laundry_exclusion_reason


def laundry_review_rows(rows, mapper, overrides=None, revisions=None):
    output, seen = [], set()
    for row in rows:
        text = " ".join(str(row.get(k, "")) for k in ("적요", "세목", "예산과목", "관리항목"))
        laundry_related = bool(re.search("세탁|침구|린넨|리넨|걸레|마포", text))
        status = "집계 제외 · 관리항목 또는 예산과목 불일치"
        dt = parse_date(row.get("집행일자"))
        month_text = ""
        amount = None
        try:
            amount = won(row.get("집행금액", 0))
            tid = transaction_id(row)
            if row.get("거래상태") in ("취소", "취소완료", "삭제", "미지급", "미집행", "반려"):
                status = "집계 제외 · 취소·미지급"
            elif tid in seen:
                status = "집계 제외 · 중복 거래"
            elif not dt:
                status = "확인 필요 · 지급일자"
            else:
                # Match management_contributions: cancelled/undated rows do
                # not claim an identity; all active dated rows do, including
                # rows whose titles are outside this review's display filter.
                seen.add(tid)
                exclusion = laundry_exclusion_reason(row.get("적요", ""))
                if exclusion:
                    status = "집계 제외 · " + exclusion
                elif mapper(row) == "세탁용역":
                    status = "집계 반영"
                    sums = management_sums([row], [], [], mapper, lambda _r: False, overrides, revisions)
                    month_text = " / ".join(f"{dt.year}년 {m}월 {v:,}원" for m, v in sorted(sums.get(dt.year, {}).get("세탁용역", {}).items()))
        except (ValidationError, TypeError, ValueError):
            status = "확인 필요 · 금액"
        if not laundry_related:
            continue
        output.append({"판정":status, "지급일":row.get("집행일자", ""), "집계월·금액":month_text,
                       "원본 세목":expense_code(row) or row.get("원본I열") or row.get("예산과목") or row.get("세목") or "원본 미제공",
                       "적요":row.get("적요", ""), "지출액":amount,
                       "원본 파일":row.get("원본파일", ""), "원본 행":row.get("원본행", "")})
    return output
