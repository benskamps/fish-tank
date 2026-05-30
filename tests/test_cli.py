import datetime as dt
import json

from tank import paths
from tank.cli import main


def test_peek_with_no_world_runs_clean(tmp_tank_dir, capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["tank"])
    main()
    out = capsys.readouterr().out
    assert "tank" in out.lower() or "0 fish" in out


def test_line_format_is_single_line(tmp_tank_dir, capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["tank", "--line"])
    main()
    out = capsys.readouterr().out.strip()
    assert "\n" not in out


def test_status_shows_diagnostics(tmp_tank_dir, capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["tank", "status"])
    main()
    out = capsys.readouterr().out
    assert "fish" in out.lower()


def test_adopt_adds_fish(tmp_tank_dir, capsys, monkeypatch):
    from tank.tick import TickEngine
    from tank.clock import FakeClock

    TickEngine(clock=FakeClock(
        dt.datetime(2026, 5, 14, tzinfo=dt.timezone.utc)
    )).run_once()

    monkeypatch.setattr("sys.argv", ["tank", "adopt", "fred"])
    main()
    blob = json.loads(paths.world_path().read_text())
    assert any(f["name"] == "fred" for f in blob["fish"])


def test_release_removes_named_fish(tmp_tank_dir, capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["tank", "adopt", "wilma"])
    main()
    monkeypatch.setattr("sys.argv", ["tank", "release", "wilma"])
    main()
    blob = json.loads(paths.world_path().read_text())
    assert not any(f["name"] == "wilma" for f in blob["fish"])


def test_graveyard_command_runs(tmp_tank_dir, capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["tank", "graveyard"])
    main()
    out = capsys.readouterr().out
    assert "empty" in out.lower() or out.strip() != ""


def test_reset_requires_confirm(tmp_tank_dir, capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["tank", "reset"])
    rc = main()
    assert rc != 0


def test_reset_with_confirm_wipes_state(tmp_tank_dir, capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["tank", "adopt", "ghost"])
    main()
    assert paths.world_path().exists()
    monkeypatch.setattr("sys.argv", ["tank", "reset", "--confirm"])
    main()
    assert not paths.world_path().exists()
