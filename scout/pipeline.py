"""수집 → 소외 필터 → 신호 결합 → 점수. 결과는 data/latest.json 에 저장한다."""
from __future__ import annotations

import datetime as dt
import re
import statistics
import threading
import time
import traceback

from . import config, macro, net, prices, sources_kr, sources_us, universe
from .sources_kr import ev

PILLARS = {"quiet": "소외도", "smart": "스마트머니", "rev": "실적 추정", "cat": "촉매"}
LATEST = config.DATA / "latest.json"
CNS_HISTORY = config.STATE / "consensus_history.json"

STEPS = ["유니버스", "가격·소외 필터", "스마트머니", "실적 추정", "촉매·거시", "점수 계산"]


class Job:
    """수집 작업 한 건의 진행 상태. 화면이 1초마다 읽어 간다."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.step = -1
        self.progress = ""
        self.log: list[dict] = []
        self.sources: dict[str, dict] = {}
        self.error = ""
        self.started = 0.0
        self.echo = False      # 명령줄 실행(publish)에서는 진행 상황을 화면에도 찍는다

    def say(self, text: str) -> None:
        if self.echo:
            print(f"[scout] {text}", flush=True)
        with self.lock:
            self.log.append({"t": dt.datetime.now().strftime("%H:%M:%S"), "m": text})
            self.log = self.log[-200:]

    def src(self, name: str, count=None, state: str = "받음", note: str = "") -> None:
        with self.lock:
            self.sources[name] = {"count": count, "state": state, "note": note}

    def snapshot(self) -> dict:
        with self.lock:
            return {"running": self.running, "step": self.step, "steps": STEPS, "progress": self.progress,
                    "log": list(reversed(self.log[-80:])), "sources": dict(self.sources), "error": self.error}


JOB = Job()


def start() -> bool:
    with JOB.lock:
        if JOB.running:
            return False
        JOB.running, JOB.error, JOB.step, JOB.started = True, "", 0, time.time()
        JOB.log, JOB.sources = [], {}
    threading.Thread(target=_run, daemon=True).start()
    return True


def _run() -> None:
    try:
        collect()
        JOB.say(f"완료 ({time.time() - JOB.started:.0f}초)")
    except Exception as exc:  # 화면에 원인을 보여 주고 서버는 계속 돈다
        JOB.error = f"{type(exc).__name__}: {exc}"
        JOB.say(f"중단: {JOB.error}")
        traceback.print_exc()
    finally:
        JOB.running, JOB.progress = False, ""


def _progress(label):
    def cb(done, total):
        if done % 10 == 0 or done == total:
            JOB.progress = f"{label} {done}/{total}"
    return cb


def _guard(name: str, fn, default):
    """소스 하나가 죽어도 나머지는 계속 받는다. 실패는 소스 목록에 그대로 표시."""
    try:
        return fn()
    except net.FetchError as exc:
        JOB.src(name, None, "실패", str(exc))
        JOB.say(f"{name} 실패: {exc}")
    except Exception as exc:
        JOB.src(name, None, "실패", f"{type(exc).__name__}: {exc}")
        JOB.say(f"{name} 오류: {type(exc).__name__}: {exc}")
        traceback.print_exc()
    return default


def collect() -> None:
    cfg = config.load()
    style = config.STYLES[cfg["style"]]
    say = JOB.say
    say(f"자료 받기 시작 — 투자 스타일: {style['label']}")
    _prune_cache()

    # ① 유니버스
    JOB.step = 0
    kr = _guard("네이버 증권 업종", lambda: universe.build_kr(style, say), [])
    JOB.src("네이버 증권 업종", len(kr)) if kr else None
    us = universe.build_us(say)
    stocks = kr + us

    # ② 가격·소외 필터
    JOB.step = 1
    bench = {m: _guard(f"벤치마크 {s}", lambda s=s: prices.history(s), None) for m, s in prices.BENCH.items()}
    bench_close = {m: round(h["c"][-1], 2) for m, h in bench.items() if h}
    hist = net.pmap(lambda s: prices.history(s["yahoo"]), stocks, workers=8, on_done=_progress("가격"))
    alive = []
    for s, h in zip(stocks, hist):
        if not h:
            continue
        s["m"] = prices.metrics(h, bench.get(s["market"]))
        if s["market"] == "US" and h["name"]:
            s["name"] = h["name"]
        q, hot, why = prices.quiet_score(s["m"])
        s["quiet"], s["hot"] = q, hot
        s["ev"] = [ev("quiet", w, "Yahoo Finance 시세", s["url"], pts=0) for w in why]
        alive.append(s)
    stocks = alive
    JOB.src("Yahoo Finance 시세", len(stocks))
    say(f"가격 지표 {len(stocks)}종목 — 이미 뜨거운 종목 {sum(s['hot'] for s in stocks)}개는 후보에서 제외")

    # 상세 자료는 '잠잠한' 종목 상위만 받는다
    def shortlist(market, n):
        pool = sorted((s for s in stocks if s["market"] == market and not s["hot"]), key=lambda s: -s["quiet"])
        return pool[:n]

    kr_short, us_short = shortlist("KR", 140), shortlist("US", 110)
    say(f"상세 조사 대상: 한국 {len(kr_short)} · 미국 {len(us_short)}")

    # ③ 스마트머니
    JOB.step = 2
    res = net.pmap(sources_kr.flows, kr_short, workers=8, on_done=_progress("수급"))
    _attach(kr_short, res)
    JOB.src("네이버 투자자별 매매(외국인·기관)", sum(1 for r in res if r is not None))

    track = _guard("KIND 공시 트래커", sources_kr.tracker, {})
    if track:
        n = 0
        for s in stocks:
            if s["market"] == "KR" and s["name"] in track:
                s["ev"] += sources_kr.tracker_evidence(track[s["name"]])
                n += 1
        JOB.src("KIND 공시 트래커(지분·시설투자·계약)", sum(len(v) for v in track.values()),
                note=f"유니버스 {n}종목에 해당")
    else:
        JOB.src("KIND 공시 트래커(지분·시설투자·계약)", 0, "없음", "docs/data/all.json 이 비어 있음")

    ciks = _guard("SEC 종목 목록", sources_us.cik_map, {})
    if ciks:
        targets = [s for s in us_short if s["ticker"] in ciks]
        res = net.pmap(lambda s: sources_us.form4(s, ciks[s["ticker"]]["cik"]), targets, workers=4,
                       on_done=_progress("내부자 매수"))
        _attach(targets, res)
        JOB.src("SEC Form 4 내부자 매수", sum(len(r or []) for r in res), note=f"{len(targets)}종목 조회")
        funds = _guard("SEC 13F", lambda: sources_us.thirteenf(say), {})
        if funds:
            n = 0
            for s in stocks:
                if s["market"] != "US" or s["ticker"] not in ciks:
                    continue
                rows = funds.get(sources_us.norm_name(ciks[s["ticker"]]["title"]))
                if rows:
                    s["ev"] += sources_us.thirteenf_evidence(rows)
                    n += 1
            JOB.src("SEC 13F 유명 펀드 신규 편입", sum(len(v) for v in funds.values()), note=f"유니버스 {n}종목에 해당")

    # ④ 실적 추정
    JOB.step = 3
    res = net.pmap(sources_kr.fundamentals, kr_short, workers=8, on_done=_progress("컨센서스"))
    _attach(kr_short, res)
    JOB.src("네이버 컨센서스·재무", sum(1 for r in res if r is not None))
    _consensus_drift(kr_short)

    reports = _guard("네이버 리서치(종목)", sources_kr.research_company, {})
    if reports:
        for s in stocks:
            if s["market"] == "KR" and s["ticker"] in reports:
                s["ev"] += sources_kr.research_evidence(reports[s["ticker"]])
                s["reports"] = len(reports[s["ticker"]])
        JOB.src("네이버 리서치(종목 리포트)", sum(len(v) for v in reports.values()))
    ind_reports = _guard("네이버 리서치(산업)", sources_kr.research_industry, {})
    if ind_reports:
        JOB.src("네이버 리서치(산업 리포트)", sum(len(v) for v in ind_reports.values()))

    yahoo_ok = _guard("Yahoo 추정치", lambda: bool(net.yahoo_crumb()), False)
    if yahoo_ok:
        res = net.pmap(sources_us.quote_summary, us_short, workers=6, on_done=_progress("추정치"))
        _attach(us_short, res)
        JOB.src("Yahoo 애널리스트 추정치", sum(1 for r in res if r is not None))
        floor = style["us_mcap"]
        for s in us_short:
            if 0 < s.get("mcap", 0) < floor:
                s["too_small"] = True

    if cfg["dart_key"]:
        codes = _guard("DART OpenAPI", lambda: sources_kr.dart_corp_codes(cfg["dart_key"]), {})
        if codes:
            res = net.pmap(lambda s: sources_kr.dart_quarter(s, cfg["dart_key"], codes), kr_short[:80],
                           workers=4, on_done=_progress("DART 재무"))
            _attach(kr_short[:80], res)
            JOB.src("DART 정기보고서 재무", sum(len(r or []) for r in res), note="상위 80종목 조회")
    else:
        JOB.src("DART 정기보고서 재무", None, "키 필요", "설정에서 DART 키 입력")

    export_rows = []
    if cfg["datago_key"]:
        export_rows = _guard("관세청 수출 통계", lambda: sources_kr.exports(cfg["datago_key"]), [])
        if export_rows:
            JOB.src("관세청 품목별 수출", len(export_rows))
    else:
        JOB.src("관세청 품목별 수출", None, "키 필요", "설정에서 공공데이터포털 키 입력")

    # ⑤ 촉매·거시
    JOB.step = 4
    regime = _guard("거시 시세", lambda: macro.regime(cfg["fred_key"], say), {"tags": [], "labels": [], "rows": []})
    JOB.src("거시 지표(금리·원자재·환율)", len(regime["rows"]))
    if not cfg["fred_key"]:
        JOB.src("FRED 경제지표", None, "키 필요", "설정에서 FRED 키 입력")
    active = set(regime["tags"])
    for s in stocks:
        hit = [t for t in s.get("macro", []) if t in active]
        if hit:
            s["ev"].append(ev("cat", f"거시 환경이 유리: {', '.join(macro.TAG_LABEL[t] for t in hit)}",
                              "거시 지표", pts=min(20, 10 * len(hit))))

    sector_extra: dict[str, list[dict]] = {}
    for row in export_rows:
        sid_list = {s["sector_id"] for s in stocks if s["market"] == "KR" and any(w in s["sector"] for w in row["words"])}
        for sid in sid_list:
            sign = "증가" if row["yoy"] > 0 else "감소"
            sector_extra.setdefault(sid, []).append(
                ev("rev", f"{row['item']} 수출 최근 3개월 전년 대비 {row['yoy'] * 100:+.1f}% {sign} ({row['latest'][:4]}.{row['latest'][4:]}까지)",
                   "관세청 수출입 통계", row["url"], pts=max(-10, min(20, row["yoy"] * 80))))
    for cat, rows in (ind_reports or {}).items():
        for sid in {s["sector_id"] for s in stocks if s["market"] == "KR" and _sector_match(cat, s["sector"])}:
            last = max(rows, key=lambda r: r.get("writeDate", ""))
            sector_extra.setdefault(sid, []).append(
                ev("cat", f"산업 리포트 {len(rows)}건 — 최신: 「{last.get('title', '')}」 {last.get('brokerName', '')}",
                   "네이버 증권 리서치", last.get("endUrl", ""), last.get("writeDate", ""), pts=min(15, 3 * len(rows))))

    # 1차 점수 → 상위 후보만 뉴스 조회
    _score(stocks, style)
    top = sorted((s for s in stocks if s.get("candidate")), key=lambda s: -s["score"])[:45]
    res = net.pmap(lambda s: macro.news(f'"{_short_name(s)}"' + ("" if s["market"] == "KR" else " stock"),
                                        "ko" if s["market"] == "KR" else "en"), top, workers=4,
                   on_done=_progress("뉴스"))
    n_news = 0
    for s, items in zip(top, res):
        if items is None:
            continue
        evs, count = macro.news_evidence(items, "종목", (_short_name(s),))
        s["ev"] += evs
        s["news_count"] = count
        n_news += count
    JOB.src("Google 뉴스(상위 후보)", n_news, note=f"{len(top)}종목 조회")

    # ⑥ 점수
    JOB.step = 5
    _score(stocks, style)
    sectors = _sectors(stocks, sector_extra, style)
    top_sectors = sorted(sectors, key=lambda x: -x["score"])[:16]
    res = net.pmap(lambda x: macro.news(f"{x['name']} 업황 전망" if x["market"] == "KR"
                                        else f"{x['etf'] or x['name']} sector outlook",
                                        "ko" if x["market"] == "KR" else "en"), top_sectors, workers=4)
    for x, items in zip(top_sectors, res):
        if items:
            evs, count = macro.news_evidence(items, "업종")
            x["ev"] += evs
            x["news_count"] = count
            x["pillars"]["cat"] = min(100, x["pillars"]["cat"] + sum(e["pts"] for e in evs))
            x["score"] = _total(x["pillars"], style)

    today = dt.date.today().isoformat()
    result = {
        "date": today, "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "style": cfg["style"], "style_label": style["label"], "weights": style["w"],
        "macro": regime, "sectors": sorted(sectors, key=lambda x: -x["score"]),
        "stocks": [_slim(s) for s in sorted(stocks, key=lambda s: -s.get("score", 0))],
        "sources": JOB.snapshot()["sources"], "bench": bench_close,
    }
    config.write_json(LATEST, result)
    config.write_json(config.HISTORY / f"{today}.json", {
        "date": today, "bench": bench_close,
        "sectors": {x["id"]: {"name": x["name"], "market": x["market"], "score": x["score"], "pillars": x["pillars"]}
                    for x in sectors},
        "stocks": {s["id"]: {"name": s["name"], "score": s["score"], "price": s["m"]["price"], "sector": s["sector"]}
                   for s in stocks if s.get("candidate")},
    })
    n_cand = sum(1 for s in stocks if s.get("candidate"))
    say(f"후보 {n_cand}종목, 업종 {len(sectors)}개 점수 계산 완료")


def _short_name(s: dict) -> str:
    """뉴스 검색용 이름. 미국 종목은 'Corporation', 'Inc.' 같은 꼬리를 뗀다."""
    if s["market"] == "KR":
        return s["name"]
    name = re.sub(r"[,.]", "", s["name"])
    name = re.sub(r"\b(Corporation|Corp|Incorporated|Inc|Company|Co|Holdings|Group|plc|PLC|Ltd|Limited|NV|SA|The)\b",
                  "", name)
    return " ".join(name.split()) or s["ticker"]


def _prune_cache(days: int = 35) -> None:
    limit = time.time() - days * 86400
    for f in config.CACHE.glob("*.json"):
        try:
            if f.stat().st_mtime < limit:
                f.unlink()
        except OSError:
            pass


def _sector_match(category: str, sector: str) -> bool:
    """네이버 리서치 산업 분류(예: '화장품')와 업종명(예: '화장품')을 느슨하게 맞춘다."""
    alias = {"유틸리티": ("전기유틸리티", "가스유틸리티"), "IT": ("IT서비스", "소프트웨어"),
             "휴대폰": ("핸드셋",), "미디어": ("방송과엔터테인먼트", "광고"), "유통": ("백화점", "소매"),
             "음식료": ("식품", "음료"), "바이오": ("생물공학", "제약"), "운송": ("항공사", "해운사", "운송"),
             "기계": ("기계", "전기장비"), "에너지": ("석유와가스", "에너지장비")}
    if not category:
        return False
    if category in sector or sector in category:
        return True
    return any(a in sector for a in alias.get(category, ()))


def _attach(stocks: list[dict], results: list) -> None:
    for s, evs in zip(stocks, results):
        if evs:
            s["ev"] += evs


def _consensus_drift(kr_short: list[dict]) -> None:
    """추정 EPS를 매번 기록해 두고, 30일 전 기록과 비교한다(직접 쌓는 리비전 이력)."""
    hist = config.read_json(CNS_HISTORY, {})
    today = dt.date.today()
    for s in kr_short:
        cns = s.get("cns_eps")
        if not cns:
            continue
        rows = [r for r in hist.get(s["id"], []) if r[0] >= (today - dt.timedelta(days=200)).isoformat()]
        old = [r for r in rows if r[0] <= (today - dt.timedelta(days=21)).isoformat()]
        if old and old[-1][1] and old[-1][1] > 0:
            chg = cns / old[-1][1] - 1
            if abs(chg) >= 0.02:
                word = "상향" if chg > 0 else "하향"
                s["ev"].append(ev("rev", f"추정 EPS가 {old[-1][0]} 기록 대비 {chg * 100:+.1f}% {word}",
                                  "이 프로그램의 누적 기록", pts=max(-20, min(30, chg * 300))))
        if not rows or rows[-1][0] != today.isoformat():
            rows.append([today.isoformat(), cns])
        hist[s["id"]] = rows
    config.write_json(CNS_HISTORY, hist)


def _total(p: dict, style: dict) -> float:
    w = style["w"]
    return round(sum(p[k] * w[k] for k in w), 1)


def _score(stocks: list[dict], style: dict) -> None:
    for s in stocks:
        p = {"quiet": min(100, s["quiet"] + sum(e["pts"] for e in s["ev"] if e["p"] == "quiet"))}
        for k in ("smart", "rev", "cat"):
            p[k] = round(max(0, min(100, sum(e["pts"] for e in s["ev"] if e["p"] == k))), 1)
        p["quiet"] = round(p["quiet"], 1)
        s["pillars"], s["score"] = p, _total(p, style)
        confirm = sum(1 for k in ("smart", "rev", "cat") if p[k] >= 25)
        s["confirm"] = confirm
        # 후보 조건: 뜨겁지 않고, 충분히 소외됐고, 소외 외에 근거 축이 하나 이상 있어야 한다
        s["candidate"] = (not s["hot"]) and (not s.get("too_small")) and p["quiet"] >= 40 and confirm >= 1


def _sectors(stocks: list[dict], extra: dict[str, list[dict]], style: dict) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for s in stocks:
        groups.setdefault(s["sector_id"], []).append(s)
    out = []
    for sid, members in groups.items():
        if len(members) < 3:
            continue
        evs = list(extra.get(sid, []))
        p = {"quiet": round(statistics.median(m["quiet"] for m in members), 1)}
        for k in ("smart", "rev", "cat"):
            vals = sorted((m["pillars"][k] for m in members), reverse=True)
            top_half = vals[: max(1, len(vals) // 2)]
            base = statistics.fmean(top_half)
            p[k] = round(max(0, min(100, base + sum(e["pts"] for e in evs if e["p"] == k))), 1)
        rel6 = statistics.median((m["m"].get("rel_6m") or 0) for m in members)
        above = sum(1 for m in members if m["m"]["price"] >= m["m"]["ma50"]) / len(members)
        hot_share = sum(1 for m in members if m["hot"]) / len(members)
        evs.insert(0, ev("quiet", f"구성 종목 6개월 시장 대비 수익률 중앙값 {rel6 * 100:+.1f}%, "
                                  f"50일선 위 종목 {above * 100:.0f}%, 이미 뜨거운 종목 {hot_share * 100:.0f}%",
                         "Yahoo Finance 시세"))
        cands = sorted((m for m in members if m.get("candidate")), key=lambda m: -m["score"])
        first = members[0]
        out.append({
            "id": sid, "kind": "sector", "market": first["market"], "name": first["sector"],
            "etf": first.get("etf", ""), "n": len(members), "pillars": p, "score": _total(p, style),
            "hot": hot_share > 0.5, "rel_6m": round(rel6, 4), "breadth": round(above, 2),
            "ev": evs, "leaders": [{"id": m["id"], "name": m["name"], "score": m["score"]} for m in cands[:5]],
            "candidate": hot_share <= 0.5 and p["quiet"] >= 40,
            "url": (f"https://finance.yahoo.com/quote/{first.get('etf')}" if first.get("etf")
                    else "https://m.stock.naver.com/domestic/industry"),
        })
    return out


def _slim(s: dict) -> dict:
    m = s["m"]
    return {
        "id": s["id"], "kind": "stock", "market": s["market"], "ticker": s["ticker"], "name": s["name"],
        "sector": s["sector"], "sector_id": s["sector_id"], "exchange": s["exchange"], "mcap": s.get("mcap", 0),
        "url": s["url"], "price": round(m["price"], 2), "hot": s["hot"], "candidate": s.get("candidate", False),
        "score": s.get("score", 0), "pillars": s.get("pillars", {}), "confirm": s.get("confirm", 0),
        "ret_1m": m.get("ret_1m"), "ret_6m": m.get("ret_6m"), "rel_6m": m.get("rel_6m"),
        "pos52": round(m["pos52"], 3), "from_hi": round(m["from_hi"], 3), "lo52": round(m["lo52"], 2),
        "below_200": m["price"] < m["ma200"], "per": s.get("per"), "pbr": s.get("pbr"),
        "fwd_pe": s.get("fwd_pe"), "next_earnings": s.get("next_earnings"), "news_count": s.get("news_count"),
        "ev": sorted(s["ev"], key=lambda e: -e["pts"]),
    }


def latest() -> dict | None:
    return config.read_json(LATEST)
