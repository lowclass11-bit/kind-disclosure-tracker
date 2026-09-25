"""공시 제목 → (카테고리, 세부유형) 분류."""
from __future__ import annotations

import re

# 카테고리
EARNINGS = "earnings"   # 매출·이익 변화
CAPEX = "capex"         # 시설투자·공급계약
STAKE = "stake"         # 대주주·내부자·자사주 매입

EXCLUDE = re.compile(r"해지|해제|취소|철회|결과보고서|거래계획보고서|양도결정|처분")

RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"매출액또는손익구조"), EARNINGS, "손익구조변경"),
    (re.compile(r"신규시설투자"), CAPEX, "신규시설투자"),
    (re.compile(r"유형자산취득결정"), CAPEX, "유형자산취득"),
    (re.compile(r"유형자산양수결정"), CAPEX, "유형자산양수"),
    (re.compile(r"단일판매ㆍ?공급계약체결"), CAPEX, "공급계약"),
    (re.compile(r"최대주주등소유주식변동신고서"), STAKE, "최대주주변동"),
    (re.compile(r"임원ㆍ?주요주주특정증권등소유상황보고서"), STAKE, "임원·주요주주"),
    (re.compile(r"주식등의대량보유상황보고서"), STAKE, "대량보유(5%)"),
    (re.compile(r"자기주식취득신탁계약체결결정"), STAKE, "자사주신탁"),
    (re.compile(r"자기주식취득결정"), STAKE, "자사주취득"),
]

AMEND = re.compile(r"^\s*\[([^\]]*정정[^\]]*)\]")
SUBSIDIARY = re.compile(r"자회사|종속회사")


def classify(title: str) -> dict | None:
    base = re.sub(r"\[[^\]]*\]", "", title).replace(" ", "")
    if EXCLUDE.search(base):
        return None
    for pat, cat, sub in RULES:
        if pat.search(base):
            m = AMEND.match(title)
            return {
                "category": cat,
                "subtype": sub,
                "amended": bool(m),
                "amend_tag": m.group(1) if m else "",
                "subsidiary": bool(SUBSIDIARY.search(base)),
            }
    return None
