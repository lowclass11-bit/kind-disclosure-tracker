"""미국 자료: SEC 내부자 매수(Form 4), 유명 펀드 신규 편입(13F), 애널리스트 추정치 변화(Yahoo)."""
from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET

from . import net
from .sources_kr import ev

# 집중 투자형·펀더멘털 중심 펀드만 본다 (CIK는 SEC에서 확인한 값)
FUNDS = {
    1067983: "버크셔 해서웨이", 1061768: "바우포스트", 1336528: "퍼싱스퀘어", 1656456: "아팔루사",
    1536411: "듀케인 패밀리오피스", 1040273: "서드포인트", 1061165: "론파인", 1167483: "타이거글로벌",
    1135730: "코튜", 1103804: "바이킹글로벌", 1029160: "소로스 펀드", 921669: "칼 아이칸",
}


def cik_map() -> dict[str, dict]:
    """티커 → {cik, title}"""
    d = net.get("https://www.sec.gov/files/company_tickers.json", headers=net.sec_headers(), ttl=7 * 24 * 3600)
    return {v["ticker"].upper(): {"cik": int(v["cik_str"]), "title": v["title"]} for v in d.values()}


def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


# ---------------------------------------------------------------- Form 4
def form4(stock: dict, cik: int, days: int = 90, max_filings: int = 10) -> list[dict]:
    """최근 90일 내부자의 장내 매수(거래코드 P)만 센다. 스톡옵션 행사·증여·매도는 제외."""
    sub = net.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=net.sec_headers(), ttl=12 * 3600)
    rec = (sub.get("filings") or {}).get("recent") or {}
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    picks = [(a, d, p) for f, a, d, p in zip(rec.get("form", []), rec.get("accessionNumber", []),
                                             rec.get("filingDate", []), rec.get("primaryDocument", []))
             if f == "4" and d >= cutoff][:max_filings]
    buyers: dict[str, float] = {}
    last_url, last_date = "", ""
    for acc, date, doc in picks:
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc.split('/')[-1]}"
        try:
            xml = net.get(url, headers=net.sec_headers(), kind="text", ttl=30 * 24 * 3600)
            root = _strip_ns(ET.fromstring(xml))
        except (net.FetchError, ET.ParseError):
            continue
        owner = root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName") or "내부자"
        title = root.findtext(".//reportingOwner/reportingOwnerRelationship/officerTitle") or ""
        for tx in root.iter("nonDerivativeTransaction"):
            if tx.findtext("./transactionCoding/transactionCode") != "P":
                continue
            try:
                shares = float(tx.findtext("./transactionAmounts/transactionShares/value") or 0)
                price = float(tx.findtext("./transactionAmounts/transactionPricePerShare/value") or 0)
            except ValueError:
                continue
            label = f"{owner.title()}{' (' + title + ')' if title else ''}"
            buyers[label] = buyers.get(label, 0) + shares * price
            if date > last_date:
                last_date = date
                last_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=4&dateb=&owner=include&count=40"
    if not buyers:
        return []
    total = sum(buyers.values())
    n = len(buyers)
    pts = (30 if n == 1 else 55 if n == 2 else 70) + (15 if total >= 1e6 else 0)
    if total < 5e4:
        pts = 10
    names = ", ".join(list(buyers)[:3])
    return [ev("smart", f"내부자 장내 매수: {n}명, 합계 약 ${total / 1e6:,.2f}M — {names}",
               "SEC Form 4", last_url, last_date, pts)]


# ---------------------------------------------------------------- 13F
_SUFFIX = re.compile(r"\b(INC|CORP|CORPORATION|CO|COMPANY|LTD|PLC|LP|LLC|HOLDINGS?|GROUP|NV|SA|AG|"
                     r"CL [ABC]|CLASS [ABC]|COM|NEW|THE|DEL|&)\b")


def norm_name(name: str) -> str:
    s = re.sub(r"[^A-Z0-9& ]", " ", name.upper())
    return " ".join(_SUFFIX.sub(" ", s).split())


def _holdings(cik: int, acc: str) -> dict[str, dict]:
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}"
    idx = net.get(f"{base}/index.json", headers=net.sec_headers(), ttl=30 * 24 * 3600)
    files = [i["name"] for i in (idx.get("directory") or {}).get("item", [])
             if i["name"].lower().endswith(".xml") and "primary_doc" not in i["name"].lower()]
    if not files:
        return {}
    root = _strip_ns(ET.fromstring(net.get(f"{base}/{files[0]}", headers=net.sec_headers(),
                                           kind="text", ttl=30 * 24 * 3600)))
    out: dict[str, dict] = {}
    for it in root.iter("infoTable"):
        if (it.findtext("putCall") or "").strip():
            continue  # 옵션 포지션 제외
        cusip = (it.findtext("cusip") or "").strip()
        try:
            shares = float(it.findtext("./shrsOrPrnAmt/sshPrnamt") or 0)
            value = float(it.findtext("value") or 0)
        except ValueError:
            continue
        h = out.setdefault(cusip, {"name": it.findtext("nameOfIssuer") or "", "shares": 0.0, "value": 0.0})
        h["shares"] += shares
        h["value"] += value
    return out


def thirteenf(log) -> dict[str, list[dict]]:
    """정규화한 회사명 → [{fund, kind, date, url, weight}] (신규 편입·50% 이상 증가)."""
    out: dict[str, list[dict]] = {}

    def one(item):
        cik, fund = item
        sub = net.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=net.sec_headers(), ttl=12 * 3600)
        rec = (sub.get("filings") or {}).get("recent") or {}
        filings = [(a, d) for f, a, d in zip(rec.get("form", []), rec.get("accessionNumber", []),
                                             rec.get("filingDate", [])) if f == "13F-HR"][:2]
        if len(filings) < 2:
            return None
        if filings[0][1] < (dt.date.today() - dt.timedelta(days=200)).isoformat():
            return None  # 더는 13F를 내지 않는 펀드
        new, old = _holdings(cik, filings[0][0]), _holdings(cik, filings[1][0])
        total = sum(h["value"] for h in new.values()) or 1
        rows = []
        url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=13F-HR&dateb=&owner=include&count=10"
        for cusip, h in new.items():
            weight = h["value"] / total
            if weight < 0.003:
                continue
            if cusip not in old:
                kind = "신규 편입"
            elif old[cusip]["shares"] > 0 and h["shares"] >= old[cusip]["shares"] * 1.5:
                kind = "50% 이상 추가 매수"
            else:
                continue
            rows.append((norm_name(h["name"]), {"fund": fund, "kind": kind, "date": filings[0][1],
                                                "url": url, "weight": weight}))
        return rows

    for rows in net.pmap(one, FUNDS.items(), workers=3):
        for name, row in rows or []:
            out.setdefault(name, []).append(row)
    log(f"13F: 유명 펀드 {len(FUNDS)}곳의 신규·확대 포지션 {sum(len(v) for v in out.values())}건")
    return out


def thirteenf_evidence(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    desc = ", ".join(f"{r['fund']} {r['kind']}(비중 {r['weight'] * 100:.1f}%)" for r in rows[:3])
    return [ev("smart", f"13F: {desc}", "SEC 13F-HR", rows[0]["url"], rows[0]["date"],
               min(40, 20 * len(rows)))]


# ---------------------------------------------------------------- Yahoo quoteSummary
def _raw(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d.get("raw") if isinstance(d, dict) else d


def quote_summary(stock: dict) -> list[dict]:
    """추정치 변화(리비전), 공매도 비중, 다음 실적 발표일, 시가총액."""
    sym = stock["yahoo"]
    d = net.get(f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}", ttl=6 * 3600,
                params={"modules": "earningsTrend,defaultKeyStatistics,calendarEvents,price,financialData",
                        "crumb": net.yahoo_crumb()}, cache_key=f"yqs-{sym}")
    res = ((d.get("quoteSummary") or {}).get("result") or [None])[0]
    if not res:
        return []
    out = []
    url = f"https://finance.yahoo.com/quote/{sym}/analysis"
    stock["mcap"] = round((_raw(res, "price", "marketCap") or 0) / 1e8, 1)   # 억달러
    stock["name"] = res.get("price", {}).get("shortName") or stock["name"]
    stock["fwd_pe"] = _raw(res, "defaultKeyStatistics", "forwardPE")
    stock["pbr"] = _raw(res, "defaultKeyStatistics", "priceToBook")

    trend = next((t for t in (res.get("earningsTrend") or {}).get("trend", []) if t.get("period") == "+1y"), None) \
        or next((t for t in (res.get("earningsTrend") or {}).get("trend", []) if t.get("period") == "0y"), None)
    if trend:
        cur, ago = _raw(trend, "epsTrend", "current"), _raw(trend, "epsTrend", "90daysAgo")
        if cur is not None and ago and ago > 0:
            chg = cur / ago - 1
            stock["eps_rev_90d"] = round(chg, 4)
            if chg > 0.01:
                out.append(ev("rev", f"내년 EPS 추정치 90일간 {chg * 100:+.1f}% (${ago:.2f} → ${cur:.2f})",
                              "Yahoo Finance 애널리스트 추정", url, pts=min(50, chg * 400)))
            elif chg < -0.03:
                out.append(ev("rev", f"주의: 내년 EPS 추정치가 90일간 {chg * 100:+.1f}% 하향 중",
                              "Yahoo Finance 애널리스트 추정", url, pts=max(-25, chg * 250)))
        up = _raw(trend, "epsRevisions", "upLast30days") or 0
        down = _raw(trend, "epsRevisions", "downLast30days") or 0
        if up + down >= 3 and up > down:
            out.append(ev("rev", f"최근 30일 추정치 상향 {up:.0f}명 / 하향 {down:.0f}명",
                          "Yahoo Finance 애널리스트 추정", url, pts=(up - down) / (up + down) * 30))
    short = _raw(res, "defaultKeyStatistics", "shortPercentOfFloat")
    if short and short > 0.10:
        stock["short_float"] = round(short, 3)
        out.append(ev("quiet", f"공매도 잔고가 유통주식의 {short * 100:.1f}% — 비관론이 많이 쌓여 있음",
                      "Yahoo Finance", f"https://finance.yahoo.com/quote/{sym}/key-statistics", pts=6))
    dates = (res.get("calendarEvents") or {}).get("earnings", {}).get("earningsDate") or []
    if dates:
        when = dt.date.fromtimestamp(dates[0].get("raw", 0))
        gap = (when - dt.date.today()).days
        if 0 <= gap <= 60:
            stock["next_earnings"] = when.isoformat()
            out.append(ev("cat", f"다음 실적 발표 예정: {when.isoformat()} ({gap}일 뒤)",
                          "Yahoo Finance", f"https://finance.yahoo.com/quote/{sym}", when.isoformat(), pts=10))
    return out
