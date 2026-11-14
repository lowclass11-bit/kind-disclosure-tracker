"""가격·거래량 지표 (Yahoo Finance 차트). '지금 잠잠한가'를 수치로 본다."""
from __future__ import annotations

import statistics

from . import net

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
BENCH = {"KR": "^KS11", "US": "SPY"}


def history(sym: str, rng: str = "2y") -> dict | None:
    d = net.get(CHART.format(sym=sym), params={"range": rng, "interval": "1d"}, ttl=4 * 3600)
    res = (d.get("chart") or {}).get("result") or []
    if not res:
        return None
    r = res[0]
    q = (r.get("indicators") or {}).get("quote") or [{}]
    closes, vols = q[0].get("close") or [], q[0].get("volume") or []
    pts = [(t, c, v or 0) for t, c, v in zip(r.get("timestamp") or [], closes, vols) if c]
    if len(pts) < 130:
        return None
    meta = r.get("meta") or {}
    return {"t": [p[0] for p in pts], "c": [p[1] for p in pts], "v": [p[2] for p in pts],
            "name": meta.get("longName") or meta.get("shortName") or "",
            "currency": meta.get("currency", "")}


def _ret(c: list[float], days: int) -> float | None:
    if len(c) <= days:
        return None
    return c[-1] / c[-1 - days] - 1


def _stdev_ret(c: list[float]) -> float:
    r = [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
    return statistics.pstdev(r) if len(r) > 2 else 0.0


def beta(h: dict, bench: dict, days: int = 252) -> float | None:
    """최근 1년 일간 수익률로 본 시장 민감도. 시장이 1% 움직일 때 이 종목이 평균 몇 % 움직였는지."""
    ref = {t // 86400: c for t, c in zip(bench["t"], bench["c"])}
    pairs = [(c, ref[t // 86400]) for t, c in zip(h["t"], h["c"]) if t // 86400 in ref][-days - 1:]
    if len(pairs) < 120:
        return None
    rs = [(pairs[i][0] / pairs[i - 1][0] - 1, pairs[i][1] / pairs[i - 1][1] - 1) for i in range(1, len(pairs))]
    ms, mb = statistics.fmean(x for x, _ in rs), statistics.fmean(y for _, y in rs)
    var = sum((y - mb) ** 2 for _, y in rs)
    if var == 0:
        return None
    return max(0.3, min(2.5, sum((x - ms) * (y - mb) for x, y in rs) / var))


def metrics(h: dict, bench: dict | None, use_beta: bool = False) -> dict:
    """use_beta면 '시장 대비'를 단순 차이가 아니라 베타를 감안한 초과수익(수익률 − 베타×시장)으로 잰다."""
    c, v = h["c"], h["v"]
    year = c[-252:]
    hi, lo = max(year), min(year)
    m = {
        "price": c[-1],
        "ret_1m": _ret(c, 21), "ret_3m": _ret(c, 63), "ret_6m": _ret(c, 126), "ret_12m": _ret(c, 252),
        "hi52": hi, "lo52": lo,
        "pos52": (c[-1] - lo) / (hi - lo) if hi > lo else 0.5,
        "from_hi": c[-1] / hi - 1,
        "ma50": statistics.fmean(c[-50:]), "ma200": statistics.fmean(c[-200:]),
    }
    v20, v250 = statistics.fmean(v[-20:]), statistics.fmean(v[-250:])
    m["vol_ratio"] = v20 / v250 if v250 else 1.0
    s20, s250 = _stdev_ret(c[-21:]), _stdev_ret(c[-251:])
    m["vola_ratio"] = s20 / s250 if s250 else 1.0
    if bench:
        b = bench["c"]
        bt = beta(h, bench) if use_beta else None
        if bt is not None:
            m["beta"] = round(bt, 2)
        for k, d in (("1m", 21), ("3m", 63), ("6m", 126)):
            own, ref = m[f"ret_{k}"], _ret(b, d)
            m[f"rel_{k}"] = own - (bt or 1.0) * ref if own is not None and ref is not None else None
    return m


def _lin(x: float, lo: float, hi: float) -> float:
    """x가 lo일 때 0, hi일 때 100 (lo>hi면 뒤집힘)."""
    if hi == lo:
        return 0.0
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))


def quiet_score(m: dict) -> tuple[float, bool, list[str], dict]:
    """소외도 0~100, 이미 뜨거운지 여부, 근거 문장, 계산 내역(화면에 그대로 보여 준다)."""
    rel6 = m.get("rel_6m") if m.get("rel_6m") is not None else (m.get("ret_6m") or 0)
    rel1 = m.get("rel_1m") or 0
    a = _lin(rel6, 0.20, -0.30)               # 6개월 시장 대비 부진할수록
    b = _lin(m["pos52"], 0.90, 0.10)           # 52주 범위 하단일수록
    c = _lin(m["vol_ratio"], 1.30, 0.60)       # 거래량이 말랐을수록
    d = _lin(m["vola_ratio"], 1.30, 0.60)      # 변동성이 줄었을수록
    turn = (50 if rel1 > 0 else 0) + (50 if m["price"] >= m["ma50"] else 0)  # 바닥 다지기
    score = 0.35 * a + 0.25 * b + 0.15 * c + 0.10 * d + 0.15 * turn
    hot = rel6 > 0.25 or (m.get("ret_3m") or 0) > 0.40 or m["pos52"] > 0.90
    versus = f"베타 {m['beta']:.2f} 감안 시 시장 대비" if m.get("beta") else "시장 대비"
    why = [f"6개월 수익률 {pct(m.get('ret_6m'))} ({versus} {pct(rel6, sign=True)})",
           f"52주 범위의 {m['pos52'] * 100:.0f}% 위치 (고점 대비 {pct(m['from_hi'])})"]
    if m["vol_ratio"] < 0.8:
        why.append(f"최근 20일 거래량이 1년 평균의 {m['vol_ratio'] * 100:.0f}%로 줄어듦")
    if turn == 100 and rel6 < 0:
        why.append("최근 1개월은 시장보다 강하고 50일선 위 — 바닥을 다지는 모습")
    elif m["price"] < m["ma200"] and rel1 < 0:
        why.append("200일선 아래에서 최근 1개월도 약세 — 아직 하락 추세")
    parts = {
        "rel6": round(rel6, 4), "a": round(a, 1),                       # 가중치 35%
        "pos52": round(m["pos52"], 3), "b": round(b, 1),                 # 25%
        "vol": round(m["vol_ratio"], 3), "c": round(c, 1),               # 15%
        "vola": round(m["vola_ratio"], 3), "d": round(d, 1),             # 10%
        "rel1_up": rel1 > 0, "above50": m["price"] >= m["ma50"], "turn": turn,  # 15%
        "beta": m.get("beta"), "base": round(score, 1),
    }
    return round(score, 1), hot, why, parts


def pct(x, sign: bool = False) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:+.1f}%" if sign else f"{x * 100:.1f}%"
