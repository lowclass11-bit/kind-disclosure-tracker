"""GitHub Pages용 정적 대시보드 자료 만들기.

    python -m scout.publish            # 수집 → docs/scout/data/ 에 저장

GitHub Actions(.github/workflows/scout.yml)가 평일마다 실행한다. 화면(docs/scout/)은 이 파일들만 읽는다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import statistics
import sys
import time

from . import config, pipeline, store, writer

OUT = config.REPO / "docs" / "scout" / "data"
ITEMS = OUT / "items"
DOC_KINDS = ("memo", "script")   # 메모에 시나리오·반론이 모두 들어 있다
TOP_KR, TOP_US, TOP_SECTORS = 40, 25, 16


def item_key(item_id: str) -> str:
    return hashlib.sha1(item_id.encode()).hexdigest()[:12]


def _stock(s: dict, full: bool) -> dict:
    keep = ("id", "kind", "market", "ticker", "name", "sector", "sector_id", "exchange", "mcap", "url", "price",
            "hot", "candidate", "score", "pillars", "confirm", "ret_1m", "ret_6m", "rel_6m", "pos52", "from_hi",
            "below_200", "per", "pbr", "fwd_pe", "next_earnings", "beta", "rank", "rank_of", "lo52",
            "quiet_parts", "quiet_base", "core", "trend", "recent_ev")
    out = {k: s.get(k) for k in keep}
    out["ev"] = s["ev"] if full else s["ev"][:2]   # 후보는 근거를 하나도 빼지 않는다
    return out


def _sector(x: dict) -> dict:
    keep = ("id", "kind", "market", "name", "etf", "n", "pillars", "score", "hot", "rel_6m", "breadth", "trend_d",
            "leaders", "candidate", "url")
    out = {k: x.get(k) for k in keep}
    out["ev"] = x["ev"]
    return out


def track_record(data: dict) -> list[dict]:
    """지난 실행에서 후보였던 종목들이 지금까지 얼마나 움직였는지 (시장 대비)."""
    price_now = {s["id"]: s["price"] for s in data["stocks"]}
    bench_now = data.get("bench", {})
    rows = []
    today = dt.date.fromisoformat(data["date"])
    for path in sorted(config.HISTORY.glob("*.json")):
        snap = config.read_json(path)
        if not snap or snap["date"] >= data["date"]:
            continue
        days = (today - dt.date.fromisoformat(snap["date"])).days
        if days < 7:
            continue
        rets, excess = [], []
        for sid, v in snap["stocks"].items():
            now = price_now.get(sid)
            if not now or not v.get("price"):
                continue
            r = now / v["price"] - 1
            rets.append(r)
            b0, b1 = (snap.get("bench") or {}).get(sid[:2]), bench_now.get(sid[:2])
            if b0 and b1:
                excess.append(r - (b1 / b0 - 1))
        if len(rets) >= 5:
            rows.append({"date": snap["date"], "days": days, "n": len(rets),
                         "avg": round(statistics.fmean(rets), 4),
                         "win": round(sum(r > 0 for r in rets) / len(rets), 3),
                         "excess": round(statistics.fmean(excess), 4) if excess else None})
    return rows[-26:]


def main() -> int:
    pipeline.JOB.echo = True
    pipeline.JOB.running, pipeline.JOB.started = True, time.time()
    try:
        pipeline.collect()
    finally:
        pipeline.JOB.running = False
    data = pipeline.latest()
    if not data or not data.get("stocks"):
        print("[scout] 결과가 비어 있어 대시보드를 갱신하지 않습니다.", file=sys.stderr)
        return 1
    cfg = config.load()
    OUT.mkdir(parents=True, exist_ok=True)
    ITEMS.mkdir(parents=True, exist_ok=True)

    stocks = data["stocks"]
    cands = [s for s in stocks if s.get("candidate")]
    detail = [s for s in cands if s["rank"] <= (TOP_KR if s["market"] == "KR" else TOP_US)]
    sec_c = [x for x in data["sectors"] if x.get("candidate")]
    detail_sec = [x for m in ("KR", "US") for x in [y for y in sec_c if y["market"] == m][:TOP_SECTORS // 2]]

    # 문서: 상위 후보만 미리 만들어 둔다 (사실 카드는 화면이 근거 목록으로 직접 그린다)
    keep_files = set()
    for item in detail + detail_sec:
        key = item_key(item["id"])
        docs = {k: writer.RULES[k](item, data) for k in DOC_KINDS}
        config.write_json(ITEMS / f"{key}.json", {"id": item["id"], "name": item["name"], "docs": docs})
        keep_files.add(f"{key}.json")
    for f in ITEMS.glob("*.json"):
        if f.name not in keep_files:
            f.unlink()

    # AI 메모: 키가 있을 때 상위 N개만 (비용 때문에 기본 3개)
    ai_top = int(os.environ.get("SCOUT_AI_TOP") or 3) if cfg["anthropic_key"] else 0
    ai_done = 0
    rec = [s for s in detail if s["confirm"] >= 2][:ai_top]
    for item in rec:
        path = ITEMS / f"{item_key(item['id'])}.json"
        body = config.read_json(path)
        try:
            body["docs"]["ai_memo"] = writer.ai_write("memo", body["docs"]["memo"], writer.ai_payload(item, data), "", cfg)
            config.write_json(path, body)
            ai_done += 1
        except RuntimeError as exc:
            print(f"[scout] AI 메모 실패({item['name']}): {exc}", file=sys.stderr)

    has_doc = {i["id"] for i in detail + detail_sec}
    board = {
        "date": data["date"], "generated_at": data["generated_at"], "style_label": data["style_label"],
        "weights": data["weights"], "pillars": pipeline.PILLARS, "macro": data["macro"],
        "sources": data["sources"],
        "bar": data.get("bar", {}), "trend_base": data.get("trend_base"),
        "sectors": [dict(_sector(x), doc=item_key(x["id"]) if x["id"] in has_doc else None)
                    for x in data["sectors"]],
        "stocks": [dict(_stock(s, True), doc=item_key(s["id"]) if s["id"] in has_doc else None)
                   for s in cands],
        "excluded": [_stock(s, False) for s in sorted((s for s in stocks if s["hot"]),
                                                      key=lambda s: -(s.get("rel_6m") or 0))[:80]],
        "stats": {"universe": len(stocks), "candidates": len(cands), "hot": sum(s["hot"] for s in stocks),
                  "kr": sum(s["market"] == "KR" for s in stocks), "us": sum(s["market"] == "US" for s in stocks),
                  "hot_kr": sum(s["hot"] and s["market"] == "KR" for s in stocks),
                  "hot_us": sum(s["hot"] and s["market"] == "US" for s in stocks), "ai_memos": ai_done},
        "track": track_record(data),
        "prices": {s["id"]: s["price"] for s in stocks},     # 추적표(브라우저 저장)의 현재가 계산용
    }
    config.write_json(OUT / "dashboard.json", board)
    config.write_json(OUT / "briefing.json", {"date": data["date"], "text": store.briefing(data)})
    print(f"[scout] 대시보드 저장: 후보 {len(cands)} · 문서 {len(keep_files)} · AI 메모 {ai_done}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
