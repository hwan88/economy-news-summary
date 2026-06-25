"""
로컬 개발 서버 — python run_local.py 로 실행
http://localhost:8080 에서 확인
"""

import os, sys, json, urllib.parse
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── .env 로드 ─────────────────────────────────────────────
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# api/ 폴더를 import 경로에 추가
sys.path.insert(0, str(Path(__file__).parent / "api"))
from news import fetch_all, summarize  # noqa: E402

ROOT = Path(__file__).parent
PORT = 8080


class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path

        # ── /api/news ──
        if path == "/api/news":
            params = urllib.parse.parse_qs(parsed.query)
            hours  = int(params.get("hours", ["14"])[0])
            try:
                articles = fetch_all(hours)
                summarize(articles)
                out  = [{k: v for k, v in a.items() if not k.startswith("_")} for a in articles]
                body = json.dumps({"articles": out, "count": len(out)}, ensure_ascii=False).encode("utf-8")
                self.respond(200, "application/json; charset=utf-8", body)
            except Exception as e:
                body = json.dumps({"error": str(e)}).encode("utf-8")
                self.respond(500, "application/json", body)

        # ── index.html ──
        elif path in ("/", "/index.html"):
            content = (ROOT / "index.html").read_bytes()
            self.respond(200, "text/html; charset=utf-8", content)

        else:
            self.respond(404, "text/plain", b"Not Found")

    def respond(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"  {args[1]}  {args[0]}")


if __name__ == "__main__":
    print(f"\n로컬 서버 시작: http://localhost:{PORT}")
    print("종료: Ctrl+C\n")
    HTTPServer(("localhost", PORT), Handler).serve_forever()
