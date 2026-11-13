"""HTTP 공통: 재시도, 호스트별 속도 제한, 디스크 캐시, 병렬 실행."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TIMEOUT = 25

# 호스트별 최소 요청 간격(초). SEC는 초당 10건 제한을 공지하고 있다.
MIN_GAP = {"www.sec.gov": 0.13, "data.sec.gov": 0.13, "efts.sec.gov": 0.13,
           "news.google.com": 0.25}
DEFAULT_GAP = 0.03

_session: requests.Session | None = None
_gate_lock = threading.Lock()
_next_ok: dict[str, float] = {}


class FetchError(Exception):
    pass


def session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        retry = Retry(total=3, backoff_factor=0.8, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=None)
        s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
        s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"})
        _session = s
    return _session


def _wait(host: str) -> None:
    gap = MIN_GAP.get(host, DEFAULT_GAP)
    with _gate_lock:
        now = time.monotonic()
        at = max(now, _next_ok.get(host, 0.0))
        _next_ok[host] = at + gap
    if at > now:
        time.sleep(at - now)


def sec_headers() -> dict:
    contact = config.load().get("sec_contact") or "contact@example.com"
    return {"User-Agent": f"stock-scout research tool {contact}"}


def get(url: str, *, ttl: float = 6 * 3600, kind: str = "json", headers: dict | None = None,
        params: dict | None = None, cache_key: str | None = None):
    """GET 후 JSON(dict/list) 또는 텍스트 반환. ttl초 동안 디스크 캐시를 쓴다.

    cache_key를 주면 그 값으로 캐시한다(주소에 API 키가 들어가는 경우 키를 파일명 계산에서 빼려고).
    """
    key_src = cache_key or (url + "?" + json.dumps(params or {}, sort_keys=True))
    path = config.CACHE / (hashlib.sha1(key_src.encode()).hexdigest() + ".json")
    if ttl > 0 and path.exists() and time.time() - path.stat().st_mtime < ttl:
        cached = config.read_json(path)
        if cached is not None:
            return cached["v"]
    host = urlparse(url).netloc
    _wait(host)
    try:
        resp = session().get(url, params=params, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise FetchError(f"{host}: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise FetchError(f"{host}: HTTP {resp.status_code}")
    if kind == "json":
        try:
            value = resp.json()
        except ValueError as exc:
            raise FetchError(f"{host}: JSON 아님") from exc
    elif kind == "bytes":
        return resp.content
    else:
        resp.encoding = resp.encoding or "utf-8"
        value = resp.text
    if ttl > 0:
        config.write_json(path, {"v": value})
    return value


def pmap(fn, items, workers: int = 8, on_done=None) -> list:
    """items 각각에 fn을 병렬 적용. 실패한 항목은 None. 순서는 입력과 같다."""
    items = list(items)
    out = [None] * len(items)
    done = [0]
    lock = threading.Lock()

    def run(i):
        try:
            out[i] = fn(items[i])
        except FetchError:
            out[i] = None
        except Exception:  # 한 종목의 파싱 오류가 전체 수집을 멈추지 않게
            out[i] = None
        if on_done:
            with lock:
                done[0] += 1
                on_done(done[0], len(items))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(run, range(len(items))))
    return out


# ---------------------------------------------------------------- Yahoo crumb
_crumb: str | None = None
_crumb_lock = threading.Lock()


def yahoo_crumb() -> str:
    """Yahoo quoteSummary는 쿠키와 crumb이 필요하다."""
    global _crumb
    with _crumb_lock:
        if _crumb:
            return _crumb
        s = session()
        try:
            s.get("https://fc.yahoo.com", timeout=TIMEOUT)
            r = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise FetchError("yahoo crumb 실패") from exc
        if r.status_code != 200 or not r.text or "<" in r.text:
            raise FetchError("yahoo crumb 실패")
        _crumb = r.text.strip()
        return _crumb
