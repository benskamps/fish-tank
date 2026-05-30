"""JSON serialization for the World dataclass tree."""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import fields, is_dataclass
from typing import Any

from tank.models import Fish, Weather, World


def _encode(obj: Any) -> Any:
    if isinstance(obj, dt.datetime):
        return {"__dt__": obj.isoformat()}
    if isinstance(obj, set):
        return {"__set__": sorted(obj)}
    if isinstance(obj, tuple):
        return {"__tuple__": [_encode(x) for x in obj]}
    if is_dataclass(obj):
        return {f.name: _encode(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, list):
        return [_encode(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _encode(v) for k, v in obj.items()}
    return obj


def _decode(obj: Any) -> Any:
    if isinstance(obj, dict):
        if "__dt__" in obj:
            return dt.datetime.fromisoformat(obj["__dt__"])
        if "__set__" in obj:
            return set(obj["__set__"])
        if "__tuple__" in obj:
            return tuple(_decode(x) for x in obj["__tuple__"])
        return {k: _decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decode(x) for x in obj]
    return obj


def _fish_from_dict(d: dict) -> Fish:
    return Fish(
        id=d["id"],
        name=d["name"],
        species=d["species"],
        glyph=d["glyph"],
        born_at=d["born_at"],
        lifespan_days=d["lifespan_days"],
        provenance=d["provenance"],
        project=d["project"],
        mood=d["mood"],
        last_position=d["last_position"] if isinstance(d["last_position"], tuple)
                       else tuple(d["last_position"]),
    )


def _weather_from_dict(d: dict) -> Weather:
    return Weather(
        temperature_c=d["temperature_c"],
        current_strength=d["current_strength"],
        silt_density=d["silt_density"],
        light_level=d["light_level"],
        pressure=d["pressure"],
        fossil_layer=list(d["fossil_layer"]),
        phase=d.get("phase", "day"),
        mood=d.get("mood", "calm"),
    )


def world_to_json(world: World) -> str:
    return json.dumps(_encode(world), indent=2)


def world_from_json(blob: str) -> World:
    raw = _decode(json.loads(blob))
    fish = [_fish_from_dict(f) for f in raw["fish"]]
    weather = _weather_from_dict(raw["weather"])
    seen_seals = raw["seen_seals"]
    seen_projects = raw["seen_projects"]
    return World(
        schema_version=raw["schema_version"],
        created_at=raw["created_at"],
        last_tick_at=raw["last_tick_at"],
        fish=fish,
        weather=weather,
        seen_commits=dict(raw["seen_commits"]),
        seen_seals=seen_seals if isinstance(seen_seals, set) else set(seen_seals),
        seen_projects=seen_projects if isinstance(seen_projects, set) else set(seen_projects),
        config_overrides=dict(raw.get("config_overrides", {})),
    )
