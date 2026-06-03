"""Shared pytest fixtures for fish-tank tests."""
from __future__ import annotations

import datetime as dt

import pytest


@pytest.fixture
def tmp_tank_dir(tmp_path, monkeypatch):
    """Redirect ~/.tank/ to a fresh temp dir for each test.

    Also points the Observer at empty paths so tests don't accidentally
    pick up the real ~/projects/ tree.
    """
    tank_dir = tmp_path / ".tank"
    tank_dir.mkdir()
    monkeypatch.setenv("TANK_HOME", str(tank_dir))
    monkeypatch.setenv("TANK_PROJECTS_ROOT", str(tmp_path / "no_projects"))
    monkeypatch.setenv("TANK_NOTES_DIR", str(tmp_path / "no_notes"))
    return tank_dir


@pytest.fixture
def fixed_now():
    """A stable timestamp tests can use as 'now'."""
    return dt.datetime(2026, 5, 14, 22, 0, 0, tzinfo=dt.timezone.utc)
