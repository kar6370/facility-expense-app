"""Compatibility safeguards for the existing monthly analysis screens."""
import json
import os
import re
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

from budget_engine import parse_date, transaction_id, won
from service_rules import is_laundry_service_text


def atomic_write_json(path, payload):
    path = Path(path)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    fd, tmp = tempfile.mkstemp(prefix=path.name+".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
        if path.exists():
            shutil.copy2(path, str(path)+".bak")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def exclusive_expenditure_frame(frame):
    """Remove only the documented water/material mirror from aggregate totals."""
    if frame is None or frame.empty or "category" not in frame:
        return frame
    output = frame[frame["category"] != "수입금"].copy()
    water = output["category"] == "상하수도"
    if "materials_overlap" not in output:
        return output[~water].copy()
    # Older snapshots did not preserve a breakdown; retain their prior behavior.
    known = water & output["materials_overlap"].notna()
    output.loc[known, "amount"] = output.loc[known, "amount"] - output.loc[known, "materials_overlap"]
    output.loc[water & ~known, "amount"] = 0
    return output[~water | (output["amount"] != 0)].copy()


def management_contributions(daily, manual_assets, fallback_laundry, mapper, water_matcher,
                             overrides=None, override_revisions=None):
    """Every monthly number is a sum of these same traceable contributions."""
    daily = [(row, "수동 조정" if str(row.get("업로드구분", "")).startswith("수동") else "업로드 원천") for row in daily]
    for manual in manual_assets:
        matched = any(parse_date(r.get("집행일자")) == parse_date(manual["집행일자"])
                      and won(r.get("집행금액", 0)) == won(manual["집행금액"])
                      and mapper(r) == "수탁자산취득비" for r, _ in daily)
        if not matched:
            daily.append((manual, "이전 확정자료"))
    output, seen = [], set()
    overrides, override_revisions = overrides or {}, override_revisions or {}
    for row, source_type in daily:
        if row.get("거래상태") in ("취소", "취소완료", "삭제", "미지급", "미집행", "반려"):
            continue
        dt = parse_date(row.get("집행일자"))
        if not dt:
            continue
        tid = transaction_id(row)
        if tid in seen:
            continue
        seen.add(tid)
        cat = mapper(row)
        if cat == "세탁용역" and not is_laundry_service_text(row.get("적요", "")):
            continue
        if not cat:
            continue
        amount = won(row.get("집행금액", 0))
        desc = str(row.get("적요", ""))
        splits, basis = None, "지급일 기준"
        if tid in overrides and override_revisions.get(tid, 0) == row.get("원천수정번호", 0):
            candidate = overrides[tid]
            if sum(won(v["amount"]) for v in candidate) == amount and all(1 <= int(v["month"]) <= 12 for v in candidate):
                splits, basis = [(int(v["month"]), won(v["amount"])) for v in candidate], "사용자 확정 귀속월"
        if splits is None:
            month = dt.month
            if row.get("업로드구분") == "5대용역수수료":
                basis = "지급월 기준" if row.get("일자근거") == "지급월(일자미상)" else "지급일 기준"
            else:
                match = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월", desc)
                if match and int(match[1]) == dt.year and 1 <= int(match[2]) <= 12:
                    month, basis = int(match[2]), "적요의 귀속월"
            splits = [(month, amount)]
        for month, part in splits:
            item = dict(year=dt.year, month=month, category=cat, amount=part, row=row,
                        source_type=source_type, id=tid, month_basis=basis, material_mirror=False)
            output.append(item)
            if cat == "일반재료비" and water_matcher(row):
                output.append({**item, "category":"상하수도", "material_mirror":True})
    # fallback_laundry is retained only for caller compatibility. No payment
    # may be created from a historical amount without a matching source row.
    return output


def sum_contributions(contributions):
    sums = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for item in contributions:
        sums[item["year"]][item["category"]][item["month"]] += item["amount"]
    return {y:{c:dict(m) for c,m in cats.items()} for y,cats in sums.items()}


def management_sums(daily, manual_assets, fallback_laundry, mapper, water_matcher, overrides=None, override_revisions=None):
    return sum_contributions(management_contributions(daily, manual_assets, fallback_laundry,
                              mapper, water_matcher, overrides, override_revisions))
