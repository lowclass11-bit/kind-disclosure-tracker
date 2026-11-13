"""한국 자료: 수급(외국인·기관), 컨센서스, 증권사 리포트, 공시(트래커·DART), 수출 통계."""
from __future__ import annotations

import datetime as dt
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict

from . import config, net

NAVER = "https://m.stock.naver.com/api"
GOOD_WORDS = ("상향", "턴어라운드", "회복", "바닥", "개선", "서프라이즈", "반등", "정상화",
              "재평가", "리레이팅", "저평가", "기대", "모멘텀", "성장", "수주", "흑자")
BAD_WORDS = ("하향", "부진", "쇼크", "우려", "둔화", "적자", "불확실")


def _num(text) -> float | None:
    if text is None:
        return None
    s = re.sub(r"[^0-9.\-]", "", str(text).replace("−", "-"))
    try:
        return float(s)
    except ValueError:
        return None


def ev(p: str, text: str, src: str, url: str = "", date: str = "", pts: float = 0) -> dict:
    return {"p": p, "t": text, "src": src, "url": url, "d": date, "pts": round(pts, 1)}


# ---------------------------------------------------------------- 수급
def flows(stock: dict) -> list[dict]:
    """외국인·기관 순매수(최근 20거래일)와 외국인 지분율 변화(약 60거래일)."""
    rows = net.get(f"{NAVER}/stock/{stock['ticker']}/trend", params={"pageSize": 60}, ttl=6 * 3600)
    if not isinstance(rows, list) or len(rows) < 20:
        return []
    recent = rows[:20]
    vol = sum(_num(r.get("accumulatedTradingVolume")) or 0 for r in recent)
    fgn = sum(_num(r.get("foreignerPureBuyQuant")) or 0 for r in recent)
    org = sum(_num(r.get("organPureBuyQuant")) or 0 for r in recent)
    out = []
    url = f"https://m.stock.naver.com/domestic/stock/{stock['ticker']}/trend"
    date = recent[0].get("bizdate", "")
    date = f"{date[:4]}-{date[4:6]}-{date[6:]}" if len(date) == 8 else ""
    if vol > 0:
        share = (fgn + org) / vol
        if share > 0.02:
            who = [n for n, q in (("외국인", fgn), ("기관", org)) if q > 0]
            out.append(ev("smart", f"최근 20거래일 {'·'.join(who)} 순매수가 거래량의 {share * 100:.1f}%",
                          "네이버 증권 투자자별 매매", url, date, min(40, share * 400)))
        stock["flow_share"] = round(share, 4)
    h0, h1 = _num(recent[0].get("foreignerHoldRatio")), _num(rows[-1].get("foreignerHoldRatio"))
    if h0 is not None and h1 is not None and h0 - h1 >= 0.3:
        out.append(ev("smart", f"외국인 지분율 {h1:.2f}% → {h0:.2f}% ({len(rows)}거래일간 +{h0 - h1:.2f}%p)",
                      "네이버 증권 투자자별 매매", url, date, min(20, (h0 - h1) * 20)))
    return out


# ---------------------------------------------------------------- 컨센서스·밸류에이션
def fundamentals(stock: dict) -> list[dict]:
    code = stock["ticker"]
    out = []
    url = f"https://m.stock.naver.com/domestic/stock/{code}/finance/annual"
    d = net.get(f"{NAVER}/stock/{code}/integration", ttl=6 * 3600)
    info = {i.get("code"): i.get("value") for i in d.get("totalInfos", [])}
    eps, cns = _num(info.get("eps")), _num(info.get("cnsEps"))
    stock["per"], stock["pbr"] = _num(info.get("per")), _num(info.get("pbr"))
    stock["cns_eps"] = cns
    if eps and cns and eps > 0:
        g = cns / eps - 1
        stock["eps_growth"] = round(g, 3)
        if g > 0.1:
            out.append(ev("rev", f"증권가 추정 EPS {cns:,.0f}원 — 최근 실적 {eps:,.0f}원 대비 {g * 100:+.0f}%",
                          "네이버 증권 컨센서스", url, pts=min(40, g * 80)))
    elif cns and cns > 0 and eps is not None and eps <= 0:
        stock["eps_growth"] = 1.0
        out.append(ev("rev", f"최근 실적은 적자(EPS {eps:,.0f}원)인데 추정치는 흑자(EPS {cns:,.0f}원) — 흑자전환 기대",
                      "네이버 증권 컨센서스", url, pts=35))

    f = net.get(f"{NAVER}/stock/{code}/finance/annual", ttl=24 * 3600)
    fi = f.get("financeInfo") or {}
    titles = fi.get("trTitleList") or []
    actual = [t["key"] for t in titles if t.get("isConsensus") == "N"]
    est = [t["key"] for t in titles if t.get("isConsensus") == "Y"]
    op = next((r for r in fi.get("rowList", []) if r.get("title") == "영업이익"), None)
    if op and actual and est:
        cols = op.get("columns", {})
        a = _num((cols.get(actual[-1]) or {}).get("value"))
        e = _num((cols.get(est[0]) or {}).get("value"))
        prev = _num((cols.get(actual[-2]) or {}).get("value")) if len(actual) > 1 else None
        if a is not None and e is not None and (cols.get(est[0]) or {}).get("value") not in ("-", ""):
            y_a, y_e = actual[-1][:4], est[0][:4]
            if a > 0 and e > a * 1.1:
                g = e / a - 1
                turn = " (직전 해에는 감익)" if prev is not None and a < prev else ""
                out.append(ev("rev", f"영업이익 {y_a}년 {a:,.0f}억 → {y_e}년 추정 {e:,.0f}억 ({g * 100:+.0f}%){turn}",
                              "네이버 증권 재무(컨센서스)", url,
                              pts=min(30, g * 50) + (8 if turn else 0)))
            elif a <= 0 < e:
                out.append(ev("rev", f"영업이익 {y_a}년 {a:,.0f}억(적자) → {y_e}년 추정 {e:,.0f}억 흑자전환",
                              "네이버 증권 재무(컨센서스)", url, pts=30))
    if stock.get("pbr") is not None and stock["pbr"] < 0.7:
        out.append(ev("quiet", f"PBR {stock['pbr']:.2f}배 — 장부가보다 싸게 거래", "네이버 증권", url, pts=6))
    return out


# ---------------------------------------------------------------- 증권사 리포트
def research_company(days: int = 45) -> dict[str, list[dict]]:
    """종목코드 → 최근 리포트 목록."""
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    by_code: dict[str, list[dict]] = defaultdict(list)
    for page in range(1, 40):
        rows = net.get(f"{NAVER}/research/company", params={"page": page, "pageSize": 100}, ttl=6 * 3600)
        if not isinstance(rows, list) or not rows:
            break
        for r in rows:
            if r.get("writeDate", "") >= cutoff and r.get("itemCode"):
                by_code[r["itemCode"]].append(r)
        if rows[-1].get("writeDate", "") < cutoff:
            break
    return by_code


def research_industry(days: int = 45) -> dict[str, list[dict]]:
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for page in range(1, 15):
        rows = net.get(f"{NAVER}/research/industry", params={"page": page, "pageSize": 100}, ttl=6 * 3600)
        if not isinstance(rows, list) or not rows:
            break
        for r in rows:
            if r.get("writeDate", "") >= cutoff:
                by_cat[r.get("category", "")].append(r)
        if rows[-1].get("writeDate", "") < cutoff:
            break
    return by_cat


def research_evidence(reports: list[dict]) -> list[dict]:
    if not reports:
        return []
    brokers = {r.get("brokerName") for r in reports}
    good = [r for r in reports if any(w in r.get("title", "") for w in GOOD_WORDS)
            and not any(w in r.get("title", "") for w in BAD_WORDS)]
    bad = [r for r in reports if any(w in r.get("title", "") for w in BAD_WORDS)]
    latest = max(reports, key=lambda r: r.get("writeDate", ""))
    out = [ev("rev", f"최근 45일 증권사 리포트 {len(reports)}건 ({len(brokers)}개사) — 최신: 「{latest.get('title', '')}」 {latest.get('brokerName', '')}",
              "네이버 증권 리서치", latest.get("endUrl", ""), latest.get("writeDate", ""),
              pts=min(12, 3 + 3 * len(brokers)))]
    if good:
        g = max(good, key=lambda r: r.get("writeDate", ""))
        out.append(ev("rev", f"긍정적 제목의 리포트 {len(good)}건 — 「{g.get('title', '')}」 {g.get('brokerName', '')}",
                      "네이버 증권 리서치", g.get("endUrl", ""), g.get("writeDate", ""),
                      pts=min(15, 5 * len(good)) - min(10, 5 * len(bad))))
    return out


# ---------------------------------------------------------------- 공시 (이 저장소의 KIND 트래커)
def tracker(days: int = 90) -> dict[str, list[dict]]:
    """회사명 → 시그널 공시 목록. 트래커가 매일 모으는 docs/data/all.json을 그대로 쓴다."""
    items = config.read_json(config.TRACKER_ALL, [])
    if isinstance(items, dict):
        items = items.get("items", [])
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    by_corp: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        if it.get("signal") and it.get("date", "") >= cutoff and not it.get("subsidiary"):
            by_corp[it.get("corp", "")].append(it)
    return by_corp


def tracker_evidence(items: list[dict]) -> list[dict]:
    out = []
    stake = [i for i in items if i.get("category") == "stake"]
    insiders = [i for i in stake if i.get("subtype") in ("임원·주요주주", "최대주주변동")]
    big = [i for i in stake if i.get("subtype") == "대량보유(5%)"]
    buyback = [i for i in stake if i.get("subtype", "").startswith("자사주")]
    if insiders:
        filers = {i.get("filer") for i in insiders}
        amount = sum((i.get("metrics") or {}).get("buy_amount") or 0 for i in insiders)
        last = max(insiders, key=lambda i: i.get("date", ""))
        amt = f", 합계 약 {amount / 1e8:,.1f}억원" if amount else ""
        pts = (20 if len(filers) == 1 else 35) + (10 if amount >= 5e8 else 0)
        if amount and amount < 3e7:
            pts = 8  # 수백만~수천만원 수준의 매수는 의미를 낮게 본다
        out.append(ev("smart", f"임원·대주주 매수 공시 {len(insiders)}건 (보고자 {len(filers)}명{amt})",
                      "KIND 공시 트래커", last.get("url", ""), last.get("date", ""), pts))
    if big:
        last = max(big, key=lambda i: i.get("date", ""))
        out.append(ev("smart", f"5% 대량보유 지분 확대: {last.get('filer', '')} — {last.get('summary', '')}",
                      "KIND 공시 트래커", last.get("url", ""), last.get("date", ""), 18))
    if buyback:
        last = max(buyback, key=lambda i: i.get("date", ""))
        out.append(ev("smart", f"자사주 취득 결정: {last.get('summary', '')}",
                      "KIND 공시 트래커", last.get("url", ""), last.get("date", ""), 25))
    capex = [i for i in items if i.get("category") == "capex"]
    plant = [i for i in capex if i.get("subtype") != "공급계약"]
    orders = [i for i in capex if i.get("subtype") == "공급계약"]
    if plant:
        last = max(plant, key=lambda i: i.get("date", ""))
        out.append(ev("cat", f"{last.get('subtype', '시설투자')} 공시: {last.get('summary', '')} — 증설은 보통 6~18개월 뒤 매출로 이어짐",
                      "KIND 공시 트래커", last.get("url", ""), last.get("date", ""), 28))
    if orders:
        last = max(orders, key=lambda i: i.get("date", ""))
        out.append(ev("cat", f"공급계약 공시 {len(orders)}건 — 최신: {last.get('summary', '')}",
                      "KIND 공시 트래커", last.get("url", ""), last.get("date", ""),
                      min(30, 12 + 6 * len(orders))))
    earn = [i for i in items if i.get("category") == "earnings" and i.get("direction") == "up"]
    if earn:
        last = max(earn, key=lambda i: i.get("date", ""))
        out.append(ev("rev", f"손익구조 변경 공시(실적 개선): {last.get('summary', '')}",
                      "KIND 공시 트래커", last.get("url", ""), last.get("date", ""), 15))
    return out


# ---------------------------------------------------------------- DART OpenAPI (키 필요)
DART = "https://opendart.fss.or.kr/api"


def dart_corp_codes(key: str) -> dict[str, str]:
    """종목코드 → DART 고유번호. 하루 한 번만 받는다."""
    path = config.CACHE / "dart_corp_codes.json"
    cached = config.read_json(path)
    if cached and cached.get("date") == dt.date.today().isoformat():
        return cached["map"]
    raw = net.get(f"{DART}/corpCode.xml", params={"crtfc_key": key}, kind="bytes", ttl=0)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            root = ET.fromstring(z.read(z.namelist()[0]))
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        raise net.FetchError("DART 고유번호 파일을 열 수 없음 (키를 확인하세요)") from exc
    mapping = {}
    for el in root.iter("list"):
        sc = (el.findtext("stock_code") or "").strip()
        if sc:
            mapping[sc] = (el.findtext("corp_code") or "").strip()
    config.write_json(path, {"date": dt.date.today().isoformat(), "map": mapping})
    return mapping


def _report_candidates(today: dt.date) -> list[tuple[int, str, str]]:
    """가장 최근에 나왔을 법한 정기보고서부터. (사업연도, 보고서코드, 이름)"""
    y, cands = today.year, []
    md = (today.month, today.day)
    if md >= (11, 14):
        cands.append((y, "11014", "3분기"))
    if md >= (8, 14):
        cands.append((y, "11012", "반기"))
    if md >= (5, 15):
        cands.append((y, "11013", "1분기"))
    if md >= (3, 20):
        cands.append((y - 1, "11011", "연간"))
    cands += [(y - 1, "11014", "3분기"), (y - 1, "11012", "반기")]
    return cands


def dart_quarter(stock: dict, key: str, corp_codes: dict[str, str]) -> list[dict]:
    """최근 정기보고서의 영업이익이 전년 같은 기간보다 늘었는지."""
    corp = corp_codes.get(stock["ticker"])
    if not corp:
        return []
    for year, code, label in _report_candidates(dt.date.today())[:3]:
        d = net.get(f"{DART}/fnlttSinglAcnt.json", ttl=12 * 3600,
                    params={"crtfc_key": key, "corp_code": corp, "bsns_year": str(year), "reprt_code": code},
                    cache_key=f"dart-fin-{corp}-{year}-{code}")
        if d.get("status") != "000":
            continue
        rows = [r for r in d.get("list", []) if r.get("account_nm") == "영업이익"]
        rows.sort(key=lambda r: r.get("fs_div") != "CFS")  # 연결 우선
        if not rows:
            continue
        cur, prev = _num(rows[0].get("thstrm_amount")), _num(rows[0].get("frmtrm_amount"))
        if cur is None or prev is None:
            return []
        url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rows[0].get('rcept_no', '')}"
        name = f"{year}년 {label}"
        if prev <= 0 < cur:
            return [ev("rev", f"{name} 영업이익 흑자전환 ({prev / 1e8:,.0f}억 → {cur / 1e8:,.0f}억)",
                       "DART 정기보고서", url, pts=20)]
        if prev > 0 and cur > prev * 1.15:
            g = cur / prev - 1
            return [ev("rev", f"{name} 영업이익 {cur / 1e8:,.0f}억 — 전년 같은 기간보다 {g * 100:+.0f}%",
                       "DART 정기보고서", url, pts=min(20, 8 + g * 20))]
        return []
    return []


# ---------------------------------------------------------------- 관세청 품목별 수출 (공공데이터포털 키 필요)
EXPORT_ITEMS = [  # (HS 코드, 품목 이름, 네이버 업종명에 들어가는 단어들)
    ("8542", "반도체(집적회로)", ("반도체",)),
    ("3304", "화장품", ("화장품",)),
    ("8504", "변압기", ("전기장비",)),
    ("8901", "선박", ("조선",)),
    ("8703", "승용차", ("자동차",)),
    ("850760", "리튬이온 배터리", ("전기제품",)),
    ("3004", "의약품", ("제약",)),
    ("190230", "라면", ("식품",)),
    ("8429", "건설기계", ("기계",)),
    ("7208", "열연강판", ("철강",)),
    ("2710", "석유제품", ("석유",)),
    ("9018", "의료기기", ("건강관리장비",)),
    ("9306", "탄약·방산", ("우주항공", "국방")),
]


def exports(key: str) -> list[dict]:
    """품목별 최근 3개월 수출액의 전년 대비 증감. [{hs, item, words, yoy, latest, url}]"""
    today = dt.date.today()
    end = (today.replace(day=1) - dt.timedelta(days=1))          # 지난달 말
    months = []
    y, m = end.year, end.month
    for _ in range(15):
        months.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    out = []

    def fetch(item):
        hs, name, words = item
        by_month: dict[str, float] = {}
        for start, stop in ((months[0], months[2]), (months[3], months[14])):  # 한 번에 최대 12개월
            xml = net.get("https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList", kind="text",
                          ttl=24 * 3600, cache_key=f"exports-{hs}-{start}-{stop}",
                          params={"serviceKey": key, "strtYymm": start, "endYymm": stop, "hsSgn": hs})
            try:
                root = ET.fromstring(xml)
            except ET.ParseError as exc:
                raise net.FetchError("관세청 응답을 읽을 수 없음") from exc
            for it in root.iter("item"):
                ym = re.sub(r"\D", "", it.findtext("year") or "")
                val = _num(it.findtext("expDlr"))
                if len(ym) == 6 and val is not None:
                    by_month[ym] = by_month.get(ym, 0) + val
        last3, prev3 = months[-3:], months[:3]
        if not all(k in by_month for k in last3 + prev3):
            return None
        a, b = sum(by_month[k] for k in last3), sum(by_month[k] for k in prev3)
        if b <= 0:
            return None
        return {"hs": hs, "item": name, "words": words, "yoy": a / b - 1, "latest": months[-1],
                "url": "https://tradedata.go.kr/cts/index.do"}

    for r in net.pmap(fetch, EXPORT_ITEMS, workers=3):
        if r:
            out.append(r)
    return out
