"""Public-naming resolver: only public GitHub repos (or a manual list) get named."""
from __future__ import annotations

import json

from tank.public_names import detect_public, resolve


def _mk_repo(parent, name):
    d = parent / name
    (d / ".git").mkdir(parents=True)
    return d


def test_detect_public_only_flags_public_github_repos(tmp_path):
    pub = _mk_repo(tmp_path, "pub")
    priv = _mk_repo(tmp_path, "priv")
    nogit = tmp_path / "nogit"
    nogit.mkdir()

    def fake_run(args, timeout=8.0):
        if "remote" in args:
            d = args[2]  # ["git", "-C", <dir>, ...]
            if d.endswith("pub"):
                return "https://github.com/me/pub.git\n"
            if d.endswith("priv"):
                return "git@github.com:me/priv.git\n"
            return ""
        if args and args[0] == "gh":
            return "PUBLIC\n" if args[3] == "me/pub" else "PRIVATE\n"
        return ""

    out = detect_public([pub, priv, nogit], run=fake_run)
    assert out == {"pub": "pub"}  # only the public repo; private + no-git excluded


def test_resolve_uses_fresh_cache_without_calling_gh(tmp_path):
    cache = tmp_path / "pn.json"
    cache.write_text(json.dumps({"resolved_at": 1000, "names": {"a": "A"}}))
    called = []

    def fake_run(args, timeout=8.0):
        called.append(args)
        return ""

    out = resolve(tmp_path, set(), {"b": "B"}, run=fake_run,
                  cache_path=cache, now=1100, ttl=3600)
    assert out == {"a": "A", "b": "B"}
    assert called == []  # fresh cache -> no git/gh calls


def test_resolve_refreshes_when_stale_and_merges_manual(tmp_path):
    cache = tmp_path / "pn.json"
    cache.write_text(json.dumps({"resolved_at": 0, "names": {"old": "Old"}}))
    _mk_repo(tmp_path, "pub")

    def fake_run(args, timeout=8.0):
        if "remote" in args:
            return "https://github.com/me/pub\n"
        if args and args[0] == "gh":
            return "PUBLIC\n"
        return ""

    out = resolve(tmp_path, set(), {"brand": "Brand"}, run=fake_run,
                  cache_path=cache, now=10 ** 9, ttl=3600)
    assert out.get("pub") == "pub"      # freshly detected
    assert out.get("brand") == "Brand"  # manual always included
    assert "old" not in out             # stale auto entries replaced


def test_resolve_manual_overrides_auto_label(tmp_path):
    _mk_repo(tmp_path, "my-tool")

    def fake_run(args, timeout=8.0):
        if "remote" in args:
            return "https://github.com/me/my-tool\n"
        if args and args[0] == "gh":
            return "PUBLIC\n"
        return ""

    out = resolve(tmp_path, set(), {"my-tool": "My Brand"}, run=fake_run,
                  cache_path=tmp_path / "pn.json", now=10 ** 9)
    assert out["my-tool"] == "My Brand"  # manual label wins over auto
