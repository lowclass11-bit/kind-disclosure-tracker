"""Claude 웹앱(아티팩트)용 발굴 지도 폴더 만들기.

    python3 -I build_artifact.py <출력폴더> [갱신작업ID] [--local <저장소 경로>]

GitHub(main)에 올라간 최신 대시보드 화면과 자료를 받아, 아티팩트로 올릴 수 있는 형태로 바꾼다.
- index.html: 아티팩트는 문서 뼈대를 자동으로 씌우므로 <body> 안쪽만 남기고, 원본 링크와 갱신 작업 ID를 넣는다
- style.css: Pretendard 대신 Google Fonts(Noto Sans KR)를 쓰고, 다크 모드 토글을 지원한다
- files.json: 함께 올릴 파일 목록, generated_at.txt: 자료 생성 시각

갱신 버튼을 누르면 Claude 작업이 이 스크립트를 받아 실행한다. 표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

RAW = "https://raw.githubusercontent.com/lowclass11-bit/kind-disclosure-tracker/main/"
LIVE = "https://lowclass11-bit.github.io/kind-disclosure-tracker/scout/"
TRACKER = "https://lowclass11-bit.github.io/kind-disclosure-tracker/"


def fetch(rel: str, local: Path | None) -> bytes:
    if local:
        return (local / rel).read_bytes()
    req = urllib.request.Request(RAW + rel, headers={"User-Agent": "scout-artifact-build"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def page(html: str, trigger: str) -> str:
    body = html[html.index("<body>") + 6: html.index("</body>")]
    body = body.replace('<a href="../">← KIND 공시 트래커</a>',
                        f'<a href="{TRACKER}" target="_blank" rel="noopener noreferrer">KIND 공시 트래커 ↗</a>')
    body = body.replace('<span class="muted" id="asof">불러오는 중…</span>',
                        '<span class="muted" id="asof">불러오는 중…</span>\n'
                        f'    <a class="live" href="{LIVE}" target="_blank" rel="noopener noreferrer" '
                        'title="평일 저녁마다 자동 갱신되는 원본">원본 ↗</a>')
    cfg = json.dumps({"trigger": trigger} if trigger else {})
    body = body.replace('<script src="app.js"></script>',
                        f'<script>window.SCOUT_REFRESH = {cfg};</script>\n<script src="app.js"></script>')
    head = ('<title>발굴 지도</title>\n'
            '<meta name="description" content="지금은 잠잠하지만 근거가 쌓이는 업종·종목">\n'
            '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
            '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;600;700;800&display=swap">\n'
            '<link rel="stylesheet" href="style.css">\n')
    return head + body


def css(src: str) -> str:
    src = src.replace('--ui: "Pretendard", "Malgun Gothic", system-ui, sans-serif;',
                      '--ui: "Pretendard", "Noto Sans KR", "Malgun Gothic", "Apple SD Gothic Neo", system-ui, sans-serif;')
    m = re.search(r'@media \(prefers-color-scheme: dark\) \{\n  :root:not\(\[data-theme="light"\]\) \{(.*?)\n  \}\n\}',
                  src, re.S)
    if m:  # 아티팩트 보기 화면의 다크 모드 토글(data-theme)도 따르게 한다
        src = src.replace(m.group(0), m.group(0) + '\n:root[data-theme="dark"] {' + m.group(1) + "\n}")
    src = src.replace(".funnel span:last-child b { background: var(--hl); } }",
                      ".funnel span:last-child b { background: var(--hl); } }\n"
                      ':root[data-theme="dark"] .funnel span:last-child b { background: var(--hl); }')
    return src + (
        "\n.live { font-size: 12.5px; font-weight: 600; color: var(--acc); text-decoration: none;"
        " border: 1px solid var(--acc2); border-radius: 999px; padding: 3px 10px; }\n"
        ".wrap { padding-left: max(16px, 2.4vw, env(safe-area-inset-left, 0px));"
        " padding-right: max(16px, 2.4vw, env(safe-area-inset-right, 0px)); }\n")


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:]]
    local = None
    if "--local" in args:
        i = args.index("--local")
        local = Path(args[i + 1])
        del args[i:i + 2]
    if not args:
        print(__doc__)
        return 2
    out = Path(args[0])
    trigger = args[1] if len(args) > 1 else ""
    (out / "data" / "items").mkdir(parents=True, exist_ok=True)

    board_raw = fetch("docs/scout/data/dashboard.json", local)
    board = json.loads(board_raw)
    files = {
        "index.html": page(fetch("docs/scout/index.html", local).decode("utf-8"), trigger).encode("utf-8"),
        "style.css": css(fetch("docs/scout/style.css", local).decode("utf-8")).encode("utf-8"),
        "app.js": fetch("docs/scout/app.js", local),
        "data/dashboard.json": board_raw,
        "data/briefing.json": fetch("docs/scout/data/briefing.json", local),
    }
    docs = sorted({x["doc"] for x in board.get("stocks", []) + board.get("sectors", []) if x.get("doc")})
    for key in docs:
        files[f"data/items/{key}.json"] = fetch(f"docs/scout/data/items/{key}.json", local)
    for rel, data in files.items():
        (out / rel).write_bytes(data)
    listed = [p for p in files if p != "index.html"]
    (out / "files.json").write_text(json.dumps(listed, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "generated_at.txt").write_text(board.get("generated_at", ""), encoding="utf-8")
    print(f"완료: {out} · 자료 {board.get('generated_at')} · 파일 {len(files)}개")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
