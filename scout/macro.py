"""거시 환경과 뉴스. 경기 순환에 따라 유리해지는 업종에 가점을 준다(투자시계 접근).

키 없이: Yahoo의 금리·원자재·환율 시세.  FRED 키가 있으면 재고·주문·주택 지표를 더한다.
"""
from __future__ import annotations

import datetime as dt
import re
import email.utils
import xml.etree.ElementTree as ET
from urllib.parse import quote

from . import net, prices

TAG_LABEL = {
    "rates_down": "금리 하락", "curve_steep": "장단기 금리차 확대", "copper_up": "구리 강세",
    "oil_up": "유가 상승", "oil_down": "유가 하락", "dollar_weak": "달러 약세", "krw_weak": "원화 약세",
    "credit_ok": "신용 여건 개선", "risk_on": "위험자산 선호", "restock": "재고 재축적",
    "housing": "주택 경기 회복", "capex": "설비투자 확대", "consumer": "소비 여건 개선",
    "defensive": "방어주 선호",
}


def _chg(sym: str, days: int = 63, diff: bool = False):
    h = prices.history(sym, "1y")
    if not h or len(h["c"]) <= days:
        return None, None
    now, then = h["c"][-1], h["c"][-1 - days]
    return (now - then if diff else now / then - 1), now


def regime(fred_key: str, log) -> dict:
    """{'tags': [활성 태그], 'rows': [{name, value, change, note}]}"""
    rows, tags = [], set()

    def add(name, value, change, note, tag=None):
        rows.append({"name": name, "value": value, "change": change, "note": note,
                     "tag": TAG_LABEL.get(tag, "") if tag else ""})
        if tag:
            tags.add(tag)

    try:
        d10, y10 = _chg("^TNX", diff=True)
        d3m, y3m = _chg("^IRX", diff=True)
        if d10 is not None:
            add("미국 10년 금리", f"{y10:.2f}%", f"{d10:+.2f}%p (3개월)",
                "하락 → 주택·리츠·바이오·성장주에 유리" if d10 < -0.25 else
                "상승 → 금융에 유리, 장기 성장주에 부담" if d10 > 0.25 else "뚜렷한 방향 없음",
                "rates_down" if d10 < -0.25 else None)
        if d10 is not None and d3m is not None:
            steep = d10 - d3m
            add("장단기 금리차(10년-3개월)", f"{y10 - y3m:+.2f}%p", f"{steep:+.2f}%p (3개월)",
                "확대 → 은행·보험 이자마진에 유리" if steep > 0.25 else "변화 작음",
                "curve_steep" if steep > 0.25 else None)
        cu, cu_p = _chg("HG=F")
        if cu is not None:
            add("구리", f"${cu_p:.2f}", prices.pct(cu, True) + " (3개월)",
                "강세 → 소재·산업재·전력설비 수요 신호" if cu > 0.08 else "중립",
                "copper_up" if cu > 0.08 else None)
        oil, oil_p = _chg("CL=F")
        if oil is not None:
            tag = "oil_up" if oil > 0.10 else "oil_down" if oil < -0.10 else None
            add("WTI 유가", f"${oil_p:.1f}", prices.pct(oil, True) + " (3개월)",
                "상승 → 에너지에 유리" if tag == "oil_up" else
                "하락 → 항공·화학·운송 원가 부담 완화" if tag == "oil_down" else "중립", tag)
        dxy, dxy_p = _chg("DX-Y.NYB")
        if dxy is not None:
            add("달러 인덱스", f"{dxy_p:.1f}", prices.pct(dxy, True) + " (3개월)",
                "약세 → 원자재·신흥국·수출 소비재에 유리" if dxy < -0.03 else "중립",
                "dollar_weak" if dxy < -0.03 else None)
        krw, krw_p = _chg("KRW=X")
        if krw is not None:
            add("원/달러 환율", f"{krw_p:,.0f}원", prices.pct(krw, True) + " (3개월)",
                "원화 약세 → 수출주(자동차·조선·반도체) 이익에 유리" if krw > 0.03 else "중립",
                "krw_weak" if krw > 0.03 else None)
        hyg, _ = _chg("HYG")
        ief, _ = _chg("IEF")
        if hyg is not None and ief is not None:
            spread = hyg - ief
            add("하이일드 채권 − 국채(3개월 수익률 차)", "", prices.pct(spread, True),
                "신용 여건 개선 → 경기민감·중소형주에 유리" if spread > 0.01 else
                "신용 경계 → 방어주 선호" if spread < -0.02 else "중립",
                "credit_ok" if spread > 0.01 else "defensive" if spread < -0.02 else None)
        iwm, _ = _chg("IWM")
        spy, _ = _chg("SPY")
        if iwm is not None and spy is not None and iwm - spy > 0.03:
            add("소형주 − 대형주(3개월)", "", prices.pct(iwm - spy, True), "위험자산 선호 확산", "risk_on")
    except net.FetchError as exc:
        log(f"거시 시세 일부 실패: {exc}")

    if fred_key:
        try:
            for sid, name, months, rule in FRED_SERIES:
                obs = fred(sid, fred_key)
                if len(obs) <= months:
                    continue
                now, then = obs[-1][1], obs[-1 - months][1]
                chg = now / then - 1 if then else 0
                tag, note = rule(chg)
                add(f"{name} (FRED {sid})", f"{now:,.2f}", f"{chg * 100:+.1f}% ({months}개월, {obs[-1][0][:7]})",
                    note, tag)
            log("FRED 지표 반영")
        except net.FetchError as exc:
            log(f"FRED 실패: {exc} — 키를 확인하세요")
    return {"tags": sorted(tags), "labels": [TAG_LABEL[t] for t in sorted(tags)], "rows": rows}


FRED_SERIES = [
    ("ISRATIO", "미국 재고/판매 비율", 6,
     lambda c: ("restock", "재고 부담 감소 → 재고 재축적 국면(산업재·소재·반도체)") if c < -0.01 else (None, "재고 조정 신호 없음")),
    ("NEWORDER", "미국 비국방 자본재 신규주문", 6,
     lambda c: ("capex", "기업 설비투자 주문 증가") if c > 0.02 else (None, "설비투자 정체")),
    ("PERMIT", "미국 건축허가", 6,
     lambda c: ("housing", "주택 착공 선행지표 회복") if c > 0.05 else (None, "주택 경기 정체")),
    ("UMCSENT", "미시간 소비자심리", 6,
     lambda c: ("consumer", "소비 심리 개선") if c > 0.05 else (None, "소비 심리 정체")),
]


def fred(series: str, key: str) -> list[tuple[str, float]]:
    start = (dt.date.today() - dt.timedelta(days=900)).isoformat()
    d = net.get("https://api.stlouisfed.org/fred/series/observations", ttl=24 * 3600,
                params={"series_id": series, "api_key": key, "file_type": "json", "observation_start": start},
                cache_key=f"fred-{series}-{start}")
    out = []
    for o in d.get("observations", []):
        try:
            out.append((o["date"], float(o["value"])))
        except (KeyError, ValueError):
            continue
    return out


# ---------------------------------------------------------------- 뉴스 (Google 뉴스 RSS)
CATALYST_KR = ("양산", "가동", "착공", "준공", "시행", "출시", "승인", "허가", "임상", "수주", "증설",
               "인수", "합병", "규제 완화", "보조금", "법안", "턴어라운드", "흑자전환", "내년", "상반기",
               "2027", "공급 부족", "가격 인상", "재고 소진", "회복")
CATALYST_EN = ("approval", "fda", "launch", "ramp", "capacity", "backlog", "contract", "subsidy",
               "rate cut", "restocking", "recovery", "turnaround", "2027", "raises guidance", "buyback",
               "shortage", "price increase", "rebound", "upgrade")


def news(query: str, lang: str, days: int = 30) -> list[dict]:
    loc = "hl=ko&gl=KR&ceid=KR:ko" if lang == "ko" else "hl=en-US&gl=US&ceid=US:en"
    url = f"https://news.google.com/rss/search?q={quote(query + f' when:{days}d')}&{loc}"
    xml = net.get(url, kind="text", ttl=6 * 3600)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    words = CATALYST_KR if lang == "ko" else CATALYST_EN
    out = []
    for it in root.iter("item"):
        title = it.findtext("title") or ""
        try:
            date = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "").date().isoformat()
        except (TypeError, ValueError):
            date = ""
        hits = [w for w in words if w in title.lower()]
        out.append({"title": title, "url": it.findtext("link") or "", "date": date,
                    "source": it.findtext("source") or "", "hits": hits})
    return out


# 제목에 이런 말이 있으면 촉매 단어가 있어도 근거로 치지 않는다 (예: "풀가동에도 적자 낸 ○○")
NEGATIVE_KR = ("적자", "부진", "하락", "급락", "감소", "쇼크", "우려", "리콜", "소송", "제재", "철회", "취소", "연기",
               "하향", "손실", "둔화", "악화", "위기", "중단", "감산", "경고", "불발", "무산", "차질", "결함", "적신호",
               "매도", "횡령", "배임", "상폐", "상장폐지", "감사의견", "유상증자")
NEGATIVE_EN = ("miss", "misses", "cut", "cuts", "downgrade", "lawsuit", "sued", "probe", "recall", "plunge", "plunges",
               "slump", "falls", "drops", "tumbles", "sinks", "warning", "warns", "weak", "loss", "losses", "delay",
               "delayed", "halt", "layoff", "layoffs", "bankruptcy", "investigation", "decline", "sell-off", "selloff",
               "downgraded", "short seller", "fraud", "lowers guidance", "slashes")


def is_negative(title: str) -> bool:
    low = title.lower()
    if any(w in title for w in NEGATIVE_KR):
        return True
    return any(re.search(r"\b" + re.escape(w) + r"\b", low) for w in NEGATIVE_EN)


def news_evidence(items: list[dict], label: str, must: tuple[str, ...] = ()) -> tuple[list[dict], int]:
    """촉매 단어가 들어간 기사 → 근거. (근거 목록, 전체 기사 수)

    must가 있으면 제목에 그중 하나가 들어간 기사만 센다(검색어와 무관한 기사 걸러내기).
    """
    from .sources_kr import ev
    if must:
        low = [m.lower() for m in must if m]
        items = [i for i in items if any(m in i["title"].lower() for m in low)]
    hot = sorted((i for i in items if i["hits"] and not is_negative(i["title"])), key=lambda i: i["date"], reverse=True)
    bad = sorted((i for i in items if is_negative(i["title"])), key=lambda i: i["date"], reverse=True)
    out = []
    if hot:
        pts = min(20, 4 * len(hot))
        for i in hot[:2]:
            out.append(ev("cat", f"뉴스({label}): {i['title']}", f"Google 뉴스 · {i['source']}", i["url"], i["date"], pts / 2))
    if bad:  # 점수에는 넣지 않고, 판단에 참고하도록 최신 부정 기사 하나를 보여 준다
        i = bad[0]
        out.append(ev("cat", f"부정적 뉴스(점수 제외, 최근 30일 {len(bad)}건): {i['title']}",
                      f"Google 뉴스 · {i['source']}", i["url"], i["date"], 0))
    return out, len(items)
