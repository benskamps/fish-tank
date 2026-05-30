from tank.render import live as live_mod


def test_live_quits_after_a_few_frames(tmp_tank_dir, monkeypatch):
    frames = {"count": 0}

    def fake_should_quit():
        frames["count"] += 1
        return frames["count"] > 2

    monkeypatch.setattr(live_mod, "_should_quit", fake_should_quit)
    monkeypatch.setattr(live_mod, "_render_frame", lambda width, world=None: "ok")
    monkeypatch.setattr(live_mod, "_sleep", lambda s: None)

    rc = live_mod.live_loop(width=40)
    assert rc == 0
    assert frames["count"] >= 2
