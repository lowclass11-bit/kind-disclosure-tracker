"""계기(촉매) 점수: 재료의 '개수'가 아니라 '종류'와 '질'로 매긴다.

1) 재료마다 종류를 정하고 종류별 기본 점수를 준다 (확정 계약 30, 증설 25, 인허가 25 … 일반 언급 4).
2) 질에 따라 곱한다
   - 공시: 규모(매출액·자기자본·자산 대비 비율), 최근성
   - 뉴스: 확정도(체결·승인 > 계획·예정 > 추진·검토·전망, 물음표 제목), 출처 신뢰도, 최근성
3) 같은 종류의 재료는 가장 좋은 하나만 센다. 같은 소재 기사가 여러 건이어도 점수는 늘지 않는다.
4) 서로 다른 종류가 겹치면 1순위 ×1, 2순위 ×0.6, 3순위 ×0.35, 4순위 ×0.2, 이후 ×0.1 로 더한다.

근거(ev)에는 원점수(raw)·종류(ct)·질 계산 내역(q)을 남기고, rescore()가 최종 점수(pts)를 다시 계산한다.
rescore()는 몇 번을 불러도 결과가 같다.
"""
from __future__ import annotations

import datetime as dt
import re

# 종류: (이름, 기본 점수, 설명)
TYPES = {
    "contract":    ("확정 공급계약(공시)", 30, "금액이 정해진 계약 — 매출로 이어질 가능성이 가장 높다"),
    "capacity":    ("증설·설비투자(공시)", 25, "생산능력 확대 — 보통 6~18개월 뒤 매출에 반영"),
    "approval":    ("인허가·임상 결과", 25, "승인·허가·임상 결과처럼 결과가 갈리는 사건"),
    "guidance":    ("가이던스·전망 상향", 20, "회사나 시장이 실적 전망을 올림"),
    "policy":      ("정책·규제 변화", 18, "법안·보조금·규제 완화 등"),
    "mna":         ("인수합병·지분 투자", 16, "사업 구조가 바뀔 수 있는 거래"),
    "shareholder": ("주주환원 확대", 15, "자사주 소각·배당 확대·밸류업"),
    "pricing":     ("판가 인상·공급 부족", 15, "가격 결정력 — 이익률에 바로 반영"),
    "order_news":  ("수주·납품 소식(뉴스)", 12, "공시로 확정되기 전의 수주 소식"),
    "launch":      ("출시·양산·가동", 12, "새 제품·공장이 실제로 돌기 시작"),
    "earnings":    ("실적 발표 일정", 8, "날짜는 확정이지만 결과 방향은 모름"),
    "sector_report": ("산업 리포트", 8, "증권사가 업종을 다시 보기 시작"),
    "turnaround":  ("턴어라운드 전망(뉴스)", 6, "실적 개선 기대 — 실적 축과 겹쳐 낮게 본다"),
    "other":       ("기타 촉매 언급", 4, "구체적인 사건이 확인되지 않은 언급"),
}
RANK_W = (1.0, 0.6, 0.35, 0.2, 0.1)

# 뉴스 제목 → 종류 (위에서부터 처음 맞는 것)
NEWS_RULES = [
    ("shareholder", ("자사주", "자기주식", "자사주 소각", "소각", "배당 확대", "배당 증가", "밸류업", "주주환원"),
                    ("buyback", "repurchase", "raises dividend", "dividend increase", "special dividend")),
    ("approval", ("품목허가", "판매 승인", "시판 승인", "허가", "승인", "임상 3상", "임상3상", "톱라인", "탑라인", "FDA", "EMA"),
                 ("approval", "approved", "clearance", "phase 3", "topline", "fda")),
    ("guidance", ("가이던스 상향", "전망 상향", "목표 상향", "실적 전망 상향"),
                 ("raises guidance", "raises outlook", "raised guidance", "boosts outlook", "lifts forecast", "raises forecast")),
    ("policy", ("법안", "보조금", "규제 완화", "세액공제", "지원책", "정책 수혜", "예산 반영", "국책"),
               ("subsidy", "legislation", "bill passes", "executive order", "tax credit", "rate cut")),
    ("mna", ("인수", "합병", "M&A", "지분 투자", "지분투자"),
            ("acquire", "acquisition", "merger", "buyout", "takeover")),
    ("pricing", ("가격 인상", "판가 인상", "판가", "공급 부족", "품귀", "재고 소진", "쇼티지"),
                ("price increase", "price hike", "raises prices", "shortage", "tight supply")),
    ("order_news", ("수주", "공급계약", "계약 체결", "납품", "공급 계약"),
                   ("contract", "orders", "new order", "backlog", "awarded", "wins deal")),
    ("launch", ("출시", "양산", "가동", "착공", "준공", "증설"),
               ("launch", "ramp", "begins production", "starts production", "opens plant", "capacity")),
    ("turnaround", ("흑자전환", "턴어라운드", "실적 개선", "회복", "반등"),
                   ("turnaround", "recovery", "rebound")),
]

CONFIRMED = ("확정", "체결", "승인", "통과", "시행", "결정", "완료", "착공", "준공", "가동", "출시", "따냈", "수주했",
             "approved", "signed", "completes", "completed", "closes", "wins", "won", "awarded", "passes", "launches")
PLANNED = ("계획", "예정", "앞둬", "앞두고", "plans", "planned", "will", "set to", "scheduled")
HOPED = ("추진", "검토", "기대", "전망", "가능성", "추정", "노린다", "노려", "타진", "모색",
         "may", "could", "considering", "expected", "eyes", "weighs", "explores", "potential", "aims")

PENDING_KR = ("신청", "심사", "제출", "접수")
PENDING_EN = ("files for", "submits", "submitted", "seeks", "under review")

TIER1 = ("연합뉴스", "연합인포맥스", "한국경제", "매일경제", "조선비즈", "조선일보", "중앙일보", "동아일보", "머니투데이",
         "이데일리", "서울경제", "아시아경제", "뉴스1", "뉴시스", "전자신문", "헤럴드경제", "파이낸셜뉴스", "비즈니스포스트",
         "한겨레", "경향신문", "SBS", "KBS", "MBC", "YTN", "디지털타임스", "더벨", "딜사이트",
         "Reuters", "Bloomberg", "The Wall Street Journal", "WSJ", "Financial Times", "CNBC", "Barron's",
         "Associated Press", "AP News", "MarketWatch", "Fortune", "Forbes", "The New York Times")
PROMO = ("Motley Fool", "Zacks", "Seeking Alpha", "Kalkine", "Quiver", "InvestorPlace", "Benzinga", "TipRanks",
         "Simply Wall St", "24/7 Wall St", "Insider Monkey", "GuruFocus", "MarketBeat", "Stock Titan",
         "TradingKey", "AInvest", "블로그", "카페")

# 사옥·투자용 부동산은 생산능력과 무관하다
NOT_CAPACITY = ("사옥", "본사", "부동산", "투자목적", "임대", "오피스", "근린생활", "주거용")


def _days(date: str, today: dt.date) -> int | None:
    try:
        return (today - dt.date.fromisoformat(date[:10])).days
    except (TypeError, ValueError):
        return None


def _recency(days: int | None, steps=((14, 1.0, "2주 이내"), (30, 0.7, "한 달 이내"), (60, 0.5, "두 달 이내"))) -> tuple[float, str]:
    if days is None:
        return 0.8, "날짜 불명"
    for limit, mult, label in steps:
        if days <= limit:
            return mult, label
    return 0.35, f"{days}일 전"


def make(p_ev, ct: str, text: str, src: str, url: str, date: str, mults: list[tuple[str, float]], extra: str = "") -> dict:
    """근거 하나. raw = 기본 점수 × 질 배수."""
    label, base, _ = TYPES[ct]
    raw = float(base)
    for _, m in mults:
        raw *= m
    q = f"{label} 기본 {base}" + "".join(f" × {name} {m:g}" for name, m in mults) + f" = {raw:.1f}"
    e = p_ev("cat", text, src, url, date, raw)
    e.update({"ct": ct, "raw": round(raw, 1), "q": q + (f" · {extra}" if extra else "")})
    return e


# ---------------------------------------------------------------- 뉴스
def _en(low: str, words) -> bool:
    """영어 단어는 단어 단위로만 맞춘다 ("order"가 "in order to"·"border"에 걸리지 않게)."""
    return any(re.search(r"(?<![a-z])" + re.escape(w.strip()) + r"(?![a-z])", low) for w in words if w.strip())


def classify(title: str) -> str | None:
    low = title.lower()
    if "주총" in title or "주주총회" in title:   # 주총 '승인'은 인허가가 아니다
        title = title.replace("승인", "의결")
    for ct, kr, en in NEWS_RULES:
        if any(w in title for w in kr) or _en(low, en):
            return ct
    return None


def certainty(title: str) -> tuple[float, str]:
    low = title.lower()
    if title.rstrip().endswith("?") or "？" in title:
        return 0.5, "질문형 제목"
    if any(w in title for w in PENDING_KR) or _en(low, PENDING_EN):
        return 0.5, "신청·심사 중"
    if any(w in title for w in HOPED) or _en(low, HOPED):
        return 0.5, "추진·기대"
    if any(w in title for w in CONFIRMED) or _en(low, CONFIRMED):
        return 1.0, "확정"
    if any(w in title for w in PLANNED) or _en(low, PLANNED):
        return 0.7, "계획·예정"
    return 0.8, "확정 여부 불명"


def source_quality(source: str) -> tuple[float, str]:
    if any(t.lower() in source.lower() for t in PROMO):
        return 0.5, "홍보·추천성 매체"
    if any(t.lower() in source.lower() for t in TIER1):
        return 1.0, "주요 매체"
    return 0.8, "일반 매체"


def from_news(p_ev, items: list[dict], label: str, today: dt.date, catalyst_words: tuple[str, ...]) -> list[dict]:
    """기사 목록 → 종류별로 가장 좋은 기사 하나씩. 같은 종류의 나머지 기사는 개수만 적는다."""
    best: dict[str, tuple[float, dict, list]] = {}
    count: dict[str, int] = {}
    for it in items:
        title = it["title"]
        ct = classify(title)
        if ct is None:
            if not it.get("hits"):
                continue  # 재료가 아닌 일반 기사
            ct = "other"
        cert = certainty(title)
        srcq = source_quality(it.get("source", ""))
        rec = _recency(_days(it.get("date", ""), today))
        mults = [("확정도(" + cert[1] + ")", cert[0]), ("출처(" + srcq[1] + ")", srcq[0]), ("최근성(" + rec[1] + ")", rec[0])]
        raw = TYPES[ct][1] * cert[0] * srcq[0] * rec[0]
        count[ct] = count.get(ct, 0) + 1
        if ct not in best or raw > best[ct][0]:
            best[ct] = (raw, it, mults)
    out = []
    for ct, (raw, it, mults) in best.items():
        dup = count[ct] - 1
        extra = f"같은 종류 기사 {dup}건 더 있음(점수 미반영)" if dup else ""
        out.append(make(p_ev, ct, f"뉴스({label}) [{TYPES[ct][0]}] {it['title']}",
                        f"Google 뉴스 · {it.get('source', '')}", it.get("url", ""), it.get("date", ""), mults, extra))
    return out


# ---------------------------------------------------------------- 공시 (KIND 공시 트래커)
def _contract_size(ratio) -> tuple[float, str]:
    if ratio is None:
        return 0.7, "규모 불명"
    if ratio >= 30:
        return 1.3, f"매출액 대비 {ratio:.0f}%"
    if ratio >= 10:
        return 1.0, f"매출액 대비 {ratio:.0f}%"
    if ratio >= 5:
        return 0.7, f"매출액 대비 {ratio:.1f}%"
    return 0.4, f"매출액 대비 {ratio:.1f}%"


def _capacity_size(ratio, base: str) -> tuple[float, str]:
    if ratio is None:
        return 0.7, "규모 불명"
    big, mid = (20, 10) if base == "자기자본" else (10, 5)
    m = 1.2 if ratio >= big else 1.0 if ratio >= mid else 0.6
    return m, f"{base} 대비 {ratio:.1f}%"


def from_disclosures(p_ev, items: list[dict], today: dt.date) -> list[dict]:
    """공급계약·시설투자 공시 → 종류별 가장 큰 재료 하나씩. 사옥·투자용 부동산은 제외(참고로만)."""
    out = []
    steps = ((30, 1.0, "한 달 이내"), (60, 0.85, "두 달 이내"), (90, 0.7, "석 달 이내"))
    orders = [i for i in items if i.get("category") == "capex" and i.get("subtype") == "공급계약"]
    if orders:
        scored = []
        for i in orders:
            m = i.get("metrics") or {}
            size = _contract_size(m.get("ratio"))
            rec = _recency(_days(i.get("date", ""), today), steps)
            scored.append((30 * size[0] * rec[0], i, [("규모(" + size[1] + ")", size[0]), ("최근성(" + rec[1] + ")", rec[0])]))
        raw, i, mults = max(scored, key=lambda x: x[0])
        extra = f"공급계약 공시 {len(orders) - 1}건 더 있음(가장 큰 것만 반영)" if len(orders) > 1 else ""
        out.append(make(p_ev, "contract", f"공급계약 공시: {i.get('summary', '')}", "KIND 공시 트래커",
                        i.get("url", ""), i.get("date", ""), mults, extra))
    plants, skipped = [], []
    for i in items:
        if i.get("category") != "capex" or i.get("subtype") == "공급계약":
            continue
        m = i.get("metrics") or {}
        what = f"{m.get('name', '')} {m.get('purpose', '')}"
        if any(w in what for w in NOT_CAPACITY):
            skipped.append(i)
            continue
        size = _capacity_size(m.get("ratio"), m.get("ratio_base") or "자기자본")
        rec = _recency(_days(i.get("date", ""), today), steps)
        plants.append((25 * size[0] * rec[0], i, [("규모(" + size[1] + ")", size[0]), ("최근성(" + rec[1] + ")", rec[0])]))
    if plants:
        raw, i, mults = max(plants, key=lambda x: x[0])
        extra = f"시설투자 공시 {len(plants) - 1}건 더 있음(가장 큰 것만 반영)" if len(plants) > 1 else ""
        out.append(make(p_ev, "capacity", f"{i.get('subtype', '시설투자')} 공시: {i.get('summary', '')} — 증설은 보통 6~18개월 뒤 매출로 이어짐",
                        "KIND 공시 트래커", i.get("url", ""), i.get("date", ""), mults, extra))
    if skipped:
        i = max(skipped, key=lambda x: x.get("date", ""))
        e = p_ev("cat", f"사옥·부동산 취득 공시(점수 제외 — 생산능력과 무관): {i.get('summary', '')}",
                 "KIND 공시 트래커", i.get("url", ""), i.get("date", ""), 0)
        out.append(e)
    return out


def earnings(p_ev, when: dt.date, gap: int, url: str) -> dict:
    return make(p_ev, "earnings", f"다음 실적 발표 예정: {when.isoformat()} ({gap}일 뒤)", "Yahoo Finance",
                url, when.isoformat(), [])


def sector_report(p_ev, rows: list[dict], positive: bool) -> dict:
    last = max(rows, key=lambda r: r.get("writeDate", ""))
    brokers = len({r.get("brokerName") for r in rows})
    tone = (1.3, "제목이 긍정적") if positive else (1.0, "제목 중립")
    return make(p_ev, "sector_report",
                f"산업 리포트 — 최신: 「{last.get('title', '')}」 {last.get('brokerName', '')}",
                "네이버 증권 리서치", last.get("endUrl", ""), last.get("writeDate", ""),
                [("논조(" + tone[1] + ")", tone[0])],
                f"최근 45일 {len(rows)}건·{brokers}개사(개수는 점수에 반영하지 않음)")


# ---------------------------------------------------------------- 합산
def rescore(evs: list[dict]) -> None:
    """같은 종류는 가장 좋은 하나만, 서로 다른 종류는 순위별로 체감해서 더한다."""
    cats = [e for e in evs if e.get("ct")]
    best: dict[str, dict] = {}
    for e in cats:
        if e["ct"] not in best or e["raw"] > best[e["ct"]]["raw"]:
            best[e["ct"]] = e
    ranked = sorted(best.values(), key=lambda e: -e["raw"])
    for e in cats:
        e["pts"], e["rank"] = 0, None
    for i, e in enumerate(ranked):
        w = RANK_W[min(i, len(RANK_W) - 1)]
        e["pts"], e["rank"], e["w"] = round(e["raw"] * w, 1), i + 1, w
    for e in cats:
        if e["rank"] is None:
            e["dup"] = True  # 같은 종류의 더 좋은 재료가 있어 반영 안 함
