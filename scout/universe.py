"""관찰 대상(유니버스) 구성.

한국: 네이버 증권 업종 분류(약 80개)에서 시총 기준으로 추린다.
미국: universe_us.json의 산업 그룹별 대표 종목.
"""
from __future__ import annotations

import json
import re

from . import config, net

NAVER = "https://m.stock.naver.com/api"

# 네이버 업종명 → 거시 환경 태그 (scout/macro.py의 태그와 맞춘다)
KR_MACRO = [
    (("은행", "보험", "증권", "카드"), ["curve_steep", "credit_ok"]),
    (("건설", "건축", "부동산", "가구"), ["rates_down", "housing"]),
    (("반도체", "디스플레이", "전자", "핸드셋", "컴퓨터", "통신장비"), ["restock", "krw_weak"]),
    (("자동차", "조선", "타이어"), ["krw_weak", "capex"]),
    (("화학", "석유", "가스"), ["restock", "oil_down"]),
    (("철강", "비철", "금속", "광업"), ["copper_up", "restock"]),
    (("기계", "전기장비", "전기제품", "건설기계", "우주항공", "국방", "복합기업"), ["capex", "copper_up"]),
    (("항공사", "해운", "운송", "호텔", "레저", "백화점", "소매", "섬유", "의류", "화장품",
      "식품", "음료", "게임", "방송", "광고", "교육"), ["consumer"]),
    (("제약", "생물공학", "생명과학", "건강관리"), ["rates_down", "risk_on"]),
    (("소프트웨어", "인터넷", "IT서비스", "양방향미디어"), ["rates_down", "risk_on"]),
    (("전기유틸리티", "가스유틸리티", "통신서비스", "담배"), ["defensive"]),
]


def _num(text) -> float:
    try:
        return float(str(text).replace(",", ""))
    except ValueError:
        return 0.0


def kr_macro_tags(sector: str) -> list[str]:
    for words, tags in KR_MACRO:
        if any(w in sector for w in words):
            return tags
    return []


_PREF = re.compile(r"(우|우B|우C|\d우B?)$")


def build_kr(style: dict, log) -> list[dict]:
    groups = net.get(f"{NAVER}/stocks/industry", params={"page": 1, "pageSize": 100}, ttl=24 * 3600)
    groups = groups.get("groups", [])

    def fetch(g):
        stocks, page = [], 1
        while page <= 4:
            d = net.get(f"{NAVER}/stocks/industry/{g['no']}",
                        params={"page": page, "pageSize": 100}, ttl=12 * 3600)
            stocks += d.get("stocks", [])
            if len(stocks) >= d.get("totalCount", 0) or not d.get("stocks"):
                break
            page += 1
        return stocks

    out, seen = [], set()
    for g, stocks in zip(groups, net.pmap(fetch, groups, workers=6)):
        if not stocks:
            continue
        picked = []
        for s in stocks:
            code, name = s.get("itemCode", ""), s.get("stockName", "")
            if s.get("stockEndType") != "stock" or not code.isdigit() or code in seen:
                continue
            if "스팩" in name or _PREF.search(name) or not code.endswith("0"):
                continue
            if (s.get("tradeStopType") or {}).get("name") not in (None, "TRADING"):
                continue
            mcap = _num(s.get("marketValue"))  # 억원
            if mcap < style["kr_mcap"]:
                continue
            exch = (s.get("stockExchangeType") or {}).get("code", "KS")
            picked.append({
                "id": f"KR:{code}", "market": "KR", "ticker": code, "name": name,
                "yahoo": f"{code}.{'KQ' if exch == 'KQ' else 'KS'}",
                "sector": g["name"], "sector_id": f"KR:{g['name']}", "mcap": mcap,
                "exchange": "KOSDAQ" if exch == "KQ" else "KOSPI",
                "macro": kr_macro_tags(g["name"]),
                "url": f"https://m.stock.naver.com/domestic/stock/{code}/total",
            })
        picked.sort(key=lambda x: -x["mcap"])
        for p in picked[: style["per_sector"]]:
            seen.add(p["ticker"])
            out.append(p)
    log(f"한국 유니버스 {len(out)}종목 / {len({s['sector'] for s in out})}개 업종")
    return out


def build_us(log) -> list[dict]:
    data = json.loads((config.ROOT / "universe_us.json").read_text(encoding="utf-8"))
    out, seen = [], set()
    for g in data["groups"]:
        for t in g["tickers"]:
            if t in seen:
                continue
            seen.add(t)
            out.append({
                "id": f"US:{t}", "market": "US", "ticker": t, "name": t, "yahoo": t,
                "sector": g["name"], "sector_id": f"US:{g['name']}", "mcap": 0.0,
                "exchange": "US", "macro": g.get("macro", []), "etf": g.get("etf", ""),
                "url": f"https://finance.yahoo.com/quote/{t}",
            })
    log(f"미국 유니버스 {len(out)}종목 / {len(data['groups'])}개 산업")
    return out
