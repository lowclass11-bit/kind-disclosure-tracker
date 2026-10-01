"""텔레그램 일일 요약 알림.

  python -m scraper.notify          # 아직 보내지 않은 시그널 공시 요약 발송
  python -m scraper.notify --test   # 연결 테스트 메시지

환경변수
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (필수, 없으면 조용히 건너뜀)
  WATCHLIST   관심종목, 쉼표 구분 (선택)
  PAGES_URL   대시보드 주소 (선택, 없으면 GITHUB_REPOSITORY로 추정)
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import datetime

import requests

from .collect import DATA, KST, _dump, _load

STATE = DATA / "notify_state.json"
MAX_PER_CAT = 10
MAX_LEN = 3900  # 텔레그램 한도 4096자
CATS = [("earnings", "📈 실적 개선"), ("capex", "🏭 시설투자·공급계약"), ("stake", "🟣 지분 매입")]
MKT = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}


def dashboard_url() -> str:
    if os.getenv("PAGES_URL"):
        return os.environ["PAGES_URL"]
    repo = os.getenv("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}/"
    return ""


def line(it: dict) -> str:
    return (f"• <b>{html.escape(it['corp'])}</b> <i>{MKT.get(it['market'], '')}</i> "
            f"{html.escape(it['subtype'])}{' [정정]' if it.get('amended') else ''}\n"
            f"   {html.escape(it.get('summary') or '')} <a href=\"{it['url']}\">원문</a>")


def build_message(date: str, items: list[dict], watch: set[str]) -> str:
    url = dashboard_url()
    parts = [f"<b>📊 {date} 공시 요약</b> (상승·매수 시그널 {len(items)}건)"]
    watched = [it for it in items if it["corp"] in watch]
    if watched:
        parts.append("\n<b>⭐ 관심종목</b>\n" + "\n".join(line(it) for it in watched))
    for key, title in CATS:
        group = [it for it in items if it["category"] == key]
        if not group:
            continue
        # 금액·지분변동 큰 순으로 상위 N건
        group.sort(key=_weight, reverse=True)
        body = "\n".join(line(it) for it in group[:MAX_PER_CAT])
        more = f"\n   … 외 {len(group) - MAX_PER_CAT}건" if len(group) > MAX_PER_CAT else ""
        parts.append(f"\n<b>{title} {len(group)}건</b>\n{body}{more}")
    if url:
        parts.append(f"\n🔗 <a href=\"{url}\">대시보드에서 전체 보기</a>")
    return "\n".join(parts)


def _weight(it: dict) -> float:
    m = it.get("metrics") or {}
    for k in ("amount", "buy_amount"):
        if m.get(k):
            return float(m[k])
    if it["category"] == "earnings":
        op = (m.get("op") or {}).get("pct")
        return 1e15 + (op or 0)
    return abs(m.get("ratio_delta") or 0) * 1e9


def split(text: str) -> list[str]:
    chunks, cur = [], ""
    for ln in text.split("\n"):
        if len(cur) + len(ln) + 1 > MAX_LEN:
            chunks.append(cur)
            cur = ""
        cur += ln + "\n"
    if cur.strip():
        chunks.append(cur)
    return chunks


def send(token: str, chat_id: str, text: str, silent: bool = False) -> None:
    for chunk in split(text):
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                  "disable_web_page_preview": True, "disable_notification": silent},
            timeout=30,
        )
        if not r.ok:
            raise RuntimeError(f"Telegram API {r.status_code}: {r.text[:200]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="발송하지 않고 메시지만 출력")
    args = ap.parse_args(argv)

    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id) and not args.dry_run:
        print("[notify] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정 - 알림 건너뜀")
        return 1 if args.test else 0  # 연결 테스트는 설정 누락을 실패로 알린다

    if args.test:
        send(token, chat_id, "✅ KIND 공시 트래커 텔레그램 연결 테스트입니다.\n"
                             f"매 영업일 19시 전후 요약이 발송됩니다.\n{dashboard_url()}")
        print("[notify] test message sent")
        return 0

    index = _load(DATA / "index.json", {})
    now = datetime.now(KST)
    today = now.date().isoformat()
    # 장 마감 전(18시 이전)의 오늘 데이터는 아직 덜 찬 상태라 다음 실행으로 미룬다
    days = [d["date"] for d in index.get("days", [])
            if d["date"] < today or (d["date"] == today and now.hour >= 18)]
    if not days:
        print("[notify] 수집된 데이터 없음")
        return 0

    first_run = not STATE.exists()
    state = _load(STATE, {"sent_ids": [], "last_date": ""})
    sent = set(state["sent_ids"])
    all_items = _load(DATA / "all.json", [])
    recent = set(days[:5])
    if first_run:
        # 첫 발송: 최근 3거래일만 보내고 그 이전 건은 보낸 것으로 처리
        sent |= {it["id"] for it in all_items if it["date"] in recent and it["date"] not in days[:3]}
        recent = set(days[:3])
    items = [it for it in all_items
             if it["date"] in recent and it.get("signal") and it["id"] not in sent]
    # 하루 한 번: 새 거래일이 생겼을 때만 보낸다. 늦게 올라온 공시는 다음 날 요약에 포함.
    if days[0] <= state.get("last_date", ""):
        print(f"[notify] {days[0]} 요약은 이미 발송됨 - 건너뜀")
        return 0

    covered = sorted({it["date"] for it in items}) or [days[0]]
    label = covered[0] if len(covered) == 1 else f"{covered[0][5:]}~{covered[-1][5:]}"
    watch = {w.strip() for w in os.getenv("WATCHLIST", "").split(",") if w.strip()}
    if items:
        text = build_message(label, items, watch)
    else:
        text = f"<b>📊 {days[0]} 공시 요약</b>\n새 상승·매수 시그널 공시가 없습니다."

    if args.dry_run:
        print(text)
        return 0
    hour = now.hour
    send(token, chat_id, text, silent=hour >= 23 or hour < 7)  # 심야 지연 발송은 무음
    print(f"[notify] sent {len(items)} items ({label})")
    state["sent_ids"] = (sorted(sent) + [it["id"] for it in items])[-5000:]
    state["last_date"] = days[0]
    state["last_sent_at"] = datetime.now(KST).isoformat(timespec="seconds")
    _dump(STATE, state)
    return 0

if __name__ == "__main__":
    sys.exit(main())
