"""Best-effort Windows crash sense: turns real machine crashes into events.

The bestiary markets the **crashstrider** as "born of a crash, short frantic
life", and both spawn.EVENT_TO_TRIGGER (kernel_error -> kernel_event) and
mortality.run (kernel_error -> kernel deaths) already react to an
``Event(kind="kernel_error")``. Nothing in the Observer ever *emitted* one,
though, so crashstrider could never spawn and kernel deaths never fired. This
module closes that gap.

How it works
------------
On Windows it asks the built-in ``wevtutil`` to dump the most recent System-log
entries for a small set of crash Event IDs as XML (locale-proof — XML output is
the same in every Windows display language), parses out the EventID and UTC
timestamp, and emits one ``kernel_error`` event per crash strictly newer than a
persisted marker.

Design rules (all load-bearing for a headless ~15-min tick run under pythonw):

* **Never breaks a tick.** Every path is wrapped so that *any* failure — no
  ``wevtutil`` on PATH, a subprocess error, a timeout, a parse error, a
  permissions denial — returns ``[]`` and lets the tick continue. A thrown
  error in a headless tick is invisible and silently freezes the tank.
* **Windows-only.** On any non-Windows platform (including CI's ubuntu) this is
  a clean no-op returning ``[]`` before any subprocess is spawned.
* **No swarm.** A dedicated marker file (``paths.last_crash_path()``) stores the
  timestamp of the newest crash already turned into an event. We emit one event
  per crash *strictly newer* than the marker, then advance the marker. On the
  very first run (no marker yet) we baseline to "now" and emit NOTHING, so a
  fresh install never retro-spawns a pile of crashstriders for historical
  crashes.
* **No schema change.** The marker is its own file, deliberately kept out of the
  world.json serdes schema — a prior schema change caused a quarantine incident.

Event ID choices
----------------
* **6008** — "The previous system shutdown ... was unexpected." Logged by the
  EventLog service after an ungraceful shutdown. HIGH confidence.
* **1001** — BugCheck (the BSOD record written by Windows Error Reporting on the
  next boot). HIGH confidence.
* **41** — Kernel-Power "the system has rebooted without cleanly shutting down
  first." This is the NOISIER one: it fires on ordinary power loss and hard
  resets, not just true crashes, so it would manufacture a crashstrider every
  time someone yanks power or holds the button. We deliberately EXCLUDE 41 and
  weight crashes on the two high-confidence IDs to avoid false positives.
"""
from __future__ import annotations

import datetime as dt
import logging
import sys
from xml.etree import ElementTree as ET

from tank import paths, proc
from tank.models import Event

logger = logging.getLogger(__name__)

# High-confidence crash Event IDs in the Windows System log. 41 (Kernel-Power)
# is intentionally excluded — see the module docstring for the false-positive
# reasoning.
CRASH_EVENT_IDS = (6008, 1001)

# The wevtutil structured query. XML output is locale-proof: unlike the default
# text rendering, the field names don't change with the Windows display
# language, so the parser below works on a German or Japanese box too.
_QUERY = (
    "*[System[(EventID=6008 or EventID=1001)]]"
)

# Windows Event Log XML namespace (every <Event> lives in this namespace).
_NS = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}

# Cap how many recent records we pull and how long we'll wait. wevtutil is fast,
# but a headless tick must never hang on it.
_COUNT = 50
_TIMEOUT_S = 6.0


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _safe_parser() -> ET.XMLParser:
    """An ElementTree parser with external-entity resolution disabled.

    wevtutil output is from a trusted local Windows binary, not the network, but
    we harden anyway: turning off entity expansion neutralizes XXE and the
    billion-laughs DoS without pulling in a non-stdlib dependency (the project is
    stdlib-only). expat does not fetch external DTDs by default; we additionally
    refuse any entity declaration so a crafted record can't expand.
    """
    parser = ET.XMLParser()
    try:
        # expat: reject any entity declaration outright (defuses billion-laughs).
        parser.parser.EntityDeclHandler = lambda *a, **k: (_ for _ in ()).throw(
            ET.ParseError("entity declarations are not permitted")
        )
    except Exception:  # noqa: BLE001 - if the underlying parser lacks the hook,
        # the default expat config still doesn't fetch external entities; the
        # wrapped fromstring call remains exception-safe regardless.
        pass
    return parser


def _run_wevtutil() -> str | None:
    """Return wevtutil's XML dump of recent crash records, or None on any error.

    Best-effort: a missing binary, a non-zero exit, a timeout, or any other
    OS-level failure yields None so the caller emits no events.
    """
    argv = [
        "wevtutil", "qe", "System",
        f"/q:{_QUERY}",
        "/rd:true",            # reverse direction: newest records first
        f"/c:{_COUNT}",
        "/f:xml",
    ]
    try:
        result = proc.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT_S,
        )
    except Exception as e:  # noqa: BLE001 - subprocess/OS errors must never escape
        logger.debug("wevtutil invocation failed: %s", e)
        return None
    if result.returncode != 0:
        logger.debug("wevtutil exited %s: %s", result.returncode,
                     (result.stderr or "").strip()[:200])
        return None
    return result.stdout


def parse_crashes(xml_text: str) -> list[tuple[int, dt.datetime]]:
    """Parse wevtutil XML into ``(event_id, utc_timestamp)`` tuples.

    ``wevtutil qe ... /f:xml`` emits a stream of sibling ``<Event>`` elements
    with no single root, so we wrap it in one before parsing. Records missing an
    EventID or a usable TimeCreated/SystemTime are skipped rather than fatal.
    Returns only the IDs we treat as crashes (CRASH_EVENT_IDS). Never raises:
    a malformed dump yields ``[]``.
    """
    out: list[tuple[int, dt.datetime]] = []
    if not xml_text or not xml_text.strip():
        return out
    try:
        root = ET.fromstring(f"<Events>{xml_text}</Events>", parser=_safe_parser())
    except ET.ParseError as e:
        logger.debug("wevtutil XML parse failed: %s", e)
        return out

    for ev in root.findall(".//e:Event", _NS):
        try:
            sys_el = ev.find("e:System", _NS)
            if sys_el is None:
                continue
            eid_el = sys_el.find("e:EventID", _NS)
            if eid_el is None or eid_el.text is None:
                continue
            try:
                eid = int(eid_el.text.strip())
            except ValueError:
                continue
            if eid not in CRASH_EVENT_IDS:
                continue
            ts = _parse_systemtime(sys_el)
            if ts is None:
                continue
            out.append((eid, ts))
        except Exception as e:  # noqa: BLE001 - one bad record must not kill the batch
            logger.debug("skipping malformed crash record: %s", e)
            continue
    return out


def _parse_systemtime(sys_el: ET.Element) -> dt.datetime | None:
    """Extract the UTC TimeCreated/@SystemTime as an aware datetime, or None."""
    tc = sys_el.find("e:TimeCreated", _NS)
    if tc is None:
        return None
    raw = tc.get("SystemTime")
    if not raw:
        return None
    # SystemTime looks like '2026-06-08T07:14:22.1234567Z'. Python's fromisoformat
    # rejects 7-digit fractional seconds and (pre-3.11) the trailing 'Z', so
    # normalize both before parsing.
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, _, tail = text.partition(".")
        frac = tail
        tz = ""
        for marker in ("+", "-"):
            if marker in frac:
                idx = frac.index(marker)
                frac, tz = frac[:idx], frac[idx:]
                break
        frac = frac[:6]  # microsecond precision
        text = f"{head}.{frac}{tz}"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _read_marker() -> dt.datetime | None:
    """Read the persisted last-crash marker, or None if absent/unreadable."""
    try:
        p = paths.last_crash_path()
        if not p.exists():
            return None
        text = p.read_text(encoding="utf-8").strip()
        if not text:
            return None
        parsed = dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception as e:  # noqa: BLE001 - a bad marker must not break the tick
        logger.debug("last_crash marker read failed: %s", e)
        return None


def _write_marker(when: dt.datetime) -> None:
    """Persist the newest-crash timestamp (best-effort; failure is non-fatal)."""
    try:
        paths.ensure_dirs()
        paths.last_crash_path().write_text(
            when.astimezone(dt.timezone.utc).isoformat(), encoding="utf-8"
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("last_crash marker write failed: %s", e)


def scan_crashes(now: dt.datetime) -> list[Event]:
    """Return one ``kernel_error`` Event per crash newer than the dedup marker.

    The whole body is guarded: on ANY failure this returns ``[]`` so the headless
    tick survives. Windows-only — a clean no-op elsewhere. On first run (no
    marker) it baselines to ``now`` and emits nothing.
    """
    try:
        if not _is_windows():
            return []

        xml_text = _run_wevtutil()
        if xml_text is None:
            return []

        crashes = parse_crashes(xml_text)
        if not crashes:
            # No crash records at all. Still establish a first-run baseline so a
            # crash that lands before the next tick is measured against "now",
            # not against the epoch (which would let an old record through).
            if _read_marker() is None:
                _write_marker(now)
            return []

        newest = max(ts for _eid, ts in crashes)
        marker = _read_marker()

        if marker is None:
            # FIRST RUN: baseline to the newest crash already on disk and emit
            # NOTHING. Never retro-spawn a pile of crashstriders for historical
            # crashes the moment someone installs the tank.
            _write_marker(newest)
            return []

        fresh = sorted(
            (ts for _eid, ts in crashes if ts > marker)
        )
        if not fresh:
            return []

        events = [
            Event(
                kind="kernel_error",
                project=None,
                # detail seeds spawn's RNG and names the fish — make it stable
                # and unique per crash so two distinct crashes don't collide.
                detail=f"crash:{ts.isoformat()}",
                at=ts,
            )
            for ts in fresh
        ]
        _write_marker(max(newest, marker))
        return events
    except Exception as e:  # noqa: BLE001 - last-resort guard; a tick is sacred
        logger.warning("crash sense failed (non-fatal): %s", e)
        return []
