"""tank serve — tiny localhost HTTP for the tank page."""
from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from tank import paths
from tank.render.frame import compose, phase_bg, render
from tank.serdes import world_from_json

logger = logging.getLogger(__name__)


def serve(port: int = 7311, _one_shot: bool = False) -> int:
    handler = _make_handler()
    httpd = HTTPServer(("127.0.0.1", port), handler)
    logger.info("tank serve listening on http://127.0.0.1:%d/tank", port)
    if _one_shot:
        def _once():
            try:
                httpd.handle_request()
            finally:
                httpd.server_close()
        threading.Thread(target=_once, daemon=True).start()
        return 0
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def _make_handler():
    class TankHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/tank"):
                self.send_error(404)
                return
            bg = phase_bg("night")
            try:
                world = world_from_json(
                    paths.world_path().read_text(encoding="utf-8"))
                frame = compose(world)
                inner = render(frame, style="html")
                bg = phase_bg(world.weather.phase)
            except Exception as e:
                inner = f"<pre>tank: no world yet — {e}</pre>"
            body = _wrap_html(inner, bg=bg)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            data = body.encode("utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args, **kwargs):
            pass

    return TankHandler


def _wrap_html(inner_html: str, bg: str = "#0b0d10") -> str:
    return f"""<!doctype html>
<html><head>
<meta http-equiv="refresh" content="5">
<title>tank</title>
<style>
  body {{ background:{bg}; color:#cbd5e1; font-family:"Cascadia Mono","Consolas",monospace; padding:24px; transition:background 2s ease; }}
  pre {{ font-size:14px; line-height:1.2; white-space:pre; }}
</style>
</head><body>{inner_html}</body></html>
"""
