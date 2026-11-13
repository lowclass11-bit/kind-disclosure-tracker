"""로컬 웹 서버. 내 PC(127.0.0.1)에서만 접속된다."""
from __future__ import annotations

import json
import mimetypes
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from . import config, pipeline, store, writer

HOST, PORT = "127.0.0.1", 8765


def _find(data: dict, item_id: str) -> dict | None:
    for group in ("stocks", "sectors"):
        for it in data.get(group, []):
            if it["id"] == item_id:
                return it
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "scout"

    def log_message(self, *args):  # 콘솔을 조용하게
        pass

    # ------------------------------------------------------------ 공통
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _local(self) -> bool:
        """다른 사이트가 이 서버를 몰래 부르지 못하게 Host·Origin을 확인한다."""
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return False
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).hostname in ("127.0.0.1", "localhost")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n < 1_000_000:
            return {}
        try:
            d = json.loads(self.rfile.read(n))
        except ValueError:
            return {}
        return d if isinstance(d, dict) else {}

    # ------------------------------------------------------------ GET
    def do_GET(self):
        if not self._local():
            return self._json({"error": "forbidden"}, 403)
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path
        if path == "/api/state":
            cfg = config.load()
            data = pipeline.latest()
            return self._json({
                "config": config.public(cfg), "styles": {k: v["label"] for k, v in config.STYLES.items()},
                "job": pipeline.JOB.snapshot(), "has_data": bool(data),
                "data_date": data["generated_at"] if data else None,
                "saved_sources": data["sources"] if data else {},
                "ai": bool(cfg["anthropic_key"]), "pillars": pipeline.PILLARS, "kinds": writer.KINDS,
            })
        if path == "/api/data":
            return self._json(pipeline.latest() or {})
        if path == "/api/watchlist":
            return self._json(store.watch_view())
        if path == "/api/watchlist.csv":
            return self._send(200, store.watch_csv().encode("utf-8"), "text/csv; charset=utf-8",
                              {"Content-Disposition": "attachment; filename*=UTF-8''" + quote("추적표.csv")})
        if path == "/api/outputs":
            files = sorted((f.name for f in config.OUTPUTS.glob("*.md")), reverse=True)[:40]
            return self._json(files)
        if path == "/api/download":
            name = q.get("file", "")
            if not re.fullmatch(r"[\w가-힣\-]+\.md", name) or not (config.OUTPUTS / name).is_file():
                return self._json({"error": "파일 없음"}, 404)
            text = (config.OUTPUTS / name).read_text(encoding="utf-8")
            if q.get("fmt") == "docx":
                try:
                    body = writer.to_docx(text)
                except ImportError:
                    return self._json({"error": "python-docx가 없습니다. pip install python-docx"}, 500)
                out, ctype = name[:-3] + ".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            else:
                body, out, ctype = text.encode("utf-8"), name, "text/markdown; charset=utf-8"
            return self._send(200, body, ctype, {"Content-Disposition": "attachment; filename*=UTF-8''" + quote(out)})
        # 정적 파일
        rel = "index.html" if path == "/" else path.lstrip("/")
        target = (config.STATIC / rel).resolve()
        if config.STATIC not in target.parents or not target.is_file():
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    # ------------------------------------------------------------ POST
    def do_POST(self):
        if not self._local():
            return self._json({"error": "forbidden"}, 403)
        path, body = urlparse(self.path).path, self._body()
        if path == "/api/collect":
            return self._json({"started": pipeline.start()})
        if path == "/api/config":
            return self._json({"config": config.public(config.save(body))})
        data = pipeline.latest()
        if not data:
            return self._json({"error": "먼저 '자료 받기'를 실행하세요."}, 400)
        if path == "/api/work":
            return self._work(body, data)
        if path == "/api/watchlist":
            if body.get("remove"):
                store.watch_remove(str(body["remove"]))
            else:
                item = _find(data, str(body.get("id", "")))
                if not item or item.get("kind") != "stock":
                    return self._json({"error": "종목만 추적표에 넣을 수 있습니다."}, 400)
                store.watch_add(item, data["date"], str(body.get("note", ""))[:300])
            return self._json(store.watch_view())
        self._json({"error": "not found"}, 404)

    def _work(self, body: dict, data: dict) -> None:
        kind = body.get("kind")
        note = str(body.get("note", ""))[:1000]
        cfg = config.load()
        if kind == "briefing":
            draft, name, payload = store.briefing(data), "주간브리핑", {"기준일": data["date"]}
        elif kind in writer.RULES:
            item = _find(data, str(body.get("id", "")))
            if not item:
                return self._json({"error": "대상을 먼저 선택하세요."}, 400)
            draft, name, payload = writer.RULES[kind](item, data), item["name"], writer.ai_payload(item, data)
        else:
            return self._json({"error": "알 수 없는 문서 종류"}, 400)
        text, mode, warn = draft, "규칙 기반", ""
        if body.get("ai"):
            if not cfg["anthropic_key"]:
                warn = "Claude API 키가 없어 규칙 기반으로 만들었습니다."
            else:
                try:
                    text, mode = writer.ai_write(kind, draft, payload, note, cfg), "Claude 해석"
                except RuntimeError as exc:
                    warn = f"{exc} — 규칙 기반 문서를 대신 보여 줍니다."
        elif note:
            text += f"\n\n## 내 메모\n{note}"
        self._json({"text": text, "mode": mode, "warn": warn, "file": writer.save(kind, name, text)})


def main() -> None:
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"종목 발굴 프로그램 실행 중: {url}  (끝내려면 Ctrl+C)")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
