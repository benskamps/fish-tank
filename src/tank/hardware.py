"""LHM -> psutil -> nvidia-smi hardware adapter chain (Windows-flavored)."""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from tank import proc

import psutil

from tank.models import HardwareSample

logger = logging.getLogger(__name__)

LHM_URL = "http://localhost:8085/data.json"

_warned = {"lhm": False, "nvidia": False, "idle": False}


def sample(timeout: float = 2.0) -> HardwareSample:
    sources: list[str] = []
    partial: dict[str, Any] = {
        "cpu_temp_c": None,
        "gpu_temp_c": None,
        "cpu_load_pct": None,
        "gpu_load_pct": None,
    }

    lhm = _try_lhm(timeout)
    if lhm:
        partial.update(lhm)
        sources.append("lhm")

    if partial["gpu_temp_c"] is None or partial["gpu_load_pct"] is None:
        nvs = _try_nvidia_smi()
        if nvs is not None:
            if partial["gpu_temp_c"] is None:
                partial["gpu_temp_c"] = nvs[0]
            if partial["gpu_load_pct"] is None:
                partial["gpu_load_pct"] = nvs[1]
            sources.append("nvidia-smi")

    partial = _psutil_fill(partial)
    sources.append("psutil")

    idle_s = _idle_seconds()
    uptime_s = int(time.time() - psutil.boot_time())

    degraded = partial["cpu_temp_c"] is None and partial["gpu_temp_c"] is None

    return HardwareSample(
        cpu_temp_c=partial["cpu_temp_c"],
        gpu_temp_c=partial["gpu_temp_c"],
        cpu_load_pct=float(partial.get("cpu_load_pct") or 0.0),
        gpu_load_pct=partial["gpu_load_pct"],
        memory_pct=float(partial.get("memory_pct", 0.0)),
        idle_seconds=idle_s,
        uptime_seconds=uptime_s,
        sources_used=sources,
        degraded=degraded,
    )


def _try_lhm(timeout: float) -> dict | None:
    try:
        with urllib.request.urlopen(LHM_URL, timeout=timeout) as resp:
            tree = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        if not _warned["lhm"]:
            logger.info("LHM unavailable: %s — falling back", e)
            _warned["lhm"] = True
        return None
    return _extract_lhm(tree)


def _extract_lhm(tree: dict) -> dict:
    out = {"cpu_temp_c": None, "gpu_temp_c": None,
           "cpu_load_pct": None, "gpu_load_pct": None}

    def walk(node: dict, parent_text: str = ""):
        text = node.get("Text", "")
        value = node.get("Value", "")
        if text == "CPU Package" and "°C" in str(value):
            out["cpu_temp_c"] = _parse_num(value)
        if text == "CPU Total" and "%" in str(value):
            out["cpu_load_pct"] = _parse_num(value)
        if text == "GPU Core" and "°C" in str(value) and "Temperatures" in parent_text:
            out["gpu_temp_c"] = _parse_num(value)
        if text == "GPU Core" and "%" in str(value) and "Load" in parent_text:
            out["gpu_load_pct"] = _parse_num(value)
        for child in node.get("Children", []):
            walk(child, text)

    for root in tree.get("Children", []):
        walk(root)
    return out


def _parse_num(value: str) -> float:
    return float(str(value).split()[0].replace(",", "."))


def _psutil_fill(partial: dict) -> dict:
    out = dict(partial)
    if out.get("cpu_load_pct") is None:
        out["cpu_load_pct"] = float(psutil.cpu_percent(interval=None))
    out["memory_pct"] = float(psutil.virtual_memory().percent)
    out["uptime_seconds"] = int(time.time() - psutil.boot_time())
    return out


def _try_nvidia_smi() -> tuple[float, float] | None:
    try:
        out = proc.check_output(
            ["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu",
             "--format=csv,noheader,nounits"],
            timeout=1.0,
            text=True,
        ).strip().splitlines()
        if not out:
            return None
        first = out[0].split(",")
        return (float(first[0].strip()), float(first[1].strip()))
    except (subprocess.SubprocessError, FileNotFoundError, ValueError) as e:
        if not _warned["nvidia"]:
            logger.info("nvidia-smi unavailable: %s", e)
            _warned["nvidia"] = True
        return None


def _idle_seconds() -> int:
    """Windows GetLastInputInfo via ctypes; 0 on non-Windows."""
    if not sys.platform.startswith("win"):
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0
        tick_count = ctypes.windll.kernel32.GetTickCount()
        return int((tick_count - info.dwTime) / 1000)
    except Exception as e:
        if not _warned["idle"]:
            logger.info("idle time unavailable: %s", e)
            _warned["idle"] = True
        return 0
