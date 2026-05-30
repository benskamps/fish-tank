import datetime as dt

from tank.models import HardwareSample, Weather
from tank.weather import synthesize


def _sample(cpu_temp=40.0, gpu_temp=50.0, cpu_load=10.0, gpu_load=20.0,
            mem=30.0, idle=0, uptime=3600, degraded=False):
    return HardwareSample(
        cpu_temp_c=cpu_temp, gpu_temp_c=gpu_temp,
        cpu_load_pct=cpu_load, gpu_load_pct=gpu_load,
        memory_pct=mem, idle_seconds=idle, uptime_seconds=uptime,
        sources_used=["test"], degraded=degraded,
    )


def _prev():
    return Weather(20.0, 0.0, 0.0, 0.5, 0.0, [])


def test_temperature_smooths_toward_sample():
    out = synthesize(_sample(cpu_temp=50.0, gpu_temp=60.0), _prev(),
                     dt=dt.timedelta(minutes=5))
    assert 25.0 < out.temperature_c < 55.0


def test_current_strength_from_gpu_load():
    out = synthesize(_sample(gpu_load=90.0), _prev(),
                     dt=dt.timedelta(minutes=5))
    assert out.current_strength > 0.3


def test_light_level_drops_with_idle():
    active = synthesize(_sample(idle=0), _prev(), dt=dt.timedelta(minutes=5))
    idle = synthesize(_sample(idle=1800), _prev(), dt=dt.timedelta(minutes=5))
    assert idle.light_level < active.light_level


def test_silt_grows_with_memory_pressure():
    low = synthesize(_sample(mem=10.0), _prev(), dt=dt.timedelta(minutes=5))
    high = synthesize(_sample(mem=95.0), _prev(), dt=dt.timedelta(minutes=5))
    assert high.silt_density > low.silt_density


def test_fossil_layer_preserved():
    prev = Weather(20.0, 0.0, 0.0, 0.5, 0.0, ["·", "✦"])
    out = synthesize(_sample(), prev, dt=dt.timedelta(minutes=5))
    assert out.fossil_layer == ["·", "✦"]


def test_degraded_sample_dims_light_and_holds_temp():
    out = synthesize(_sample(cpu_temp=None, gpu_temp=None, degraded=True),
                     _prev(), dt=dt.timedelta(minutes=5))
    assert out.temperature_c == 20.0
    assert out.light_level <= 0.3


def _local(hour: int) -> dt.datetime:
    return dt.datetime(2026, 5, 29, hour, 0)


def test_circadian_phase_recorded_when_now_given():
    out = synthesize(_sample(), _prev(), dt=dt.timedelta(minutes=5),
                     now=_local(1))
    assert out.phase == "witching"
    out_day = synthesize(_sample(), _prev(), dt=dt.timedelta(minutes=5),
                         now=_local(12))
    assert out_day.phase == "day"


def test_witching_is_darker_than_noon_same_activity():
    night = synthesize(_sample(idle=0), _prev(), dt=dt.timedelta(minutes=5),
                       now=_local(1))
    noon = synthesize(_sample(idle=0), _prev(), dt=dt.timedelta(minutes=5),
                      now=_local(12))
    assert night.light_level < noon.light_level


def test_no_now_preserves_legacy_light_behavior():
    # Without `now`, phase holds at prev and circ==1.0 -> unchanged from v0.1.
    out = synthesize(_sample(idle=0), _prev(), dt=dt.timedelta(minutes=5))
    assert out.phase == "day"
    assert out.light_level > 0.5  # full daytime light, no circadian dimming
