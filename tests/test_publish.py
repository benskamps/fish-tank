"""Publish: the sanitizer is the trust boundary for the public web snapshot.

The leak test is the load-bearing one — it constructs a world full of private
strings (project-derived fish names, provenance, seen_commits/seals/projects)
and asserts NONE of them survive into the serialized public snapshot.
"""
from __future__ import annotations

import datetime as dt
import json

from tank.models import Death, Fish, Weather, World
from tank.publish import publish, publish_gist, to_public_snapshot


def _world_with_private_data() -> World:
    now = dt.datetime(2026, 5, 30, 1, 30, tzinfo=dt.timezone.utc)
    fish = [
        # Name + provenance + project all derive from a PRIVATE project name.
        Fish(id="a1b2c3d4", name="my-secret-app-shipfish", species="shipfish",
             glyph="><(((°>", born_at=now, lifespan_days=90.0,
             provenance="event:ship:my-secret-app:deadbeef", project="my-secret-app",
             mood="calm", last_position=(3, 4)),
        Fish(id="e5f6g7h8", name="client-alpha-founderfish", species="founderfish",
             glyph="<°)F)><", born_at=now, lifespan_days=365.0,
             provenance="event:new_project:client-alpha", project="client-alpha",
             mood="sleeping", last_position=(10, 2)),
    ]
    return World(
        schema_version=1, created_at=now, last_tick_at=now, fish=fish,
        weather=Weather(28.0, 0.3, 0.4, 0.2, 0.5, ["◇", "✦"],
                        phase="witching", mood="haunted"),
        seen_commits={"~/projects/client-alpha": "deadbeefcafe"},
        seen_seals={"2026-05-29-private-thing-seal.md"},
        seen_projects={"client-alpha", "my-secret-app", "confidential-thing"},
    )


PRIVATE_STRINGS = [
    "my-secret-app", "client-alpha", "deadbeef", "private-thing",
    "founderfish-", "shipfish-", "confidential", "provenance", "seen_commits",
    "seen_seals", "seen_projects", "event:ship", "2026-05-29-private",
]


def test_snapshot_leaks_no_private_strings():
    snap = to_public_snapshot(_world_with_private_data())
    blob = json.dumps(snap)
    for s in PRIVATE_STRINGS:
        assert s not in blob, f"PRIVATE STRING LEAKED into public snapshot: {s!r}"


def test_snapshot_has_no_name_provenance_project_keys():
    snap = to_public_snapshot(_world_with_private_data())
    for fish in snap["fish"]:
        assert set(fish.keys()) <= {"species", "glyph", "mood", "zone"}, (
            f"fish entry exposed extra keys: {fish.keys()}"
        )


def test_snapshot_includes_safe_render_fields():
    snap = to_public_snapshot(_world_with_private_data())
    assert snap["fish_count"] == 2
    assert snap["weather"]["phase"] == "witching"
    assert snap["weather"]["mood"] == "haunted"
    assert snap["fossil_layer"] == ["◇", "✦"]
    assert "tick_at" in snap
    # species type is generic and safe to keep (used for styling)
    assert {f["species"] for f in snap["fish"]} == {"shipfish", "founderfish"}
    assert snap["fish"][0]["glyph"] == "><(((°>"


def test_snapshot_is_json_serializable():
    # Must round-trip cleanly for the POST body.
    json.loads(json.dumps(to_public_snapshot(_world_with_private_data())))


def test_public_names_names_only_allowlisted_fish():
    world = _world_with_private_data()
    projects = [f.project for f in world.fish]   # two distinct private projects
    snap = to_public_snapshot(world, {projects[0]: "My Public App"})
    named = [f for f in snap["fish"] if "name" in f]
    assert len(named) == 1
    assert named[0]["name"] == "My Public App"      # the allow-listed project's label
    # The non-listed private project is still never named or leaked, and the raw
    # (allow-listed) project name itself is replaced by the public label.
    blob = json.dumps(snap)
    assert projects[1] not in blob
    assert projects[0] not in blob


def test_no_public_names_keeps_everyone_anonymous():
    snap = to_public_snapshot(_world_with_private_data())
    assert all("name" not in f for f in snap["fish"])


# ── publish() transport ───────────────────────────────────────────

def test_publish_swallows_network_errors(monkeypatch):
    def boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    # Must not raise; returns False on failure.
    assert publish({"x": 1}, "https://example.com/api/tank-push", "tok") is False


def test_publish_posts_bearer_and_json(monkeypatch):
    captured = {}

    class _Resp:
        status = 204
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["auth"] = req.get_header("Authorization")
        captured["ctype"] = req.get_header("Content-type")
        captured["body"] = req.data
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    ok = publish({"fish_count": 7}, "https://example.com/api/tank-push", "s3cret")
    assert ok is True
    assert captured["method"] == "POST"
    assert captured["auth"] == "Bearer s3cret"
    assert captured["ctype"] == "application/json"
    assert json.loads(captured["body"]) == {"fish_count": 7}


def test_publish_gist_patches_github_api(monkeypatch):
    captured = {}

    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = json.loads(req.data)
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    ok = publish_gist({"fish_count": 3}, "abc123gist", "ghtok")
    assert ok is True
    assert captured["url"] == "https://api.github.com/gists/abc123gist"
    assert captured["method"] == "PATCH"
    assert captured["auth"] == "token ghtok"
    # content is the snapshot, JSON-encoded inside the gist file payload
    inner = json.loads(captured["body"]["files"]["tank.json"]["content"])
    assert inner == {"fish_count": 3}


def test_publish_gist_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise OSError("github down")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert publish_gist({"x": 1}, "gid", "tok") is False


# ── tick integration: env-gated publish ───────────────────────────

def test_tick_publishes_only_when_configured(tmp_tank_dir, fixed_now, monkeypatch):
    from tank.bestiary import load_bundled
    from tank.clock import FakeClock
    from tank.models import HardwareSample
    from tank.tick import TickEngine

    def _quiet():
        return HardwareSample(
            cpu_temp_c=40.0, gpu_temp_c=50.0, cpu_load_pct=10.0, gpu_load_pct=10.0,
            memory_pct=30.0, idle_seconds=0, uptime_seconds=3600,
            sources_used=["test"], degraded=False,
        )

    class _HW:
        def sample(self, timeout=2.0):
            return _quiet()

    class _Obs:
        def scan_since(self, since, world):
            return []

    posts, gists = [], []
    monkeypatch.setattr("tank.tick.publish",
                        lambda snap, url, tok, **k: posts.append((snap, url, tok)) or True)
    monkeypatch.setattr("tank.tick.publish_gist",
                        lambda snap, gid, tok, **k: gists.append((snap, gid, tok)) or True)

    def run():
        TickEngine(clock=FakeClock(fixed_now), hardware=_HW(), observer=_Obs(),
                   species=load_bundled()).run_once()

    for v in ("TANK_PUBLISH_URL", "TANK_PUBLISH_TOKEN", "TANK_GIST_ID", "TANK_GIST_TOKEN"):
        monkeypatch.delenv(v, raising=False)

    # No env -> no publish of any kind.
    run()
    assert posts == [] and gists == []

    # HTTP POST target set -> POST publish with a sanitized snapshot.
    monkeypatch.setenv("TANK_PUBLISH_URL", "https://example.com/api/tank-push")
    monkeypatch.setenv("TANK_PUBLISH_TOKEN", "tok123")
    run()
    assert len(posts) == 1 and gists == []
    snap, url, tok = posts[0]
    assert url.endswith("/api/tank-push") and tok == "tok123"
    assert "fish_count" in snap and "weather" in snap
    for fish in snap["fish"]:
        assert set(fish) <= {"species", "glyph", "mood", "zone"}

    # Gist target set -> gist is PREFERRED over the POST target.
    monkeypatch.setenv("TANK_GIST_ID", "gid42")
    monkeypatch.setenv("TANK_GIST_TOKEN", "ghtok")
    run()
    assert len(gists) == 1 and len(posts) == 1  # no new POST
    gsnap, gid, gtok = gists[0]
    assert gid == "gid42" and gtok == "ghtok" and "fish_count" in gsnap
