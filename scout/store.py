"""관심종목 추적표와 주간 섹터 브리핑."""
from __future__ import annotations

import datetime as dt
import threading

from . import config, net, prices
from .pipeline import PILLARS
from .writer import DISCLAIMER

WATCH = config.DATA / "watchlist.json"
_lock = threading.Lock()


def _yahoo(item: dict) -> str:
    if item["market"] == "US":
        return item["ticker"]
    return f"{item['ticker']}.{'KQ' if item.get('exchange') == 'KOSDAQ' else 'KS'}"


def _last(sym: str) -> float | None:
    try:
        h = prices.history(sym, "1y")
    except net.FetchError:
        return None
    return h["c"][-1] if h else None


def watch_list() -> list[dict]:
    return config.read_json(WATCH, [])


def watch_add(item: dict, date: str, note: str = "") -> list[dict]:
    with _lock:
        rows = watch_list()
        if any(r["id"] == item["id"] for r in rows):
            return rows
        why = [e["t"] for e in item.get("ev", []) if e["pts"] > 0][:3]
        rows.append({
            "id": item["id"], "name": item["name"], "market": item["market"], "ticker": item["ticker"],
            "sector": item["sector"], "yahoo": _yahoo(item), "url": item.get("url", ""),
            "added": date, "price0": item["price"], "score0": item["score"], "pillars0": item["pillars"],
            "bench0": _last(prices.BENCH[item["market"]]), "why": why, "note": note,
        })
        config.write_json(WATCH, rows)
        return rows


def watch_remove(item_id: str) -> list[dict]:
    with _lock:
        rows = [r for r in watch_list() if r["id"] != item_id]
        config.write_json(WATCH, rows)
        return rows


def watch_view() -> list[dict]:
    """현재가를 받아 발굴 이후 수익률과 시장 대비 초과수익을 계산한다."""
    rows = watch_list()
    today = dt.date.today()
    bench_now = {m: _last(s) for m, s in prices.BENCH.items()} if rows else {}
    now = net.pmap(lambda r: _last(r["yahoo"]), rows, workers=6)
    out = []
    for r, p in zip(rows, now):
        v = dict(r)
        added = dt.date.fromisoformat(r["added"])
        v["days"] = (today - added).days
        v["review"] = (added + dt.timedelta(days=182)).isoformat()
        v["price"] = p
        v["ret"] = p / r["price0"] - 1 if p and r["price0"] else None
        b0, b1 = r.get("bench0"), bench_now.get(r["market"])
        v["bench_ret"] = b1 / b0 - 1 if b0 and b1 else None
        v["excess"] = v["ret"] - v["bench_ret"] if v["ret"] is not None and v["bench_ret"] is not None else None
        out.append(v)
    return out


def watch_csv() -> str:
    head = "종목,시장,업종,발굴일,발굴가,현재가,수익률,시장수익률,초과수익,경과일,6개월재평가일,발굴근거"
    lines = [head]
    for v in watch_view():
        f = lambda x: "" if x is None else f"{x * 100:.1f}%"
        why = " / ".join(v["why"]).replace('"', "'")
        lines.append(f'"{v["name"]}",{v["market"]},"{v["sector"]}",{v["added"]},{v["price0"]},'
                     f'{v["price"] or ""},{f(v["ret"])},{f(v["bench_ret"])},{f(v["excess"])},{v["days"]},{v["review"]},"{why}"')
    return "﻿" + "\n".join(lines)


# ---------------------------------------------------------------- 주간 브리핑
def _snapshots() -> list[dict]:
    return [d for d in (config.read_json(p) for p in sorted(config.HISTORY.glob("*.json"))) if d]


def briefing(data: dict) -> str:
    snaps = _snapshots()
    today = dt.date.fromisoformat(data["date"])
    cur = next((s for s in snaps if s["date"] == data["date"]), None)
    older = [s for s in snaps if s["date"] <= (today - dt.timedelta(days=5)).isoformat()]
    prev = older[-1] if older else None
    out = [f"# 주간 섹터 브리핑 — {data['date']}", f"투자 스타일 {data['style_label']}", ""]

    out.append("## 거시 환경")
    rows = data.get("macro", {}).get("rows", [])
    labels = data.get("macro", {}).get("labels", [])
    out.append("활성 신호: " + (", ".join(labels) if labels else "없음 — 어느 업종에도 뚜렷한 순풍이 없다"))
    out += [f"- {r['name']}: {r['value']} ({r['change']}) — {r['note']}" for r in rows]
    out.append("")

    cands = [x for x in data["sectors"] if x.get("candidate")]
    out.append("## 잠잠한데 신호가 쌓이는 업종 (상위 10)")
    out.append("| 업종 | 시장 | 종합 | " + " | ".join(PILLARS.values()) + " | 변화 |")
    out.append("|---|---|---|---|---|---|---|---|")
    for x in cands[:10]:
        delta = ""
        if prev and x["id"] in prev["sectors"]:
            d = x["score"] - prev["sectors"][x["id"]]["score"]
            delta = f"{d:+.1f}"
        p = x["pillars"]
        out.append(f"| {x['name']} | {x['market']} | {x['score']:.0f} | {p['quiet']:.0f} | {p['smart']:.0f} | "
                   f"{p['rev']:.0f} | {p['cat']:.0f} | {delta or '–'} |")
    out.append("")
    for x in cands[:5]:
        lead = ", ".join(m["name"] for m in x.get("leaders", [])[:3])
        out.append(f"**{x['name']}** — {lead or '후보 종목 없음'}")
        out += [f"- {e['t']}" for e in x["ev"][:3]]
        out.append("")

    if prev and cur:
        out.append(f"## 지난 기록({prev['date']}) 이후 달라진 것")
        moves = sorted(((sid, v["score"] - prev["sectors"][sid]["score"], v) for sid, v in cur["sectors"].items()
                        if sid in prev["sectors"]), key=lambda t: -t[1])
        up = [m for m in moves if m[1] >= 2][:6]
        down = [m for m in reversed(moves) if m[1] <= -2][:4]
        out.append("**점수가 오른 업종**")
        out += [f"- {v['name']} ({v['market']}) {d:+.1f} → {v['score']:.0f}" for _, d, v in up] or ["- 없음"]
        out.append("**점수가 내린 업종**")
        out += [f"- {v['name']} ({v['market']}) {d:+.1f} → {v['score']:.0f}" for _, d, v in down] or ["- 없음"]
        new = [v for sid, v in cur["stocks"].items() if sid not in prev["stocks"]]
        gone = [v for sid, v in prev["stocks"].items() if sid not in cur["stocks"]]
        new.sort(key=lambda v: -v["score"])
        out.append(f"**새로 후보에 든 종목 ({len(new)})**")
        out += [f"- {v['name']} ({v['sector']}) {v['score']:.0f}점" for v in new[:10]] or ["- 없음"]
        out.append(f"**후보에서 빠진 종목 ({len(gone)})** — 주가가 이미 올랐거나 근거가 사라짐")
        out += [f"- {v['name']} ({v['sector']})" for v in gone[:10]] or ["- 없음"]
        out.append("")
    else:
        out += ["## 지난 기록과의 비교", "아직 비교할 기록이 없습니다. 5일 이상 간격을 두고 '자료 받기'를 다시 실행하면 "
                "업종 점수 변화와 새 후보가 여기에 표시됩니다.", ""]

    stocks = [s for s in data["stocks"] if s.get("candidate")]
    tb = data.get("trend_base")
    rising = sorted((s for s in stocks if (s.get("trend") or {}).get("d", 0) >= 3), key=lambda s: -s["trend"]["d"])
    out.append(f"## 근거가 쌓이는 종목 ({tb['date']} 대비)" if tb else "## 근거가 쌓이는 종목")
    if not tb:
        out.append("비교할 지난 기록이 아직 없습니다. 다음 수집부터 표시됩니다.")
    names = {"smart": "수급", "rev": "실적", "cat": "계기"}
    for s in rising[:10]:
        ax = ", ".join(f"{names[k]} {v:+.0f}" for k, v in s["trend"]["axes"].items() if abs(v) >= 1)
        out.append(f"- **{s['name']}** ({s['sector']}, {s['market']}) 근거 점수 {s['trend']['d']:+.1f}"
                   + (" · 새로 등장" if s["trend"]["new"] else "") + (f" — {ax}" if ax else ""))
    if tb and not rising:
        out.append("- 근거 점수가 3점 이상 늘어난 후보가 없습니다.")
    out.append("")
    out.append("## 근거가 가장 많이 겹친 종목")
    for s in sorted(stocks, key=lambda s: (-s["confirm"], -s["score"]))[:8]:
        out.append(f"- **{s['name']}** ({s['sector']}, {s['market']}) {s['score']:.0f}점 — "
                   + " / ".join(e["t"] for e in s["ev"][:2]))
    out.append("")

    watch = watch_view()
    if watch:
        out.append("## 추적표 점검")
        for v in watch:
            f = lambda x: "n/a" if x is None else f"{x * 100:+.1f}%"
            out.append(f"- {v['name']}: 발굴 {v['added']} 이후 {f(v['ret'])} (시장 대비 {f(v['excess'])}), "
                       f"{v['days']}일 경과 · 재평가일 {v['review']}")
        out.append("")
    out.append(DISCLAIMER)
    return "\n".join(out)
