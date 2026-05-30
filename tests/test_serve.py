import datetime as dt
import threading
import time
import urllib.request

from tank.clock import FakeClock
from tank.serve import serve
from tank.tick import TickEngine


def test_serve_returns_html_with_pre_tag(tmp_tank_dir):
    TickEngine(clock=FakeClock(
        dt.datetime(2026, 5, 14, tzinfo=dt.timezone.utc)
    )).run_once()

    port = 7333
    t = threading.Thread(target=serve,
                         kwargs={"port": port, "_one_shot": True},
                         daemon=True)
    t.start()
    time.sleep(0.3)

    with urllib.request.urlopen(f"http://localhost:{port}/tank", timeout=2.0) as r:
        body = r.read().decode("utf-8")
    assert "<pre>" in body
    assert "</pre>" in body
    assert "tank" in body.lower()
