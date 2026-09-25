"""공시 목록 수집: KIND 우선, 실패 시 DART(전자공시) 웹 목록으로 대체.

KIND 접수번호(acptno)와 DART 접수번호(rcpNo)는 거래소 공시에서 동일하므로
어느 소스에서 받아도 같은 id로 저장된다.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, asdict

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TIMEOUT = 30


def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, connect=4, read=4, backoff_factor=1.5,
                  status_forcelist=(429, 500, 502, 503, 504), allowed_methods=None)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    return s


@dataclass
class ListItem:
    id: str          # 접수번호 (14자리)
    date: str        # YYYY-MM-DD
    time: str        # HH:MM
    market: str      # KOSPI | KOSDAQ
    corp: str
    title: str
    filer: str
    corp_code: str = ""   # KIND 회사코드 또는 DART 고유번호

    def to_dict(self) -> dict:
        return asdict(self)


def _clean(text: str) -> str:
    return " ".join(text.split())


def _market_from(text: str) -> str | None:
    t = text.lower()
    if "kospi" in t or "유가" in t:
        return "KOSPI"
    if "kosdaq" in t or "코스닥" in t:
        return "KOSDAQ"
    return None


# ---------------------------------------------------------------- KIND
KIND_URL = "https://kind.krx.co.kr/disclosure/todaydisclosure.do"


def fetch_kind(session: requests.Session, date: str) -> list[ListItem]:
    """KIND '오늘의 공시' 목록. date=YYYY-MM-DD. 접근 차단 시 예외 발생."""
    items: list[ListItem] = []
    for page in range(1, 60):
        resp = session.post(
            KIND_URL,
            data={
                "method": "searchTodayDisclosureSub",
                "currentPageSize": "100",
                "pageIndex": str(page),
                "orderMode": "0",
                "orderStat": "D",
                "marketType": "",
                "forward": "todaydisclosure_sub",
                "chose": "S",
                "todayFlag": "N",
                "selDate": date,
            },
            headers={"Referer": KIND_URL + "?method=searchTodayDisclosureMain"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        if "Access Denied" in resp.text[:500]:
            raise RuntimeError("KIND access denied")
        soup = BeautifulSoup(resp.content, "lxml")
        rows = [tr for tr in soup.select("tr") if tr.select_one("a[onclick*=openDisclsViewer]")]
        if not rows:
            break
        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue
            a = tr.select_one("a[onclick*=openDisclsViewer]")
            m = re.search(r"openDisclsViewer\('(\d{14})'", a.get("onclick", ""))
            if not m:
                continue
            market_hint = " ".join(
                f"{img.get('alt', '')} {img.get('src', '')} {img.get('class', '')}"
                for img in tds[1].find_all(["img", "span"])
            )
            market = _market_from(market_hint)
            if not market:
                continue
            ca = tds[1].select_one("a[onclick*=companysummary_open]")
            cm = re.search(r"companysummary_open\('(\w+)'", ca.get("onclick", "")) if ca else None
            items.append(
                ListItem(
                    id=m.group(1),
                    date=date,
                    time=_clean(tds[0].get_text()),
                    market=market,
                    corp=_clean((ca or tds[1]).get_text()),
                    title=_clean(a.get("title") or a.get_text()),
                    filer=_clean(tds[3].get_text()) if len(tds) > 3 else "",
                    corp_code=cm.group(1) if cm else "",
                )
            )
        if len(rows) < 100:
            break
        time.sleep(0.4)
    return items


# ---------------------------------------------------------------- DART
DART_LIST_URL = "https://dart.fss.or.kr/dsac001/search.ax"


def fetch_dart(session: requests.Session, date: str) -> list[ListItem]:
    """DART '최근공시' 일자별 전체 목록(개인 제출 보고서 포함)."""
    items: list[ListItem] = []
    sel = date.replace("-", ".")
    for page in range(1, 60):
        resp = session.post(
            DART_LIST_URL,
            data={
                "currentPage": str(page),
                "maxResults": "100",
                "maxLinks": "10",
                "sort": "",
                "series": "",
                "pageGrouping": "A",
                "mdayCnt": "",
                "selectDate": sel,
                "textCrpCik": "",
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "lxml")
        rows = [tr for tr in soup.select("tbody tr") if tr.select_one("a[id^=r_]")]
        if not rows:
            break
        for tr in rows:
            tds = tr.find_all("td")
            span = tds[1].select_one("span[class^=tagCom]")
            market = _market_from(" ".join(span.get("class", []))) if span else None
            if not market:
                continue
            a = tr.select_one("a[id^=r_]")
            ca = tds[1].select_one("a")
            cm = re.search(r"openCorpInfoNew\('(\d+)'", ca.get("href", "")) if ca else None
            items.append(
                ListItem(
                    id=a["id"][2:],
                    date=date,
                    time=_clean(tds[0].get_text()),
                    market=market,
                    corp=_clean(ca.get_text()) if ca else "",
                    title=_clean(a.get_text()),
                    filer=_clean(tds[3].get_text()),
                    corp_code=cm.group(1) if cm else "",
                )
            )
        if len(rows) < 100:
            break
        time.sleep(0.4)
    return items


def fetch_list(session: requests.Session, date: str) -> tuple[list[ListItem], str]:
    """KIND 우선, 실패 시 DART. (목록, 사용한 소스명) 반환."""
    try:
        items = fetch_kind(session, date)
        if items:
            return items, "KIND"
        print(f"[list] KIND returned 0 rows for {date}, trying DART")
    except Exception as e:  # noqa: BLE001 - 어떤 실패든 DART로 대체
        print(f"[list] KIND failed ({e.__class__.__name__}: {e}); falling back to DART")
    return fetch_dart(session, date), "DART"
