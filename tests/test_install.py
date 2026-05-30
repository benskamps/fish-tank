from tank import install as install_mod


def test_install_calls_powershell(monkeypatch):
    called = {"args": None}

    def fake_run(args, **kw):
        called["args"] = args

        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(install_mod.subprocess, "run", fake_run)
    rc = install_mod.install_scheduled_task()
    assert rc == 0
    assert "install-scheduled-task.ps1" in " ".join(called["args"])


def test_uninstall_calls_powershell(monkeypatch):
    called = {"args": None}

    def fake_run(args, **kw):
        called["args"] = args

        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(install_mod.subprocess, "run", fake_run)
    rc = install_mod.uninstall_scheduled_task()
    assert rc == 0
    assert "uninstall.ps1" in " ".join(called["args"])
