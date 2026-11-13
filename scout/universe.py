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
        for p in picked:   # 업종당 개수 제한 없음: 스타일의 시가총액 기준만 적용
            seen.add(p["ticker"])
            out.append(p)
    log(f"한국 유니버스 {len(out)}종목 / {len({s['sector'] for s in out})}개 업종")
    return out


NASDAQ_SCREENER = "https://api.nasdaq.com/api/screener/stocks"

# 나스닥 스크리너의 세부 업종 → 이 프로그램의 산업 그룹 (universe_us.json의 그룹 이름과 맞춘다).
# 위에서부터 처음 맞는 규칙을 쓴다. 어디에도 안 맞으면 큰 분류(sector)로 묶는다.
US_INDUSTRY_RULES = [
    (("Semiconductors",), "반도체"),
    (("Computer Software", "EDP Services"), "소프트웨어"),
    (("Computer Manufacturing", "Computer peripheral", "Electronic Components", "Computer Communications",
      "Telecommunications Equipment", "Radio And Television Broadcasting And Communications Equipment",
      "Retail: Computer Software"), "IT 하드웨어·네트워크"),
    (("Laboratory Analytical", "In Vitro", "Commercial Physical & Biological"), "생명과학 장비"),
    (("Biotechnology", "Medicinal Chemicals", "Pharmaceuticals"), "바이오텍"),
    (("Medical/Dental", "Medical Specialities", "Medical Electronics", "Ophthalmic", "Precision Instruments"), "의료기기"),
    (("Hospital/Nursing", "Medical/Nursing", "Misc Health", "Accident &Health"), "의료 서비스·보험"),
    (("Major Banks", "Commercial Banks", "Savings Institutions"), "지역 은행"),
    (("Life Insurance", "Property-Casualty", "Specialty Insurers"), "보험"),
    (("Investment Managers", "Investment Bankers", "Finance/Investors", "Diversified Financial", "Trusts Except"), "자산운용·거래소"),
    (("Finance: Consumer", "Finance Companies"), "결제·핀테크"),
    (("Real Estate", "Building operators"), "리츠"),
    (("Homebuilding", "Building Materials", "Building Products", "Home Furnishings", "General Bldg"), "주택건설·건자재"),
    (("Oilfield Services", "Oil and Gas Field Machinery"), "유전 서비스"),
    (("Oil & Gas Production", "Integrated oil", "Coal Mining", "Oil/Gas Transmission"), "석유·가스"),
    (("Electric Utilities", "Natural Gas Distribution", "Water Supply", "Power Generation"), "유틸리티"),
    (("Precious Metals", "Metal Mining", "Other Metals", "Steel/Iron", "Aluminum", "Mining & Quarrying"), "금속·광업"),
    (("Chemicals", "Paints", "Plastic", "Forest Products", "Paper", "Containers/Packaging"), "화학·소재"),
    (("Aerospace", "Military/Government", "Ordnance"), "항공우주·방산"),
    (("Railroads", "Trucking", "Air Freight", "Integrated Freight", "Marine Transportation", "Transportation Services"), "운송·물류"),
    (("Hotels/Resorts", "Amusement & Recreation"), "항공·여행"),
    (("Auto Manufacturing", "Motor Vehicles", "Auto Parts", "Auto Dealers", "Auto & Home Supply", "Automotive Aftermarket"), "자동차·부품"),
    (("Restaurants",), "외식"),
    (("Apparel", "Shoe Manufacturing", "Garments"), "의류·신발·사치재"),
    (("Retail", "Stores", "Catalog/Specialty", "Food Chains", "Video Chains"), "소매"),
    (("Beverages", "Packaged Foods", "Specialty Foods", "Meat/Poultry", "Package Goods/Cosmetics", "Farming/Seeds", "Food Distributors"), "식품·음료·생활용품"),
    (("Broadcasting", "Movies", "Newspapers", "Publishing", "Books", "Advertising", "Cable & Other Pay"), "미디어·엔터"),
    (("Industrial Machinery", "Fluid Controls", "Metal Fabrications", "Construction/Ag", "Tools/Hardware", "Pollution Control",
      "Electrical Products", "Industrial Specialties", "Engineering & Construction", "Water Sewer", "Miscellaneous manufacturing"), "산업재·기계"),
    (("Business Services", "Commercial Services", "Professional Services", "Rental/Leasing", "Corporate Leasing",
      "Other Consumer Services", "Office Equipment", "Environmental Services", "Wholesale"), "상업 서비스"),
    (("Recreational Games", "Consumer Electronics/Appliances", "Consumer Specialties", "Durable Goods"), "레저·내구소비재"),
]
US_SECTOR_KO = {"Technology": "기타 기술", "Health Care": "기타 헬스케어", "Finance": "기타 금융", "Energy": "기타 에너지",
                "Industrials": "기타 산업재", "Consumer Discretionary": "기타 경기소비재", "Consumer Staples": "기타 필수소비재",
                "Basic Materials": "기타 소재", "Utilities": "유틸리티", "Real Estate": "리츠", "Telecommunications": "통신",
                "Miscellaneous": "기타"}
EXTRA_MACRO = {"상업 서비스": ["consumer"], "레저·내구소비재": ["consumer", "rates_down"]}


def us_group(industry: str, sector: str) -> str:
    for words, group in US_INDUSTRY_RULES:
        if any(w in industry for w in words):
            return group
    return US_SECTOR_KO.get(sector, "기타")


def build_us(style: dict, log) -> list[dict]:
    """나스닥 스크리너 전체(NYSE·나스닥·AMEX)에서 스타일의 시가총액 기준 이상인 보통주.

    universe_us.json에 직접 적어 둔 종목은 그 그룹(한국어 이름·거시 태그·ETF)을 그대로 쓰고,
    나머지는 세부 업종으로 같은 그룹에 넣는다. 스크리너를 못 받으면 universe_us.json만 쓴다.
    """
    data = json.loads((config.ROOT / "universe_us.json").read_text(encoding="utf-8"))
    curated = {t: g for g in data["groups"] for t in g["tickers"]}
    groups = {g["name"]: g for g in data["groups"]}
    floor = style["us_mcap"] * 1e8  # 억달러 → 달러

    def make(t, group, mcap=0.0):
        g = groups.get(group, {})
        sym = t.replace("/", "-").replace(".", "-")
        return {"id": f"US:{t}", "market": "US", "ticker": t, "name": t, "yahoo": sym,
                "sector": group, "sector_id": f"US:{group}", "mcap": round(mcap / 1e8, 1),
                "exchange": "US", "macro": g.get("macro", EXTRA_MACRO.get(group, [])), "etf": g.get("etf", ""),
                "url": f"https://finance.yahoo.com/quote/{sym}"}

    try:
        d = net.get(NASDAQ_SCREENER, params={"tableonly": "true", "download": "true"}, ttl=20 * 3600,
                    headers={"Accept": "application/json"})
        rows = (d.get("data") or {}).get("rows") or []
    except net.FetchError as exc:
        log(f"나스닥 전체 목록 실패({exc}) — 직접 고른 {len(curated)}종목만 봅니다")
        rows = []
    out, seen = [], set()
    for r in rows:
        t = (r.get("symbol") or "").strip()
        try:
            mcap = float(r.get("marketCap") or 0)
        except ValueError:
            continue
        if not t or t in seen or mcap < floor or re.search(r"\.WS|\^|\.U$|\.R$", t):
            continue
        name = r.get("name") or ""
        if re.search(r"Warrant|Preferred|Depositary Shares? representing|Units?$|Rights?$| Notes? ", name):
            continue
        seen.add(t)
        group = curated[t]["name"] if t in curated else us_group(r.get("industry") or "", r.get("sector") or "")
        out.append(make(t, group, mcap))
    if not rows:  # 목록을 못 받았을 때의 대비책
        for t, g in curated.items():
            if t not in seen:
                seen.add(t)
                out.append(make(t, g["name"]))
    log(f"미국 유니버스 {len(out)}종목 / {len({s['sector'] for s in out})}개 산업 (시가총액 {style['us_mcap']:,}억달러 이상)")
    return out
