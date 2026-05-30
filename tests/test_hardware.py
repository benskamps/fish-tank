from tank.hardware import sample


def test_sample_uses_lhm_when_available(monkeypatch):
    import tank.hardware as hw

    monkeypatch.setattr(hw, "_try_lhm", lambda timeout: {
        "cpu_temp_c": 45.0, "gpu_temp_c": 55.0,
        "cpu_load_pct": 12.5, "gpu_load_pct": 22.0,
    })
    monkeypatch.setattr(hw, "_psutil_fill", lambda partial: {
        **partial, "memory_pct": 40.0, "uptime_seconds": 3600,
        "cpu_load_pct": partial.get("cpu_load_pct") or 0.0,
    })
    monkeypatch.setattr(hw, "_idle_seconds", lambda: 5)

    s = sample()
    assert s.cpu_temp_c == 45.0
    assert s.gpu_temp_c == 55.0
    assert "lhm" in s.sources_used
    assert s.degraded is False


def test_sample_degrades_when_no_temps(monkeypatch):
    import tank.hardware as hw

    monkeypatch.setattr(hw, "_try_lhm", lambda timeout: None)
    monkeypatch.setattr(hw, "_try_nvidia_smi", lambda: None)
    monkeypatch.setattr(hw, "_psutil_fill", lambda partial: {
        **partial,
        "cpu_load_pct": 10.0, "memory_pct": 30.0, "uptime_seconds": 3600,
    })
    monkeypatch.setattr(hw, "_idle_seconds", lambda: 0)

    s = sample()
    assert s.cpu_temp_c is None
    assert s.gpu_temp_c is None
    assert s.degraded is True


def test_sample_nvidia_smi_fills_gpu(monkeypatch):
    import tank.hardware as hw

    monkeypatch.setattr(hw, "_try_lhm", lambda timeout: {
        "cpu_temp_c": 40.0, "cpu_load_pct": 10.0,
    })
    monkeypatch.setattr(hw, "_try_nvidia_smi", lambda: (62.0, 38.0))
    monkeypatch.setattr(hw, "_psutil_fill", lambda partial: {
        **partial, "memory_pct": 25.0, "uptime_seconds": 100,
        "cpu_load_pct": partial.get("cpu_load_pct") or 0.0,
    })
    monkeypatch.setattr(hw, "_idle_seconds", lambda: 0)

    s = sample()
    assert s.gpu_temp_c == 62.0
    assert s.gpu_load_pct == 38.0
    assert "nvidia-smi" in s.sources_used
