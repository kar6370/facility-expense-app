"""Shared, evidence-backed monthly execution and editable asset explanations.

Imported ledger amounts are the reference. Explanations never change a reference,
and missing monthly evidence is never filled by spreading a cumulative balance.
"""
import copy
import hashlib
import json
import re
from calendar import monthrange
from datetime import date, datetime

from budget_engine import ValidationError, parse_date, won


ASSET_CODE = "405-12"
ASSET_CATEGORY = "수탁자산취득비"
QUICK_CODES = {ASSET_CODE: ASSET_CATEGORY, "206-01": "일반재료비",
               "233": "상품매입비", "207-02": "전산개발비"}
PERIODS = {"연간 누계": (1, 12), "1분기": (1, 3), "2분기": (4, 6),
           "상반기": (1, 6), "3분기": (7, 9), "4분기": (10, 12), "하반기": (7, 12)}


def token(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    default=str).encode("utf-8")).hexdigest()[:20]


def month_end(year, month):
    return date(year, month, monthrange(year, month)[1]).isoformat()


def capture_report_source(config):
    """Freeze imported per-account values separately from the editable report rows.

    Built-in June examples lack fingerprints and are not uploaded evidence.
    The first upgrade can recover the latest genuinely imported saved report.
    """
    audit = config.get("source_audit") or {}
    if not audit.get("cumulative_fingerprint") or not audit.get("monthly_fingerprint"):
        return None
    if audit.get("can_apply") is False or audit.get("status") == "반영 차단":
        return None
    month = int(audit.get("base_month", 0))
    year = int(config.get("meta", {}).get("year", 2026))
    if not 1 <= month <= 12:
        return None
    lines = config.get("lines", [])
    if not any(r.get("세목코드") == ASSET_CODE for r in lines):
        return None
    exact_date=parse_date(audit.get('as_of'))
    exact_date=exact_date if exact_date and (exact_date.year,exact_date.month)==(year,month) else None
    return {"year": year, "month": month, "as_of": exact_date.isoformat() if exact_date else month_end(year, month),
            "date_precision":"day" if exact_date else "month",
            "monthly": {r["세목코드"]: won(r.get("월지출액", 0)) for r in lines},
            "cumulative": {r["세목코드"]: won(r.get("누적지출액", 0)) for r in lines},
            "monthly_file": audit.get("monthly_file", "보고서 단월 원장"),
            "cumulative_file": audit.get("cumulative_file", "보고서 누계 원장"),
            "monthly_fingerprint": audit["monthly_fingerprint"],
            "cumulative_fingerprint": audit["cumulative_fingerprint"],
            "updated_at": audit.get("updated_at", ""), "verified": True,
            "report_snapshot":copy.deepcopy({k:config.get(k,{}) for k in ('totals','business','lines')})}


def preserve_execution_config(incoming, merged):
    for key in ("execution_sources", "asset_detail_reviews", "asset_detail_history", "quick_goals", "manual_report_review"):
        if key in incoming:
            merged[key] = copy.deepcopy(incoming[key])
    sources = merged.setdefault("execution_sources", {})
    latest = capture_report_source(incoming)
    if latest:
        key = f"{latest['year']}-{latest['month']:02d}"
        # Never replace a frozen source with a later manual report edit.
        if key not in sources:
            sources[key] = latest
    return merged


def record_report_import(previous, imported):
    result = copy.deepcopy(imported)
    sources = copy.deepcopy(previous.get("execution_sources", {}))
    old = capture_report_source(previous)
    if old:
        sources.setdefault(f"{old['year']}-{old['month']:02d}", old)
    new = capture_report_source(imported)
    if not new:
        return result, False
    sources[f"{new['year']}-{new['month']:02d}"] = new
    prior_months = [r["month"] for r in sources.values()
                    if r.get("verified") and r.get("year") == new["year"]]
    history_only = new["month"] < max(prior_months, default=new["month"])
    if history_only:
        result = copy.deepcopy(previous)
        updated_month = next(r for r in imported["monthly"] if r["월"] == new["month"])
        result["monthly"] = [copy.deepcopy(updated_month) if r["월"] == new["month"] else r
                             for r in result["monthly"]]
    result["execution_sources"] = sources
    if not history_only:result.pop('manual_report_review',None)
    return result, history_only


def reference_index(config, state=None, year=2026):
    """Index direct monthly and cumulative evidence, retaining conflicts."""
    result = {"year": year, "monthly": {}, "cumulative": {}, "conflicts": [], "as_of": ""}
    candidates = {"monthly": {}, "cumulative": {}}

    def add(kind, month, code, amount, filename, fingerprint, as_of, priority, basis):
        item = {"amount": won(amount), "file": filename, "fingerprint": fingerprint,
                "as_of": as_of, "basis": basis, "priority": priority, "month": month, "code": code}
        item["token"] = token({k: item[k] for k in ("amount", "fingerprint", "as_of", "code", "basis")})
        candidates[kind].setdefault((month, code), []).append(item)
        result["as_of"] = max(result["as_of"], as_of)

    sources = copy.deepcopy(config.get("execution_sources", {}))
    recovered = capture_report_source(config)
    if recovered:
        sources.setdefault(f"{recovered['year']}-{recovered['month']:02d}", recovered)
    for source in sources.values():
        if not source.get("verified") or source.get("year") != year:
            continue
        month = int(source["month"])
        for kind in ("monthly", "cumulative"):
            for code, amount in source.get(kind, {}).items():
                add(kind, month, code, amount, source.get(kind+"_file", "보고서 원장"),
                    source.get(kind+"_fingerprint", ""), source["as_of"], 2,
                    "보고서 단월 원장" if kind == "monthly" else "보고서 누계 원장")
    if state and state.get("year") == year:
        docs = dict(state.get("ledger_snapshots", {}))
        if state.get("ledger"):
            docs[state["ledger"]["as_of"]] = state["ledger"]
        for doc in docs.values():
            stop = parse_date(doc.get("as_of"))
            if not stop or stop.year != year or doc.get("period_start") != f"{year}-01-01":
                continue
            amounts = {}
            for line in doc.get("lines", {}).values():
                amounts[line["code"]] = amounts.get(line["code"], 0) + won(line["expense"])
            for code, amount in amounts.items():
                add("cumulative", stop.month, code, amount, doc.get("filename", "통제원장"),
                    doc.get("fingerprint", ""), stop.isoformat(), 1, "편성예산 누계 원장")
    for kind, grouped in candidates.items():
        for (month, code), items in grouped.items():
            # Later cutoffs within a month precede earlier partial-month reports.
            items.sort(key=lambda x: (x["as_of"], x["priority"]), reverse=True)
            chosen = copy.deepcopy(items[0])
            conflicts = [x for x in items[1:] if x["as_of"] == chosen["as_of"] and x["amount"] != chosen["amount"]]
            chosen["conflicts"] = conflicts
            if conflicts:
                result["conflicts"].append({"kind": kind, "month": month, "code": code,
                                            "chosen": chosen, "alternatives": conflicts})
            result[kind].setdefault(month, {})[code] = chosen
    for month, lines in result["cumulative"].items():
        for code, current in lines.items():
            prior = result["cumulative"].get(month-1, {}).get(code)
            if month != 1 and (not prior or prior["as_of"] != month_end(year, month-1)):
                continue
            derived = copy.deepcopy(current)
            derived.update(amount=current["amount"] - (prior["amount"] if prior else 0),
                           basis="연속 누계 원장 차감",
                           file=current["file"] + (" − " + prior["file"] if prior else ""))
            derived["token"] = token([current["token"], prior["token"] if prior else "January"])
            direct = result["monthly"].setdefault(month, {}).get(code)
            if direct is None:
                result["monthly"][month][code] = derived
            elif direct["amount"] != derived["amount"]:
                result["conflicts"].append({"kind": "monthly", "month": month, "code": code,
                                            "chosen": direct, "alternatives": [derived]})
    return result


def reference_period(index, code, start, end):
    """Prefer reported period boundaries; never use an annual cumulative as H2."""
    if start > end:
        return None
    monthly = [index["monthly"].get(m, {}).get(code) for m in range(start, end+1)]
    current = index["cumulative"].get(end, {}).get(code)
    prior = index["cumulative"].get(start-1, {}).get(code) if start > 1 else None
    if current and (start == 1 or prior and prior["as_of"] == month_end(index["year"], start-1)):
        amount = current["amount"] - (prior["amount"] if prior else 0)
        return {"amount": amount, "basis": current["basis"] + (" · 기간 전 누계 차감" if prior else ""),
                "as_of": current["as_of"], "file": current["file"],
                "monthly_gap": amount - sum(x["amount"] for x in monthly) if all(monthly) else None}
    if monthly and all(monthly):
        return {"amount": sum(x["amount"] for x in monthly), "basis": "월별 원장 합계",
                "as_of": monthly[-1]["as_of"], "file": " / ".join(dict.fromkeys(x["file"] for x in monthly)),
                "monthly_gap": 0}
    return None


def raw_asset_rows(contributions, year, month):
    return [{"지급일": str(x["row"].get("집행일자", "")), "세부내역": str(x["row"].get("적요", "")),
             "지출액": won(x["amount"]), "근거": x["source_type"]}
            for x in contributions if (x["year"], x["month"], x["category"]) == (year, month, ASSET_CATEGORY)]


def asset_review(config, index, contributions, month):
    year = index["year"]
    reference = index["monthly"].get(month, {}).get(ASSET_CODE)
    saved = config.get("asset_detail_reviews", {}).get(f"{year}-{month:02d}")
    raw_rows = raw_asset_rows(contributions, year, month)
    rows = copy.deepcopy(saved["rows"]) if saved else raw_rows
    amount = sum(won(r["지출액"]) for r in rows)
    current = bool(saved and reference and saved.get("reference_token") == reference["token"])
    confirmed = bool(current and saved.get("confirmed") and amount == reference["amount"])
    return {"month": month, "reference": reference, "rows": rows, "raw_rows": raw_rows,
            "detail_amount": amount, "raw_amount": sum(r["지출액"] for r in raw_rows),
            "gap": reference["amount"] - amount if reference else None,
            "confirmed": confirmed, "stale": bool(saved and saved.get("reference_token") and not current), "saved": saved,
            "needs_review": bool(reference and (amount != reference["amount"] or saved and not confirmed))}


def validate_detail_rows(rows, year, month):
    cleaned = []
    for number, row in enumerate(rows, 1):
        desc = str(row.get("세부내역") or "").strip()
        raw = row.get("지출액")
        dt = str(row.get("지급일") or "").strip()
        if not desc and not dt and (raw is None or raw == "" or raw == 0):
            continue
        if not desc:
            raise ValidationError(f"{number}행 세부내역을 입력하세요.")
        amount = won(raw)
        if dt:
            parsed = parse_date(dt)
            if not parsed or (parsed.year, parsed.month) != (year, month):
                raise ValidationError(f"{number}행 지급일은 {year}년 {month}월이어야 합니다. 일자를 모르면 비워 두세요.")
            dt = parsed.isoformat()
        cleaned.append({"지급일": dt, "세부내역": desc, "지출액": amount,
                        "근거": str(row.get("근거") or "사용자 입력").strip()})
    return cleaned


def save_asset_review(config, index, month, rows, *, confirmed=False, note=""):
    reference = index["monthly"].get(month, {}).get(ASSET_CODE)
    cleaned = validate_detail_rows(rows, index["year"], month)
    total = sum(r["지출액"] for r in cleaned)
    if confirmed and (not reference or total != reference["amount"]):
        raise ValidationError("세부내역 합계가 원장 기준금액과 정확히 일치해야 확정할 수 있습니다.")
    result = copy.deepcopy(config)
    key = f"{index['year']}-{month:02d}"
    reviews = result.setdefault("asset_detail_reviews", {})
    previous = reviews.get(key)
    record = {"rows": cleaned, "confirmed": bool(confirmed), "note": str(note),
              "reference_token": reference["token"] if reference else None,
              "reference_amount": reference["amount"] if reference else None,
              "updated_at": datetime.now().isoformat(timespec="seconds")}
    result.setdefault("asset_detail_history", []).append({"period": key, "before": previous, "after": copy.deepcopy(record)})
    reviews[key] = record
    return result


def reconcile_asset_contributions(contributions, config, index):
    output = list(contributions)
    year = index["year"]
    for month, accounts in index["monthly"].items():
        reference = accounts.get(ASSET_CODE)
        if not reference:
            continue
        review = asset_review(config, index, contributions, month)
        # Drafts never affect figures or replace source transactions.
        if review["confirmed"]:
            output = [x for x in output if (x["year"], x["month"], x["category"]) != (year, month, ASSET_CATEGORY)]
            for i, detail in enumerate(review["rows"]):
                row = {"집행일자": detail["지급일"], "적요": detail["세부내역"],
                       "집행금액": detail["지출액"], "세목": ASSET_CODE,
                       "원본파일": reference["file"], "업로드구분": "원장 대조 확정 세부내역"}
                output.append({"year": year, "month": month, "category": ASSET_CATEGORY,
                               "amount": detail["지출액"], "row": row, "source_type": "사용자 대조 확정",
                               "id": f"asset-detail:{year}:{month}:{i}", "month_basis": "원장 기준월",
                               "material_mirror": False})
        actual = sum(x["amount"] for x in output if (x["year"], x["month"], x["category"]) == (year, month, ASSET_CATEGORY))
        gap = reference["amount"] - actual
        if gap:
            row = {"집행일자": "", "적요": "원장 기준금액 동기화 차액 · 세부내역 확인 필요",
                   "집행금액": gap, "세목": ASSET_CODE, "원본파일": reference["file"]}
            output.append({"year": year, "month": month, "category": ASSET_CATEGORY, "amount": gap,
                           "row": row, "source_type": "원장 대조 차액", "id": "asset-reference:"+reference["token"],
                           "month_basis": reference["basis"], "material_mirror": False})
    return output


def quick_period_rows(frame, plan_frame, index, period, cutoff, targets):
    start, stop = PERIODS[period]
    stop = min(stop, int(cutoff))
    rows = []
    for code, category in QUICK_CODES.items():
        official = reference_period(index, code, start, stop)
        subset = frame[(frame["year"] == index["year"]) & (frame["category"] == category)
                       & frame["month"].between(start, stop)] if frame is not None and not frame.empty else None
        raw = official["amount"] if official else int(subset["amount"].sum()) if subset is not None else 0
        target = won(targets.get(category, 0))
        recognized = min(max(raw, 0), target) if target > 0 else 0
        planned = 0
        if plan_frame is not None and not plan_frame.empty:
            for row in plan_frame.to_dict("records"):
                match = re.search(r"\d+", str(row.get("월", "")))
                if row.get("세목") == category and match and start <= int(match[0]) <= stop:
                    planned += won(row.get("집행예정액", 0))
        rows.append({"세목코드": code, "집행관리 항목": category, "대상액": target,
                     "기간 계획액": planned, "지출액": raw, "인정 지출액": recognized,
                     "집행률(%)": recognized/target*100 if target else 0,
                     "기준": official["basis"] if official else "관리내역 잠정값 · 기간 원장 필요",
                     "조회종료일": official["as_of"] if official else "미확인",
                     "월별합계 차이": official["monthly_gap"] if official else None,
                     "원장확인": bool(official), "조회기간": period, "기간시작월": start, "기간종료월": stop})
    return rows


def report_account_label(row):
    name = re.sub(r"^\s*\d{3}(?:-\d{2})?[.\s]*", "", str(row.get("세목명", row.get("name", "")))).strip()
    scope = str(row.get("경비구분") or row.get("scope") or "")
    if not scope and row.get("사업키") in ("entrust", "daily"):
        scope = "운영경비" if row["사업키"] == "entrust" else "일상경비"
    prefix = {"운영경비": "운영", "일상경비": "일상", "운영·일상": "운영·일상"}.get(scope, "경비구분 확인")
    return f"({prefix}){name}"


def report_presentation_lines(lines):
    from expense_scope_report import valid_parts
    result = []
    for row in lines:
        details = row.get("경비별내역", [])
        if valid_parts(row, details):
            for part in details:
                allocation, actual = won(part["예산배정액"]), won(part["지출액"])
                result.append({**row, "경비구분": part["경비구분"], "예산배정액": allocation,
                               "누적지출액": actual, "예산현액": part.get("예산현액", allocation),
                               "원인행위액": part.get("원인행위액"), "원천행": part.get("원천행"),
                               "잔액": allocation-actual, "집행률": actual/allocation*100 if allocation else 0})
        else:
            result.append(row)
    return result
