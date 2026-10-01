"""일일 수집 진입점.

  python -m scraper.collect                 # 오늘(KST) + 직전 수집일 재확인
  python -m scraper.collect --date 2026-09-22
  python -m scraper.collect --backfill 10   # 최근 10일(주말 제외) 소급 수집
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .classify import classify
from .details import parse
from .sources import ListItem, fetch_lists, make_session

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
DAYS = DATA / "days"
WORKERS = 4  # DART 본문 동시 요청 수


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def _key(text: str) -> str:
    return re.sub(r"[\s\[\]()ㆍ·.,㈜]|\(주\)|주식회사", "", text)


def match_kind(dart_items: list[dict], kind: list[ListItem]) -> list[ListItem]:
    """KIND 공시를 같은 회사·같은 유형·같은 제출인의 DART 공시와 짝짓는다. 짝 없는 KIND 공시를 반환."""
    pool = [k for k in kind if classify(k.title)]
    # 1차: 제출인까지 같은 것끼리, 2차: 남은 것끼리 시각이 가까운 순
    for same_filer in (True, False):
        for it in dart_items:
            if "kind_id" in it:
                continue
            cands = [k for k in pool if _key(k.corp) == _key(it["corp"])
                     and classify(k.title)["subtype"] == it["subtype"]
                     and (not same_filer or _key(k.filer) == _key(it["filer"]))]
            if not cands:
                continue
            k = min(cands, key=lambda k: abs(_minutes(k.time) - _minutes(it["time"])))
            pool.remove(k)
            it["kind_id"] = k.id
            it["kind_url"] = f"https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno={k.id}"
    return pool


def _minutes(hhmm: str) -> int:
    try:
        h, m = hhmm.split(":")[:2]
        return int(h) * 60 + int(m)
    except ValueError:
        return 0


def collect_day(session, date: str) -> dict | None:
    listing, kind = fetch_lists(session, date)
    source = "KIND+DART" if kind else "DART"
    print(f"[{date}] DART {len(listing)} / KIND {len(kind) if kind is not None else 'n/a'} KOSPI/KOSDAQ disclosures")
    if not listing and not kind:
        return None

    cached = {it["id"]: it for it in _load(DAYS / f"{date}.json", {}).get("items", [])}
    items = []
    for li in listing:
        cls = classify(li.title)
        if not cls:
            continue
        item = {**li.to_dict(), **cls}
        item["url"] = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={li.id}"
        old = cached.get(li.id)
        if old and old.get("parsed"):
            item.update({k: old[k] for k in ("metrics", "direction", "signal", "summary", "parsed")})
        items.append(item)

    pending = []
    if kind:
        for k in match_kind(items, kind):
            # KIND에는 있지만 DART 목록에 아직 없는 공시: 다음 수집 때 다시 짝을 찾는다
            pending.append({**k.to_dict(), **classify(k.title), "id": f"K{k.id}", "kind_id": k.id,
                            "url": "", "kind_url": f"https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno={k.id}",
                            "metrics": {}, "direction": None, "signal": False, "parsed": False,
                            "summary": "DART 등록 대기 - 다음 수집 때 수치 반영"})

    def enrich(item: dict) -> None:
        if item.get("parsed"):
            return
        try:
            item.update(parse(make_session(), item))
            item["parsed"] = True
        except Exception as e:  # noqa: BLE001 - 한 건 실패로 전체를 멈추지 않는다
            print(f"  ! parse failed {item['id']} {item['corp']} {item['title']}: {e}")
            item.update({"metrics": {}, "direction": None, "signal": False,
                         "summary": "본문 파싱 실패 - 원문 확인", "parsed": False})
        print(f"  + {item['category']:8} {item['corp']} | {item['title']} | {item.get('summary', '')}")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(enrich, items))
    items += pending

    items.sort(key=lambda x: (x["time"], x["id"]), reverse=True)
    day = {
        "date": date,
        "source": source,
        "collected_at": datetime.now(KST).isoformat(timespec="seconds"),
        "total_disclosures": len(listing) or len(kind or []),
        "items": items,
    }
    _dump(DAYS / f"{date}.json", day)
    return day


def rebuild_index() -> None:
    """day 파일들을 합쳐 index.json(메타)과 all.json(전체 항목)을 만든다."""
    days, all_items = [], []
    for p in sorted(DAYS.glob("*.json"), reverse=True):
        d = _load(p, None)
        if not d:
            continue
        counts: dict[str, int] = {}
        signals: dict[str, int] = {}
        for it in d["items"]:
            counts[it["category"]] = counts.get(it["category"], 0) + 1
            if it.get("signal"):
                signals[it["category"]] = signals.get(it["category"], 0) + 1
        days.append({"date": d["date"], "source": d["source"], "collected_at": d["collected_at"],
                     "total_disclosures": d["total_disclosures"], "counts": counts,
                     "signals": signals})
        all_items.extend(d["items"])
    _dump(DATA / "all.json", all_items)
    _dump(DATA / "index.json", {
        "updated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "latest": days[0]["date"] if days else None,
        "days": days,
    })


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (KST)")
    ap.add_argument("--backfill", type=int, default=0, help="최근 N일 소급")
    args = ap.parse_args(argv)

    session = make_session()
    today = datetime.now(KST).date()
    if args.date:
        dates = [args.date]
    elif args.backfill:
        dates = [(today - timedelta(days=i)).isoformat() for i in range(args.backfill)]
        dates = [d for d in dates if datetime.fromisoformat(d).weekday() < 5]
    else:
        # 직전 수집일부터 오늘까지의 평일 전부. 스케줄이 밀리거나 빠진 날도 메운다.
        # 직전 수집일은 장 마감 후 늦게 올라온 공시 반영을 위해 다시 확인한다.
        prev = _load(DATA / "index.json", {}).get("latest")
        start = max(date.fromisoformat(prev), today - timedelta(days=14)) if prev else today
        dates = []
        d = today
        while d >= start:
            if d.weekday() < 5:
                dates.append(d.isoformat())
            d -= timedelta(days=1)

    for d in dates:
        collect_day(session, d)
    rebuild_index()
    return 0


if __name__ == "__main__":
    sys.exit(main())
