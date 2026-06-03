"""Dataclass models for the tank's world state."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass
class HardwareSample:
    cpu_temp_c: float | None
    gpu_temp_c: float | None
    cpu_load_pct: float
    gpu_load_pct: float | None
    memory_pct: float
    idle_seconds: int
    uptime_seconds: int
    sources_used: list[str]
    degraded: bool


@dataclass
class Fish:
    id: str
    name: str
    species: str
    glyph: str
    born_at: dt.datetime
    lifespan_days: float
    provenance: str
    project: str | None
    mood: str
    last_position: tuple[int, int]
    zone: str = "mid"      # vertical habitat: surface / mid / bottom


@dataclass
class Death:
    fish_id: str
    name: str
    species: str
    born_at: dt.datetime
    died_at: dt.datetime
    cause: str
    epitaph: str
    fossil_glyph: str


@dataclass
class Event:
    kind: str
    project: str | None
    detail: str
    at: dt.datetime


@dataclass
class Weather:
    temperature_c: float
    current_strength: float
    silt_density: float
    light_level: float
    pressure: float
    fossil_layer: list[str]
    phase: str = "day"      # circadian phase: dawn/day/dusk/night/witching
    mood: str = "calm"      # felt inner state, one word


@dataclass
class World:
    schema_version: int
    created_at: dt.datetime
    last_tick_at: dt.datetime
    fish: list[Fish]
    weather: Weather
    seen_commits: dict[str, str]
    seen_notes: set[str]
    seen_projects: set[str]
    config_overrides: dict = field(default_factory=dict)
