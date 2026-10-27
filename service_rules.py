"""Shared management labels for imported and previously saved service payments."""
import re
import unicodedata


def compact(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"[\s·ㆍ・\u200b\ufeff]+", "", text).lower()


def is_laundry_equipment_expense(desc="", row_cat="", budget_subj=""):
    text = compact(" ".join(map(str, (desc, row_cat, budget_subj))))
    equipment = ("건조기", "세탁기", "탈수기", "세탁장비", "세탁설비", "세탁실")
    work = ("수리", "수선", "보수", "교체", "부품", "점검", "설치", "구매", "구입")
    return any(k in text for k in equipment) and any(k in text for k in work)


def is_laundry_supply_expense(desc):
    text = compact(desc)
    if any(k in text for k in ("기름걸레", "마포", "걸레")):
        return True
    return (any(k in text for k in ("세제", "섬유유연제", "표백제", "세탁망", "세탁바구니"))
            and any(k in text for k in ("구입", "구매", "납품")))


def is_laundry_service_text(desc="", row_cat="", budget_subj=""):
    if is_laundry_equipment_expense(desc, row_cat, budget_subj) or is_laundry_supply_expense(desc):
        return False
    text = re.sub(r"[()\[\]{}:：,_-]", "", compact(desc))
    return bool(re.search(r"(?<!\d)2026년(?:0?[1-9]|1[0-2])월(?:분)?정약용펀그라운드세탁용역", text))


def laundry_exclusion_reason(desc="", row_cat="", budget_subj=""):
    if is_laundry_equipment_expense(desc, row_cat, budget_subj):
        return "설비·수리비"
    if is_laundry_supply_expense(desc):
        return "세탁용 물품·걸레"
    return "지정 제목 불일치" if not is_laundry_service_text(desc, row_cat, budget_subj) else ""


# This is a management label, not a change to the official budget account.
# An actual laundry service recorded under office expenses can still be shown;
# equipment repairs and supplies remain excluded. Bookkeeping keeps its source code.
LAUNDRY_MANAGEMENT_CODES = {"201-01", "201-11"}
ELEVATOR_MANAGEMENT_CODES = {"201-01", "201-02", "201-11", "214-05"}
WATER_MANAGEMENT_CODES = {"201-02", "201-21", "206-01"}


def is_elevator_service_text(*values):
    text = compact(" ".join(map(str, values)))
    return (any(k in text for k in ("승강기", "엘리베이터"))
            and any(k in text for k in ("유지", "보수", "관리", "점검", "검사")))


def is_water_charge_text(*values):
    text = compact(" ".join(map(str, values)))
    if any(k in text for k in ("배관수리", "배관교체", "설비공사", "수도공사", "계량기교체")):
        return False
    return any(k in text for k in ("상하수도요금", "상하수도사용료", "수도요금", "수도사용료", "수도료", "수도대금", "물사용료", "하수도사용료")) or any(compact(v) == "상하수도" for v in values)


def special_category(title):
    t = compact(title)
    if is_elevator_service_text(title):
        return "승강기점검"
    if is_water_charge_text(title):
        return "상하수도"
    if is_laundry_service_text(title):
        return "세탁용역"
    if "기름" in t and "걸레" in t:
        return ""
    if "신용카드" in t and "수수료" in t:
        return "신용카드수수료"
    if "무인경비" in t:
        return "무인경비"
    if any(k in t for k in ("고객편의기기", "공기청정기", "공청기", "비데")):
        return "공청기비데"
    if any(k in t for k in ("야간경비", "당직용역")):
        return "야간경비"
    if any(k in t for k in ("청소용역", "환경용역", "미화용역", "청소환경관리용역", "환경관리용역", "청소관리용역")):
        return "환경용역"
    return ""
