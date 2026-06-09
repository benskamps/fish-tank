"""Tests for the best-effort Windows crash detector (tank.crashsense).

Never calls the real wevtutil — every test mocks the subprocess seam, so the
suite runs identically on CI's ubuntu and on a Windows dev box.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from tank import crashsense, paths
from tank.bestiary import load_bundled
from tank.clock import FakeClock
from tank.models import HardwareSample
from tank.tick import TickEngine


# A wevtutil-shaped XML dump: two sibling <Event> elements (no single root),
# newest first, exactly as `wevtutil qe ... /rd:true /f:xml` emits them.
def _event_xml(event_id: int, system_time: str) -> str:
    return (
        '<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">'
        "<System>"
        f"<EventID>{event_id}</EventID>"
        f'<TimeCreated SystemTime="{system_time}"/>'
        "<Channel>System</Channel>"
        "</System>"
        "</Event>"
    )


SAMPLE_XML = (
    _event_xml(6008, "2026-06-08T07:14:22.1234567Z")
    + _event_xml(1001, "2026-06-07T23:59:00.0000000Z")
)


def _quiet() -> HardwareSample:
    return HardwareSample(
        cpu_temp_c=40.0, gpu_temp_c=50.0, cpu_load_pct=10.0,
        gpu_load_pct=10.0, memory_pct=30.0, idle_seconds=0,
        uptime_seconds=3600, sources_used=["test"], degraded=False,
    )


# ---------------------------------------------------------------------------
# (a) parser turns sample crash XML into the right events
# ---------------------------------------------------------------------------
def test_parser_extracts_crash_ids_and_utc_timestamps():
    crashes = crashsense.parse_crashes(SAMPLE_XML)
    ids = sorted(eid for eid, _ in crashes)
    assert ids == [1001, 6008]
    for _eid, ts in crashes:
        assert ts.tzinfo is not None
        assert ts.utcoffset() == dt.timedelta(0)  # normalized to UTC


def test_parser_ignores_kernel_power_41_and_unknown_ids():
    """41 (Kernel-Power) is deliberately excluded — too noisy. Unknown IDs too."""
    xml = (
        _event_xml(41, "2026-06-08T07:00:00.0000000Z")
        + _event_xml(7, "2026-06-08T06:00:00.0000000Z")
    )
    assert crashsense.parse_crashes(xml) == []


def test_parser_survives_garbage_xml():
    assert crashsense.parse_crashes("not xml <<<") == []
    assert crashsense.parse_crashes("") == []


def test_scan_emits_one_kernel_error_per_fresh_crash(monkeypatch, tmp_tank_dir):
    monkeypatch.setattr(crashsense, "_is_windows", lambda: True)
    monkeypatch.setattr(crashsense, "_run_wevtutil", lambda: SAMPLE_XML)
    # Seed an OLD marker so both sample crashes count as fresh.
    paths.last_crash_path().write_text(
        "2026-01-01T00:00:00+00:00", encoding="utf-8"
    )
    now = dt.datetime(2026, 6, 8, 12, 0, tzinfo=dt.timezone.utc)
    events = crashsense.scan_crashes(now)
    assert [e.kind for e in events] == ["kernel_error", "kernel_error"]
    # Marker advanced to the newest crash (the 6008 at 07:14).
    marker = paths.last_crash_path().read_text().strip()
    assert marker.startswith("2026-06-08T07:14:22")


# ---------------------------------------------------------------------------
# (b) dedup: the same crash across two ticks emits once
# ---------------------------------------------------------------------------
def test_dedup_same_crash_emits_once_across_two_scans(monkeypatch, tmp_tank_dir):
    monkeypatch.setattr(crashsense, "_is_windows", lambda: True)
    monkeypatch.setattr(crashsense, "_run_wevtutil", lambda: SAMPLE_XML)
    # Old marker -> first scan emits both crashes.
    paths.last_crash_path().write_text(
        "2026-01-01T00:00:00+00:00", encoding="utf-8"
    )
    now = dt.datetime(2026, 6, 8, 12, 0, tzinfo=dt.timezone.utc)
    first = crashsense.scan_crashes(now)
    assert len(first) == 2
    # Second scan sees the SAME XML; marker has advanced -> nothing new.
    second = crashsense.scan_crashes(now)
    assert second == []


# ---------------------------------------------------------------------------
# (c) first-run baseline emits nothing
# ---------------------------------------------------------------------------
def test_first_run_baselines_and_emits_nothing(monkeypatch, tmp_tank_dir):
    monkeypatch.setattr(crashsense, "_is_windows", lambda: True)
    monkeypatch.setattr(crashsense, "_run_wevtutil", lambda: SAMPLE_XML)
    assert not paths.last_crash_path().exists()  # no marker yet
    now = dt.datetime(2026, 6, 8, 12, 0, tzinfo=dt.timezone.utc)
    events = crashsense.scan_crashes(now)
    assert events == []  # never retro-spawn historical crashes on install
    # But the marker is now written (baselined to the newest on-disk crash).
    assert paths.last_crash_path().exists()
    # A subsequent NEW crash (after the baseline) does emit.
    newer = _event_xml(6008, "2026-06-09T01:00:00.0000000Z") + SAMPLE_XML
    monkeypatch.setattr(crashsense, "_run_wevtutil", lambda: newer)
    later = crashsense.scan_crashes(now)
    assert [e.kind for e in later] == ["kernel_error"]


def test_first_run_with_no_crashes_baselines_to_now(monkeypatch, tmp_tank_dir):
    monkeypatch.setattr(crashsense, "_is_windows", lambda: True)
    monkeypatch.setattr(crashsense, "_run_wevtutil", lambda: "")  # no records
    now = dt.datetime(2026, 6, 8, 12, 0, tzinfo=dt.timezone.utc)
    assert crashsense.scan_crashes(now) == []
    assert paths.last_crash_path().read_text().strip().startswith("2026-06-08T12:00")


# ---------------------------------------------------------------------------
# (d) non-Windows -> []
# ---------------------------------------------------------------------------
def test_non_windows_is_clean_noop(monkeypatch, tmp_tank_dir):
    monkeypatch.setattr(crashsense, "_is_windows", lambda: False)

    def _boom():
        raise AssertionError("wevtutil must not run off-Windows")

    monkeypatch.setattr(crashsense, "_run_wevtutil", _boom)
    now = dt.datetime(2026, 6, 8, 12, 0, tzinfo=dt.timezone.utc)
    assert crashsense.scan_crashes(now) == []
    # And no marker is touched.
    assert not paths.last_crash_path().exists()


# ---------------------------------------------------------------------------
# (e) subprocess failure / timeout -> [] (tick survives)
# ---------------------------------------------------------------------------
def test_subprocess_timeout_returns_empty(monkeypatch, tmp_tank_dir):
    import subprocess

    monkeypatch.setattr(crashsense, "_is_windows", lambda: True)

    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="wevtutil", timeout=6.0)

    monkeypatch.setattr(crashsense.proc, "run", _timeout)
    now = dt.datetime(2026, 6, 8, 12, 0, tzinfo=dt.timezone.utc)
    assert crashsense.scan_crashes(now) == []


def test_subprocess_missing_binary_returns_empty(monkeypatch, tmp_tank_dir):
    monkeypatch.setattr(crashsense, "_is_windows", lambda: True)

    def _no_binary(*a, **k):
        raise FileNotFoundError("wevtutil not on PATH")

    monkeypatch.setattr(crashsense.proc, "run", _no_binary)
    now = dt.datetime(2026, 6, 8, 12, 0, tzinfo=dt.timezone.utc)
    assert crashsense.scan_crashes(now) == []


def test_nonzero_exit_returns_empty(monkeypatch, tmp_tank_dir):
    monkeypatch.setattr(crashsense, "_is_windows", lambda: True)

    class _Result:
        returncode = 5
        stdout = ""
        stderr = "Access is denied."

    monkeypatch.setattr(crashsense.proc, "run", lambda *a, **k: _Result())
    now = dt.datetime(2026, 6, 8, 12, 0, tzinfo=dt.timezone.utc)
    assert crashsense.scan_crashes(now) == []


# ---------------------------------------------------------------------------
# (f) end-to-end: an injected kernel_error event spawns a crashstrider through
#     the REAL spawn path (Observer -> tick -> spawn).
# ---------------------------------------------------------------------------
class _FakeHardware:
    def sample(self, timeout=2.0):
        return _quiet()


class _CrashObserver:
    """Real-ish observer whose only event is a kernel_error — exercises the
    actual tick -> spawn(crashstrider) wiring, not a stub spawn."""

    def scan_since(self, since, world):
        from tank.models import Event
        return [Event(kind="kernel_error", project=None,
                      detail="crash:2026-06-08T07:14:22+00:00", at=since)]


def test_kernel_error_event_spawns_crashstrider_end_to_end(tmp_tank_dir, fixed_now):
    clock = FakeClock(fixed_now)
    engine = TickEngine(clock=clock, hardware=_FakeHardware(),
                        observer=_CrashObserver(), species=load_bundled())
    engine.run_once()
    world_blob = json.loads(paths.world_path().read_text())
    species = [f["species"] for f in world_blob["fish"]]
    assert "crashstrider" in species


def test_observer_scan_includes_crashsense(monkeypatch, tmp_tank_dir, fixed_now):
    """The detector is actually wired into Observer.scan_since (not just callable
    in isolation)."""
    from tank.models import Event, Weather, World
    from tank.observer import Observer

    sentinel = Event(kind="kernel_error", project=None,
                     detail="crash:sentinel", at=fixed_now)
    monkeypatch.setattr(crashsense, "scan_crashes", lambda now: [sentinel])

    world = World(
        schema_version=1, created_at=fixed_now, last_tick_at=fixed_now,
        fish=[], weather=Weather(20.0, 0.0, 0.0, 0.5, 0.0, []),
        seen_commits={}, seen_notes=set(), seen_projects=set(),
        config_overrides={},
    )
    obs = Observer(projects_root=tmp_tank_dir / "no_projects",
                   notes_dir=tmp_tank_dir / "no_notes")
    events = obs.scan_since(world.last_tick_at, world)
    assert sentinel in events
