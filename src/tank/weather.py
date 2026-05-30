"""Synthesizes Weather from HardwareSample + previous Weather (smoothed)."""
from __future__ import annotations

import datetime as dt

from tank import circadian
from tank.models import HardwareSample, Weather

ALPHA_TEMP = 0.3
ALPHA_CURRENT = 0.5
ALPHA_SILT = 0.2
ALPHA_LIGHT = 0.4
ALPHA_PRESSURE = 0.25


def _ewma(prev: float, new: float, alpha: float) -> float:
    return prev + alpha * (new - prev)


def synthesize(sample: HardwareSample, prev: Weather, dt: dt.timedelta,
               now: "dt.datetime | None" = None) -> Weather:
    # Circadian phase folds into light: a tank at 2am is dark even under load.
    # When `now` is absent (older callers/tests) phase holds and circ == 1.0,
    # so behavior is unchanged.
    phase = circadian.phase_for(now) if now is not None else prev.phase
    circ = circadian.circadian_light(phase)

    if sample.degraded or (sample.cpu_temp_c is None and sample.gpu_temp_c is None):
        return Weather(
            temperature_c=prev.temperature_c,
            current_strength=_ewma(prev.current_strength, 0.0, ALPHA_CURRENT),
            silt_density=_ewma(prev.silt_density, sample.memory_pct / 100.0, ALPHA_SILT),
            light_level=min(prev.light_level * 0.7, 0.3) * circ,
            pressure=_ewma(prev.pressure, 0.0, ALPHA_PRESSURE),
            fossil_layer=list(prev.fossil_layer),
            phase=phase,
            mood=prev.mood,
        )

    cpu = sample.cpu_temp_c if sample.cpu_temp_c is not None else prev.temperature_c
    gpu = sample.gpu_temp_c if sample.gpu_temp_c is not None else cpu
    new_temp = (cpu + gpu) / 2.0

    gpu_load = (sample.gpu_load_pct or 0.0) / 100.0
    cpu_load = sample.cpu_load_pct / 100.0

    idle_fac = max(0.2, 1.0 - sample.idle_seconds / 1800.0)
    new_light = idle_fac * circ

    uptime_factor = min(1.0, sample.uptime_seconds / (3600.0 * 24.0))
    new_silt = 0.6 * (sample.memory_pct / 100.0) + 0.4 * uptime_factor

    new_pressure = (cpu_load + gpu_load) / 2.0

    return Weather(
        temperature_c=_ewma(prev.temperature_c, new_temp, ALPHA_TEMP),
        current_strength=_ewma(prev.current_strength, gpu_load, ALPHA_CURRENT),
        silt_density=_ewma(prev.silt_density, new_silt, ALPHA_SILT),
        light_level=_ewma(prev.light_level, new_light, ALPHA_LIGHT),
        pressure=_ewma(prev.pressure, new_pressure, ALPHA_PRESSURE),
        fossil_layer=list(prev.fossil_layer),
        phase=phase,
        mood=prev.mood,
    )
