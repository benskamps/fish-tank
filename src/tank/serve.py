"""tank serve — tiny localhost HTTP for the tank page.

Serves two things, no dependencies:
  GET /tank.json  — the current world as a sanitized snapshot in the same
                    shape ``tank.publish`` sends the site: ``schema``, a nested
                    ``weather`` block (phase/temp/current/silt/light/pressure/
                    mood), the fish roster (species/glyph/zone/mood/name), the
                    fossil layer, a ``fish_count`` and a ``tick_at`` stamp. The
                    page polls this every 15s.
  GET / or /tank  — a self-contained animated aquarium. This is the *full*
                    renderer ported from brokenbranch.dev/aquarium: a
                    requestAnimationFrame simulation with wander + loose
                    schooling (boids), a closed-form flow field, burst-and-coast
                    locomotion, habitat zones, god rays, caustics, depth
                    attenuation, swaying weeds/decor, bioluminescent night glow,
                    near-surface reflections, light-trails, and click-to-feed.
                    A <noscript> block keeps the static ASCII tank for JS-off
                    viewers, so the terminal soul survives either way.

The renderer is the same code that runs on the author's site; here it polls this
server's /tank.json instead of the site's /api/tank, and the snapshot is built
locally from ~/.tank/world.json. Still no dependencies, still one file.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from tank import paths
from tank.render.frame import compose, render
from tank.publish import SNAPSHOT_SCHEMA
from tank.serdes import world_from_json

logger = logging.getLogger(__name__)


def serve(port: int = 7311, _one_shot: bool = False) -> int:
    handler = _make_handler()
    httpd = HTTPServer(("127.0.0.1", port), handler)
    logger.info("tank serve listening on http://127.0.0.1:%d/tank", port)
    if _one_shot:
        def _once():
            try:
                httpd.handle_request()
            finally:
                httpd.server_close()
        threading.Thread(target=_once, daemon=True).start()
        return 0
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def _world_snapshot() -> dict:
    """The current world as a render-ready snapshot for the animated page.

    Same shape as ``publish.to_public_snapshot`` (the site's feed), so the one
    renderer works on both: ``schema``, ``tick_at``, ``fish_count``, the fish
    roster, a nested ``weather`` block (phase/temp/current/silt/light/pressure/
    mood) and the fossil layer. Two local liberties: every fish carries its
    ``name`` (this page is loopback-only, there is no trust boundary to guard),
    and ``tick_at`` falls back to the world file's mtime for a world that
    predates ``last_tick_at``.
    """
    path = paths.world_path()
    world = world_from_json(path.read_text(encoding="utf-8"))
    w = world.weather
    last = getattr(world, "last_tick_at", None)
    if last is not None:
        tick_at = last.isoformat()
    else:
        try:
            tick_at = datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            tick_at = None
    fish = [
        {
            "species": f.species,
            "glyph": f.glyph,
            "mood": getattr(f, "mood", "calm"),
            "zone": getattr(f, "zone", "mid"),
            "name": getattr(f, "name", None),
        }
        for f in world.fish
    ]
    return {
        "schema": SNAPSHOT_SCHEMA,
        "tick_at": tick_at,
        "fish_count": len(fish),
        "fish": fish,
        "weather": {
            "temperature_c": w.temperature_c,
            "current_strength": w.current_strength,
            "silt_density": w.silt_density,
            "light_level": w.light_level,
            "pressure": w.pressure,
            "phase": w.phase,
            "mood": w.mood,
        },
        "fossil_layer": list(w.fossil_layer),
    }


def _make_handler():
    class TankHandler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, ctype: str):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.split("?")[0] == "/tank.json":
                try:
                    payload = _world_snapshot()
                except Exception:
                    # No world yet (or unreadable): the renderer treats
                    # {"empty": true} as "warming up" and settles to glassy idle.
                    payload = {"empty": True}
                self._send(json.dumps(payload).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if self.path not in ("/", "/tank"):
                self.send_error(404)
                return
            phase = "night"
            noscript = "<pre>tank: no world yet — run `tank tick`</pre>"
            try:
                world = world_from_json(
                    paths.world_path().read_text(encoding="utf-8"))
                noscript = render(compose(world), style="html")
                phase = world.weather.phase
            except Exception as e:
                noscript = f"<pre>tank: no world yet — {e}</pre>"
            page = _PAGE.replace("__PHASE__", phase).replace(
                "__NOSCRIPT__", noscript)
            self._send(page.encode("utf-8"), "text/html; charset=utf-8")

        def log_message(self, *args, **kwargs):
            pass

    return TankHandler


# The animated client — the full brokenbranch.dev/aquarium renderer, ported here
# verbatim and pointed at this server's /tank.json. Kept as a raw (r) string so
# the JS/CSS/regex braces and backslashes need no escaping.
_JS = r"""
/* Ben's Dev System — live-ish aquarium renderer.
   Polls /api/tank for a sanitized snapshot and rebuilds the tank in the
   site's design language. No private data — the snapshot is glyphs, weather,
   phase, mood, and habitat zone only.

   Motion: a requestAnimationFrame simulation. Each fish is an entity with a
   velocity, steered by wander + loose schooling (boids) + soft habitat
   containment + burst-and-coast locomotion + cursor-startle, and turns at the
   walls instead of teleporting. Glyphs flip to face their travel direction
   (no moonwalking) and the body undulates with a speed-locked tail beat.
   Constants are grounded in real fish kinematics — see the alive-pass notes.

   Reusable: `BBTank.create(cfg)` builds a tank against any DOM. The standalone
   /aquarium/ page auto-initializes (full chrome, body-scoped phase); the
   front-door lobby calls create() with a scoped, minimal config so it can be
   the full-bleed background without recolouring the whole homepage. The data
   pipeline (/api/tank) is shared and untouched. */
(function () {
  'use strict';

  var SLEEP_AFTER_MS = 30 * 60 * 1000;
  var REDUCED_MOTION = typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var PHASES = {
    dawn:     { sky: '◔', bg: '#1b1712', glow: 'rgba(224,169,109,0.07)', fish: '#e6d6bf', accent: '#e0a96d', surface: '~' },
    day:      { sky: '☀', bg: '#12161a', glow: 'rgba(16,185,129,0.07)',  fish: '#cdd6c4', accent: '#10b981', surface: '~' },
    dusk:     { sky: '◕', bg: '#1a130e', glow: 'rgba(217,138,90,0.07)',  fish: '#e2cdb2', accent: '#d98a5a', surface: '~' },
    night:    { sky: '☾', bg: '#0a0a0b', glow: 'rgba(125,167,217,0.05)', fish: '#aebacc', accent: '#7da7d9', surface: '·' },
    witching: { sky: '☾', bg: '#140b1a', glow: 'rgba(139,92,246,0.08)',  fish: '#c4b6d8', accent: '#a78bfa', surface: '·' },
  };

  // Species field guide — labels + one-line descriptions (legend + tooltips).
  var SPECIES = {
    guppy:        ['Guppy', 'Cheerful and always around. Schools in mid-water.'],
    tetra:        ['Tetra', 'Small, quick, schooling instinct.'],
    rummynose:    ['Rummy-nose tetra', 'Nose flushes red when the water is clean — a living health gauge.'],
    hatchetfish:  ['Hatchetfish', 'Hangs at the very surface. A notorious jumper.'],
    killifish:    ['Killifish', 'A brief, brilliant bloom. Lives fast.'],
    snail:        ['Snail', 'Eats silt. Mostly stationary. Outlives most fish.'],
    pleco:        ['Royal pleco', 'Armored bottom wood-eater. Slow, ancient, unbothered.'],
    coldfin:      ['Coldfin', 'Drifts in when the machine runs cool.'],
    frostneon:    ['Frostneon', 'Rare cold-water visitor. Faint glow.'],
    thermalwisp:  ['Thermalwisp', 'Heat-borne. Rides the warm surface layer.'],
    emberlung:    ['Emberlung', 'Rare hot-water visitor. Breathes the warmth.'],
    crashstrider: ['Crashstrider', 'Born of a crash. Short, frantic life.'],
    shipfish:     ['Shipfish', 'Born when a project ships. Long-lived and proud.'],
    founderfish:  ['Founderfish', 'Born when a new project appears. Long-lived.'],
    driftfish:    ['Driftfish', 'Born from commits. Schools with its kin.'],
    witnessfish:  ['Witnessfish', 'Born when you write notes, plans, or history. It remembers what you wrote.'],
    notefish:     ['Notefish', 'Born when you write notes, plans, or history. It remembers what you wrote.'],
    cleanershrimp:['Cleaner shrimp', 'Rare. Tends the reef. A small good omen.'],
    crab:         ['Crab', 'Scuttles sideways along the bottom, claws raised, scavenging.'],
    'night-fish': ['Night-fish', 'Only surfaces 00:00–03:00. It knows when you are up too late.'],
    // Adopt-only resident (never rolled by the bestiary). Its glyph is a
    // thermometer, not a costume: the tick rewrites it every pass from work the
    // machine actually did — ><{~> asleep, ><{·> dark, ><{°> warm, ><{*> lit.
    ember:        ['Ember', 'Adopted, never spawned. Its glow is work actually done — dark when idle, lit when it has written back.'],
  };

  // Per-species size (rem). Big proud fish read as foreground; fry stay tiny.
  var SIZE = {
    pleco: 1.15, shipfish: 1.12, founderfish: 1.06, 'night-fish': 1.0,
    witnessfish: 0.94, notefish: 0.94, coldfin: 0.92, crashstrider: 0.86, emberlung: 0.84,
    thermalwisp: 0.82, guppy: 0.8, snail: 0.8, frostneon: 0.78, rummynose: 0.78,
    driftfish: 0.76, hatchetfish: 0.74, tetra: 0.68, killifish: 0.66, cleanershrimp: 0.6,
    crab: 0.92,
    ember: 0.9,
    eel: 1.18, anglerfish: 1.0,
  };

  // Global size of everything rendered inside the tank (fish + plants + decor).
  // Lower = finer/denser aquarium. Tune this one knob to scale the whole scene.
  var TANK_SCALE = 0.7;

  // === NIGHT ALIVE — one global knob per visual system (subtle defaults). ===

  // AQUASCAPE — how densely the panoramic floor is populated with hardscape +
  // plants. 1.0 = tuned default (intentional negative space). <1 sparser,
  // >1 lusher. The open swimming channel between masses is protected at any
  // density; this scales counts only.
  var DECOR_DENSITY = 1.0;

  // SKULL — the tank's centrepiece. Exactly one per scape, always placed (never a
  // dice roll), always the largest hardscape in the water. Standalone page only —
  // the lobby embed (cfg.minimal) keeps its original random keystone untouched.
  //
  // THIS IS THE "bigger/smaller" DIAL — the only number you need to touch.
  // Measured on a 1440px viewport (tank 418px tall): 1.0 renders the skull 187px,
  // i.e. 45% of the water column and roughly 1.9x the other hardscape — which
  // dominates the tank rather than anchoring it. 0.80 renders ~150px: still far
  // and away the largest thing down there, still the first thing the eye finds,
  // but the water keeps its room. Raise toward 1.0 if it should loom.
  var SKULL_SCALE = 0.80;

  // WATER LEVEL-UP — one knob per of the seven levers (subtle defaults).
  var CURRENT_FLOOR = 0.08;   // (f) min effective current — water never dead-still
  var MEDIUM_N      = 18;     // (a) shared drifting-medium tracer cap
  var MEDIUM_FLOOR  = 0.05;   // (a) never-zero medium opacity (alive at idle)
  var DEPTH_SPREAD  = 1.0;    // (d) parallax strength (0 = flat, 1 = default)
  var STREAK_MAX    = 14;     // (b) streakline pool hard cap
  var RIPPLE_N      = 6;      // (c) surface ripple-ring pool cap
  var REFLECT_BAND  = 26;     // (c) fish above this y% cast a surface reflection
  var REFLECT_GAIN  = 1.0;    // (c) reflection opacity multiplier (0 = off)
  var RAYS_LIVE     = 1;      // (e) animated god rays (0 = static CSS only)
  var SCHLIEREN     = 0;      // (g) whole-scene refraction wobble — DEFAULT OFF

  // BIOLUMINESCENT NIGHT — master halo/trail/plankton intensity in the dark
  // hours (night + witching). 0 = off, ~0.8 = the subtle default. Never gaudy.
  var GLOW = 0.8;

  // EEL + ANGLERFISH — eel hunt aggression + anglerfish lure brightness.
  // 0 = eel only lurks & lure is dim; 1 = full hunts + brightest lure.
  var CREATURE_FX = 0.7;

  var PERF_PROBE = 0;  // 1 = measure step() cost (EMA ms), read via handle._stepMs(). 0 = zero-cost off.

  var EEL_LOBBY_HUNTS = 1;  // 1 = full lurk/emerge/hunt/retreat in the lobby; 0 = dim floor patrol (pre-2026-08)

  // BEHAVIOR-STATE COST CONTRACT — every new per-fish behavior state added to
  // step() binds itself to ALL of these, or it does not land:
  //   * every field it reads/writes is pre-declared in the makeEntity return
  //     literal (hidden-class stable — no ad-hoc property adds after creation);
  //   * timers are dt-accumulated only (no setTimeout/setInterval — states must
  //     pause with the loop, exactly as the IntersectionObserver expects);
  //   * O(1) work per entity per frame, zero per-frame allocation;
  //   * it writes only style properties the entity already writes (left/top/
  //     transform/opacity/textShadow) — new filters/shadows need explicit sign-off;
  //   * its forces fold into the ax/ay accumulator, never a position teleport;
  //   * it ships behind a module dial, and risky-aesthetic defaults ship OFF;
  //   * measured budget <= 0.05ms per state at N=64 (PERF_PROBE is the meter).

  // FEEDING (the play) — both surfaces; FEED.on master, FEED.lobby gates the
  // embed. Click drops sinking food flakes; nearby fish seek + peck + consume
  // them. Pooled, capped, no per-frame allocation.
  var FEED = {
    on: true,         // master knob — flip off to disable the whole lane
    lobby: 1,         // kill switch for the embed: 0 = standalone-only feeding (exact old behavior)
    pool: 14,         // hard cap on simultaneous flakes (fixed ring of spans)
    perDrop: 3,       // max flakes spawned per click (1..perDrop)
    throttle: 140,    // ms between drops (rapid-click guard)
    radius: 22,       // % distance a fish will notice/seek a flake
    seek: 26,         // seek accel toward the nearest flake (overrides wander)
    sink: 5.5,        // %/sec gravity on a flake (the flow field bends the rest)
    life: 6.0,        // seconds a flake drifts before it dissolves
    eat: 2.2,         // % proximity at which a fish consumes the flake
  };
  var FEED_SCALE = 0.7;       // visual size of flakes (rides TANK_SCALE feel)

  // CURSOR CURIOSITY — park the pointer still over the water and one free
  // swimmer drifts over to inspect it. The standoff is an equilibrium, not a
  // scripted stop: the constant pull (gain 22) balances the quadratic startle
  // repulsion 160*(1-d/16)^2 at d ~= 10.1%, so the volunteer holds a shy ring
  // just outside touching distance. If the fish jitters on the ring, drop
  // gain to 18 — never touch startle().
  var CURIO = {
    on: true,     // the dial — false = behavior dark
    still: 1.2,   // sec of pointer stillness before a volunteer is chosen
    range: 34,    // % — only fish this close may volunteer
    gain: 22,     // %/s^2 constant pull toward the cursor (vs startle 160 quad)
    hold: 5.0,    // sec a volunteer stays interested
    rest: 8.0,    // sec cooldown before the next volunteer
    spook: 0.25,  // sec — pointer movement newer than this breaks the nerve
  };

  // SCHOOLMATE TAG — every min..max seconds two same-species schoolers play a
  // short game of chase, then melt back into the school via boids. The tank
  // starts on a post-load idle callback (~1-3s), so first:6 lands the debut
  // chase ~7-9s after landing — do not lower below ~4 (it competes with the
  // hero copy reveal). A third schoolmate briefly recruited by alignment
  // reads as play — accepted.
  var PLAY = {
    on: true,     // the dial — false = behavior dark
    first: 6,     // sec after load until the debut game
    min: 16,      // sec between games (min)
    max: 30,      // ...and max
    dur: 2.4,     // sec a game lasts
    chase: 1.1,   // chaser pressure (accel factor)
    flee: 0.7,    // leader evasion (accel factor)
    dash: 1.25,   // burst-coast multiplier boost while playing (capped 1.6)
  };

  var ZONE_BAND = { surface: [8, 28], mid: [30, 64], bottom: [66, 86] };
  // Proud project fish hold their place as landmarks; everyone else swims.
  var ANCHOR = { shipfish: 1, founderfish: 1, witnessfish: 1, notefish: 1 };
  // Loose shoalers — they flock with their own kind.
  var SCHOOL = { tetra: 1, guppy: 1, rummynose: 1, driftfish: 1, killifish: 1, frostneon: 1 };
  // Shared empty mates list for non-schoolers — one frozen-shape sentinel, so
  // f.mates is always an array and the hot loop never branches on null.
  var EMPTY_SCHOOL = [];
  // Per-species shoaling weights. One personality per schooling species —
  // rummynose swims as one body (high alignment, tight), killifish is barely a
  // school at all (loose, separation-led). Sums held near the old shared 4.05
  // so no species gets a net-stronger flock force. The tetra row is
  // byte-identical to the old shared literal — the built-in control group.
  // If rummynose reads twitchy, lower ITS ali (2.20 -> 1.9) — never touch the
  // shared x8 gain at the call site.
  var SHOAL = {
    rummynose: { sep: 1.4, ali: 2.20, coh: 1.00, sepRadius:  6.0, neighborRadius: 24 },
    frostneon: { sep: 1.9, ali: 1.50, coh: 0.90, sepRadius:  7.5, neighborRadius: 20 },
    tetra:     { sep: 2.2, ali: 1.00, coh: 0.85, sepRadius:  9.0, neighborRadius: 18 },
    driftfish: { sep: 2.3, ali: 0.85, coh: 0.75, sepRadius: 10.0, neighborRadius: 18 },
    guppy:     { sep: 2.5, ali: 0.50, coh: 0.50, sepRadius: 11.0, neighborRadius: 14 },
    killifish: { sep: 2.7, ali: 0.35, coh: 0.30, sepRadius: 12.0, neighborRadius: 12 },
    // ember is `social: solo` in the bestiary and is NOT in SCHOOL, so boids
    // never runs for it. The row is its personality on record — separation-led,
    // barely cohesive — so that if it is ever given mates it keeps its own
    // company rather than inheriting the tetra default.
    ember:     { sep: 2.8, ali: 0.30, coh: 0.25, sepRadius: 12.0, neighborRadius: 12 },
  };
  var SHOAL_DEFAULT = SHOAL.tetra;
  // Darters: short, sharp burst-and-coast. Grazers: long, gentle.
  var DARTY = { crashstrider: 1, killifish: 1, thermalwisp: 1, emberlung: 1, hatchetfish: 1 };
  var CALM  = { snail: 1, pleco: 1, cleanershrimp: 1, coldfin: 1, 'night-fish': 1, anglerfish: 1, ember: 1 };
  // Station-holders: armored bottom dwellers that HOLD a spot on the substrate
  // for long stretches, then relocate a few body lengths. They leave the whole
  // free-swim chain (wander/boids/seekZone/startle/eel-scatter) — armored
  // catfish don't flee. Both species stay in CALM: burstCoast is simply
  // unreachable for them now, and removal is risk without benefit.
  // Empty the STATION table to revert both the branch AND the spawn pin.
  var STATION = { pleco: 1, cleanershrimp: 1 };
  var STATION_HOLD = { pleco: [8, 22], cleanershrimp: [5, 14] };   // sec parked
  var STATION_MOVE = { pleco: [1.8, 2.7], cleanershrimp: [1.2, 2.2] }; // sec dashing
  var STATION_DASH = 1.6;    // dash speed, x cruise (pleco 1.16%/s -> ~3 body lengths)
  var STATION_Y = { pleco: 1.5, cleanershrimp: 1.0 };   // % above floorPct — ON the substrate
  function holdFor(species) { var r = STATION_HOLD[species]; return r[0] + Math.random() * (r[1] - r[0]); }
  function moveFor(species) { var r = STATION_MOVE[species]; return r[0] + Math.random() * (r[1] - r[0]); }

  // Seconds to cross the tank, per species — a creature's true pace. Bottom
  // grazers creep; darters streak; crabs scuttle quick. (Scaled by current.)
  var CROSS = {
    snail: 150, pleco: 95, cleanershrimp: 85,                 // grazers: glacial
    crab: 18,                                                 // quick scuttle
    crashstrider: 12, killifish: 13, thermalwisp: 13,         // darty
    emberlung: 14, hatchetfish: 16,
    coldfin: 30, frostneon: 28, 'night-fish': 24,             // languid
    ember: 32,                                                // resident: unhurried mid-water cruise
    guppy: 22, tetra: 20, rummynose: 21, driftfish: 20,       // mid school
    eel: 26, anglerfish: 42,                                  // lurker / deep drifter
  };

  // Per-SECOND turn rates. These were per-frame probabilities — every creature
  // turned twice as often on a 120Hz display. p = 1 - exp(-rate*dt) reproduces
  // 60Hz exactly.
  var TURN_HZ = { crab: 0.72, snail: 0.24, eelCalm: 0.24, eelLurk: 0.36 };
  function turnDue(rate, dt) { return Math.random() < 1 - Math.exp(-rate * dt); }

  // Drawn direction per glyph.
  //
  // As of 2026-07-26 the bestiary is CANONICAL: every glyph is authored facing
  // RIGHT (tail "><" left, body parens curving back, eye, snout ">" right), and
  // symmetric creatures are symmetric on purpose. Facing is no longer a property
  // of the data at all — it is a property of the render. Here that is the scaleX
  // sign; in the terminal renderer it is tank.glyphs.mirror_glyph(). So natFace
  // is +1 for everything the tank ships today, and there is nothing left to
  // infer or to get wrong.
  //
  // This replaced a hand-maintained lookup table plus an eye-position heuristic.
  // That design was unsound in a way no test could catch: every verification
  // (including the repo's own probe) computed facing as sign(scaleX) * natFace,
  // i.e. it multiplied by the very table it was checking, so a wrong entry
  // reported "ok" forever while rendering a permanently backwards species. The
  // fix was to delete the ambiguity upstream, not to add another assertion.
  //
  // LEGACY_FACING below is the retirement home for the OLD mixed-facing
  // spellings. Fish keep the glyph they were born with, so a fish that spawned
  // before the migration can still be swimming one of these; fossils and the
  // graveyard hold them permanently. Keep this map — do not "clean it up" —
  // until the published snapshot has been free of these glyphs for a full
  // lifespan (30+ days). Nothing new should ever be added to it.
  var LEGACY_FACING = {
    '<°))>': -1,                                               // guppy
    '<·><': -1,                                                // tetra
    '<(°<=': -1,                                               // rummynose
    '<^v^': -1,                                                // hatchetfish
    '<≈><': -1,                                                // killifish
    '_@': -1,                                                  // snail
    '<=#>': -1,                                                // pleco
    '<°)))><': -1,                                             // coldfin
    '<(*°)<': -1,                                              // frostneon
    '<≈≈<': -1,                                                // emberlung
    '<°))))><': -1,                                            // shipfish
    '<°)F)><': -1,                                             // founderfish
    '<o><': -1,                                                // driftfish
    '<°)W><': -1,                                              // notefish / witnessfish
    '°<))°<': -1,                                              // night-fish
    // The three the 2026-07-25 audit disputed. The answer turned out to be
    // "neither": these are MALFORMED, not left-facing. Each opens with ">" and
    // closes with "<", so both wedges point inward, and mirroring produces
    // another glyph that is equally not-a-fish:
    //     '>°))<'  mirrors to  '>((°<'
    //     '>o))<'  mirrors to  '>((o<'
    //     '>°))°<' mirrors to  '>°((°<'
    // No reading could settle them because there was nothing to settle — which
    // is why the audit deadlocked and why the answer was to redraw them, not to
    // pick a sign. The canonical bestiary no longer contains any of them.
    // The -1 below is a judgement call for stragglers only: the eye sits at
    // index 1, so the head reads left. Arbitrary, and deliberately so.
    '>°))<': -1, '>o))<': -1,                                  // guppy (retired, malformed)
    '>°))°<': -1,                                              // night-fish (retired, malformed)
    // Web-only creatures — not in the python bestiary, so they never went
    // through the migration. Left as authored.
    '~~~∋°>': 1, '<°∈~~~': -1,                                 // eel
    '∽∽∽°>': 1, '<°∽∽∽': -1,                                   // anglerfish
  };

  // Canonical bestiary => +1. Only the retired spellings need a lookup.
  function natFace(g) {
    if (!g) return 1;
    if (Object.prototype.hasOwnProperty.call(LEGACY_FACING, g)) return LEGACY_FACING[g];
    return 1;
  }

  // Glow + lure brightness live ONLY in night/witching (constraint #1).
  function isDarkPhase(phase) {
    return phase === 'night' || phase === 'witching';
  }

  // Per-species bioluminescence weight: how brightly a species glows in the
  // dark hours. 1 = lure-bright, ~0.2 = the faint bottom dwellers. Looked up
  // ONCE at makeEntity and cached on the entity (f.bio) — never per frame.
  var BIO_WEIGHT = {
    anglerfish: 1.0, frostneon: 0.9, notefish: 0.85, witnessfish: 0.85,
    driftfish: 0.8, 'night-fish': 0.8, killifish: 0.7, thermalwisp: 0.6,
    rummynose: 0.6, tetra: 0.55, guppy: 0.5, coldfin: 0.5, shipfish: 0.5,
    founderfish: 0.5, hatchetfish: 0.5, crashstrider: 0.45, emberlung: 0.45,
    cleanershrimp: 0.4, crab: 0.3, pleco: 0.25, snail: 0.2,
    ember: 0.75,   // a warm coal in the dark: bright enough to read, never lure-bright
  };
  function bioWeight(species) {
    var w = BIO_WEIGHT[species];
    return (w == null) ? 0.5 : w;
  }
  var TRAIL_LEN = 7;                        // points per light-trail — capped, short
  var TRAIL_SPECIES = { driftfish: 1, notefish: 1 };   // D5: commits + notes glow

  // -----------------------------------------------------------------------
  // The water itself — ONE closed-form 2D flow field every moving thing
  // samples (fish, bubbles, motes, surface), so the whole tank agrees which
  // way the water moves. Two summed sine eddies in curl form (u from sin*cos,
  // v from -cos*sin => roughly divergence-free: things circulate, never pile
  // up) plus a laminar drift. Amplitudes scale with current_strength, so an
  // idle machine is glassy and a working one visibly streams. Coordinates in
  // tank percent (0..100), velocities in percent/second.
  // -----------------------------------------------------------------------
  // Optional `out` scratch param: stepWater/step call this ~46x/frame, so the two
  // wrappers (flowAt/flowEff) each hand in a dedicated reused object and FLOW writes
  // into it instead of allocating a fresh literal — zero per-frame allocation on the
  // medium+streak+flake+fish+bubble passes. Omitting `out` keeps the old alloc path
  // for any one-off caller. Safe because no two flow results are ever alive at once.
  // 1 = the laminar drift re-circulates (surface right, floor left, zero net flux
  // over the column) so the tank turns over and the school uses its whole width.
  // 0 = the historical constant one-way drift. See the note inside FLOW.
  var GYRE_LAMINAR = 1;

  // SCHOOL-DISTRIBUTION INSTRUMENT — a per-species occupancy census over
  // vertical fifths of the tank (the units the original left-pile was measured
  // in — see the FLOW note below), smoothed by an EMA so a moment's clumping
  // doesn't read as a pile. Pure measurement; the P4 spread force reads it.
  var SPREAD_COLS = 5;    // fifths — the units the original pile was measured in
  var SPREAD_TAU  = 2.5;  // sec EMA on the census (the damping)
  // Occupancy-gradient lateral spread — the missing long-range, NON-saturating
  // crowding term (separation saturates via limit(); cohesion doesn't). The
  // force is the gradient of the measured occupancy field above: identically
  // zero at a flat distribution and zero-mean by construction. It is NOT a
  // drift term and must never be turned into one. If the _spread trace shows
  // sloshing, raise SPREAD_TAU before lowering SPREAD_GAIN — the smoothing IS
  // the damping.
  // SHIP-GATE VERDICT (2026-08-01, 24x10s fixture A/B): the force works — at
  // gain 2.4 median evenness rummynose 0.572 / guppy 0.782 / tetra 0.543 vs
  // the gain-0 control's 0.218 / 0.605 / 0.331 (low-column streaks 5 vs 16) —
  // but no species clears the >=0.85 gate: a P7-cohesive school moves as one
  // body, so per-sample occupancy stays clumped no matter how well it
  // circulates. Per the gate, the default ships OFF. Turn it to 2.4 to get
  // the measured 2-3x spread improvement; the instrument (_spread) reports
  // either way.
  var SPREAD_GAIN = 0;    // %/s^2 per fish of column gradient. 0 = OFF — the kill switch. Measured-good value: 2.4.
  var SPREAD_MAX  = 7.0;  // clamp; stays well under boids sep envelope (2.2*8 = 17.6)

  var FLOW = (function () {
    var e1 = { k1: 0.06, k2: 0.05, w: 0.18 };
    var e2 = { k1: 0.13, k2: 0.11, w: 0.31 };
    return function (x, y, t, s, out) {
      var A1 = 0.15 + s * 3.2, A2 = 0.08 + s * 1.6;
      var u = A1 * Math.sin(e1.k1 * y + t * e1.w) * Math.cos(e1.k2 * x)
            + A2 * Math.sin(e2.k1 * y - t * e2.w) * Math.cos(e2.k2 * x);
      var v = -A1 * Math.cos(e1.k1 * y + t * e1.w) * Math.sin(e1.k2 * x)
            - A2 * Math.cos(e2.k1 * y - t * e2.w) * Math.sin(e2.k2 * x);
      var r = out || {};
      // Laminar term. The eddies above are divergence-free, but a CONSTANT drift
      // in a bounded box is not: it is a one-way river with no return path, so
      // everything it touches piles up downstream against the right wall. Measured
      // on main: the school spent 89% of four minutes in the right two fifths and
      // never once entered the left third — two thirds of the tank sat empty.
      //
      // Real tanks re-circulate. GYRE makes the drift depth-dependent — rightward
      // along the surface, leftward along the floor, zero at mid-depth — so the
      // laminar flux integrates to zero over the column and the water turns over
      // instead of pushing one way forever. Set GYRE_LAMINAR = 0 to restore the
      // old one-way drift exactly.
      r.vx = u + s * 2.0 * (GYRE_LAMINAR ? Math.cos(Math.PI * y / 100) : 1);
      r.vy = v;
      return r;
    };
  })();

  // Hardscape — richer + bigger. Bottom row is the base (renderer bottom-aligns).
  // RE-AUTHORED 2026-06-09 in the JetBrains-Mono-700 1-cell palette: every glyph
  // measures exactly one monospace advance (block elements + light shades + light
  // box-drawing + a plain ○ for round inlays + ─ for seabed lines). No arcs, no
  // box-diagonals, no heavy/mixed box, no quadrant blocks, no floret/diamond/wavy
  // glyphs — those fall back to a wider system font and SHEAR under white-space:pre.
  // Widths verified in a browser harness (max residual ~0.18 cell from a couple of
  // interior spaces, which the short pieces never let accumulate).
  var STRUCTURES = [
    ' ▄█▄  ▄▄\n▄███▄▄██▄\n█████████\n█████████',                // iwagumi stone trio
    ' ▄█▄\n▄███▄\n▐███▌\n█████\n█████',                          // standing rock pillar
    '▄▄    ▄▄\n▐██▄▄▄██▌\n─┴──┴──┴─',                           // driftwood branch
    ' ▄██▄\n▐████▌\n ████\n ▐██▌\n  ██',                         // amphora — block-filled bulb, short neck, side handles, foot
    '▐█████▌\n ▐███▌\n ▐███▌\n▐█████▌\n███████',                 // ruined doric column
    ' ▄███▄\n▐█████▌\n│░░○░░│\n└─────┘',                         // treasure chest — domed lid, hasp ○, body
    '  ▄▀▀▀▄\n ▄▀▀▀▀▀▄\n▐░░░░░░░▌\n│││││││││\n ▀▀▀▀▀▀▀',      // giant clam — the piece that reads as a skull; grown 7x4 -> 9x5
    '█ █ █\n ▀█▀\n  █\n ▄█▄',                                    // branching coral — three branches on a base
    '│ │ │\n▐███▌\n█████\n ▀▀▀',                                  // sea anemone — tentacles over a rounded body
    ' ▄▄▄▄▄\n███████\n█░██░██\n▀█▀▀█▀▀\n───────',                 // shipwreck — solid block hull, inset round portholes, silt line
    '▄█████▄\n██   ██\n██   ██\n██   ██\n██   ██',                 // ruined archway — clean arch + standing legs
    '  ▄█▄\n ▐███▌\n  ███\n ▄███▄',                              // seashell cluster
    '  ○\n  │\n─█████─\n█▄ │ ▄█\n ▀█▄█▀',                          // ship anchor — ring, vertical shank, straight stock crossbar, block U-base
    '  ▄\n  █\n ▄█▄\n│░▒░│\n└───┘',                              // bottle — cork+neck, shoulders, scroll inside
    ' ▄▀▀▄\n▐████▌\n│░██░│\n▐████▌\n██████',                     // pagoda lantern stone
    ' ▄███▄\n▐░▒░▒▌\n▐▒░▒░▌\n └───┘',                            // brain coral
  ];

  // THE SKULL — the tank's keystone. Deliberately NOT a member of STRUCTURES:
  // pickRandom() would let it never appear, or appear twice at throwaway sizes.
  // seedDecorComposed() places it by hand, exactly once, in the focal slot (it is
  // the sole member of HERO_STRUCTURES, declared below).
  //
  //   rows 1-3  domed cranium, brightest (lit from above)
  //   rows 4-5  two deep-set orbits, 3 cells wide x 2 rows, each with a ░ glint
  //             of settled silt on the socket floor; a 1-cell nasal septum between
  //   row  6    orbit floor + shadowed zygomatic cheeks (▓) + the nasal void
  //   row  7    maxilla corners with a five-tooth row
  //   rows 8-9  the silt mound it is half-sunk into, and the sand line
  //
  // 11 columns x 9 rows. Every row column-counted and mirrored about column 6.
  // At JetBrains Mono's 0.6em advance against .structure's 0.95 line-height the
  // seven UNBURIED rows measure 6.6em x 6.65em — a true skull silhouette, not a
  // stretched one. Every glyph is from the verified 1-cell palette (█ ▐ ▌ ▄ ░ ▒ ▓
  // │ ─): no arcs, no diagonals, no heavy box, nothing that shears the column.
  var SKULL = '  ▄▄▄▄▄▄▄\n ▄███████▄\n▐█████████▌\n▐█   █   █▌\n▐█ ░ █ ░ █▌\n▐▓███ ███▓▌\n ▒█│││││█▒\n░▒▒▒▒▒▒▒▒▒░\n───────────';
  // Plants in tiers for layered aquascaping (background → foreground), plus a
  // top-rooted `epi` tier that hangs from high in the column to fill the dead
  // vertical space above the substrate. RE-AUTHORED 2026-06-09 in the same
  // JetBrains-Mono-700 1-cell palette as STRUCTURES — the old thin-wavy specks
  // (⌇ ≀ ∾) and florets (❀ ❁ ✿ ⚘) and box-diagonals (╲ ╱) all measured 0.69–1.82
  // cells and sheared. Stems are light verticals │, leaves are half-blocks ▌▐ or
  // light box tees, blossoms a plain ○ — all exactly one advance, so columns stack.
  var PLANTS = {
    tall: [
      '  │\n │││\n │││\n  ││\n  │\n  ┴',                          // jungle val ribbons
      '  ○\n ▐│▌\n  │\n ▐│▌\n  │\n  ┴',                          // flowering tall stem
      '  │\n │││\n │││\n │││\n  │\n ─┴─',                         // rotala bush
      '  │\n ▐│\n │▌\n ▐│\n  │\n  ┴',                             // corkscrew val — readable twist
      ' │ │\n │││\n │││\n  │\n ─┴─',                              // reedy spire
    ],
    mid: [
      '  ○\n ▌│▐\n ▌│▐\n └┼┘',                                    // amazon sword
      ' ○ ○\n ▌│▐\n ▌│▐\n  │',                                    // bushy flowering stem
      '  ○\n ▌ ▐\n  │\n ▌│▐\n  ┴',                                // broad-leaf cluster
      ' ○ ○\n └┼┘\n  │\n  ┴',                                     // anubias — paired leaves on a clear rhizome
      ' │││\n │││\n │││\n  ┴',                                    // water wisteria
    ],
    carpet: [
      ' │││││\n │││││',                                          // dwarf hairgrass
      '│ │ │\n┴ ┴ ┴',                                            // micro sword tufts
      '  ○\n ▐│▌\n  ┴',                                          // flowering rosette
      ' ▒▒▒▒\n ▒▒▒▒',                                            // moss clump
      ' │ │ │\n  ┴ ┴',                                           // pearlweed sprigs
      '▌ ▐\n ┴',                                                 // short broadleaf pair
    ],
    epi: [
      '▌ ○ ▐\n ▐│▌\n  │\n  │\n  │',                              // epiphyte fern on driftwood (rooted high)
      ' │││\n  ││\n  │\n  │\n  │',                               // hanging vine strand (upper-mid curtain)
      ' ▒▒▒\n ▒▒▒\n  ▒▒\n  ▒\n  ▒',                              // trailing moss veil
    ],
  };
  function pickRandom(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

  // The KEYSTONE slot — the ONE dramatic centrepiece the composed scape builds
  // itself around (foreground, largest, crispest, standing on a golden third).
  // Anything in here WINS that slot; empty falls back to a random STRUCTURES
  // pick. Seeded with SKULL (declared above, deliberately outside STRUCTURES) so
  // the skull is placed exactly once, every load, at a fixed size.
  var HERO_STRUCTURES = [SKULL];

  // Composed-scape colour — aerial perspective. Distance drains warmth and
  // saturation, so the far wall goes cool-blue and only the near mass keeps the
  // warm hardscape brown. Fixes the "everything is the same tan mud" read.
  var SCAPE_TINT = {
    far:     '#4a5a68',
    mid:     '#6b6355',
    near:    '#9c876a',
    leafFar: '#3d6f70',
    leafMid: '#2f8f70'
  };

  // Composed-scape depth ramp. depthStyle() below only spans 0.46..1.00 of the
  // intended opacity with <=1.3px of blur, which is why the old bottom had every
  // tier fighting at the same visual weight. This is quadratic and much wider:
  // the far wall drops to ~22% and carries real blur, the foreground stays full
  // and razor-sharp. Pure — seed-time only, never called per frame.
  function scapeDepth(depth, baseOp) {
    var d = depth < 0 ? 0 : depth > 1 ? 1 : depth;
    var k = 1 - d;
    var op = baseOp * (0.22 + 0.78 * d * d);
    var blur = k * k * 2.2;
    if (blur < 0.2) blur = 0;
    return { op: op, blur: blur };
  }

  // Per-piece size jitter — tighter than the old 0.78..1.28 so the keystone can
  // never randomly shrink below the echo (which broke the focal read outright).
  function scapeJitter() { return 0.86 + Math.random() * 0.28; }

  // Density knob for the composed scape (mirrors seedDecorLegacy's dcount).
  function scapeCount(base) {
    var n = Math.round(base * DECOR_DENSITY);
    return n < 0 ? 0 : n;
  }

  // GLYPH CYCLING — movement in the ASCII itself. A few soft pieces (moss,
  // hairgrass) swap between authored frames on a slow, jittered clock: light
  // dappling through a moss pad, blade tips caught by the current. Keyed by the
  // EXACT art string in PLANTS, so a piece only cycles if the seeder picked it.
  // Frame 0 is always the resting pose, and EVERY frame is dimension-identical
  // to it (same line count, same characters per line) so a swap can never
  // reflow the element. Palette is the same verified 1-cell set as
  // PLANTS/STRUCTURES — light verticals, half-blocks and light shades only.
  var GLYPH_CYCLES = {};
  GLYPH_CYCLES[' │││││\n │││││'] = [
    ' │││││\n │││││',   // rest — blades upright
    ' ▐│▐│▐\n │││││',   // tips caught leaning right (bases hold)
    ' ▌│▌│▌\n │││││'    // tips caught leaning left
  ];
  GLYPH_CYCLES[' ▒▒▒▒\n ▒▒▒▒'] = [
    ' ▒▒▒▒\n ▒▒▒▒',             // rest — even moss pad
    ' ▒░▒▒\n ▒▒░▒',             // light dapples through
    ' ░▒▒░\n ▒▒▒▒'              // the dapple moves on
  ];
  GLYPH_CYCLES[' ▒▒▒\n ▒▒▒\n  ▒▒\n  ▒\n  ▒'] = [
    ' ▒▒▒\n ▒▒▒\n  ▒▒\n  ▒\n  ▒',
    ' ▒░▒\n ░▒▒\n  ▒░\n  ▒\n  ▒',
    ' ░▒▒\n ▒▒░\n  ░▒\n  ▒\n  ▒'
  ];
  var CYCLE_MIN = 3.5;    // seconds between frame swaps, per element (min)
  var CYCLE_MAX = 9.0;    // ...and max. Slow and sparse — never a strobe.
  var CYCLE_CAP = 4;      // hard cap on how many pieces may cycle at once

  // GLYPH CAUSTIC — one band of surface light crosses the whole scape every
  // GLINT_DUR seconds. Each piece is phase-keyed to its own x so the band
  // SWEEPS left->right (agreeing with the laminar drift in FLOW) instead of
  // every piece flashing together, with a touch of one-sided jitter so the
  // wavefront isn't a ruler-straight line. Seed-time only — the animation
  // itself is pure CSS, zero per-frame cost.
  var GLINT_DUR = 16;     // seconds; MUST match --glint-dur in the page CSS
  function glintDelay(x) {
    return (-((100 - x) / 100) * GLINT_DUR - Math.random() * 0.9).toFixed(2) + 's';
  }

  // Atmospheric depth ramp for decor. depth: 0 (far bg) .. 1 (near fg).
  // baseOp is the piece's intended foreground opacity; we fade it back with
  // distance and add a touch of blur to the far tier so the back wall recedes
  // instead of competing with the fish. Pure — seed-time only.
  function depthStyle(depth, baseOp) {
    var d = depth < 0 ? 0 : depth > 1 ? 1 : depth;
    var op = baseOp * (0.46 + 0.54 * d);          // far ~46% of intended, near full
    var blur = (1 - d) * 1.3;                       // up to ~1.3px softening on the far wall
    if (blur < 0.15) blur = 0;                      // foreground stays razor-crisp
    return { op: op, blur: blur };
  }

  // Is x% inside the protected open swimming channel (a keep-out band centered
  // between the focal & echo masses)? Keeps the deliberate negative space clear.
  function inChannel(x, chanCenter, chanHalf) {
    return Math.abs(x - chanCenter) < chanHalf;
  }

  // -----------------------------------------------------------------------
  // Steering kit — pure helpers. Coordinates: x,y in percent 0..100, y=0 is
  // the TOP, velocities in percent/second, accel in percent/second^2.
  // (Grounded boids/kinematics; see alive-pass research memo.)
  // -----------------------------------------------------------------------
  function limit(vx, vy, max) {
    if (!max || max <= 0) return { x: 0, y: 0 };
    var m2 = vx * vx + vy * vy;
    if (m2 <= max * max) return { x: vx, y: vy };
    var m = Math.sqrt(m2);
    if (m === 0) return { x: 0, y: 0 };
    var s = max / m;
    return { x: vx * s, y: vy * s };
  }

  function wander(fish, dt) {
    if (typeof fish.wanderAngle !== 'number') {
      fish.wanderAngle = (fish.vx || fish.vy) ? Math.atan2(fish.vy, fish.vx) : Math.random() * Math.PI * 2;
    }
    fish.wanderAngle += (Math.random() * 2 - 1) * 1.2 * dt;     // lazy heading drift
    return { ax: Math.cos(fish.wanderAngle) * 6, ay: Math.sin(fish.wanderAngle) * 6 };
  }

  // Loose Reynolds flocking. Separation-dominant so schools breathe.
  function boids(fish, neighbors, opts) {
    var wSep = opts.sep, wAli = opts.ali, wCoh = opts.coh;
    var sep2 = opts.sepRadius * opts.sepRadius;
    var nb2 = opts.neighborRadius * opts.neighborRadius;
    if (!neighbors.length) return { ax: 0, ay: 0 };
    var sepX = 0, sepY = 0, sc = 0, aliX = 0, aliY = 0, aliC = 0, cohX = 0, cohY = 0, cohC = 0;
    for (var i = 0; i < neighbors.length; i++) {
      var o = neighbors[i];
      if (o === fish) continue;
      var dx = o.x - fish.x, dy = o.y - fish.y, d2 = dx * dx + dy * dy;
      if (d2 === 0) continue;
      if (d2 < sep2) { sepX -= dx / d2; sepY -= dy / d2; sc++; }
      if (d2 < nb2) { aliX += o.vx; aliY += o.vy; aliC++; cohX += o.x; cohY += o.y; cohC++; }
    }
    var ax = 0, ay = 0;
    if (sc) { var sL = limit(sepX, sepY, 1); ax += sL.x * wSep; ay += sL.y * wSep; }
    if (aliC) { var aL = limit(aliX / aliC - fish.vx, aliY / aliC - fish.vy, 1); ax += aL.x * wAli; ay += aL.y * wAli; }
    if (cohC) { var cL = limit(cohX / cohC - fish.x, cohY / cohC - fish.y, 1); ax += cL.x * wCoh; ay += cL.y * wCoh; }
    return { ax: ax, ay: ay };
  }

  // Soft vertical habitat containment. y=0 is top: below the band => push up
  // (negative ay); above the band => push down (positive ay).
  function seekZone(fish, lo, hi) {
    if (fish.y < lo) return { ax: 0, ay: 4 * (lo - fish.y) - 1.5 * fish.vy };
    if (fish.y > hi) return { ax: 0, ay: -4 * (fish.y - hi) - 1.5 * fish.vy };
    return { ax: 0, ay: 0 };
  }

  // Cursor as predator: flee away, harder the closer it is.
  function startle(fish, mouse, radius, strength) {
    if (!mouse) return { ax: 0, ay: 0 };
    var dx = fish.x - mouse.x, dy = fish.y - mouse.y, d = Math.sqrt(dx * dx + dy * dy);
    if (d >= radius) return { ax: 0, ay: 0 };
    var c = 1 - d / radius, mag = strength * c * c;
    if (d < 1e-4) return { ax: mag, ay: 0 };
    return { ax: dx / d * mag, ay: dy / d * mag };
  }

  // Smooth horizontal U-turn near a wall (no teleport, no stick).
  function wallTurn(fish, marginX, dt) {
    var TA = 60, le = marginX, re = 100 - marginX;
    if (fish.x < le) {
      var dL = Math.min(1, (le - fish.x) / marginX);
      if (fish.vx < 0 || fish.x < le * 0.5) fish.vx += TA * dL * dt;
      if (fish.x < 0) { fish.x = 0; if (fish.vx < 0) fish.vx = -fish.vx * 0.5; }
    } else if (fish.x > re) {
      var dR = Math.min(1, (fish.x - re) / marginX);
      if (fish.vx > 0 || fish.x > 100 - marginX * 0.5) fish.vx -= TA * dR * dt;
      if (fish.x > 100) { fish.x = 100; if (fish.vx > 0) fish.vx = -fish.vx * 0.5; }
    }
  }

  function integrate(fish, accel, dt, maxSpeed, drag, cruise) {
    fish.vx += (accel.ax || 0) * dt;
    fish.vy += (accel.ay || 0) * dt;
    if (drag > 0) {
      var sp = Math.sqrt(fish.vx * fish.vx + fish.vy * fish.vy);
      if (sp > 1e-6) {
        var k = Math.min(1, drag * dt), target = sp + (cruise - sp) * k, s = target / sp;
        fish.vx *= s; fish.vy *= s;
      }
    }
    var cl = limit(fish.vx, fish.vy, maxSpeed);
    fish.vx = cl.x; fish.vy = cl.y;
    fish.x += fish.vx * dt; fish.y += fish.vy * dt;
  }

  // Burst-and-coast multiplier (~0.4..1.6): a quick thrust then a longer glide.
  // burst:coast ~1:3, period short for darters / long for grazers.
  function burstCoast(fish, dt) {
    if (!fish.burstPeriod) {
      var base = fish.darty ? 0.5 : (fish.calm ? 2.6 : 1.4);
      fish.burstPeriod = base * (0.85 + Math.random() * 0.3);
      fish.burstPhase = Math.random() * fish.burstPeriod;
    }
    fish.burstPhase += dt;
    if (fish.burstPhase >= fish.burstPeriod) fish.burstPhase %= fish.burstPeriod;
    var p = fish.burstPhase / fish.burstPeriod;
    var bf = fish.darty ? 0.22 : (fish.calm ? 0.42 : 0.3);
    var peak = fish.darty ? 1.6 : (fish.calm ? 1.28 : 1.45);
    var floor = fish.calm ? 0.65 : 0.5, m;
    if (p < bf) {
      var arc = Math.sin(Math.PI * (p / bf));
      if (fish.darty) arc = Math.pow(arc, 0.7);
      m = 1 + (peak - 1) * arc;
    } else {
      m = 1 - (1 - floor) * Math.sin(Math.PI * ((p - bf) / (1 - bf)));
    }
    return m < 0.4 ? 0.4 : (m > 1.6 ? 1.6 : m);
  }

  // Span transform: face travel (scaleX sign = dir*natFace) + a speed-locked
  // tail swish and a subtle area-conserving body flex. Idles, never freezes.
  //
  // On the rotate-after-scaleX question: `scaleX(-1) rotate(t)` is NOT a broken
  // swish. diag(-1,1)·R(t) === R(-t)·diag(-1,1), so a left-facing fish is the
  // mirror of the right-facing one rotated the other way — and because the body
  // is mirrored by the same operation, both read nose-down/tail-up at the same
  // wigglePhase. The swish is already direction-symmetric; leave it alone.
  //
  // What IS approximate is the PIVOT: transform-origin is the glyph's middle, so
  // the rotation sways the whole body and half of it reads as a nose-bob rather
  // than a tail-wag. SWISH_PIVOT moves ONLY the rotation's pivot toward the nose
  // (scaleX stays centred, so flipping direction still never translates the
  // fish). 0 = the historical centre pivot; 1 = pivot at the nose.
  var SWISH_PIVOT = 0;

  // Kick-and-glide: the tail beats hard during the burst (thrust high) and
  // nearly stills during the coast, instead of metronoming at every speed.
  // thrust is the burst-coast multiplier (~0.4..1.6); the ramp maps LO..HI to
  // beat 0..1, which scales tail amplitude (floor BEAT_AMIN) and frequency
  // (floor BEAT_FMIN). Reversibility: AMIN = FMIN = 1 restores today's tail
  // byte-for-byte, as does omitting the 4th argument (anchors do).
  var BEAT_LO   = 0.60;   // thrust at/below which the tail is at its floor
  var BEAT_HI   = 1.30;   // thrust at/above which the tail beats full
  var BEAT_AMIN = 0.16;   // amplitude floor during the glide (0..1 of full)
  var BEAT_FMIN = 0.45;   // frequency floor during the glide (0..1 of full)

  function tailTransform(fish, dt, scaleSign, thrust) {
    if (typeof fish.wigglePhase !== 'number') fish.wigglePhase = Math.random() * Math.PI * 2;
    var speed = fish.speed || 0;
    var beat = 1;
    if (typeof thrust === 'number') { beat = (thrust - BEAT_LO) / (BEAT_HI - BEAT_LO); beat = beat < 0 ? 0 : (beat > 1 ? 1 : beat); }
    var amp = BEAT_AMIN + (1 - BEAT_AMIN) * beat;
    var freq = Math.min(22, 2 + 0.45 * speed) * (fish.beatK || 1) * (BEAT_FMIN + (1 - BEAT_FMIN) * beat);
    fish.wigglePhase += freq * dt;
    if (fish.wigglePhase > Math.PI * 2) fish.wigglePhase %= Math.PI * 2;
    var ph = fish.wigglePhase;
    var rot = (3 + Math.min(3, speed * 0.2)) * amp * Math.sin(ph);
    // C-start pose: the whole-body C-bend, decaying over ESCAPE_DUR. Composes
    // AFTER the amp scaling — deliberately not scaled by beat, because the
    // C-bend is a whole-body event, not a tail beat. Without it the escape
    // reads as fast translation, not a flinch.
    if (fish.escT > 0) { var eN = fish.escT / ESCAPE_DUR; rot += ESCAPE_BEND * fish.escSign * eN * Math.sqrt(eN); }
    var flex = 0.02 * amp * Math.sin(ph + 0.6);
    var sx = (scaleSign * (1 - flex)).toFixed(4), sy = (1 + flex).toFixed(4);
    if (!SWISH_PIVOT) {
      return 'scaleX(' + sx + ') rotate(' + rot.toFixed(2) + 'deg) scaleY(' + sy + ')';
    }
    if (typeof fish.pivotEm !== 'number') {
      // Half a monospace advance is ~0.3em, so the nose of an n-glyph fish sits
      // ~0.3n em from centre — at the glyph's NAT end, in its own pre-flip
      // coordinates (the translateX pair lives inside the scaleX). Computed once.
      fish.pivotEm = (fish.nat < 0 ? -1 : 1) *
        ((fish.glyph ? fish.glyph.length : 3) * 0.25) * SWISH_PIVOT;
    }
    return 'scaleX(' + sx + ') translateX(' + fish.pivotEm.toFixed(3) + 'em) rotate(' +
      rot.toFixed(2) + 'deg) translateX(' + (-fish.pivotEm).toFixed(3) + 'em) scaleY(' + sy + ')';
  }

  // --- Facing: ONE authority for every creature in the tank ----------------
  // The rule is invariant across the bestiary: rendered scaleX sign must be
  // sign(the creature's ACTUAL on-screen horizontal motion) * natFace. Reading
  // that straight off vx is correct but jittery — a hovering fish, or a landmark
  // easing through the turnaround of its hover, has vx wobbling around zero and
  // would strobe its glyph every few frames, which looks as broken as moonwalk.
  // So: a dead-band commits a new heading only once the creature is genuinely
  // moving that way, and the emitted sign eases across zero instead of snapping
  // inside-out — the fish goes edge-on for a beat, the way a real one rolls into
  // a turn. Pure arithmetic on the entity: no DOM, no layout read, no allocation,
  // no timer. Rides the existing per-frame step().
  var FACE_DEAD = 0.35;   // %/sec — below this a creature holds its last heading
  var FACE_TURN = 7;      // sign units/sec — a full flip takes 2/FACE_TURN sec
  var FACE_MIN  = 0.06;   // never emit scaleX(0): keep a sliver of width

  // C-START ESCAPE (Mauthner flinch) — an abrupt threat inside the trigger
  // radius fires a one-shot impulse along the away-vector plus a whole-body
  // C-bend. Scale notes: trigger 11% ~ 2-3 body lengths; the kick is 1.1x
  // maxSpeed so the flinch visibly outruns cruise; during the escape window
  // the speed cap is a flat maxSpeed*ESCAPE_CAP (NO burst-coast term — at the
  // bc floor 0.4 the coast would swallow the kick same-frame); the existing
  // 1.2/s drag relaxes the burst in ~0.83s, no decay code needed.
  var ESCAPE_TRIG = 11;    // % trigger radius, scaled by f.bold. 0 disables the whole lane.
  var ESCAPE_DUR  = 0.18;  // sec of C-bend pose
  var ESCAPE_KICK = 1.10;  // impulse, in units of the fish's maxSpeed
  var ESCAPE_CAP  = 2.6;   // flat speed-cap multiplier during the escape window
  var ESCAPE_REFR = 1.4;   // sec refractory, scaled by 1/f.bold — no machine-gun flinch
  var ESCAPE_BEND = 26;    // deg of C-bend at the moment of the kick

  function faceSign(fish, vx, dt) {
    if (typeof fish.faceDir !== 'number') {          // first sight: snap, don't ease
      fish.faceDir = vx < 0 ? -1 : 1;
      fish.face = fish.faceDir;
    }
    if (vx > FACE_DEAD) fish.faceDir = 1;
    else if (vx < -FACE_DEAD) fish.faceDir = -1;
    if (REDUCED_MOTION) { fish.face = fish.faceDir; return fish.faceDir; }
    var rate = FACE_TURN * (dt > 0 ? dt : 0);
    var gap = fish.faceDir - fish.face;
    if (gap > rate) fish.face += rate;
    else if (gap < -rate) fish.face -= rate;
    else fish.face = fish.faceDir;
    if (fish.face > -FACE_MIN && fish.face < FACE_MIN) {
      return fish.faceDir < 0 ? -FACE_MIN : FACE_MIN;
    }
    return fish.face;
  }

  // -----------------------------------------------------------------------
  // Factory — one tank bound to a config (DOM scope + optional chrome).
  // -----------------------------------------------------------------------
  function createTank(cfg) {
    cfg = cfg || {};
    var root = cfg.root || document;
    var scopeEl = cfg.scope || document.body;
    var POLL_MS = cfg.pollMs || 15000;   // tank serve: the local world ticks often; poll faster than the site
    // Three independent switches, so an embed can pick and choose:
    //   minimal  — chrome + decor: no name labels, hatch silt, the legacy decor
    //              sprinkle and four weed curtains (the lobby's keystone stays
    //              untouched — see seedDecor). Says nothing about the water.
    //   water    — 'rich' (the JS living water: drifting medium, streaklines,
    //              ripples, live bubbles, reflections, light-trails, the per-glyph
    //              surface wave) or 'classic' (the CSS-only water). Defaults to
    //              classic for minimal embeds, rich otherwise — the old coupling.
    //   wideFX   — the wide-tank gate for the rich water and the ray/caustic
    //              layers: true forces it on, false forces it off, 'auto' (the
    //              default) follows the tank's width (> 900px). Reduced motion
    //              always wins: the rich water needs the rAF loop.
    var richWater = cfg.water ? cfg.water === 'rich' : !cfg.minimal;
    var wideMode = (cfg.wideFX === true || cfg.wideFX === false) ? cfg.wideFX : 'auto';
    var ids = Object.assign({
      tank: 'tank', sky: 'sky', phase: 'phase', temp: 'temp', mood: 'mood',
      surface: 'surface', silt: 'silt', legend: 'legend', named: 'named',
      memorial: 'memorial', status: 'status', decor: 'decor', weeds: 'weeds',
      bubbles: 'bubbles', fossils: 'fossils',
    }, cfg.ids || {});

    function pick(id) {
      if (!id) return null;
      return root.getElementById ? root.getElementById(id) : root.querySelector('#' + id);
    }
    var $ = function (name) { return pick(ids[name]); };

    var tank = $('tank');
    if (!tank) return null;

    var currentStrength = 0;
    var sizeScale = Math.max(0.5, Math.min(2.5, Number(cfg.sizeScale) || 1));
    var timer = null;
    var lastPollAt = 0;         // Date.now() at the start of the last poll() attempt
    var entities = [];          // live fish sim entities
    var spreadKeys = [], spreadCount = {}, spreadEMA = {};   // (_spread) census state
    var rosterSig = null;       // signature of the current roster
    var rafId = null;
    var lastT = 0;
    var stepMsEma = 0;          // PERF_PROBE: EMA of step() cost in ms
    var mouse = null;           // {x,y} percent, or null when pointer is away
    var curioF = null, curioT = 0, curioRest = 0, mouseStillT = 0;   // (curiosity)
    var playA = null, playB = null, playT = 0;                        // (tag) leader / chaser / game clock
    var playTimer = PLAY.first + Math.random() * 3;                   // (tag) countdown to the next game

    // The true floor in percent terms. Zones are %-based but the silt strip is
    // a fixed 26px, so on a tall tank "82%" floats well above the substrate —
    // bottom dwellers (snail, crab) need the real floor, not the bottom band.
    var floorPct = 90;
    var wideFX = false;       // rich water effects: wide tank + motion allowed
    function computeFloor() {
      var r = tank.getBoundingClientRect();
      if (r.height) floorPct = Math.max(84, Math.min(94, 100 - 3400 / r.height));
      // The rich water rides the rAF loop, so reduced motion always parks it on
      // the classic CSS water; otherwise cfg.water picks the system and
      // cfg.wideFX (or the 900px width gate) decides whether the tank is wide
      // enough to carry it.
      var wide = wideMode === 'auto' ? r.width > 900 : wideMode;
      wideFX = richWater && wide && !REDUCED_MOTION;
      // Stamped for the page CSS: data-water says which water system this tank
      // runs, data-fx whether the wide effects (rays, caustics, pools) are on.
      tank.dataset.water = wideFX ? 'rich' : 'classic';
      tank.dataset.fx = wide && !REDUCED_MOTION ? 'wide' : 'narrow';
    }
    // Crossing the wide/narrow boundary (maximize, rotate) swaps the water
    // system — reseed the passengers so a maximized window isn't stuck with
    // the narrow tank's still water (and vice versa). Debounced.
    var resizeT = null;
    function onResize() {
      var was = wideFX;
      computeFloor();
      if (wideFX === was) return;
      if (resizeT) clearTimeout(resizeT);
      resizeT = setTimeout(reseedWater, 250);
    }
    window.addEventListener('resize', onResize);

    // Water-sim state. flowT advances once per frame; heat/silt arrive at poll
    // cadence and are cached here so step() never reads styles or layout.
    var flowT = 0;
    var heatLevel = 0;        // 0..1 from temperature_c
    var masses = [];          // hardscape anchors -> lee eddies in their wake
    var liveBubbles = [];     // wide tanks: JS bubbles advected by the field
    var motes = [];           // wide tanks: silt tracers that reveal the flow
    var surfSpans = [];       // wide tanks: per-glyph surface wave
    var surfKey = '';
    var streaks = [];         // (b) flow-streakline pool
    var ripples = [];         // (c) surface ripple-ring pool
    var siltBoost = 0;        // (a) medium brightness add from silt_density
    var rayLayer = null;      // (e) cached .rays element for live CSS vars
    var rayOpacity = 0.035;   // (e) current phase ray opacity (dust-in-beam gate)
    var lightLevel = null;    // weather.light_level 0..1 from the last snapshot (null = not published)
    var lampK = 1;            // within-phase lamp multiplier derived from it (LAMP_FLOOR..1)
    var trailFish = [];       // (glow) subset of entities that carry a light-trail
    var brightSwimmers = [];  // (glow) luminous fish plankton can twinkle against
    var lastPhaseObj = PHASES.night;
    var darkPhase = true;     // (eel/glow) night/witching => glow allowed
    var eelHuntPos = null;    // (eel) per-frame pointer to a hunting eel's position
    var flakes = [];          // (feeding) pooled flake records (fixed length)
    var cyclers = [];         // (shimmer) decor pieces that swap authored frames
    var lastDropT = 0;        // (feeding) throttle clock (performance.now ms)
    var flakeBubbleI = 0;     // (feeding) round-robin bubble index for consume
    var ZERO_STEER = { ax: 0, ay: 0, peck: 0 };  // (feeding) shared no-op steer
    var STEER = { ax: 0, ay: 0, peck: 0 };        // (feeding) shared steer result

    function reseedWater() {
      var layer = $('bubbles');
      if (layer) {
        // #bubbles holds bubbles + medium + streaks + ripples + trail points;
        // textContent='' wipes them all, so clear every array alongside.
        liveBubbles.length = 0;
        motes.length = 0;
        streaks.length = 0;
        ripples.length = 0;
        trailFish.length = 0;
        for (var ti = 0; ti < entities.length; ti++) { entities[ti].trail = null; }
        layer.textContent = '';
        seedBubbles();
        seedMedium();
        seedStreaks();
        seedRipples();
        seedTrails();
      }
      // Glyph cycling parks on its resting frame when the water system swaps
      // (wide <-> narrow) — never leave a piece frozen mid-pose.
      for (var cri = 0; cri < cyclers.length; cri++) {
        if (cyclers[cri].i === 0) continue;
        cyclers[cri].i = 0;
        cyclers[cri].el.textContent = cyclers[cri].frames[0];
      }
      // Feeding flakes live in #feed/tank (not #bubbles): park them so a
      // maximize/rotate doesn't strand a flake mid-fall against the new flow.
      for (var ffi = 0; ffi < flakes.length; ffi++) { flakes[ffi].live = false; flakes[ffi].el.style.opacity = '0'; }
      surfKey = '';
      renderSurface(lastPhaseObj);
      if (wideFX && !document.hidden) startLoop();
    }

    // Two reused scratch objects (one per wrapper) so the per-frame water passes
    // allocate zero {vx,vy} literals. flowAt and flowEff are never both alive at the
    // same instant, and no two flowAt (or two flowEff) results coexist, so a single
    // scratch each is sufficient.
    var _flowAtScratch = { vx: 0, vy: 0 };
    var _flowEffScratch = { vx: 0, vy: 0 };

    // Sample the water at a point: the shared field plus a recirculating eddy
    // and flow shadow in the lee of each hardscape mass (current splits around
    // rock; fish hold station in the calm behind it). Early-outs keep it cheap.
    function flowAt(x, y) {
      var f = FLOW(x, y, flowT, currentStrength, _flowAtScratch);
      for (var i = 0; i < masses.length; i++) {
        var m = masses[i];
        var dx = x - m.x, dy = y - m.y, d2 = dx * dx + dy * dy;
        if (d2 >= m.r * m.r) continue;
        var fall = 1 - Math.sqrt(d2) / m.r;
        var spin = currentStrength * 2.5 * fall;
        f.vx += -dy * spin * 0.05;
        f.vy += dx * spin * 0.05;
        f.vx *= (1 - 0.5 * fall);               // shelter: calmer in the lee
      }
      return f;
    }

    // --- Lever (f): ambient current floor + coupling. Water is never dead-still;
    // real current scales turbulence/chop/streak-speed ON TOP of the floor. ---
    function effStrength() {
      var s = currentStrength < 0 ? 0 : (currentStrength > 1 ? 1 : currentStrength);
      return CURRENT_FLOOR + (1 - CURRENT_FLOOR) * s;   // floor..1
    }

    // Sample the shared field at the FLOORED strength (lever f), keeping the same
    // lee-eddy + shelter math flowAt() applies, so the medium drifts at idle while
    // fish keep using the raw flowAt() for their advection. One result object.
    function flowEff(x, y) {
      var es = effStrength();
      var f = FLOW(x, y, flowT, es, _flowEffScratch);
      for (var i = 0; i < masses.length; i++) {
        var m = masses[i];
        var dx = x - m.x, dy = y - m.y, d2 = dx * dx + dy * dy;
        if (d2 >= m.r * m.r) continue;
        var fall = 1 - Math.sqrt(d2) / m.r;
        var spin = es * 2.5 * fall;
        f.vx += -dy * spin * 0.05;
        f.vy += dx * spin * 0.05;
        f.vx *= (1 - 0.5 * fall);
      }
      return f;
    }

    // Lever (e): is percent-x inside a (slowly swaying) god-ray shaft right now?
    // Two shafts, phase-locked to the same --ray-shift the CSS uses. Closed form;
    // returns 0..~1 extra brightness. Cheap enough to call per-mote per-frame.
    function rayBeam(x) {
      if (!RAYS_LIVE || rayOpacity <= 0.001) return 0;
      var sway = Math.sin(flowT * 0.5) * 14;            // shafts drift +/-14% slowly
      var b = 0;
      var c1 = 32 + sway, c2 = 68 + sway;               // shaft centers ~32%/68%
      var d1 = Math.abs(x - c1), d2 = Math.abs(x - c2);
      if (d1 < 11) b += (1 - d1 / 11);
      if (d2 < 11) b += (1 - d2 / 11);
      var fl = 0.7 + 0.3 * Math.sin(flowT * 1.3 + 0.6);
      return b * fl * (rayOpacity / 0.13);              // normalize to the day peak
    }

    // === WATER LEVEL-UP: shared medium pool + streaklines + ripples + the
    // one per-frame water driver (stepWater). All pools capped; no per-frame
    // allocation; no layout reads (floorPct cached at poll/resize). ===

    // (a) always-on medium. Phase-aware glyphs: detritus by day, plankton by night.
    var MEDIUM_DAY = ['·', '˙', '∙', '‧', '•'];      // gritty detritus, lit from above
    var MEDIUM_NIGHT = ['·', '˙', '∘', '°', '‧'];    // faint drifting plankton
    function mediumGlyphs() {
      var ph = scopeEl.dataset.phase;
      return (ph === 'night' || ph === 'witching') ? MEDIUM_NIGHT : MEDIUM_DAY;
    }
    // Builds the SHARED, capped, parallax-tagged medium pool (the reusable mote
    // pool other lanes read via api.medium()). Each tracer rides flowEff() so
    // the water is alive even at idle. Reduced-motion places them once, static.
    function seedMedium() {
      var layer = $('bubbles');
      if (!layer || !wideFX || motes.length) return;     // idempotent
      var dark = scopeEl.dataset.phase === 'night' || scopeEl.dataset.phase === 'witching';
      var gl = mediumGlyphs();
      for (var i = 0; i < MEDIUM_N; i++) {
        var m = document.createElement('span');
        m.className = 'mote' + (dark ? ' plankton' : '');
        m.textContent = gl[i % gl.length];
        var depth = (i % 3) / 2;                        // 0, .5, 1 banded
        var dfac = 0.55 + depth * 0.80 * DEPTH_SPREAD;  // advection multiplier
        m.style.fontSize = (0.55 + depth * 0.32).toFixed(2) + 'rem';
        m.style.opacity = '0';
        layer.appendChild(m);
        motes.push({
          el: m, x: 4 + Math.random() * 92, y: 16 + Math.random() * 64,
          depth: depth, dfac: dfac,
          base: (MEDIUM_FLOOR * (0.5 + depth * 0.7)),    // never-zero presence
          lit: 0, spark: 0, denseBias: depth,
        });
      }
      if (REDUCED_MOTION) placeMediumStatic();
    }
    function placeMediumStatic() {
      for (var i = 0; i < motes.length; i++) {
        var mo = motes[i];
        mo.el.style.left = mo.x + '%';
        mo.el.style.top = mo.y + '%';
        mo.el.style.opacity = mo.base.toFixed(2);
      }
    }

    // (b) flow streaklines. A capped pool of short fading segments seeded on a
    // coarse grid; each rides flowEff() for `life` frames then recycles.
    var STREAK_CHARS = '──┉';            // short dash trail "──┉"
    function seedStreaks() {
      var layer = $('bubbles');
      if (!layer || !wideFX || REDUCED_MOTION || streaks.length) return;   // idempotent
      var rect = tank.getBoundingClientRect();
      var cap = Math.max(8, Math.min(STREAK_MAX, Math.round((rect.width || 760) / 90)));
      for (var i = 0; i < cap; i++) {
        var s = document.createElement('span');
        s.className = 'streak';
        s.textContent = STREAK_CHARS;
        s.style.opacity = '0';
        layer.appendChild(s);
        streaks.push({ el: s, x: 0, y: 0, ang: 0, life: 0, max: 1, seedT: -Math.random() * 3 });
      }
    }
    function respawnStreak(s) {
      s.x = 6 + Math.random() * 88;
      s.y = 14 + Math.random() * (Math.max(20, floorPct - 4 - 14));
      s.life = 0;
      s.max = 0.7 + Math.random() * 0.8;        // seconds of visible travel
      s.ang = 0;
    }

    // (c) surface ripple rings + the SHARED ripple(x) entry point other lanes
    // call (lure pulse, eel emerge, feeding splash). Capped pool, no per-call alloc.
    function spawnRipple(x, strength) {
      if (!wideFX || REDUCED_MOTION) return;
      if (x == null) x = 50;
      var st = strength == null ? 0.5 : (strength < 0 ? 0 : strength > 1 ? 1 : strength);
      var r = null;
      for (var i = 0; i < ripples.length; i++) { if (ripples[i].life <= 0) { r = ripples[i]; break; } }
      if (!r) return;                          // pool exhausted -> drop (cheap)
      r.x = x < 1 ? 1 : x > 99 ? 99 : x;
      r.life = 0; r.max = 0.9 + st * 0.6; r.str = st;
      r.el.style.left = r.x + '%';
      r.el.style.display = 'block';
    }
    function seedRipples() {
      var layer = $('bubbles');
      if (!layer || !wideFX || ripples.length) return;     // idempotent
      for (var i = 0; i < RIPPLE_N; i++) {
        var d = document.createElement('div');
        d.className = 'ripple';
        d.style.display = 'none';
        layer.appendChild(d);
        ripples.push({ el: d, x: 50, life: 0, max: 1, str: 0 });
      }
    }

    // Lever (c): faint inverted reflection of a fish riding near the surface.
    // Lazily attaches ONE ghost child per fish on first need (no per-frame alloc
    // thereafter). Visible only when y is in the top band and wideFX.
    function writeReflection(f, scaleSign) {
      if (!wideFX || REFLECT_GAIN <= 0) return;
      var inBand = f.y <= REFLECT_BAND;
      if (!inBand) { if (f.ghost) f.ghost.style.opacity = '0'; return; }
      if (!f.ghost) {
        var g = document.createElement('span');
        g.className = 'fish-reflect';
        g.textContent = f.glyph;
        f.el.appendChild(g);
        f.ghost = g;
      }
      var dist = REFLECT_BAND - f.y;                          // 0 at band edge, max at top
      var op = Math.min(0.22, dist / REFLECT_BAND * 0.22) * REFLECT_GAIN;
      f.ghost.style.opacity = op.toFixed(3);
      f.ghost.style.transform = 'scaleX(' + Number(scaleSign).toFixed(3) + ') scaleY(-1) translateY(-150%)';
    }

    // One per-frame pass over all NON-fish water passengers. Zero allocation; all
    // pools fixed-size; no getBoundingClientRect (floorPct cached). Reflections are
    // written by the fish loop hook (writeReflection), not here.
    function stepWater(dt) {
      // (e) live god rays: drive two CSS vars the inline ray CSS consumes.
      if (RAYS_LIVE && rayLayer) {
        var shift = Math.sin(flowT * 0.5) * 6;                 // % sway
        var flick = 0.78 + 0.22 * Math.sin(flowT * 1.3);       // breath
        rayLayer.style.setProperty('--ray-shift', shift.toFixed(2) + '%');
        rayLayer.style.setProperty('--ray-flicker', flick.toFixed(3));
      }

      // (a)+(d) medium: ride the FLOORED field (alive at idle), parallax by depth,
      // brighten inside god-ray shafts (dust-in-beam). Opacity eases to a target.
      var witch = darkPhase && lastPhaseObj === PHASES.witching;
      // GLOW is the night-master for the witching plankton spark too (normalized to
      // its 0.8 default): lowering GLOW dims the twinkle with the rest of the dark.
      var witchSparkK = witch ? 0.6 * Math.min(1, GLOW / 0.8) : 0;
      for (var mi = 0; mi < motes.length; mi++) {
        var mo = motes[mi];
        var mf = flowEff(mo.x, mo.y);
        mo.x += mf.vx * mo.dfac * dt;
        mo.y += (mf.vy * mo.dfac + 0.5) * dt;                  // gentle sink
        if (mo.x < 1) mo.x = 99; else if (mo.x > 99) mo.x = 1;
        if (mo.y < 12) mo.y = 12;
        if (mo.y > floorPct) { mo.y = 14 + Math.random() * 10; mo.x = 4 + Math.random() * 92; }
        if (mo.spark > 0) { mo.spark -= dt * 2.2; if (mo.spark < 0) mo.spark = 0; }
        var beam = rayBeam(mo.x);
        var target = mo.base + siltBoost + beam * 0.16 + mo.spark * witchSparkK;
        if (target > 0.4) target = 0.4;
        mo.lit += (target - mo.lit) * Math.min(1, dt * 4);    // ease, no flicker pops
        mo.el.style.left = mo.x + '%';
        mo.el.style.top = mo.y + '%';
        mo.el.style.opacity = mo.lit.toFixed(3);
      }

      // (b) streaklines: advect a few frames, fade in/out over life, recycle.
      if (streaks.length) {
        var es = effStrength();
        var vis = (currentStrength - 0.04);                   // idle => invisible
        vis = vis < 0 ? 0 : vis > 1 ? 1 : vis;
        var spd = 0.6 + es * 1.6;                              // travel speed couples
        // Streaks read as a faint trace, not a light source (D1): base 0.38 (down
        // from 0.5). At night they glow on the accent, so GLOW is their master too —
        // normalized to its 0.8 default so turning GLOW down calms the night streaks
        // with the rest of the dark; day streaks are unaffected.
        var streakK = 0.38 * (darkPhase ? Math.min(1, GLOW / 0.8) : 1);
        for (var ti = 0; ti < streaks.length; ti++) {
          var s = streaks[ti];
          if (s.seedT < 0) { s.seedT += dt; continue; }       // staggered start
          if (s.life <= 0) respawnStreak(s);
          var sf = flowEff(s.x, s.y);
          var sp = Math.sqrt(sf.vx * sf.vx + sf.vy * sf.vy) || 0.0001;
          s.x += sf.vx * spd * dt;
          s.y += sf.vy * spd * dt;
          s.ang = Math.atan2(sf.vy, sf.vx) * 57.29578;
          s.life += dt;
          if (s.life >= s.max || s.x < 2 || s.x > 98 || s.y < 10 || s.y > floorPct) { s.life = 0; s.el.style.opacity = '0'; continue; }
          var p = s.life / s.max;
          var fade = Math.sin(Math.PI * p);                   // in then out
          var op = fade * streakK * vis * (0.4 + Math.min(1, sp * 0.5));
          s.el.style.left = s.x + '%';
          s.el.style.top = s.y + '%';
          s.el.style.transform = 'rotate(' + s.ang.toFixed(1) + 'deg)';
          s.el.style.opacity = op.toFixed(3);
        }
      }

      // (c) ripple rings: expand + fade, then sleep.
      if (ripples.length) {
        for (var ri = 0; ri < ripples.length; ri++) {
          var rp = ripples[ri];
          if (rp.life <= 0) continue;
          rp.life += dt;
          var rpp = rp.life / rp.max;
          if (rpp >= 1) { rp.life = 0; rp.el.style.display = 'none'; continue; }
          var size = 6 + rpp * (26 + rp.str * 30);            // px diameter
          var op = (1 - rpp) * (0.10 + rp.str * 0.16);
          rp.el.style.width = size.toFixed(1) + 'px';
          rp.el.style.height = (size * 0.34).toFixed(1) + 'px';
          rp.el.style.opacity = op.toFixed(3);
        }
      }

      // GLYPH CYCLING: the ASCII itself moves. A capped handful of soft pieces
      // (moss, hairgrass) swap between authored, dimension-identical frames on
      // a slow jittered clock. We always bounce back through frame 0 (the
      // resting pose), so a piece breathes rather than flickers. Cost: one
      // float subtract + compare per registered element per frame (<=4), and
      // ONE textContent write per element every 3.5-9s. No new timer — dt is
      // the same per-frame clock flowT rides.
      if (wideFX && cyclers.length) {
        for (var cyi = 0; cyi < cyclers.length; cyi++) {
          var cy = cyclers[cyi];
          cy.t -= dt;
          if (cy.t > 0) continue;
          cy.t = CYCLE_MIN + Math.random() * (CYCLE_MAX - CYCLE_MIN);
          cy.i = cy.i === 0 ? (1 + Math.floor(Math.random() * (cy.frames.length - 1))) : 0;
          cy.el.textContent = cy.frames[cy.i];
        }
      }
    }

    // === BIOLUMINESCENT NIGHT: per-fish halo + light-trails + plankton twinkle.
    // All night-only; no-ops off the glow phases. ===

    function isGlowPhase() {
      var a = lastPhaseObj && lastPhaseObj.accent;
      return a === '#7da7d9' || a === '#a78bfa';   // night | witching
    }

    // Per-fish halo channel — SEPARATE from span.transform (which step owns), so
    // motion and glow never fight. Writes the breathing bioluminescent shadow
    // INLINE on the .fish div (f.el.style.textShadow). Inline beats any stylesheet
    // rule, including the static body[data-phase="night"|"witching"] .fish floor
    // (specificity 0,0,1,1) that used to outrank the var(--halo) consumer and hide
    // the per-fish glow entirely. On the dark->light transition or the dim cutoff
    // we set textShadow to '' (empty) so the fish FALLS BACK to the stylesheet
    // .fish legibility drop-shadow — never to "none" (which would strip the day
    // legibility shadow for the rest of the session).
    function updateHalo(f, glow) {
      if (!glow) {                      // not a glow phase: clear inline, fall back to CSS floor
        if (f._lit) { f.el.style.textShadow = ''; f._lit = false; }
        return;
      }
      var depth = 1 - (f.y - 8) / 110;                  // surface bright -> floor dim
      if (depth < 0.4) depth = 0.4; else if (depth > 1) depth = 1;
      f.bioT += f._dtCache;
      var breathe = 0.85 + 0.15 * Math.sin(f.bioT * 1.26 + f.bioPhase);
      var b = glow * f.bio * depth * breathe;            // 0..~1
      if (b < 0.08) {                                    // (D1) faint/low-bio fish go fully dark
        if (f._lit) { f.el.style.textShadow = ''; f._lit = false; }
        return;
      }
      f._lit = true;
      var r1 = (5 + 9 * b).toFixed(1);                   // inner soft radius px
      var o1 = (0.20 + 0.45 * b).toFixed(2);             // inner alpha
      var r2 = (10 + 16 * b).toFixed(1);                 // outer bloom radius px
      var o2 = (0.04 + 0.26 * b).toFixed(2);             // (D1) lower outer-ring base — un-stack the bloom
      f.el.style.textShadow =
        '0 0 ' + r1 + 'px rgba(var(--accent-rgb), ' + o1 + '), ' +
        '0 0 ' + r2 + 'px rgba(var(--accent-rgb), ' + o2 + '), ' +
        '0 0 2px rgba(0,0,0,0.55)';
    }

    // Light-trails (D5): a fixed ring of pre-created luminous spans per
    // driftfish/notefish entity, seeded ONCE and recycled (no per-frame DOM
    // creation). Stored on f.trail. Wide tanks only.
    function seedTrails() {
      var layer = $('bubbles');
      if (!layer || !wideFX) return;
      for (var i = 0; i < entities.length; i++) {
        var f = entities[i];
        if (!TRAIL_SPECIES[f.species] || f.trail) continue;
        var spans = [];
        for (var k = 0; k < TRAIL_LEN; k++) {
          var s = document.createElement('span');
          s.className = 'trail-pt';
          s.style.opacity = '0';
          layer.appendChild(s);
          spans.push(s);
        }
        f.trail = { spans: spans, i: 0, lastX: f.x, lastY: f.y, acc: 0 };
        trailFish.push(f);
      }
    }
    // Advance all light-trails once per frame. Fixed pool, recycled ring index —
    // no allocation, no DOM creation. Night-only; off glow phases parks the ring.
    function updateTrails(dt, glow) {
      for (var n = 0; n < trailFish.length; n++) {
        var f = trailFish[n];
        var tr = f.trail;
        if (!tr) continue;
        if (!glow) {
          if (tr.acc !== -1) {
            for (var z = 0; z < tr.spans.length; z++) tr.spans[z].style.opacity = '0';
            tr.acc = -1;
          }
          continue;
        }
        if (tr.acc === -1) tr.acc = 0;
        tr.acc += dt;
        var dx = f.x - tr.lastX, dy = f.y - tr.lastY;
        if (tr.acc >= 0.09 || dx * dx + dy * dy > 1.2) {
          tr.acc = 0; tr.lastX = f.x; tr.lastY = f.y;
          var sp = tr.spans[tr.i];
          sp.style.left = f.x + '%';
          sp.style.top = f.y + '%';
          sp.style.opacity = (0.5 * f.bio * GLOW).toFixed(2);
          sp._age = 0;
          tr.i = (tr.i + 1) % tr.spans.length;
        }
        for (var k = 0; k < tr.spans.length; k++) {
          var s = tr.spans[k];
          if (s._age == null || s._age < 0) continue;
          s._age += dt;
          var life = 1 - s._age / 0.7;
          if (life <= 0) { s.style.opacity = '0'; s._age = -1; }
          else s.style.opacity = (life * 0.5 * f.bio * GLOW).toFixed(2);
        }
      }
    }
    // Witching plankton twinkle: motes already trace the flow (shared pool).
    // Test motes only against the small brightSwimmers list — never all fish.
    function twinklePlankton(dt, witching) {
      if (!witching) return;
      for (var i = 0; i < motes.length; i++) {
        var mo = motes[i];
        for (var j = 0; j < brightSwimmers.length; j++) {
          var f = brightSwimmers[j];
          var dx = f.x - mo.x, dy = f.y - mo.y;
          if (dx * dx + dy * dy < 9) { mo.spark = 1; break; }   // within ~3%
        }
      }
    }

    // === EEL + ANGLERFISH ===

    // The eel: an ambient bottom predator. Lurks dim & low near a wreck mass,
    // rarely emerges to hunt, scatters the nearest school, then retreats. It
    // does NOT eat (constraint D4). huntActive is read by the fish loop to drive
    // startle() as a 2nd predator.
    function stepEel(f, dt, floor, dark, fx) {
      f.t += dt;
      f.huntActive = false;
      var lowY = floor - 3;                 // resting depth: just off the substrate

      if (f.eelCalm) {                      // reduced-motion / rollback (EEL_LOBBY_HUNTS=0) path
        if (turnDue(TURN_HZ.eelCalm, dt)) f.vx = -f.vx;
        f.x += f.vx * 0.5 * dt;
        if (f.x < 4) { f.x = 4; f.vx = Math.abs(f.vx); }
        else if (f.x > 96) { f.x = 96; f.vx = -Math.abs(f.vx); }
        f.y += (lowY - f.y) * 0.6 * dt;
        f.speed = Math.abs(f.vx) * 0.5;
        renderEel(f, dt, dark);
        return;
      }

      if (!f.mode) { f.mode = 'lurk'; f.modeT = 14 + Math.random() * 16; f.y = lowY; }
      f.modeT -= dt;

      if (f.mode === 'lurk') {
        f.y += (lowY - f.y) * 0.5 * dt;
        if (turnDue(TURN_HZ.eelLurk, dt)) f.vx = -f.vx;
        f.x += f.vx * 0.35 * dt;
        if (f.x < 5) { f.x = 5; f.vx = Math.abs(f.vx); }
        else if (f.x > 95) { f.x = 95; f.vx = -Math.abs(f.vx); }
        f.speed = 2;
        if (f.modeT <= 0) { f.mode = 'emerge'; f.modeT = 1.6; f.lungeY = (40 + Math.random() * 20); }
      } else if (f.mode === 'emerge') {
        f.y += (f.lungeY - f.y) * 1.4 * dt;
        f.x += f.vx * (1.2 + fx) * dt;
        f.speed = 8 + 8 * fx;
        if (f.modeT <= 0) { f.mode = 'hunt'; f.modeT = 2.2 + Math.random() * 1.4; }
      } else if (f.mode === 'hunt') {
        f.x += f.vx * (2.4 + fx * 1.6) * dt;
        f.y += Math.sin(f.t * 1.3) * 4 * dt;
        if (f.x < 6) { f.x = 6; f.vx = Math.abs(f.vx); }
        else if (f.x > 94) { f.x = 94; f.vx = -Math.abs(f.vx); }
        f.huntActive = true;
        f.huntPos.x = f.x; f.huntPos.y = f.y;     // reused object, no alloc
        f.speed = 16 + 10 * fx;
        if (f.modeT <= 0) { f.mode = 'retreat'; f.modeT = 3.5; }
      } else {                                  // retreat
        f.y += (lowY - f.y) * 0.9 * dt;
        f.x += f.vx * 1.0 * dt;
        f.speed = 6;
        if (f.modeT <= 0) { f.mode = 'lurk'; f.modeT = 18 + Math.random() * 22; }
      }
      renderEel(f, dt, dark);
    }
    // Eel DOM write: undulating body + facing flip + lurk-dimming.
    function renderEel(f, dt, dark) {
      if (typeof f.wigglePhase !== 'number') f.wigglePhase = Math.random() * 6.28;
      f.wigglePhase += (1.4 + Math.min(6, f.speed * 0.4)) * dt;
      if (f.wigglePhase > 6.28318) f.wigglePhase %= 6.28318;
      var undulate = (4 + Math.min(8, f.speed * 0.5)) * Math.sin(f.wigglePhase);
      // Deliberate exception to the faceSign authority: eel flips are rare
      // (turnDue-gated) and the undulation masks the snap. Everything else in
      // the tank goes through faceSign.
      var sign = (f.vx >= 0 ? 1 : -1) * f.nat;
      f.el.style.left = f.x + '%';
      f.el.style.top = f.y + '%';
      f.span.style.transform = 'scaleX(' + sign + ') rotate(' + undulate.toFixed(2) +
        'deg) skewX(' + (undulate * 0.4).toFixed(2) + 'deg)';
      var lurking = (f.mode === 'lurk' || f.eelCalm);
      f.el.style.opacity = lurking ? '0.34' : '0.92';
      f.el.style.filter = (dark && !lurking)
        ? 'drop-shadow(0 0 5px rgba(120,200,170,0.5))' : 'none';
    }
    // The lure: a tiny bright point bobbing AHEAD of the anglerfish's mouth.
    // Lead direction = facing sign so it never drags behind. Glow only in dark.
    function renderLure(f, dt, dark, fx, still) {
      if (!f.lure) return;
      // The COMMITTED heading, not raw vx: the body eases through a turn, so the
      // bait must not teleport to the far side while the fish is still flipping.
      var lead = (typeof f.faceDir === 'number') ? f.faceDir : (f.vx >= 0 ? 1 : -1);
      var bob = still ? 0 : Math.sin((f.wigglePhase || 0)) * 1.4;
      f.lure.style.left = (lead * 1.1).toFixed(2) + 'em';
      f.lure.style.top = (-0.2 + bob * 0.06).toFixed(3) + 'em';
      if (dark) {
        // Wider CREATURE_FX authority (D1): floor 0.25 + 0.75*fx so the knob is a
        // usable dimmer (default fx=0.7 stays showpiece-bright ~0.78). GLOW is the
        // night-master — normalized to its 0.8 default (like rayBeam normalizes to
        // the day peak) so GLOW=0.8 leaves the lure exactly at 0.78 and lowering
        // GLOW calms the lure with the rest of the dark, never brightening it.
        var glowK = Math.min(1, GLOW / 0.8);
        var b = (0.25 + 0.75 * fx) * glowK;
        f.lure.style.opacity = b.toFixed(2);
        f.lure.style.textShadow = '0 0 6px rgba(180,150,255,' + b.toFixed(2) +
          '), 0 0 14px rgba(167,139,250,' + (b * 0.7).toFixed(2) + ')';
      } else {
        f.lure.style.opacity = '0.22';          // a dull bead in daylight
        f.lure.style.textShadow = 'none';
      }
    }

    // === FEEDING (the play) — flake pool + seek/peck + click handler ===

    function seedFeed() {
      var layer = pick('feed') || tank;     // dedicated layer if present, else tank
      if (!layer || flakes.length) return;
      var FLAKE_GLYPHS = ['·', '∘', '°', '٠'];
      for (var i = 0; i < FEED.pool; i++) {
        var el = document.createElement('span');
        el.className = 'flake';
        el.textContent = FLAKE_GLYPHS[i % FLAKE_GLYPHS.length];
        el.style.opacity = '0';
        el.style.fontSize = (FEED_SCALE * (0.7 + (i % 3) * 0.18)).toFixed(2) + 'rem';
        layer.appendChild(el);
        flakes.push({ el: el, live: false, x: 0, y: 0, vx: 0, vy: 0, t: 0, floor: false });
      }
      lastDropT = 0;
    }
    function freeFlake() {
      for (var i = 0; i < flakes.length; i++) if (!flakes[i].live) return flakes[i];
      return null;
    }
    // Surface ripple at x%: prefer the shared ripple ring; else a self-contained
    // inline ring so feeding never blocks on the water lane.
    function feedRipple(xPct) {
      if (REDUCED_MOTION) return;
      if (wideFX) { spawnRipple(xPct, 0.5); return; }
      var layer = pick('feed') || tank;
      if (!layer) return;
      var r = document.createElement('span');
      r.className = 'feed-ripple';
      r.style.left = xPct + '%';
      r.addEventListener('animationend', function () { r.remove(); });
      layer.appendChild(r);
    }
    function dropFood(xPct) {
      if (!FEED.on) return;
      var now = (window.performance && performance.now) ? performance.now() : Date.now();
      if (now - lastDropT < FEED.throttle) return;
      lastDropT = now;
      var x = xPct < 2 ? 2 : (xPct > 98 ? 98 : xPct);
      var n = REDUCED_MOTION ? 1 : (1 + Math.floor(Math.random() * FEED.perDrop));
      var spawned = 0;
      for (var i = 0; i < n; i++) {
        var fk = freeFlake();
        if (!fk) break;
        var spread = REDUCED_MOTION ? 0 : (Math.random() * 2 - 1) * 3;
        fk.live = true; fk.floor = false; fk.t = 0;
        fk.x = x + spread; fk.y = 6 + Math.random() * 3;
        fk.vx = REDUCED_MOTION ? 0 : (Math.random() * 2 - 1) * 1.5;
        fk.vy = FEED.sink * 0.4;
        fk.el.style.opacity = '0.85';
        fk.el.style.left = fk.x + '%';
        fk.el.style.top = fk.y + '%';
        spawned++;
      }
      if (spawned) feedRipple(x);
      if (spawned && !REDUCED_MOTION) startLoop();   // empty tank may not be looping
    }
    function killFlake(fk) {
      fk.live = false;
      fk.el.style.opacity = '0';
    }
    // Eat: retire the flake and outgas one bubble from the bite.
    function consumeFlake(fk, f) {
      killFlake(fk);
      if (liveBubbles.length) {
        var b = liveBubbles[(flakeBubbleI++) % liveBubbles.length];
        b.x = f.x; b.y = f.y - 1; b.vy = 7 + Math.random() * 4;
      }
    }
    // Per-fish feeding desire: nearest eligible live flake within radius -> seek
    // accel. Returns {ax,ay,peck}. Allocation-free.
    function feedSteer(f) {
      if (!FEED.on || REDUCED_MOTION) return ZERO_STEER;
      var bottom = f.crab || f.snail || f.species === 'pleco' || f.species === 'cleanershrimp';
      var bestD2 = FEED.radius * FEED.radius, bx = 0, by = 0, hit = null;
      for (var i = 0; i < flakes.length; i++) {
        var fk = flakes[i];
        if (!fk.live) continue;
        if (bottom && !fk.floor) continue;        // bottom feeders wait for it to land
        if (!bottom && fk.floor) continue;         // swimmers ignore floored crumbs
        var dx = fk.x - f.x, dy = fk.y - f.y, d2 = dx * dx + dy * dy;
        if (d2 < bestD2) { bestD2 = d2; bx = dx; by = dy; hit = fk; }
      }
      if (!hit) return ZERO_STEER;
      var d = Math.sqrt(bestD2) || 1e-4;
      if (bestD2 <= FEED.eat * FEED.eat) {         // arrived — consume + bubble
        consumeFlake(hit, f);
        STEER.ax = 0; STEER.ay = 0; STEER.peck = 1;
        return STEER;
      }
      var c = FEED.seek * (1 - d / FEED.radius + 0.4);   // stronger up close
      STEER.ax = bx / d * c; STEER.ay = by / d * c; STEER.peck = 0;
      return STEER;
    }
    // Advance every live flake: sink + advect on the shared flow, fade, settle.
    function stepFlakes(dt) {
      if (!FEED.on) return;
      for (var i = 0; i < flakes.length; i++) {
        var fk = flakes[i];
        if (!fk.live) continue;
        fk.t += dt;
        if (fk.t >= FEED.life) { killFlake(fk); continue; }
        if (REDUCED_MOTION) {
          fk.el.style.opacity = (0.85 * (1 - fk.t / FEED.life)).toFixed(2);
          continue;
        }
        if (!fk.floor) {
          var fl = flowAt(fk.x, fk.y);
          fk.x += (fl.vx * 0.5 + fk.vx) * dt;
          fk.y += (fk.vy + FEED.sink) * dt;
          fk.vx *= 0.96;
          if (fk.x < 1) fk.x = 1; else if (fk.x > 99) fk.x = 99;
          if (fk.y >= floorPct - 1) { fk.y = floorPct - 1; fk.floor = true; }
        }
        fk.el.style.left = fk.x + '%';
        fk.el.style.top = fk.y + '%';
        fk.el.style.opacity = (0.85 * (1 - fk.t / FEED.life * 0.7)).toFixed(2);
      }
    }
    // Pointer/touch -> drop food at the cursor x.
    function onTankDown(e) {
      if (!FEED.on) return;
      var r = tank.getBoundingClientRect();
      if (!r.width) return;
      var cx = (e.clientX != null) ? e.clientX
             : (e.touches && e.touches[0] ? e.touches[0].clientX : null);
      if (cx == null) return;
      dropFood((cx - r.left) / r.width * 100);
    }

    // Per-phase light: the sun's rake angle and strength through the water.
    // Dawn/dusk rake low and warm; noon stands steep; night nearly dies;
    // witching swaps to thin still violet shafts (see CSS override).
    var RAY = {
      dawn:     { angle: '72deg', op: 0.10 },
      day:      { angle: '88deg', op: 0.13 },
      dusk:     { angle: '70deg', op: 0.09 },
      night:    { angle: '84deg', op: 0.035 },
      witching: { angle: '90deg', op: 0.05 },
    };
    // Depth tint: the water column cools and darkens toward the silt.
    var DEPTH_TINT = {
      dawn:     'rgba(26,20,12,0.38)',
      day:      'rgba(10,18,22,0.32)',
      dusk:     'rgba(30,18,10,0.40)',
      night:    'rgba(16,24,46,0.50)',
      witching: 'rgba(30,16,46,0.50)',
    };

    // LIGHT LEVEL — how much of the phase's lamp the machine has actually earned.
    // fish-tank publishes weather.light_level = idle_factor * circadian(phase),
    // so the number already carries the time of day; dividing the circadian
    // ceiling back out leaves the idle factor (0.2..1 — an idle machine, a busy
    // one) and the phase stays the palette while light_level dims WITHIN it.
    // The multiplier never drops below LAMP_FLOOR: a dark tank is a phase, not a
    // fault. It is a colour, not motion, so reduced motion gets it too.
    var CIRCADIAN = { day: 1.0, dusk: 0.6, dawn: 0.55, night: 0.3, witching: 0.2 };
    var LAMP_FLOOR = 0.55;
    function lampFor(phase, level) {
      if (level == null || isNaN(level)) return 1;          // old snapshot: full lamp
      var ceiling = CIRCADIAN[phase] || 1;
      var k = level / ceiling;                              // idle factor, 0..1
      if (k < 0) k = 0; else if (k > 1) k = 1;
      return LAMP_FLOOR + (1 - LAMP_FLOOR) * k;
    }
    // Scale the alpha of an rgba() string — the phase glow is its lamp.
    function scaleAlpha(rgba, k) {
      return rgba.replace(/,\s*([0-9.]+)\s*\)\s*$/, function (_, a) { return ',' + (Number(a) * k).toFixed(3) + ')'; });
    }
    function applyLight(level) {
      var v = (level == null || level === '') ? null : Number(level);
      lightLevel = (v == null || isNaN(v)) ? null : (v < 0 ? 0 : v > 1 ? 1 : v);
      var phase = scopeEl.dataset.phase || 'night';
      lampK = lampFor(phase, lightLevel);
      var s = scopeEl.style;
      s.setProperty('--lamp', lampK.toFixed(3));
      s.setProperty('--light', lightLevel == null ? '1' : lightLevel.toFixed(2));
      tank.dataset.light = lightLevel == null ? '' : lightLevel.toFixed(2);
      var p = lastPhaseObj || PHASES.night;
      s.setProperty('--phase-glow', scaleAlpha(p.glow, lampK));
      var ray = RAY[phase] || RAY.night;
      rayOpacity = ray.op * lampK;                          // rayBeam() dims the dust-in-beam with it
      s.setProperty('--ray-opacity', rayOpacity.toFixed(4));
    }

    function applyPhase(phase) {
      var p = PHASES[phase] || PHASES.night;
      var s = scopeEl.style;
      s.setProperty('--phase-bg', p.bg);
      s.setProperty('--phase-accent', p.accent);
      s.setProperty('--phase-fish', p.fish);
      // The lamp: phase glow + ray strength, both modulated by the last
      // light_level (applyLight re-derives lampK for THIS phase).
      lampK = lampFor(phase, lightLevel);
      s.setProperty('--lamp', lampK.toFixed(3));
      s.setProperty('--phase-glow', scaleAlpha(p.glow, lampK));
      var ray = RAY[phase] || RAY.night;
      s.setProperty('--ray-angle', ray.angle);
      rayOpacity = ray.op * lampK;
      s.setProperty('--ray-opacity', rayOpacity.toFixed(4));
      if (!rayLayer) rayLayer = tank.querySelector('.rays');
      s.setProperty('--depth-tint', DEPTH_TINT[phase] || DEPTH_TINT.night);
      scopeEl.dataset.phase = phase;
      darkPhase = isDarkPhase(phase);
      var sky = $('sky'); if (sky) sky.textContent = p.sky;
      var ph = $('phase'); if (ph) ph.textContent = phase;
      lastPhaseObj = p;
      // Keep the always-on medium phase-appropriate (detritus <-> plankton).
      if (wideFX && motes.length) {
        var _g = mediumGlyphs();
        for (var _i = 0; _i < motes.length; _i++) {
          motes[_i].el.textContent = _g[_i % _g.length];
          motes[_i].el.className = darkPhase ? 'mote plankton' : 'mote';
        }
      }
      renderSurface(p);
    }

    // The free surface. Narrow tanks / reduced motion keep the classic single
    // swaying line. Wide tanks split it into per-glyph spans the rAF loop
    // displaces as a traveling wave — amplitude and speed from the current,
    // direction agreeing with the laminar drift below. Rebuilt only when the
    // phase glyph, chop tier, or span count changes (text mutation = layout).
    function renderSurface(p) {
      var surf = $('surface');
      if (!surf) return;
      var w = tank.getBoundingClientRect().width || 760;
      var n = Math.max(28, Math.round(w / 26));
      var glyph = currentStrength > 0.66 ? '≈' : (currentStrength > 0.33 ? '~' : p.surface);
      var key = (wideFX ? 'w' : 'n') + ':' + glyph + ':' + n;
      if (key === surfKey) return;
      surfKey = key;
      if (!wideFX) {
        surfSpans = [];
        surf.style.animation = '';
        surf.textContent = Array.from({ length: n }, function () { return glyph; }).join(' ');
        return;
      }
      surf.style.animation = 'none';     // the rAF wave owns the surface here
      surf.textContent = '';
      surfSpans = [];
      for (var i = 0; i < n; i++) {
        var sp = document.createElement('span');
        sp.textContent = glyph;
        sp.style.display = 'inline-block';
        surf.appendChild(sp);
        if (i < n - 1) surf.appendChild(document.createTextNode(' '));
        surfSpans.push(sp);
      }
    }

    function applyTemp(c) {
      // Heat becomes legible in the water: 0 below 24°C ramping to 1 at 45°C+.
      // The surface trembles with it (JS wave) and warms in color (CSS var).
      heatLevel = c == null ? 0
        : Math.max(0, Math.min(1, c < 24 ? 0 : c < 36 ? (c - 24) / 30 : 0.4 + (c - 36) / 15));
      scopeEl.style.setProperty('--heat', heatLevel.toFixed(2));
      var el = $('temp');
      if (el == null) return;
      if (c == null) { el.textContent = ''; return; }
      el.textContent = Math.round(c) + '°C';
      var cls = 'temp-mid';
      if (c < 24) cls = 'temp-cool';
      else if (c >= 45) cls = 'temp-hot';
      else if (c >= 36) cls = 'temp-warm';
      el.className = 'temp ' + cls;
    }

    function applyCurrent(strength) {
      var raw = Math.max(0, Math.min(1, strength || 0));
      var s = CURRENT_FLOOR + (1 - CURRENT_FLOOR) * raw;   // (f) ambient floor
      var st = scopeEl.style;
      // One normalized knob the CSS layers read for amplitude — idle water is
      // glassy (dim caustics, near-still plants), busy water visibly stirs.
      st.setProperty('--flow', s.toFixed(2));
      st.setProperty('--caustic-dur', (22 - s * 11) + 's');
      st.setProperty('--rise-dur', (11 - s * 4) + 's');
      st.setProperty('--ray-dur', (34 - s * 16) + 's');
      st.setProperty('--sway-amp', (2 + s * 7).toFixed(1) + 'deg');
      st.setProperty('--sway-fast', (1 - s * 0.4).toFixed(2));
    }

    // Build a fresh sim entity for one snapshot fish.
    function makeEntity(f, i) {
      var species = f.species || 'driftfish';
      var zone = ZONE_BAND[f.zone] ? f.zone : 'mid';
      var band = ZONE_BAND[zone];
      var anchor = !!ANCHOR[species];
      var cross = CROSS[species] || 22;
      // Allometry: the per-individual size jitter (fry..adult) now buys its
      // kinematics too. U ~ L^0.5 (bigger fish cruise faster), f ~ L^-0.5
      // (smaller fish beat faster), so stride ~ L: constant body-lengths per
      // beat across the population. Cruise moves +/-15%; tailbeat x1.18 for
      // the smallest fry down to x0.86 for the biggest adult.
      var grow = 0.72 + Math.random() * 0.62;
      var cruise = 110 / cross;                 // percent/sec to cross the tank
      var moodK = f.mood === 'darting' ? 1.4 : f.mood === 'sleeping' ? 0.6 : 1;
      cruise *= moodK;
      cruise *= Math.sqrt(grow);
      var x = anchor ? (8 + ((i * 53) % 84)) : (5 + Math.random() * 90);
      var y = band[0] + Math.random() * (band[1] - band[0]);
      // Bottom dwellers start ON the substrate, not somewhere in the band.
      if (species === 'snail' || species === 'crab') y = floorPct - 1;
      if (species === 'eel') y = floorPct - 3;          // the eel lurks LOW
      // Station-holders pin to the substrate at spawn — the floating-bottom-
      // dweller fix lands for free on the reduced-motion static path too.
      if (STATION[species]) y = floorPct - STATION_Y[species];
      var dir = (i % 2 === 0) ? 1 : -1;
      var el = document.createElement('div');
      el.className = 'fish' + ' ' + species + (anchor ? ' anchor' : '');
      var span = document.createElement('span');
      span.textContent = f.glyph || '><>';
      el.appendChild(span);
      // Per-fish size jitter — a mixed population (fry to fully-grown), not
      // clones. `grow` is the same draw the kinematics above spend.
      el.style.fontSize = ((SIZE[species] || 0.8) * TANK_SCALE * sizeScale * grow).toFixed(3) + 'rem';
      var named = (typeof f.name === 'string' && f.name) ? f.name : '';
      var guide = SPECIES[species];
      if (guide) el.title = guide[0] + (named ? ' · ' + named : '') + ' — ' + guide[1];
      if (anchor && named && !cfg.minimal) {
        var lbl = document.createElement('span');
        lbl.className = 'fish-name';
        lbl.textContent = named;
        el.appendChild(lbl);
      }
      // The anglerfish carries a separate bright LURE span ahead of its mouth.
      var lureEl = null;
      if (species === 'anglerfish') {
        lureEl = document.createElement('span');
        lureEl.className = 'lure';
        lureEl.textContent = '•';            // the bait light
        lureEl.style.position = 'absolute';
        lureEl.style.pointerEvents = 'none';
        el.appendChild(lureEl);
      }
      tank.appendChild(el);
      return {
        el: el, span: span, species: species, glyph: f.glyph || '><>',
        nat: natFace(f.glyph || '><>'), anchor: anchor, school: !!SCHOOL[species],
        mates: EMPTY_SCHOOL, shoal: SHOAL[species] || SHOAL_DEFAULT,
        darty: !!DARTY[species], calm: !!CALM[species], crab: species === 'crab',
        snail: species === 'snail', lo: band[0], hi: band[1],
        eel: species === 'eel', eelCalm: (species === 'eel' && (REDUCED_MOTION || (cfg.minimal && !EEL_LOBBY_HUNTS))),
        // mode/modeT declared here for hidden-class stability; '' is falsy so
        // the eel's and snail's `if (!f.mode)` inits still fire.
        station: !!STATION[species], mode: '', modeT: 0,
        lure: lureEl, huntPos: { x: 0, y: 0 }, huntActive: false,
        bio: bioWeight(species), bioPhase: Math.random() * 6.28, bioT: 0,
        sizeK: grow, beatK: 1 / Math.sqrt(grow),
        // C-start: bold spreads trigger radius AND refractory, so a school
        // never flinches in lockstep — do not drop it.
        escT: 0, escRefr: 0, escSign: 1, bold: 0.75 + Math.random() * 0.5,
        _dtCache: 0, _lit: false,
        x: x, y: y, homeX: x, homeY: y, vx: dir * cruise * 0.6, vy: 0,
        cruise: cruise, maxSpeed: cruise * 2.2, speed: cruise, t: Math.random() * 6.28,
      };
    }

    function clearEntities() {
      tank.querySelectorAll('.fish').forEach(function (n) { n.remove(); });
      entities = [];
      curioF = null;
      playA = playB = null; playT = 0;
    }

    function buildEntities(fish) {
      computeFloor();
      clearEntities();
      entities = fish.map(makeEntity);
      // Bioluminescent night: rebuild the small precomputed lists on roster change.
      // Same pass buckets schoolers by species into f.mates — built ONCE per
      // roster, so the per-frame boids call never rebuilds its neighbor list.
      // buildEntities is the only fill site; clearEntities empties `entities`,
      // making stale mates unreachable — no extra reset needed.
      trailFish.length = 0; brightSwimmers.length = 0;
      var buckets = {};
      for (var bi = 0; bi < entities.length; bi++) {
        var be = entities[bi];
        if (be.bio >= 0.7) brightSwimmers.push(be);   // luminous twinkle sources
        if (be.school) (buckets[be.species] || (buckets[be.species] = [])).push(be);
      }
      for (bi = 0; bi < entities.length; bi++) {
        entities[bi].mates = entities[bi].school ? buckets[entities[bi].species] : EMPTY_SCHOOL;
      }
      // (_spread) rebuild the census arrays for the schooling species on this
      // roster — one fixed array of SPREAD_COLS zeros per species, allocated
      // here (roster change) and never on the rAF path.
      spreadKeys.length = 0; spreadCount = {}; spreadEMA = {};
      for (var sk in buckets) {
        if (!Object.prototype.hasOwnProperty.call(buckets, sk)) continue;
        var zc = [], ze = [];
        for (var zi = 0; zi < SPREAD_COLS; zi++) { zc.push(0); ze.push(0); }
        spreadCount[sk] = zc; spreadEMA[sk] = ze;
        spreadKeys.push(sk);
      }
      seedTrails();   // builds the capped trail ring for driftfish/notefish
    }

    // One simulation step for all fish — plus the water itself.
    function step(dt) {
      flowT += dt;                       // the water's clock, once per frame
      eelHuntPos = null;                 // reset the per-frame hunt pointer
      var _glowOn = isGlowPhase() ? GLOW : 0;
      var _witch = _glowOn && lastPhaseObj === PHASES.witching;

      // (_spread) census prepass: zero the fixed count arrays, bin every
      // schooler into its column, fold into the EMA. Zero-alloc, fixed arrays.
      for (var ck = 0; ck < spreadKeys.length; ck++) { var cc = spreadCount[spreadKeys[ck]]; for (var cz = 0; cz < SPREAD_COLS; cz++) cc[cz] = 0; }
      for (var sI = 0; sI < entities.length; sI++) { var sE = entities[sI]; if (!sE.school) continue;
        var sIx = (sE.x * SPREAD_COLS / 100) | 0; if (sIx < 0) sIx = 0; else if (sIx >= SPREAD_COLS) sIx = SPREAD_COLS - 1;
        spreadCount[sE.species][sIx]++; }
      var kE = dt / SPREAD_TAU; if (kE > 1) kE = 1;
      for (var eK = 0; eK < spreadKeys.length; eK++) { var cn = spreadCount[spreadKeys[eK]], sm = spreadEMA[spreadKeys[eK]];
        for (var cE = 0; cE < SPREAD_COLS; cE++) sm[cE] += (cn[cE] - sm[cE]) * kE; }

      // (curiosity) prologue: O(1) idle; the volunteer scan is O(n) at
      // assignment time only, at most once per ~9s (hold+rest cadence).
      mouseStillT += dt;
      if (CURIO.on && !REDUCED_MOTION) {
        if (curioRest > 0) curioRest -= dt;
        if (curioF && (!mouse || mouseStillT < CURIO.spook)) {
          // Nerve break: the pointer moved (or left) — release the volunteer
          // BEFORE the entity loop, so a sudden move can C-start it this frame.
          curioF = null; curioT = 0; curioRest = CURIO.rest;
        }
        if (curioF) {
          curioT -= dt;
          if (curioT <= 0 || !curioF.el.isConnected) { curioF = null; curioRest = CURIO.rest; }
        } else if (mouse && curioRest <= 0 && mouseStillT >= CURIO.still) {
          var _cbest = CURIO.range * CURIO.range, _cf = null;
          for (var _ci = 0; _ci < entities.length; _ci++) {
            var _ce = entities[_ci];
            if (_ce.anchor || _ce.eel || _ce.crab || _ce.snail || _ce.station) continue;  // free swimmers only
            var _cdx = _ce.x - mouse.x, _cdy = _ce.y - mouse.y, _cd2 = _cdx * _cdx + _cdy * _cdy;
            if (_cd2 < _cbest) { _cbest = _cd2; _cf = _ce; }
          }
          if (_cf) { curioF = _cf; curioT = CURIO.hold; }
          else curioRest = CURIO.rest * 0.5;   // nothing in range: nap, don't rescan per-frame
        }
      }

      // (tag) prologue: O(1) idle; three zero-alloc index passes at game start
      // (count schoolers -> random leader -> nearest same-species chaser),
      // every 16-30s.
      if (PLAY.on && !REDUCED_MOTION) {
        if (playT > 0) {
          playT -= dt;
          if (playT <= 0 || !playA || !playB || !playA.el.isConnected || !playB.el.isConnected) {
            playA = playB = null; playT = 0;
            playTimer = PLAY.min + Math.random() * (PLAY.max - PLAY.min);
          }
        } else {
          playTimer -= dt;
          if (playTimer <= 0) {
            playTimer = PLAY.min + Math.random() * (PLAY.max - PLAY.min);
            var _pn = 0, _pi;
            for (_pi = 0; _pi < entities.length; _pi++) if (entities[_pi].school) _pn++;
            if (_pn >= 2) {
              var _pk = (Math.random() * _pn) | 0, _pa = null;
              for (_pi = 0; _pi < entities.length; _pi++) {
                if (!entities[_pi].school) continue;
                if (_pk === 0) { _pa = entities[_pi]; break; }
                _pk--;
              }
              var _pb = null, _pd = Infinity;
              for (_pi = 0; _pi < entities.length; _pi++) {
                var _pe = entities[_pi];
                if (_pe === _pa || !_pe.school || _pe.species !== _pa.species) continue;
                var _px = _pe.x - _pa.x, _py = _pe.y - _pa.y, _pq = _px * _px + _py * _py;
                if (_pq < _pd) { _pd = _pq; _pb = _pe; }
              }
              if (_pa && _pb && _pa.el.isConnected && _pb.el.isConnected) {
                playA = _pa; playB = _pb; playT = PLAY.dur;
              }
            }
          }
        }
      }

      for (var i = 0; i < entities.length; i++) {
        var f = entities[i];

        if (f.anchor) {
          // Landmarks hover in place: gentle drift around home + idle wiggle.
          // The drift IS the motion, so its derivative IS the velocity. Carry it
          // on f.vx/f.vy instead of leaving the spawn value stale: facing, the
          // reflection, the lobby readout and levelup's stir all read vx, and all
          // four were being lied to. (This is the moonwalk: the old code handed
          // tailTransform a bare f.nat, so nat=-1 fish — notefish, witnessfish,
          // founderfish — faced right forever, and every anchor slid backwards
          // for half of each 5.24s hover cycle.)
          f.t += dt;
          f.x = f.homeX + Math.sin(f.t * 0.6) * 1.6;
          f.y = f.homeY + Math.sin(f.t * 0.9 + 1) * 1.2;
          f.vx = Math.cos(f.t * 0.6) * 0.96;          // d/dt of the x drift above
          f.vy = Math.cos(f.t * 0.9 + 1) * 1.08;      // d/dt of the y drift above
          f.speed = 2;
          var aSign = faceSign(f, f.vx, dt) * f.nat;
          f.el.style.left = f.x + '%';
          f.el.style.top = f.y + '%';
          f.span.style.transform = tailTransform(f, dt, aSign);
          f._dtCache = dt; updateHalo(f, _glowOn);
          writeReflection(f, aSign);              // the ghost mirrors WITH its fish
          continue;
        }

        if (f.snail) {
          // Snails don't swim. They graze along the substrate with long feeding
          // pauses, and once in a while commit to the pilgrimage: up the glass,
          // a long hang, then the slow grind back down. Glacial throughout —
          // that's the charm. States: graze -> climb -> hang -> descend -> graze.
          f.t += dt;
          if (!f.mode) { f.mode = 'graze'; f.y = floorPct - 1; f.pauseT = 0; }
          if (f.mode === 'graze') {
            f.y = floorPct - 1;
            if (f.pauseT > 0) { f.pauseT -= dt; }
            else {
              if (turnDue(TURN_HZ.snail, dt)) f.pauseT = 2 + Math.random() * 6;  // stop to graze
              f.x += f.vx * dt;
            }
            if (f.x <= 2) {
              f.x = 2;
              if (Math.random() < 0.5) { f.mode = 'climb'; f.wall = -1; f.climbTo = 16 + Math.random() * 34; }
              else f.vx = Math.abs(f.vx);
            } else if (f.x >= 97) {
              f.x = 97;
              if (Math.random() < 0.5) { f.mode = 'climb'; f.wall = 1; f.climbTo = 16 + Math.random() * 34; }
              else f.vx = -Math.abs(f.vx);
            }
          } else if (f.mode === 'climb') {
            f.x = f.wall < 0 ? 1.2 : 97.8;
            f.y -= f.cruise * 0.8 * dt;
            if (f.y <= f.climbTo) { f.mode = 'hang'; f.pauseT = 8 + Math.random() * 16; }
          } else if (f.mode === 'hang') {
            f.pauseT -= dt;
            if (f.pauseT <= 0) f.mode = 'descend';
          } else {                                   // descend
            f.y += f.cruise * 0.7 * dt;
            if (f.y >= floorPct - 1) {
              f.y = floorPct - 1;
              f.mode = 'graze';
              f.vx = (f.wall < 0 ? 1 : -1) * f.cruise;
              f.pauseT = 1 + Math.random() * 3;
            }
          }
          f.speed = f.cruise;
          f.el.style.left = f.x + '%';
          f.el.style.top = f.y + '%';
          if (f.mode !== 'graze') {
            // On the glass: foot against the pane, head pointing the way it is
            // ACTUALLY travelling. Both fall out of the glyph's own nat.
            // CSS `scaleX(s) rotate(r)` is M = Sx(s)·R(r), so the head vector
            // (nat,0) maps to nat·(s·cos r, sin r) and the glyph's top (0,-1) maps
            // to (s·sin r, -cos r). Solving head -> (0,gDir) and top -> (-gWall,0)
            // gives r = 90·gDir·nat and s = -gWall·gDir·nat. The old fixed pose was
            // correct descending and upside-down climbing — head-down for half the
            // pilgrimage.
            if (f.mode === 'climb') f.glassDir = -1;                     // going up
            else if (f.mode === 'descend') f.glassDir = 1;               // going down
            else if (typeof f.glassDir !== 'number') f.glassDir = -1;    // hang: hold
            var gDir = f.glassDir, gWall = (f.wall < 0 ? -1 : 1);
            f.span.style.transform = 'scaleX(' + (-gWall * gDir * f.nat) +
              ') rotate(' + (90 * gDir * f.nat) + 'deg)';
          } else {
            f.span.style.transform = 'scaleX(' + (faceSign(f, f.vx, dt) * f.nat).toFixed(4) + ')';
          }
          f._dtCache = dt; updateHalo(f, _glowOn);
          continue;
        }

        var ax = 0, ay = 0;
        if (f.eel) {
          stepEel(f, dt, floorPct, darkPhase, CREATURE_FX);
          if (f.huntActive && !REDUCED_MOTION) eelHuntPos = f.huntPos;
          f._dtCache = dt; updateHalo(f, _glowOn);
          continue;
        }
        if (f.crab) {
          // Sideways stop-start scuttle on the floor, no tail beat. Pinned to
          // the real substrate, not the bottom band (which floats on tall tanks).
          f.t += dt;
          if (turnDue(TURN_HZ.crab, dt)) f.vx = -f.vx;        // random direction flips
          var scoot = (Math.sin(f.t * 9) > 0.4) ? 1 : 0.15;    // stepped gait
          f.x += f.vx * scoot * dt;
          ay += seekZone(f, floorPct - 4, floorPct - 1).ay;
          f.vy += ay * dt; f.vy *= 0.9; f.y += f.vy * dt;
          wallTurn(f, 6, dt);
          f.speed = Math.abs(f.vx);
          f.el.style.left = f.x + '%';
          f.el.style.top = f.y + '%';
          // Same eased faceSign authority every swimmer uses. FACE_DEAD 0.35 is
          // far under crab cruise (~6 %/s), so heading commit is immediate; the
          // flip itself becomes a 0.29s roll instead of a one-frame snap.
          f.span.style.transform = 'scaleX(' + (faceSign(f, f.vx, dt) * f.nat).toFixed(4) + ') rotate(' + (Math.sin(f.t * 9) * 4).toFixed(1) + 'deg)';
          f._dtCache = dt; updateHalo(f, _glowOn);
          continue;
        }

        if (f.station) {
          // Station hold <-> relocation dash. Hold: velocity damps out and the
          // tail idles (P5 thrust 0.5 keeps it visibly breathing — that hook is
          // why the hold doesn't read frozen). Move: a short dash at
          // cruise*STATION_DASH, direction wall-aware. Food breaks the hold —
          // feedSteer's bottom-feeder list already names both species (floored
          // flakes only).
          f.t += dt;
          if (!f.mode) { f.mode = 'hold'; f.modeT = holdFor(f.species); }
          f.modeT -= dt;
          var fs2 = feedSteer(f);
          if (f.mode === 'hold') {
            f.vx *= Math.max(0, 1 - 6 * dt);            // settle to stillness
            if (fs2.ax || fs2.ay) {                     // floored food nearby: break the hold
              f.mode = 'move'; f.modeT = moveFor(f.species);
              f.vx += fs2.ax * dt;
            } else if (f.modeT <= 0) {
              f.mode = 'move'; f.modeT = moveFor(f.species);
              // Wall-aware direction pick: never dash INTO a wall.
              var dashDir = f.x < 12 ? 1 : (f.x > 88 ? -1 : (Math.random() < 0.5 ? -1 : 1));
              f.vx = dashDir * f.cruise * STATION_DASH;
            }
          } else {                                      // move
            f.vx += fs2.ax * dt;                        // food steers the dash
            if (f.modeT <= 0) { f.mode = 'hold'; f.modeT = holdFor(f.species); }
          }
          if (fs2.peck) f.wigglePhase = (f.wigglePhase || 0) + 1.1;  // tail-flick on the bite
          f.x += f.vx * dt;
          wallTurn(f, 6, dt);
          // Ease onto the substrate pin at 2.2/s. Reads floorPct LIVE, so a
          // window resize reads as the fish settling to the new floor (~0.5s),
          // never sliding.
          var stY = floorPct - STATION_Y[f.species];
          f.y += (stY - f.y) * Math.min(1, 2.2 * dt);
          f.vy = 0;
          f.speed = Math.abs(f.vx);
          var dirSign = faceSign(f, f.vx, dt) * f.nat;
          f.el.style.left = f.x + '%';
          f.el.style.top = f.y + '%';
          f.span.style.transform = tailTransform(f, dt, dirSign, f.mode === 'hold' ? 0.5 : 1.25);
          f._dtCache = dt; updateHalo(f, _glowOn);
          writeReflection(f, dirSign);
          continue;
        }

        var w = wander(f, dt); ax += w.ax * 0.5; ay += w.ay * 0.5;

        if (f.school) {
          // f.mates is the roster-time species bucket (may contain f itself —
          // boids() skips o === fish, so the neighbor set is bit-identical to
          // the old per-frame rebuild).
          var b = boids(f, f.mates, f.shoal);
          ax += b.ax * 8; ay += b.ay * 8;
        }

        // Occupancy-gradient spread (schoolers only — loners have no cohesion
        // to fight): push DOWN the measured occupancy gradient. Replicate-edge
        // (no-flux) sampling at the walls — zero-padding would make the walls
        // read as empty and therefore attractive.
        if (SPREAD_GAIN && f.school) {
          var occ = spreadEMA[f.species];
          var iC = (f.x * SPREAD_COLS / 100) | 0; if (iC < 0) iC = 0; else if (iC >= SPREAD_COLS) iC = SPREAD_COLS - 1;
          var oL = occ[iC > 0 ? iC - 1 : iC], oR = occ[iC < SPREAD_COLS - 1 ? iC + 1 : iC];
          var gS = (oR - oL) * 0.5 * SPREAD_GAIN;
          if (gS > SPREAD_MAX) gS = SPREAD_MAX; else if (gS < -SPREAD_MAX) gS = -SPREAD_MAX;
          ax -= gS;
        }

        var z = seekZone(f, f.lo, f.hi); ax += z.ax; ay += z.ay;
        var st = startle(f, mouse, 16, 160); ax += st.ax; ay += st.ay;
        // (curiosity) the volunteer feels a constant pull toward the still
        // cursor; it composes with startle above into the ~10% standoff ring.
        if (f === curioF && mouse) {
          var cqx = mouse.x - f.x, cqy = mouse.y - f.y;
          var cqd = Math.sqrt(cqx * cqx + cqy * cqy) || 1e-4;
          ax += cqx / cqd * CURIO.gain;
          ay += cqy / cqd * CURIO.gain;
        }
        // A hunting eel is a second predator: nearby fish flee, the school
        // shatters, then re-coheres via boids once the eel retreats.
        var threat = null;
        if (eelHuntPos) {
          var es = startle(f, eelHuntPos, 22, 240 * CREATURE_FX);
          ax += es.ax; ay += es.ay;
          threat = eelHuntPos;                    // a hunting eel always qualifies
        }
        // A STILL cursor is not a C-start trigger — it would deadlock with
        // curiosity (approach, flinch, approach, forever). The cursor
        // qualifies only while recently moving (Mauthner cells fire on
        // abrupt/looming stimuli, not parked ones). Sequence this buys:
        // still -> approach; sudden move -> the prologue released curioF this
        // same frame -> the ex-volunteer C-starts away.
        if (!threat && mouse && mouseStillT < CURIO.spook) threat = mouse;

        // C-start escape: one-shot kick + C-bend, refractory-gated.
        if (f.escT > 0) f.escT -= dt;
        if (f.escRefr > 0) f.escRefr -= dt;
        if (ESCAPE_TRIG && threat && f.escT <= 0 && f.escRefr <= 0) {
          var tdx = f.x - threat.x, tdy = f.y - threat.y, td2 = tdx * tdx + tdy * tdy;
          var trig = ESCAPE_TRIG * f.bold;
          if (td2 < trig * trig) {
            var td = Math.sqrt(td2) || 1e-4;
            var kick = f.maxSpeed * ESCAPE_KICK;
            f.vx += tdx / td * kick;
            f.vy += tdy / td * kick;
            f.escT = ESCAPE_DUR;
            f.escRefr = ESCAPE_REFR / f.bold;
            f.escSign = tdy >= 0 ? 1 : -1;        // bend toward the get-away side
            f.wigglePhase = 0;                    // the C-bend starts from zero
          }
        }
        // (tag) the two players: the chaser presses (harder the further away,
        // 6.6-13.2 %/s^2 — between wander x0.5 and boids x8), the leader
        // flees softly (harder when cornered). Everyone else just sees two
        // schoolmates dash — a third briefly recruited by alignment is play.
        if (playT > 0 && (f === playA || f === playB)) {
          var mate = (f === playA) ? playB : playA;
          var gdx = mate.x - f.x, gdy = mate.y - f.y;
          var gd = Math.sqrt(gdx * gdx + gdy * gdy) || 1e-4;
          if (f === playB) {
            var chaseK = PLAY.chase * (6 + 6 * Math.min(1, gd / 30));
            ax += gdx / gd * chaseK; ay += gdy / gd * chaseK;
          } else {
            var fleeK = PLAY.flee * (6 + 6 * Math.max(0, 1 - gd / 12));
            ax -= gdx / gd * fleeK; ay -= gdy / gd * fleeK;
          }
        }
        // Feeding: seek the nearest flake (folds into the same accumulator).
        var fs = feedSteer(f); ax += fs.ax; ay += fs.ay;
        // The water carries the fish: a soft drag toward the local flow
        // velocity (advection as a force, never a teleport — it composes with
        // boids/zone/burst-coast instead of fighting them). Weaker on y so
        // fish keep their habitat band against vertical eddy components.
        var fl = flowAt(f.x, f.y);
        ax += (fl.vx - f.vx) * 0.8;
        ay += (fl.vy - f.vy) * 0.4;

        var bc = burstCoast(f, dt);
        if (fs.peck) bc *= 1.8;                 // quick lunge on the bite
        if (playT > 0 && (f === playA || f === playB)) bc = Math.min(1.6, bc * PLAY.dash);  // (tag) playful dash
        // During an escape the cap is FLAT maxSpeed*ESCAPE_CAP (no bc term —
        // a coast-phase bc of 0.4 would swallow the kick same-frame).
        integrate(f, { ax: ax, ay: ay }, dt, f.escT > 0 ? f.maxSpeed * ESCAPE_CAP : f.maxSpeed * bc, 1.2, f.cruise * bc);
        if (fs.peck) f.wigglePhase = (f.wigglePhase || 0) + 1.1;  // tail-flick
        wallTurn(f, 8, dt);
        if (f.y < 1) { f.y = 1; if (f.vy < 0) f.vy = 0; }
        if (f.y > 96) { f.y = 96; if (f.vy > 0) f.vy = 0; }

        f.speed = Math.sqrt(f.vx * f.vx + f.vy * f.vy);
        // Same dead-banded, eased heading every other creature uses — a fish
        // hovering with vx jittering across zero must not strobe its glyph.
        var dirSign = faceSign(f, f.vx, dt) * f.nat;
        f.el.style.left = f.x + '%';
        f.el.style.top = f.y + '%';
        f.span.style.transform = tailTransform(f, dt, dirSign, bc);
        f._dtCache = dt; updateHalo(f, _glowOn);
        writeReflection(f, dirSign);
        if (f.lure) renderLure(f, dt, darkPhase, CREATURE_FX, false);
      }

      // --- The water's own passengers (wide tanks only; arrays stay empty
      // elsewhere). Everything samples the SAME field the fish feel. ---

      // Bubbles: buoyant rise bent by the flow, a small wobble, expansion as
      // pressure falls off near the surface, recycle at the waterline.
      for (var bi = 0; bi < liveBubbles.length; bi++) {
        var b = liveBubbles[bi];
        var bf = flowAt(b.x, b.y);
        b.x += (bf.vx * 0.6 + Math.sin(flowT * 3 + b.wob) * 0.4) * dt;
        b.y -= b.vy * (b.y < 14 ? 1.5 : 1) * dt;
        if (b.x < 1) b.x = 1; else if (b.x > 98) b.x = 98;
        if (b.y <= 8) { b.y = 99; b.x = bubbleSpawnX(); }
        b.el.style.left = b.x + '%';
        b.el.style.top = b.y + '%';
        b.el.style.transform = b.y < 14 ? 'scale(' + (1 + (14 - b.y) / 14 * 0.6).toFixed(2) + ')' : '';
      }

      // The drifting medium (lever a/d), streaklines (b), and ripple rings (c)
      // are all advanced by stepWater() below — the single per-frame water driver.

      // Bioluminescent night: light-trails on driftfish/notefish + witching
      // plankton twinkle. Cheap, capped, night-only (no-ops off glow phases).
      updateTrails(dt, _glowOn);
      twinklePlankton(dt, _witch);

      // Surface: a traveling wave whose amplitude and speed are the current,
      // running the same direction as the laminar drift — the surface agrees
      // with the depths. The floored strength (lever f) keeps it always alive.
      // Heat adds a fine fast tremble (water about to boil).
      if (surfSpans.length) {
        var amp = 1 + effStrength() * 5;
        var spd = flowT * (0.8 + effStrength() * 1.2);
        for (var si = 0; si < surfSpans.length; si++) {
          var fx = si / surfSpans.length * 100;
          var dy = Math.sin(spd - fx * 0.18) * amp
                 + (heatLevel > 0.3 ? Math.sin(flowT * 9 + si * 1.7) * heatLevel * 2 : 0);
          surfSpans[si].style.transform = 'translateY(' + dy.toFixed(2) + 'px)';
        }
      }

      stepWater(dt);       // medium + streaklines + ripples + live god rays
      stepFlakes(dt);      // feeding flakes: sink + advect + fade
    }

    function loop(t) {
      if (!lastT) lastT = t;
      var dt = (t - lastT) / 1000;
      lastT = t;
      if (dt > 0.05) dt = 0.05;                 // clamp after tab refocus
      if (dt > 0) {
        if (PERF_PROBE) { var _t0 = performance.now(); step(dt); stepMsEma += (performance.now() - _t0 - stepMsEma) * 0.05; }
        else step(dt);
      }
      rafId = window.requestAnimationFrame(loop);
    }

    function startLoop() {
      if (rafId != null || REDUCED_MOTION) return;
      lastT = 0;
      rafId = window.requestAnimationFrame(loop);
    }
    function stopLoop() {
      if (rafId != null) { window.cancelAnimationFrame(rafId); rafId = null; }
    }

    // Place fish statically (reduced-motion / no rAF) — calm and readable.
    function placeStatic() {
      for (var i = 0; i < entities.length; i++) {
        var f = entities[i];
        f.el.style.left = f.x + '%';
        f.el.style.top = f.y + '%';
        f.span.style.transform = 'scaleX(' + (faceSign(f, f.vx, 0) * f.nat) + ')';
        if (f.lure) renderLure(f, 0, darkPhase, CREATURE_FX, true);  // static bait light
      }
    }

    function renderFish(fish) {
      var sig = fish.map(function (f) { return (f.species || '') + ':' + (f.glyph || '') + ':' + (f.mood || '') + ':' + (f.name || ''); }).sort().join('|');
      if (sig !== rosterSig || entities.length !== fish.length) {
        buildEntities(fish);
        rosterSig = sig;
        if (REDUCED_MOTION) placeStatic(); else startLoop();
      }
      // Same roster: keep entities swimming, no reset.
    }

    function renderLegend(fish) {
      var legend = $('legend');
      if (!legend) return;
      legend.textContent = '';
      var seen = {};
      for (var i = 0; i < fish.length; i++) {
        var f = fish[i];
        if (seen[f.species]) continue;
        seen[f.species] = 1;
        var guide = SPECIES[f.species] || [f.species, ''];
        var item = document.createElement('span');
        item.className = 'legend-item';
        item.title = guide[1];
        var g = document.createElement('span');
        g.className = 'legend-glyph';
        g.textContent = f.glyph;
        var name = document.createElement('span');
        name.className = 'legend-name';
        name.textContent = guide[0];
        item.appendChild(g);
        item.appendChild(name);
        legend.appendChild(item);
      }
    }

    function renderSilt(density) {
      var d = Math.max(0, Math.min(1, density || 0));
      // Stirred sediment clouds the lower water: the murk-line of the depth
      // column rises with silt, and the suspended motes wake up (wide tanks).
      scopeEl.style.setProperty('--murk-start', (62 - d * 26).toFixed(0) + '%');
      siltBoost = d * 0.14;   // (a) silt raises medium brightness; stepWater applies per-tracer
      var el = $('silt');
      if (!el) return;
      if (cfg.minimal) {
        var chars = d > 0.5 ? ['▒', '▓'] : ['░', '▒'];
        var s = '';
        for (var i = 0; i < 170; i++) s += chars[(i * 7 + Math.round(d * 9)) % chars.length];
        el.textContent = s;
        return;
      }
      // Standalone: the sand BED is painted by CSS (.tank::before); this strip
      // carries only occasional GRAIN. A solid 170-cell hatch is what read as
      // noise. 37 and 23 are coprime, so (g*37)%23 walks all 23 residues and the
      // specks land irregularly rather than on a visible period. Stirred water
      // raises both the count and the weight of the grain.
      var hits = d > 0.5 ? 5 : 2;                  // of every 23 cells
      var grain = d > 0.5 ? '░' : '·';             // both exactly one cell
      var out = '';
      for (var g = 0; g < 170; g++) out += (((g * 37) % 23) < hits) ? grain : ' ';
      el.textContent = out;
    }

    function renderMemorial(fossils) {
      var el = $('memorial');
      if (!el) return;
      var glyphs = (fossils || []).filter(Boolean);
      el.textContent = glyphs.length ? 'memorial reef  ' + glyphs.slice(-40).join(' ') : '';
    }

    function renderNamed(fish) {
      var el = $('named');
      if (!el) return;
      var names = [];
      var seen = {};
      for (var i = 0; i < fish.length; i++) {
        var n = fish[i].name;
        if (n && !seen[n]) { seen[n] = 1; names.push(n); }
      }
      el.textContent = '';
      if (!names.length) return;
      el.appendChild(document.createTextNode('swimming today  '));
      names.forEach(function (n, i) {
        if (i) el.appendChild(document.createTextNode(' · '));
        var s = document.createElement('span');
        s.className = 'named-proj';
        s.textContent = n;
        el.appendChild(s);
      });
    }

    function ago(ms) {
      var m = Math.round(ms / 60000);
      if (m < 1) return 'just now';
      if (m < 60) return m + 'm ago';
      var h = Math.round(m / 60);
      if (h < 24) return h + 'h ago';
      return Math.round(h / 24) + 'd ago';
    }

    function setStatus(parts) {
      var status = $('status');
      if (!status) return;
      status.textContent = '';
      for (var i = 0; i < parts.length; i++) {
        var part = parts[i];
        if (typeof part === 'string') { status.appendChild(document.createTextNode(part)); }
        else {
          var span = document.createElement('span');
          span.className = part.cls;
          span.textContent = part.text;
          status.appendChild(span);
        }
      }
    }

    function renderFreshness(tickAt, count) {
      var n = Number(count) || 0;
      if (!tickAt) { setStatus([n + ' fish']); return; }
      var age = Date.now() - new Date(tickAt).getTime();
      if (age > SLEEP_AFTER_MS) {
        setStatus([{ cls: 'sleeping', text: n + ' fish · sleeping — last seen ' + ago(age) + ' 🌙' }]);
      } else {
        setStatus([n + ' fish · last tick ' + ago(age) + ' · ', { cls: 'live', text: 'live-ish' }]);
      }
    }

    function renderEmpty() {
      applyPhase('night');
      applyLight(null);
      applyTemp(null);
      applyCurrent(0);
      currentStrength = 0;            // an empty tank settles to glassy idle
      clearEntities();
      rosterSig = null;
      var mood = $('mood'); if (mood) mood.textContent = 'warming up';
      var status = $('status'); if (status) status.textContent = 'the tank is warming up…';
      renderMemorial([]);
      renderLegend([]);
      renderNamed([]);
      renderSilt(0);
    }

    function applySnapshot(snap) {
      if (!snap || snap.empty) { renderEmpty(); return; }
      var w = snap.weather || {};
      var fish = Array.isArray(snap.fish) ? snap.fish : [];
      applyPhase(w.phase || 'night');
      applyLight(w.light_level);      // after the phase: the lamp dims within it
      applyTemp(w.temperature_c);
      applyCurrent(w.current_strength);
      currentStrength = Math.max(0, Math.min(1, w.current_strength || 0));
      renderSurface(PHASES[w.phase] || PHASES.night);  // chop tier sees fresh current
      var mood = $('mood'); if (mood) mood.textContent = w.mood || '—';
      renderFish(fish);
      renderLegend(fish);
      renderSilt(w.silt_density);
      renderMemorial(snap.fossil_layer);
      renderNamed(fish);
      renderFreshness(snap.tick_at, snap.fish_count != null ? snap.fish_count : fish.length);
      maybeShootingStar();
    }

    // Timer half of the poll loop — separate from startLoop/stopLoop (the rAF
    // half) so pause()/resume() and onVisibility() can stop BOTH: an
    // off-screen or backgrounded tank should stop fetching /api/tank, not
    // just stop animating.
    function startPoll() {
      if (timer) return; // already running
      if (Date.now() - lastPollAt >= POLL_MS) poll(); // snapshot is stale — catch up now
      timer = setInterval(poll, POLL_MS);
    }
    function stopPoll() {
      if (timer) { clearInterval(timer); timer = null; }
    }

    async function poll() {
      lastPollAt = Date.now();
      try {
        var res = await fetch('/tank.json', { cache: 'no-store' });   // tank serve: this server's snapshot, not the site's proxy
        var snap = await res.json();
        applySnapshot(snap);
      } catch (err) {
        var status = $('status');
        if (status) status.textContent = 'tank unreachable — retrying…';
      }
    }

    function maybeShootingStar() {
      if (Math.random() > 0.08) return;
      var koi = document.createElement('div');
      koi.className = 'koi';
      koi.textContent = '✦ ><(((°>';
      koi.addEventListener('animationend', function () { koi.remove(); });
      tank.appendChild(koi);
    }

    function bubbleSpawnX() {
      // Bubbles mostly outgas from the substrate near the hardscape masses.
      // A mass may declare a tighter `vent`: the skull gets a narrow one, so its
      // share of the outgassing rises as a thin column out of the cranium instead
      // of a broad fizz around the base. No new pool, no new timer, no second
      // clock — the same liveBubbles the wide tank already recycles at the
      // waterline, just respawned in a narrower band.
      if (masses.length && Math.random() < 0.4) {
        var m = masses[Math.floor(Math.random() * masses.length)];
        var spread = m.vent || 6;
        return Math.max(2, Math.min(97, m.x + (Math.random() * 2 - 1) * spread));
      }
      return 4 + Math.random() * 92;
    }

    function seedBubbles() {
      var layer = $('bubbles');
      if (!layer) return;
      var wide = tank.getBoundingClientRect().width > 900;
      var nBub = wide ? 17 : 11;
      for (var i = 0; i < nBub; i++) {
        var b = document.createElement('span');
        b.className = 'bubble';
        b.style.setProperty('--size', (2 + (i % 4)) + 'px');
        if (wideFX) {
          // Live bubbles: the rAF loop owns them — advected by the same flow
          // field as the fish, wobbling as they rise, swelling near the
          // surface as pressure falls off. CSS animation disabled via .live.
          b.className = 'bubble live';
          b.style.opacity = '0.18';
          liveBubbles.push({
            el: b, x: bubbleSpawnX(), y: 20 + Math.random() * 80,
            vy: 6 + Math.random() * 6, wob: Math.random() * 6.28,
          });
        } else {
          b.style.left = (4 + (i * 53) % 92) + '%';
          b.style.setProperty('--rise', (8 + (i * 13) % 9) + 's');
          b.style.animationDelay = (-(i * 1.7)) + 's';
        }
        layer.appendChild(b);
      }
    }

    // The always-on drifting medium replaces the old silt-gated motes — see
    // seedMedium()/stepWater() above. The medium is the shared mote pool other
    // lanes reuse (api.medium()); it is alive at idle (lever a/f) and brightens
    // with silt_density (siltBoost) instead of vanishing when the water is clean.

    // A layered Nature-Aquarium scape. On a wide tank: TWO masses — a keystone
    // mass on one golden third, a quieter echo on the other — with an open
    // swimming channel between, talls spread across the whole back wall, and
    // carpet scattered wide so the scape reads as one reef, not potted plants.
    // Narrow tanks (the lobby embed) keep the original single-focal layout.
    // Depth is faked — background bigger/dimmer/slower, foreground crisper.
    // The lobby embed (cfg.minimal) keeps the original sprinkle verbatim — it is
    // tuned for that crop and must not change. The standalone page gets the
    // composed aquascape (seedDecorComposed, below seedDecorLegacy).
    function seedDecor() {
      if (cfg.minimal) { seedDecorLegacy(); return; }
      seedDecorComposed();
    }

    // TWIN NOTE: seedDecorLegacy's addStruct/addPlant/addEpi below have a twin
    // set inside seedDecorComposed. Any change to how a decor node is BUILT
    // (classes, custom properties, animation delays, cycle registration) must be
    // made in BOTH or the standalone page and the lobby will silently diverge.
    function seedDecorLegacy() {
      var layer = $('decor');
      if (!layer) return;
      var rect = tank.getBoundingClientRect();
      var wide = rect.width > 900;
      var focal = (Math.random() < 0.5 ? 34 : 66);          // golden-ratio third
      var echo = 100 - focal;
      function near(center, spread) {                        // x% biased to a mass
        return Math.max(2, Math.min(97, center + (Math.random() * 2 - 1) * spread));
      }
      function across() {                                    // full-width scatter
        return 4 + Math.random() * 92;
      }
      function addStruct(art, x, size, op, depth) {
        var el = document.createElement('div');
        el.className = 'structure';
        el.textContent = art;
        el.style.left = x + '%';
        el.style.fontSize = (size * TANK_SCALE * (0.78 + Math.random() * 0.5)).toFixed(3) + 'rem';
        el.style.fontWeight = '700';                         // bold strokes
        var ds = depthStyle(depth, op);
        el.style.setProperty('--d-opacity', ds.op.toFixed(2));
        if (ds.blur) el.style.filter = 'blur(' + ds.blur.toFixed(2) + 'px)';
        el.dataset.depth = depth.toFixed(2);                 // handoff to WATER parallax
        layer.appendChild(el);
      }
      function addPlant(art, x, size, op, sway, depth) {
        var el = document.createElement('div');
        el.className = 'plant';
        el.textContent = art;
        el.style.left = x + '%';
        el.style.fontSize = (size * TANK_SCALE * (0.78 + Math.random() * 0.5)).toFixed(3) + 'rem';
        el.style.fontWeight = '700';                         // bold strokes
        var dp = depthStyle(depth, op);
        el.style.opacity = dp.op.toFixed(2);
        if (dp.blur) el.style.filter = 'blur(' + dp.blur.toFixed(2) + 'px)';
        el.dataset.depth = depth.toFixed(2);                 // handoff to WATER parallax
        el.style.setProperty('--sway-dur', sway.toFixed(1) + 's');
        el.style.animationDelay = (-(Math.random() * 4)).toFixed(1) + 's';
        layer.appendChild(el);
      }
      // Top-rooted epiphyte/curtain: hangs high on the column (CSS .plant.epi
      // anchors it near the surface) to fill the dead vertical space without
      // crowding the floor. Reuses the .plant sway keyframe (calm under reduced
      // motion for free). Fixed deep-background depth so it recedes.
      function addEpi(art, x, size, op) {
        var el = document.createElement('div');
        el.className = 'plant epi';
        el.textContent = art;
        el.style.left = x + '%';
        el.style.fontSize = (size * TANK_SCALE * (0.78 + Math.random() * 0.5)).toFixed(3) + 'rem';
        el.style.fontWeight = '700';
        var ds = depthStyle(0.18, op);            // deep background: dim + soft
        el.style.opacity = ds.op.toFixed(2);
        if (ds.blur) el.style.filter = 'blur(' + ds.blur.toFixed(2) + 'px)';
        el.dataset.depth = '0.18';
        el.style.setProperty('--sway-dur', (10 + Math.random() * 3).toFixed(1) + 's');
        el.style.animationDelay = (-(Math.random() * 5)).toFixed(1) + 's';
        layer.appendChild(el);
      }

      // Density knob folds in: scale every tier's count, floor at the base shape
      // so the panoramic floor is populated but never cluttered; the open sand
      // channel between masses is a hard keep-out so fish keep a cruising lane.
      function dcount(base) { var n = Math.round(base * DECOR_DENSITY); return n < 0 ? 0 : n; }

      masses.length = 0;
      var chanCenter = 50, chanHalf = wide ? 13 : 9;   // protected swimming lane

      // Keystone hardscape pair anchors the focal third (foreground, crisp).
      var kx = near(focal, 8);
      addStruct(pickRandom(STRUCTURES), kx, wide ? 2.0 : 1.7, 0.52, 0.9);
      masses.push({ x: kx, y: floorPct - 8, r: 20 });
      addStruct(pickRandom(STRUCTURES), near(focal, 20), 1.2, 0.42, 0.7);
      if (wide) {
        var ex = near(echo, 10);
        addStruct(pickRandom(STRUCTURES), ex, 1.35, 0.45, 0.8);
        masses.push({ x: ex, y: floorPct - 7, r: 15 });
        chanCenter = (kx + ex) / 2;                     // channel runs between the masses
        if (Math.random() < 0.30) {                     // rare lone channel-edge accent
          var ax = chanCenter + (Math.random() < 0.5 ? -1 : 1) * (chanHalf + 6 + Math.random() * 8);
          addStruct(pickRandom(STRUCTURES), ax, 0.9, 0.30, 0.55);
        }
      } else {
        addStruct(pickRandom(STRUCTURES), near(focal, 32), 1.05, 0.34, 0.6);
        if (Math.random() < 0.6) {
          addStruct(pickRandom(STRUCTURES), 50 + (focal < 50 ? 1 : -1) * (28 + Math.random() * 18), 1.0, 0.30, 0.5);
        }
      }

      // BACKGROUND tier — tall stems + epiphytes, dim/soft/slow, clustered INTO
      // the masses so the back wall keeps open water (and the channel) between.
      var nb = dcount(wide ? 6 : 5);
      for (var i = 0; i < nb; i++) {
        var bx = wide ? near(i % 2 ? focal : echo, 12) : near(52, 50);
        if (inChannel(bx, chanCenter, chanHalf)) continue;       // never fill the lane
        addPlant(pickRandom(PLANTS.tall), bx, 1.5 + Math.random() * 0.3, 0.62, 8.5 + Math.random() * 3, 0.12);
      }
      // Use the dead vertical column: 0-1 epiphyte/curtain rooted HIGH on a mass,
      // hanging into upper-mid water (CSS .plant.epi anchors near the mass top).
      var ne = dcount(wide ? 1 : 0);
      for (var e = 0; e < ne; e++) {
        var epx = near(e % 2 ? focal : echo, 6);
        if (inChannel(epx, chanCenter, chanHalf)) epx = chanCenter + chanHalf + 5;
        addEpi(pickRandom(PLANTS.epi), epx, 1.25 + Math.random() * 0.25, 0.18);
      }
      // MIDGROUND clumps — baseline size, split between masses, mid depth.
      var nm = dcount(wide ? 4 : 3);
      for (var j = 0; j < nm; j++) {
        var mx = near(j % 2 ? focal : (wide ? echo : focal), wide ? 14 : 36);
        if (inChannel(mx, chanCenter, chanHalf)) continue;
        addPlant(pickRandom(PLANTS.mid), mx, 1.1 + Math.random() * 0.2, 0.85, 6.5 + Math.random() * 2, 0.55);
      }
      // FOREGROUND carpet — small but crisp/bright, skirting the mass feet.
      var nc = dcount(wide ? 4 : 3);
      for (var k = 0; k < nc; k++) {
        var cx = wide ? near(k % 2 ? focal : echo, 24) : near(focal, 30);
        if (inChannel(cx, chanCenter, chanHalf)) continue;
        addPlant(pickRandom(PLANTS.carpet), cx, 0.92 + Math.random() * 0.18, 0.98, 5 + Math.random() * 1.5, 0.92);
      }
    }

    // ------------------------------------------------------------------
    // THE COMPOSED AQUASCAPE — standalone page only.
    //
    // What was wrong with the old bottom (all confirmed in seedDecorLegacy):
    //   * ~21 bottom-rooted nodes across the full width => a continuous ridge.
    //   * EVERY node pinned to one flat baseline (CSS bottom:20px), so the far
    //     and near tiers shared a single line: zero vertical depth cue.
    //   * depthStyle()'s 0.46..1.00 ramp left the three structures at 0.35/0.39/
    //     0.49 effective opacity — indistinguishable visual weight.
    //   * No collision test at all => pieces stacked into lumps.
    //   * inChannel() tested only a piece's CENTRE, so wide pieces leaked into
    //     the "protected" lane and the negative space never actually existed.
    //
    // What this builds instead:
    //   * TWO masses on the golden thirds with a wide, genuinely protected sand
    //     lane between them, and the outer sixths left as open water.
    //   * Half the piece count, but a much larger keystone — always the SKULL.
    //   * Three depth BANDS. Collision is enforced INSIDE a band (no two pieces
    //     of the same tier lump together) and deliberately allowed ACROSS bands
    //     — that is what occlusion, i.e. depth, looks like.
    //   * Each band roots on its OWN substrate line (rootAt): the far tier
    //     stands high on the receding sand, the foreground low with open sand in
    //     front of it. That vertical split is what actually sells depth.
    //   * scapeDepth() opacity/blur is quadratic and far wider; the far tier is
    //     tinted cool (aerial perspective) so it stops competing.
    // Seed-time only — nothing here runs per frame.
    //
    // TWIN NOTE: the addStruct/addPlant/addEpi below are twins of the ones inside
    // seedDecorLegacy. Any change to how a decor node is BUILT must be made in
    // BOTH, or the standalone page and the lobby silently diverge.
    // ------------------------------------------------------------------
    function seedDecorComposed() {
      var rect = tank.getBoundingClientRect();
      var W = rect.width || 900;
      var H = rect.height || 420;
      var wide = W > 900;

      // The sand bed. JS owns the height so the CSS band (.tank::before) and
      // every root line below come from the SAME number and can never disagree,
      // including after a resize (both are frozen together at seed time).
      var bedH = Math.round(Math.max(34, Math.min(58, H * 0.105)));
      tank.style.setProperty('--bed-h', bedH + 'px');

      var layer = $('decor');
      if (!layer) return;
      layer.textContent = '';

      // Where a piece of a given depth stands. The substrate recedes UPWARD:
      // far pieces root near the top sand line, near pieces root low in the bed
      // with open sand in front. Nothing roots below 0.48*bedH, so no piece is
      // ever clipped by the tank's bottom edge or buried in the grain strip.
      function rootAt(depth) {
        return Math.round(bedH * 0.48 + (1 - depth) * bedH * 0.44);
      }
      // Half-width of a piece in tank-percent, from its widest row. The
      // JetBrains Mono advance is ~0.6em, so this is an honest footprint.
      function halfPct(art, rem) {
        var lines = art.split('\n'), cols = 0, i;
        for (i = 0; i < lines.length; i++) {
          if (lines[i].length > cols) cols = lines[i].length;
        }
        return (cols * 0.6 * rem * 16) / W * 50;
      }
      // Register a piece for glyph cycling if we authored frames for its art.
      // wideFX is the single gate: narrow tanks and prefers-reduced-motion both
      // fold into it (the lobby never reaches this function at all), so none of
      // them ever cycles. Hard-capped so the scape can never become a flipbook.
      function registerCycle(el, art) {
        if (!wideFX || cyclers.length >= CYCLE_CAP) return;
        var frames = GLYPH_CYCLES[art];
        if (!frames) return;
        cyclers.push({
          el: el, frames: frames, i: 0,
          t: CYCLE_MIN + Math.random() * (CYCLE_MAX - CYCLE_MIN)
        });
      }

      var focal = (Math.random() < 0.5 ? 27 : 73);
      var echo = 100 - focal;
      // Wide: the lane runs down the middle between the two masses. Narrow: one
      // mass only, and the ENTIRE opposite half is the open water.
      var chanCenter = wide ? 50 : (focal < 50 ? 74 : 26);
      var chanHalf = wide ? 15 : 20;
      var edge = wide ? 8 : 12;           // keep-in margin: nothing side-clipped
      var placedBack = [], placedMid = [], placedFore = [];

      function bandOf(depth) {
        return depth < 0.4 ? placedBack : (depth < 0.9 ? placedMid : placedFore);
      }
      // Which placed-lists a new piece must clear.
      //
      // The far band is deliberately EXEMPT from the others: the backdrop slab
      // is supposed to sit behind the keystone, and that reads as depth, not as
      // collision. But mid and fore must check EACH OTHER. They did not, and
      // that was the bug: the skull lands mid-band (depth 0.85) while carpet
      // plants land fore-band (depth >= 0.9), so nothing ever tested a leaf
      // against a stone face and the weeds drew straight through the skull's
      // jaw. Measured on the 2026-07-26 scape: ship anchor x plant 67% overlap,
      // plant x plant 38%, plants through the skull 12%.
      function bandsFor(depth) {
        return depth < 0.4 ? [placedBack] : [placedMid, placedFore];
      }
      // Stone wants more elbow room than a leaf: a frond brushing another frond
      // reads as planting, a frond crossing a skull reads as a glitch.
      function clearance(aStruct, bStruct) {
        return (aStruct || bStruct) ? 1.12 : 0.9;
      }
      function freeAt(x, half, depth, isStruct) {
        if (x - half < edge || x + half > 100 - edge) return false;
        var lean = depth < 0.4 ? 0.35 : 1.0;   // the far wall may lean into the lane
        if (Math.abs(x - chanCenter) < chanHalf + half * lean) return false;
        var lists = bandsFor(depth), li, arr, p;
        for (li = 0; li < lists.length; li++) {
          arr = lists[li];
          for (p = 0; p < arr.length; p++) {
            if (Math.abs(x - arr[p].x) < (half + arr[p].h) * clearance(isStruct, arr[p].s)) return false;
          }
        }
        return true;
      }
      // Jittered slot around a mass. Returns null rather than stacking two pieces
      // of the same tier — a dropped piece is cheaper than a lump.
      function slot(center, spread, half, depth, isStruct) {
        for (var t = 0; t < 14; t++) {
          var x = center + (Math.random() * 2 - 1) * spread;
          if (freeAt(x, half, depth, isStruct)) {
            bandOf(depth).push({ x: x, h: half, s: isStruct ? 1 : 0 });
            return x;
          }
        }
        return null;
      }

      function addStruct(art, x, rem, op, depth, tint, cls) {
        var el = document.createElement('div');
        el.className = cls ? 'structure ' + cls : 'structure';
        el.textContent = art;
        el.style.left = x.toFixed(2) + '%';
        el.style.bottom = rootAt(depth) + 'px';
        el.style.fontSize = rem.toFixed(3) + 'rem';
        el.style.fontWeight = '700';
        if (tint) el.style.color = tint;
        var ds = scapeDepth(depth, op);
        el.style.setProperty('--d-opacity', ds.op.toFixed(3));
        if (ds.blur) el.style.filter = 'blur(' + ds.blur.toFixed(2) + 'px)';
        el.dataset.depth = depth.toFixed(2);
        // Depth must drive PAINT ORDER, not just opacity/blur. Without this a
        // far-tier plant appended after the keystone paints ON TOP of it, so a
        // frond meant to stand behind the skull grew straight through its jaw
        // (measured 41% overlap). Overlap itself is fine — an aquascape wants
        // plants behind stone — it just has to read as behind.
        el.style.zIndex = String(Math.round(depth * 100) + 1);
        // Rock does NOT sway — only the light moves over it. Matte stone takes
        // ~70% of the caustic a leaf does. ONE animation here (the glint), so
        // ONE delay, keyed to x like every other piece in the scape.
        el.style.setProperty('--glint-k', ((0.45 + 0.55 * depth) * 0.7).toFixed(2));
        el.style.animationDelay = glintDelay(x);
        layer.appendChild(el);
      }
      // NOTE: a .plant is still exactly one <div class="plant"> with textContent,
      // --sway-dur and a negative animation-delay — the sway lane's CSS keyframe
      // on .plant keeps working unchanged.
      function addPlant(art, x, rem, op, sway, depth, tint) {
        var el = document.createElement('div');
        el.className = 'plant';
        el.textContent = art;
        el.style.left = x.toFixed(2) + '%';
        el.style.bottom = rootAt(depth) + 'px';
        el.style.fontSize = rem.toFixed(3) + 'rem';
        el.style.fontWeight = '700';
        if (tint) el.style.color = tint;
        var dp = scapeDepth(depth, op);
        el.style.opacity = dp.op.toFixed(3);
        if (dp.blur) el.style.filter = 'blur(' + dp.blur.toFixed(2) + 'px)';
        el.dataset.depth = depth.toFixed(2);
        // Depth must drive PAINT ORDER, not just opacity/blur. Without this a
        // far-tier plant appended after the keystone paints ON TOP of it, so a
        // frond meant to stand behind the skull grew straight through its jaw
        // (measured 41% overlap). Overlap itself is fine — an aquascape wants
        // plants behind stone — it just has to read as behind.
        el.style.zIndex = String(Math.round(depth * 100) + 1);
        // Tier-scaled aliveness. --sway-k: how much THIS piece bends — tall
        // background stems ride the current, the foreground carpet barely stirs.
        // --glint-k: how brightly it catches the travelling caustic band.
        // --d-opacity: the base the glint keyframe modulates AROUND. NOT
        // optional — a CSS animation outranks the inline opacity above, so
        // without this every plant snaps to the 0.3 fallback and the whole depth
        // ramp dies silently.
        el.style.setProperty('--sway-k', (1.45 - depth).toFixed(2));
        el.style.setProperty('--glint-k', (0.45 + 0.55 * depth).toFixed(2));
        el.style.setProperty('--d-opacity', dp.op.toFixed(3));
        el.style.setProperty('--sway-dur', sway.toFixed(1) + 's');
        // TWO animations (sway, glyph-glint) => TWO delays. Sway keeps its random
        // phase; the glint phase is keyed to x so the light band sweeps the scape.
        el.style.animationDelay = (-(Math.random() * 4)).toFixed(1) + 's, ' + glintDelay(x);
        registerCycle(el, art);
        layer.appendChild(el);
      }
      function addEpi(art, x, rem, op) {
        var el = document.createElement('div');
        el.className = 'plant epi';
        el.textContent = art;
        el.style.left = x.toFixed(2) + '%';
        el.style.fontSize = rem.toFixed(3) + 'rem';
        el.style.fontWeight = '700';
        el.style.color = SCAPE_TINT.leafFar;
        var ds = scapeDepth(0.18, op);
        el.style.opacity = ds.op.toFixed(3);
        if (ds.blur) el.style.filter = 'blur(' + ds.blur.toFixed(2) + 'px)';
        el.dataset.depth = '0.18';
        // Same depth->z rule as the other composed helpers (epi tier is 0.18), not just opacity/blur. Without this a
        el.style.zIndex = String(Math.round(0.18 * 100) + 1);
        el.style.setProperty('--sway-k', '1.35');            // a hanging curtain swings most
        el.style.setProperty('--glint-k', '0.70');           // hangs IN the ray column
        el.style.setProperty('--d-opacity', ds.op.toFixed(3));
        el.style.setProperty('--sway-dur', (10 + Math.random() * 3).toFixed(1) + 's');
        el.style.animationDelay = (-(Math.random() * 5)).toFixed(1) + 's, ' + glintDelay(x);
        registerCycle(el, art);
        layer.appendChild(el);
      }
      // Size FIRST, then test the real footprint, then place. (The old seeder
      // sized AFTER placing, which is why collisions were invisible to it.)
      function plantAt(tier, center, spread, rem, op, sway, depth, tint) {
        var art = pickRandom(tier);
        var size = rem * TANK_SCALE * scapeJitter();
        var x = slot(center, spread, halfPct(art, size), depth, false);
        if (x === null) return;
        addPlant(art, x, size, op, sway, depth, tint);
      }
      function structAt(art, center, spread, rem, op, depth, tint) {
        var size = rem * TANK_SCALE * scapeJitter();
        var x = slot(center, spread, halfPct(art, size), depth, true);
        if (x === null) return null;
        addStruct(art, x, size, op, depth, tint);
        return x;
      }

      masses.length = 0;
      cyclers.length = 0;                              // drop stale registrations

      // --- THE KEYSTONE -------------------------------------------------
      // The one dramatic foreground mass, on a golden third: biggest, crispest,
      // warmest, placed unconditionally (it owns its ground). HERO_STRUCTURES
      // holds the SKULL, so this slot is the skull every load, at a FIXED size —
      // scapeJitter is deliberately not applied, because a centrepiece that is
      // sometimes small is not a centrepiece. 1.95 * TANK_SCALE => ~186px tall on
      // a wide tank; SKULL_SCALE is the one dial for "bigger"/"calmer".
      var heroArt = HERO_STRUCTURES.length ? pickRandom(HERO_STRUCTURES) : pickRandom(STRUCTURES);
      var heroSize = (wide ? 1.95 : 1.5) * TANK_SCALE * SKULL_SCALE;
      var kx = focal;
      placedMid.push({ x: kx, h: halfPct(heroArt, heroSize), s: 1 });
      addStruct(heroArt, kx, heroSize, 0.80, 0.85, SCAPE_TINT.near, 'skull');
      // Taller mass => its centre of volume sits higher and its lee is wider.
      // `vent` is the narrow outgassing band read by bubbleSpawnX().
      masses.push({ x: kx, y: floorPct - 14, r: 24, vent: 1.6 });

      // A far backdrop slab BEHIND the keystone — cool, dim, blurred, rooted on
      // the high sand line. It is what makes the keystone read as foreground.
      structAt(pickRandom(STRUCTURES), kx + (focal < 50 ? 4 : -4), 6,
        wide ? 1.95 : 1.5, 0.50, 0.14, SCAPE_TINT.far);

      // --- THE ECHO -----------------------------------------------------
      // Wide tanks get a second, quieter, further-back mass on the other third;
      // narrow tanks stay single-mass and leave the opposite half as open water.
      var ex = kx;
      if (wide) {
        var exFound = structAt(pickRandom(STRUCTURES), echo, 7, 1.7, 0.60, 0.50, SCAPE_TINT.mid);
        ex = (exFound === null) ? echo : exFound;
        masses.push({ x: ex, y: floorPct - 7, r: 15 });
        chanCenter = (kx + ex) / 2;
      }

      // --- PLANTS -------------------------------------------------------
      // Clustered INTO the masses, never sprinkled. Far tier fills the back wall
      // behind them, midground skirts them, crisp carpet hugs their feet — and
      // the carpet is its own band, so it may sit IN FRONT of the keystone.
      var i, home, ctier;
      var nTall = scapeCount(wide ? 3 : 2);
      for (i = 0; i < nTall; i++) {
        home = (wide && (i % 2)) ? ex : kx;
        plantAt(PLANTS.tall, home, 9, 1.6 + Math.random() * 0.3, 0.72,
          8.5 + Math.random() * 3, 0.12, SCAPE_TINT.leafFar);
      }
      var nMid = scapeCount(wide ? 3 : 2);
      for (i = 0; i < nMid; i++) {
        home = (wide && (i % 2)) ? ex : kx;
        plantAt(PLANTS.mid, home, 10, 1.15 + Math.random() * 0.2, 0.90,
          6.5 + Math.random() * 2, 0.62, SCAPE_TINT.leafMid);
      }
      // Carpet arts that have authored cycle frames, discovered from GLYPH_CYCLES
      // rather than by brittle array index.
      var cycCarpet = [];
      for (i = 0; i < PLANTS.carpet.length; i++) {
        if (GLYPH_CYCLES[PLANTS.carpet[i]]) cycCarpet.push(PLANTS.carpet[i]);
      }
      var nCarpet = scapeCount(wide ? 3 : 2);
      for (i = 0; i < nCarpet; i++) {
        home = (wide && (i % 2)) ? ex : kx;
        // The first carpet piece is drawn from the cycle-capable arts, so a wide
        // tank always has at least one place where the ASCII itself moves. The
        // rest stay a free pick so no two loads look alike.
        ctier = (i === 0 && wideFX && cycCarpet.length) ? cycCarpet : PLANTS.carpet;
        plantAt(ctier, home, 13, 0.95 + Math.random() * 0.18, 0.85,
          5 + Math.random() * 1.5, 0.96, null);
      }
      // One top-rooted curtain over the keystone: uses the dead vertical column
      // without adding a single thing to the floor.
      if (scapeCount(1) > 0) {
        addEpi(pickRandom(PLANTS.epi), kx + (focal < 50 ? -6 : 6),
          (1.25 + Math.random() * 0.25) * TANK_SCALE * scapeJitter(), 0.24);
      }
    }

    function seedWeeds() {
      var layer = $('weeds');
      if (!layer) return;
      // Four clusters at 6/14/83/92 are what extended the old ridge to the full
      // width. The composed scape gets just TWO far-corner curtains framing the
      // open sand; the lobby keeps its four (its narrower crop needs them).
      var specs = cfg.minimal ? [
        { left: '6%', fronds: 5, dur: '7s', delay: '0s' },
        { left: '14%', fronds: 4, dur: '8.5s', delay: '-2s' },
        { left: '83%', fronds: 5, dur: '7.6s', delay: '-1s' },
        { left: '92%', fronds: 3, dur: '9s', delay: '-3s' },
      ] : [
        { left: '4%', fronds: 6, dur: '8.5s', delay: '0s' },
        { left: '96%', fronds: 5, dur: '9.5s', delay: '-3s' },
      ];
      for (var i = 0; i < specs.length; i++) {
        var s = specs[i];
        var wd = document.createElement('div');
        wd.className = 'weed';
        wd.style.left = s.left;
        wd.style.setProperty('--sway-dur', s.dur);
        wd.style.animationDelay = s.delay;
        wd.textContent = Array.from({ length: s.fronds }, function () { return '│'; }).join('\n');
        layer.appendChild(wd);
      }
    }

    // Cursor tracking for the startle response (percent coords within the tank).
    function onMove(e) {
      var r = tank.getBoundingClientRect();
      if (!r.width || !r.height) { mouse = null; return; }
      mouse = { x: (e.clientX - r.left) / r.width * 100, y: (e.clientY - r.top) / r.height * 100 };
      mouseStillT = 0;
    }
    function onLeave() { mouse = null; curioF = null; }
    function onVisibility() {
      if (document.hidden) {
        stopLoop();
        stopPoll();
      } else {
        if ((entities.length || wideFX) && !REDUCED_MOTION) startLoop();
        startPoll();
      }
    }

    // An embed that asks for the rich water paints from its own stylesheet
    // (the lobby: css/main.css), which carries none of the pool rules the
    // standalone page's <style> has. Bring them along, once per document,
    // scoped to tanks stamped data-water="rich" so the standalone page (its
    // own rules, same names) is untouched. Rays/caustics of a rich-water tank
    // that resolved narrow are hidden here too — the page CSS may override.
    var WATER_CSS =
      '[data-water="rich"] .bubble.live{animation:none;bottom:auto}' +
      '[data-water="rich"] .mote{position:absolute;pointer-events:none;user-select:none;font-family:var(--font-mono,monospace);font-size:0.7rem;color:var(--phase-fish);opacity:0;transition:opacity 3s ease}' +
      '[data-water="rich"] .mote.plankton{color:#c4b6ff;text-shadow:0 0 4px rgba(167,139,250,0.7),0 0 8px rgba(167,139,250,0.4)}' +
      '[data-water="rich"] .streak{position:absolute;pointer-events:none;user-select:none;font-family:var(--font-mono,monospace);font-size:0.6rem;line-height:1;color:var(--phase-accent);text-shadow:0 0 5px var(--phase-accent);white-space:pre;transform-origin:center center;opacity:0;will-change:left,top,opacity,transform;font-variant-ligatures:none}' +
      '[data-water="rich"] .ripple{position:absolute;top:var(--bb-waterline,9%);pointer-events:none;transform:translate(-50%,-50%);border:1px solid var(--phase-accent);border-radius:50%;opacity:0;will-change:width,height,opacity;mix-blend-mode:screen}' +
      '[data-water="rich"] .fish-reflect{position:absolute;left:0;top:0;display:inline-block;transform-origin:center center;color:var(--phase-fish);opacity:0;pointer-events:none;filter:blur(0.4px);will-change:transform,opacity;font-variant-ligatures:none}' +
      '[data-water="rich"] .trail-pt{position:absolute;width:3px;height:3px;margin:-1.5px 0 0 -1.5px;border-radius:50%;pointer-events:none;opacity:0;background:rgba(var(--accent-rgb,125,167,217),0.9);box-shadow:0 0 5px rgba(var(--accent-rgb,125,167,217),0.8);will-change:opacity,left,top}' +
      '[data-water][data-fx="narrow"].bb-water-injected .rays,[data-water][data-fx="narrow"].bb-water-injected .caustic{display:none}' +
      '@media (prefers-reduced-motion:reduce){[data-water="rich"] .streak,[data-water="rich"] .ripple,[data-water="rich"] .fish-reflect,[data-water="rich"] .trail-pt{opacity:0!important}}';
    function injectWaterCSS() {
      if (!richWater || !cfg.minimal) return;             // the standalone page has its own rules
      tank.classList.add('bb-water-injected');
      if (document.getElementById('bb-water-css')) return;
      var st = document.createElement('style');
      st.id = 'bb-water-css';
      st.textContent = WATER_CSS;
      document.head.appendChild(st);
    }

    function start() {
      injectWaterCSS();
      computeFloor();           // wideFX + floor before anything seeds
      seedDecor();
      seedWeeds();
      seedBubbles();
      seedMedium();             // shared drifting-medium pool (replaces motes)
      seedStreaks();            // flow streaklines (wide + motion only)
      seedRipples();            // surface ripple-ring pool
      seedFeed();               // feeding flake pool (standalone-safe; needs a click)
      rayLayer = tank.querySelector('.rays');   // cache for live god rays
      if (wideFX) startLoop();  // the water moves even before fish arrive
      if (!REDUCED_MOTION) {
        tank.addEventListener('pointermove', onMove);
        tank.addEventListener('pointerleave', onLeave);
        document.addEventListener('visibilitychange', onVisibility);
      }
      // Feeding click — both surfaces (FEED.lobby gates the embed). Attaches
      // under reduced motion too (calm drop, no fish seek).
      if (FEED.on && (!cfg.minimal || FEED.lobby)) {
        tank.addEventListener('pointerdown', onTankDown);
      }
      // Schlieren (D9) — whole-scene refraction, default OFF (SCHLIEREN=0).
      if (SCHLIEREN && wideFX && !REDUCED_MOTION) tank.classList.add('schlieren');
      startPoll();
    }

    function stop() {
      stopPoll();
      stopLoop();
      if (resizeT) { clearTimeout(resizeT); resizeT = null; }   // debounced reseed must not outlive the tank
      window.removeEventListener('resize', onResize);
      tank.removeEventListener('pointermove', onMove);
      tank.removeEventListener('pointerleave', onLeave);
      tank.removeEventListener('pointerdown', onTankDown);
      document.removeEventListener('visibilitychange', onVisibility);
    }

    if (cfg.autostart !== false) start();

    // Expose injection hooks for offline/visual testing (no network), plus the
    // shared water API sibling lanes reuse (additive — API stays stable).
    return {
      start: start, stop: stop, poll: poll, _apply: applySnapshot,
      // Off-screen embed lifecycle (the lobby's IntersectionObserver calls
      // these). Stops/restarts BOTH halves — the rAF animation loop and the
      // /api/tank poll timer — so an off-screen tank does neither. Mirrors
      // onVisibility(), which does the same for a backgrounded tab.
      pause: function () {
        stopLoop();
        stopPoll();
      },
      resume: function () {
        if (document.hidden) return; // tab is backgrounded — onVisibility owns this
        if (entities.length || wideFX) startLoop();
        startPoll();
      },
      _entities: function () { return entities; },
      _stepMs: function () { return stepMsEma; },
      // Per-species school distribution over SPREAD_COLS fifths: { cols, n,
      // evenness } where evenness is normalized Shannon entropy H/ln(cols).
      // CAVEAT: under REDUCED_MOTION the census never advances (step() never
      // runs), so n:0 / evenness:1 means "nothing is moving" — it is NOT a
      // pass and must never be cited as one. Allocates only when called —
      // never on the rAF path.
      _spread: function () {
        var out = {};
        for (var i = 0; i < spreadKeys.length; i++) {
          var key = spreadKeys[i], sm = spreadEMA[key];
          var n = 0, c;
          for (c = 0; c < SPREAD_COLS; c++) n += sm[c];
          var H = 0;
          if (n > 0) {
            for (c = 0; c < SPREAD_COLS; c++) {
              var p = sm[c] / n;
              if (p > 0) H -= p * Math.log(p);
            }
          }
          out[key] = { cols: sm.slice(), n: n, evenness: n > 0 ? H / Math.log(SPREAD_COLS) : 1 };
        }
        return out;
      },
      ripple: spawnRipple,                    // shared surface-event entry
      medium: function () { return motes; },  // shared tracer pool
    };
  }

  // Public API for embeds (e.g. the front-door lobby).
  window.BBTank = { create: createTank };

  // Auto-initialize the standalone /aquarium/ page when its DOM is present.
  // The instance handle is kept on BBTank.page for offline/visual testing.
  if (document.getElementById('tank') && document.getElementById('legend')) {
    window.BBTank.page = createTank();
  }
})();
"""

# The page chrome. A self-contained adaptation of the site's /aquarium/ page:
# the web-fonts (served from the site) are dropped in favour of the system mono/
# serif stack, the lab/home links point at the absolute site, the renderer is
# inlined instead of loaded from /aquarium/aquarium.js, and a <noscript> block
# restores the static ASCII tank for JS-off viewers. Plain (non-f) string so the
# CSS braces need no escaping; __PHASE__ / __NOSCRIPT__ are filled per request.
_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>tank — live aquarium</title>
  <meta name="description" content="A terminal aquarium fed by this machine — CPU heat as weather, commits and ships as fish, a night-fish that only surfaces after midnight.">
  <meta name="theme-color" content="#0a0a0b">
  <style>
    :root {
      --bg: #0a0a0b;
      --surface: #1e1e21;
      --border: #2a2a2d;
      --text: #fafafa;
      --text-secondary: #a0a0a5;
      --text-muted: #8a8a8f;
      --primary: #10b981;
      --accent: #8b5cf6;
      --font-sans: Georgia, 'Times New Roman', serif;
      --font-mono: 'JetBrains Mono', 'Cascadia Mono', 'Fira Code', 'Consolas', monospace;

      /* Phase palette — set by JS on <body data-phase> */
      --phase-bg: #0a0a0b;
      --phase-glow: rgba(16, 185, 129, 0.06);
      --phase-fish: #cbd5c0;
      --phase-accent: var(--primary);

      /* RGB triplet of the phase accent so JS can build rgba(var(--accent-rgb), a)
         halos at runtime without color-mix. Per-phase overrides below. */
      --accent-rgb: 125,167,217;

      /* GLYPH SHIMMER — the travelling caustic band that crawls over the
         aquascape. --glint-dur MUST stay in sync with GLINT_DUR in
         aquarium.js: that constant is what each piece's x-keyed
         animation-delay is computed against. --glint-phase is the sun gate,
         overridden per phase below. */
      --glint-dur: 16s;
      --glint-phase: 0.8;

      /* LAMP — how much of the phase's light the machine has earned. applyLight
         derives it from weather.light_level (idle factor within the phase) and
         writes --lamp on <body> and data-light on #tank. --ray-opacity already
         carries it; the caustic sheet and the glass meniscus read it below, so
         an idle machine at noon sits visibly dimmer than a busy one. */
      --lamp: 1;
    }
    /* --glint-phase: a caustic is refracted SUNLIGHT, so it follows the sun.
       Day full; dawn/dusk raked and weaker; night is a bare moonlit trace; the
       witching hour has no sun, so the glint all but vanishes and the
       bioluminescent halos keep the dark to themselves (constraint: shimmer
       must not fight the night lanes). */
    body[data-phase="night"]    { --accent-rgb: 125,167,217; --glint-phase: 0.22; }
    body[data-phase="witching"] { --accent-rgb: 167,139,250; --glint-phase: 0.10; }
    body[data-phase="day"]      { --accent-rgb: 16,185,129;  --glint-phase: 1; }
    body[data-phase="dawn"]     { --accent-rgb: 224,169,109; --glint-phase: 0.72; }
    body[data-phase="dusk"]     { --accent-rgb: 217,138,90;  --glint-phase: 0.66; }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    /* ASCII art must render literally — kill the code-font ligatures that would
       turn pairs like <=, =>, >< into single glyphs and mangle the fish. */
    .fish, .legend-glyph, .surface-line, .silt, .fossils, .koi, .weed, #memorial {
      font-variant-ligatures: none;
      font-feature-settings: "liga" 0, "calt" 0;
    }

    body {
      font-family: var(--font-sans);
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 2rem 1rem 3rem;
      transition: background 2.5s cubic-bezier(0.25, 0.46, 0.45, 0.94);
      background:
        radial-gradient(ellipse 80% 60% at 50% 0%, var(--phase-glow), transparent 70%),
        var(--phase-bg);
    }

    .wrap { width: 100%; max-width: 760px; }

    .topbar {
      display: flex; align-items: baseline; justify-content: space-between;
      gap: 1rem; margin-bottom: 0.25rem;
    }
    .label {
      font-family: var(--font-mono);
      font-weight: 700; font-size: 1.05rem; letter-spacing: -0.01em;
      color: var(--text); margin: 0;
    }
    .label .sky { color: var(--phase-accent); margin-right: 0.4rem; }
    .phase-mood {
      font-family: var(--font-mono); font-size: 0.85rem;
      color: var(--text-secondary);
    }
    .phase-mood .mood { color: var(--phase-accent); }
    .temp { font-weight: 600; }
    .temp-cool { color: #7da7d9; }
    .temp-mid  { color: #8fb89a; }
    .temp-warm { color: #e0a96d; }
    .temp-hot  { color: #e3645a; text-shadow: 0 0 8px rgba(227,100,90,0.4); }

    .tagline {
      font-size: 0.82rem; color: var(--text-muted); margin-bottom: 1.25rem;
      line-height: 1.5;
    }

    /* The tank — breaks out of the text column. The page reads at 760px;
       the aquarium deserves the wall. Height rides the viewport. */
    .tank {
      position: relative;
      --tank-w: min(1200px, calc(100vw - 2.5rem));
      width: var(--tank-w);
      margin-left: calc((100% - var(--tank-w)) / 2);
      height: clamp(380px, 58vh, 640px);
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
      background:
        linear-gradient(180deg, transparent 0%, rgba(0,0,0,0.14) 74%, rgba(0,0,0,0.26) 100%),
        var(--surface);
      box-shadow: inset 0 0 60px rgba(0,0,0,0.35);
    }
    /* THE SAND BED. A calm, low, mostly-uniform floor with ONE clean top line
       that fades out at the glass — this, not the old speckle strip, is the
       thing that reads as "floor". The height is written by the seeder as
       --bed-h, computed from the same number seedDecorComposed()'s rootAt()
       uses, so the bed and every root line can never disagree. Purely static:
       no animation, no per-frame write, nothing for reduced-motion to disable.
       Standalone page only — the lobby paints from css/main.css and never sees
       this rule, so its composition is unchanged. */
    .tank::before {
      content: ''; position: absolute; left: 0; right: 0; bottom: 0;
      height: var(--bed-h, 48px); pointer-events: none;
      border-radius: 0 0 11px 11px;
      background:
        linear-gradient(90deg, transparent 0%, rgba(186,168,138,0.30) 10%,
          rgba(186,168,138,0.30) 90%, transparent 100%) left top / 100% 1px no-repeat,
        linear-gradient(180deg,
          rgba(124,110,88,0.00) 0%,
          rgba(124,110,88,0.15) 26%,
          rgba(136,120,95,0.24) 64%,
          rgba(146,129,102,0.28) 100%);
    }
    /* (g) Schlieren — whole-scene refraction wobble, DEFAULT OFF (SCHLIEREN=0 =>
       JS never adds .schlieren). When on, ONE SVG-turbulence filter on the tank,
       not per-entity. The SVG <animate> is self-driving (no JS per frame). */
    .tank.schlieren { filter: url(#bb-schlieren); }
    @media (prefers-reduced-motion: reduce) { .tank.schlieren { filter: none; } }
    .surface-line {
      position: absolute; top: 14px; left: 0; right: 0;
      text-align: center; font-family: var(--font-mono);
      font-size: 0.78rem; color: var(--phase-accent); opacity: 0.5;
      letter-spacing: 0.45em; pointer-events: none; user-select: none;
      animation: surface-wave 7s ease-in-out infinite alternate;
    }
    /* Hot water warms the surface line's color (--heat from applyTemp). */
    @supports (color: color-mix(in srgb, red, blue)) {
      .surface-line {
        color: color-mix(in srgb, var(--phase-accent), #e3645a calc(var(--heat, 0) * 55%));
      }
    }
    @keyframes surface-wave {
      from { transform: translateX(-6px); opacity: 0.4; }
      to   { transform: translateX(6px);  opacity: 0.6; }
    }
    /* Fish are positioned by the JS sim (left/top); the inner span carries the
       facing flip + tail-wiggle transform. No CSS keyframe motion here — the
       requestAnimationFrame loop drives every fish. */
    .fish {
      position: absolute;
      font-family: var(--font-mono);
      font-size: 0.8rem;            /* smaller base; JS sets per-species size */
      color: var(--phase-fish);
      white-space: pre;
      will-change: left, top;
      /* Day legibility floor. At night the luminescence lane writes the breathing
         per-fish glow INLINE on the element (f.el.style.textShadow), which beats
         every stylesheet rule; on the dark->light transition / dim cutoff it sets
         the inline value back to '' so the fish falls back to exactly this shadow. */
      text-shadow: 0 0 8px rgba(0,0,0,0.5);
    }
    /* The span is what flips and undulates — needs inline-block to transform,
       and a centered origin so the tail swish pivots through the body. */
    .fish span {
      display: inline-block;
      transform-origin: center center;
      will-change: transform;
    }

    /* Randomized bottom decor: structures (static) + extra plants (sway). */
    .decor { position: absolute; inset: 0; pointer-events: none; }
    .structure {
      position: absolute; bottom: 20px; font-family: var(--font-mono);
      font-size: 0.9rem; line-height: 0.95; white-space: pre; color: #7a6a55;
      opacity: var(--d-opacity, 0.18); font-variant-ligatures: none;
      transform: translateX(-50%);
      /* Stone never sways — the light moves over it. Opacity-only keyframe:
         composited, no paint, and the biggest glyph masses in the tank. */
      animation: stone-glint var(--glint-dur, 16s) linear infinite;
    }
    .plant {
      position: absolute; bottom: 20px; font-family: var(--font-mono);
      font-size: 1rem; line-height: 0.82; white-space: pre; color: var(--primary);
      opacity: 0.3; font-variant-ligatures: none; transform-origin: bottom center;
      /* TWO animations: the bend (transform) and the light passing over it
         (opacity). They never touch the same property, and JS writes a matching
         two-value animation-delay at seed time. */
      animation: sway var(--sway-dur, 8s) ease-in-out infinite alternate,
                 glyph-glint var(--glint-dur, 16s) linear infinite;
    }
    /* The epiphyte/curtain tier hangs from HIGH in the column (fills the dead
       vertical space above the substrate) — top-rooted, not floor-rooted, so
       the existing sway keyframe rotates it from its hanging anchor. */
    .plant.epi {
      position: absolute; top: 26%; bottom: auto;
      transform-origin: top center; line-height: 0.82; white-space: pre;
    }

    /* Glass — the front pane catches the light: top highlight, an off-center
       specular streak, a corner vignette, and a thin meniscus line riding
       just under the surface, brighter when the water is moving. All static. */
    .glass {
      position: absolute; inset: 0; pointer-events: none;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.05) 0%, transparent 12%),
        linear-gradient(100deg, transparent 40%, rgba(255,255,255,0.04) 50%, transparent 60%),
        radial-gradient(ellipse at 50% 40%, transparent 62%, rgba(0,0,0,0.22) 100%);
    }
    .glass::after {
      content: ''; position: absolute; top: 26px; left: 0; right: 0; height: 2px;
      background: var(--phase-accent);
      opacity: calc((0.08 + var(--flow, 0) * 0.14) * var(--lamp, 1));
      transition: opacity 2s ease;
    }
    .fish.night-fish { color: var(--accent); opacity: 0.92; filter: drop-shadow(0 0 6px rgba(139,92,246,0.5)); }
    .fish.shipfish { color: var(--primary); }
    /* Public-named fish wear a small label (only on non-flipping anchor fish). */
    .fish-name {
      position: absolute; top: 100%; left: 50%; transform: translateX(-50%);
      margin-top: 2px; font-size: 0.58rem; line-height: 1; white-space: nowrap;
      color: var(--phase-accent); opacity: 0.75; pointer-events: none;
      text-shadow: 0 0 4px rgba(0,0,0,0.7); font-variant-ligatures: none;
    }

    /* The eel — a muted green serpent, shifting toward bioluminescent teal in
       the dark. JS sets opacity/filter inline so the lobby degrades cleanly. */
    .fish.eel { color: #8fb89a; white-space: pre; }
    body[data-phase="night"] .fish.eel,
    body[data-phase="witching"] .fish.eel { color: #7fb0a0; }

    /* The anglerfish LURE — the bioluminescent showpiece. Glow (text-shadow) is
       set INLINE by renderLure ONLY in night/witching, so the day lure stays a
       dull unlit bead with no glow (constraint 1). */
    .fish.anglerfish .lure {
      display: inline-block; font-family: var(--font-mono); color: #fff;
      pointer-events: none; transition: opacity 1.5s ease;
    }

    /* (c) Near-surface fish reflection: a faint inverted ghost above the
       waterline. Child span of .fish so it inherits font-size + follows the
       fish; writeReflection flips it scaleY(-1) and sets opacity inline. */
    .fish-reflect {
      position: absolute; left: 0; top: 0;
      display: inline-block; transform-origin: center center;
      color: var(--phase-fish); opacity: 0; pointer-events: none;
      filter: blur(0.4px); will-change: transform, opacity;
      font-variant-ligatures: none;
    }

    /* (D5) Light-trail points: luminous dots dropped behind driftfish/notefish
       at night. Positioned + faded inline by the rAF loop; pooled + capped. */
    .trail-pt {
      position: absolute; width: 3px; height: 3px; margin: -1.5px 0 0 -1.5px;
      border-radius: 50%; pointer-events: none; opacity: 0;
      background: rgba(var(--accent-rgb), 0.9);
      box-shadow: 0 0 5px rgba(var(--accent-rgb), 0.8);
      will-change: opacity, left, top;
    }

    /* Witching plankton: the same mote pool, tinted violet + softly glowing.
       The rAF twinkle raises opacity when a luminous fish passes. */
    .mote.plankton {
      color: #c4b6ff;
      text-shadow: 0 0 4px rgba(167,139,250,0.7), 0 0 8px rgba(167,139,250,0.4);
    }

    /* (b) Flow streakline: a short luminous dash tracing local velocity.
       Position + rotation + opacity all set inline by stepWater(). */
    .streak {
      position: absolute; pointer-events: none; user-select: none;
      font-family: var(--font-mono); font-size: 0.6rem; line-height: 1;
      color: var(--phase-accent);
      text-shadow: 0 0 5px var(--phase-accent);
      white-space: pre; transform-origin: center center;
      opacity: 0; will-change: left, top, opacity, transform;
      font-variant-ligatures: none;
    }

    /* (c) Surface ripple ring: a flat ellipse expanding at the waterline.
       Size + opacity animated inline by stepWater(); armed via ripple(x). */
    .ripple {
      position: absolute; top: 22px;
      pointer-events: none; transform: translate(-50%, -50%);
      border: 1px solid var(--phase-accent); border-radius: 50%;
      opacity: 0; will-change: width, height, opacity;
      mix-blend-mode: screen;
    }

    /* (feeding) Food flakes: glyphs positioned by JS (left/top %). Glow gates on
       data-phase so they only light at night/witching (lit-from-above by day). */
    .flake {
      position: absolute; pointer-events: none; user-select: none;
      font-family: var(--font-mono); color: var(--phase-accent); opacity: 0;
      text-shadow: 0 0 4px var(--phase-glow);
      transition: opacity 0.4s ease; will-change: left, top, opacity;
      transform: translate(-50%, -50%); font-variant-ligatures: none;
    }
    body[data-phase="night"] .flake   { text-shadow: 0 0 6px rgba(125,167,217,0.5); }
    body[data-phase="witching"] .flake { text-shadow: 0 0 8px var(--phase-accent); }

    /* (feeding) Self-contained ripple — used ONLY when wideFX is off (the wide
       page delegates to the shared ripple ring). Drop splash at the waterline. */
    .feed-ripple {
      position: absolute; top: 26px;
      width: 14px; height: 14px; margin: -7px 0 0 -7px;
      border-radius: 50%; pointer-events: none;
      border: 1px solid var(--phase-accent); opacity: 0;
      animation: feed-ring 0.7s ease-out forwards;
    }
    @keyframes feed-ring {
      0%   { opacity: 0.5; transform: scale(0.3); }
      100% { opacity: 0;   transform: scale(3.2); }
    }
    /* Bioluminescence: the dark hours make the fish glow. The JS luminescence lane
       owns the live per-fish glow — it writes the breathing shadow INLINE (which
       beats every rule), and on the dim cutoff (D1) it writes '' so the fish falls
       back to the base .fish dark legibility shadow and goes genuinely dark, letting
       the anglerfish lure keep the single brightest point. The colored night-glow
       floor therefore lives ONLY in the reduced-motion block below (no rAF there).
       These rules just carry a slightly stronger dark drop so unlit night fish stay
       readable against the dark water. Day/dawn/dusk get NO glow (constraint 1). */
    body[data-phase="night"]    .fish { text-shadow: 0 0 2px rgba(0,0,0,0.55); }
    body[data-phase="witching"] .fish { text-shadow: 0 0 2px rgba(0,0,0,0.6); }

    /* Rare shooting-star koi — streaks across, then gone. */
    .koi {
      position: absolute; top: 12%; left: -8%;
      font-family: var(--font-mono); font-size: 1.1rem; color: var(--phase-accent);
      pointer-events: none; white-space: pre;
      text-shadow: 0 0 12px var(--phase-accent);
      animation: koi-streak 2.6s ease-in forwards;
    }
    @keyframes koi-streak {
      0%   { left: -8%;  top: 10%; opacity: 0; transform: rotate(8deg); }
      15%  { opacity: 0.9; }
      85%  { opacity: 0.9; }
      100% { left: 104%; top: 30%; opacity: 0; transform: rotate(8deg); }
    }
    /* Light rays slanting down from the surface. Phase sets the sun's rake
       angle and strength (vars from applyPhase); load sets the drift speed.
       Dawn/dusk rake low and warm, noon stands steep, night nearly dies. */
    .rays {
      position: absolute; inset: 0; pointer-events: none;
      /* (e) Live god rays: stepWater() drives --ray-shift (sway) + --ray-flicker
         (breath) per frame; these COMPOSE with the keyframe drift. Defaults keep
         the static look when RAYS_LIVE=0 (vars simply never get set). */
      --ray-shift: 0%;
      --ray-flicker: 1;
      opacity: calc(var(--ray-opacity, 0.09) * var(--ray-flicker));
      transform: translateX(var(--ray-shift));
      mix-blend-mode: screen;
      background: repeating-linear-gradient(var(--ray-angle, 80deg),
        transparent 0 110px, var(--phase-accent) 110px 116px, transparent 116px 240px);
      background-size: 200% 100%;
      animation: rays var(--ray-dur, 30s) linear infinite;
      filter: blur(1px);
      transition: opacity 0.6s ease, transform 0.6s ease;
    }
    @keyframes rays { from { background-position: 0 0; } to { background-position: 180% 0; } }
    /* The witching hour has no sun: a few thin, still, violet shafts breathe
       up from the dark instead of raking down from the surface. */
    [data-phase="witching"] .rays {
      background: repeating-linear-gradient(90deg,
        transparent 0 200px, var(--phase-accent) 200px 203px, transparent 203px 420px);
      animation: witch-breathe 9s ease-in-out infinite alternate;
    }
    @keyframes witch-breathe { from { opacity: 0.03; } to { opacity: 0.08; } }

    /* Depth attenuation — the water column cools and darkens toward the silt.
       Phase tints it (cool blue night, warm shadow dusk); stirred silt raises
       the murk-line up the glass (--murk-start from renderSilt). Static layer,
       multiply-blended, sits under decor + fish so they stay legible. */
    .depth {
      position: absolute; inset: 0; pointer-events: none;
      mix-blend-mode: multiply;
      background: linear-gradient(180deg,
        transparent 0%, transparent var(--murk-start, 55%),
        var(--depth-tint, rgba(16,24,46,0.5)) 100%);
      transition: opacity 2s ease;
    }

    /* Swaying seaweed in the back corners. */
    .weeds { position: absolute; inset: 0; pointer-events: none; }
    /* The corner curtains are BACKGROUND, not part of the ridge: rooted on the
       high (far) sand line, dim, cool and softened so the eye files them as the
       back wall. The seeder places only two of them on this page. */
    .weed {
      position: absolute; bottom: calc(var(--bed-h, 48px) - 6px);
      font-family: var(--font-mono); font-size: 1.25rem; line-height: 0.85;
      color: #3d6f70; opacity: 0.17; white-space: pre;
      filter: blur(1.4px);
      transform-origin: bottom center;
      animation: sway var(--sway-dur, 8s) ease-in-out infinite alternate;
    }
    /* Sway amplitude rides the current (--sway-amp from applyCurrent) AND the
       piece's own tier (--sway-k, seeded from its depth in seedDecorComposed): a
       hanging epiphyte curtain swings, tall background stems ride the current,
       the foreground carpet barely stirs. An idle machine still leaves the
       whole bed nearly still; load bends it. --sway-k defaults to 1, so .weed
       (which shares this keyframe and is never given the var) is unchanged. */
    @keyframes sway {
      from { transform: rotate(calc(var(--sway-amp, 5deg) * var(--sway-k, 1) * -1)) skewX(calc(3deg * var(--sway-k, 1))); }
      to   { transform: rotate(calc(var(--sway-amp, 5deg) * var(--sway-k, 1)))      skewX(calc(-3deg * var(--sway-k, 1))); }
    }

    /* GLYPH CAUSTIC — a single band of surface light crawling across the
       aquascape. Each piece is phase-keyed to its own x (seedDecorComposed
       writes the animation-delay), so the band SWEEPS left->right with the
       laminar drift instead of the whole scape pulsing at once. The .caustic
       sheet below paints UNDER #decor and so never reaches a glyph; this is what
       puts the light ON the ASCII. Amplitude is the product of exactly three
       things: how near the piece is (--glint-k, from depth), how much sun the
       phase has (--glint-phase), and how hard the water is moving (--flow — the
       same knob .caustic reads; the 0.3 floor mirrors its own `0.3 + flow*0.25`,
       so a glassy idle tank keeps a faint honest ripple instead of going dead).
       Base opacity comes back through --d-opacity, so the depth ramp survives
       the animation. Flat for two-thirds of the cycle, one soft ~4.8s pass.
       OPACITY ONLY — deliberately no text-shadow: that would be a main-thread
       paint on ~10 multi-line white-space:pre blocks every frame, forever. */
    @keyframes glyph-glint {
      0%, 34%, 64%, 100% { opacity: var(--d-opacity, 0.3); }
      48% {
        opacity: calc(var(--d-opacity, 0.3) *
          (1 + 0.42 * var(--glint-k, 0.8) * var(--glint-phase, 0.8) * (0.3 + 0.7 * var(--flow, 0.5))));
      }
    }
    /* Same band over the hardscape. Stone is matte (it takes no wet glint), and
       the rock masses are the largest glyph blocks in the tank. */
    @keyframes stone-glint {
      0%, 34%, 64%, 100% { opacity: var(--d-opacity, 0.18); }
      48% {
        opacity: calc(var(--d-opacity, 0.18) *
          (1 + 0.38 * var(--glint-k, 0.5) * var(--glint-phase, 0.8) * (0.3 + 0.7 * var(--flow, 0.5))));
      }
    }

    /* Substrate GRAIN — no longer a band. The bed itself is painted by
       .tank::before; this layer only carries occasional specks of sediment
       (renderSilt emits mostly spaces on this page), so it reads as texture IN
       sand rather than a strip of noise laid ON it. Warm sand tint, not grey.
       NOTE: the shimmer lane's travelling `.silt::after` highlight was
       deliberately NOT taken — the static sand bed above is the better read and
       the two together were redundant. */
    .silt {
      position: absolute; bottom: 0; left: 0; right: 0; height: 22px;
      font-family: var(--font-mono); font-size: 0.72rem; line-height: 22px;
      letter-spacing: 0.14em;
      color: rgba(186,170,142,0.16); white-space: nowrap; overflow: hidden;
      user-select: none; pointer-events: none;
    }
    .fossils {
      position: absolute; bottom: 3px; left: 6px; right: 6px;
      font-family: var(--font-mono); font-size: 0.74rem; letter-spacing: 0.3em;
      color: rgba(170,158,136,0.26); white-space: nowrap; overflow: hidden;
      user-select: none; pointer-events: none;
    }

    /* Caustic light — two superimposed gradient sheets at different angles
       drifting at different speeds in opposite directions. Where they cross
       they brighten; the eye reads woven, dancing pool-floor light from two
       cheap gradients. Brightness rides the machine's load (--flow): idle
       water goes genuinely glassy, busy water glitters. */
    .caustic {
      position: absolute; inset: 0; pointer-events: none;
      /* Brightness rides load (--flow) AND the lamp (--lamp, from light_level):
         idle water goes glassy and dim, busy water glitters. */
      opacity: calc((0.3 + var(--flow, 0) * 0.25) * var(--lamp, 1));
      background: repeating-linear-gradient(105deg,
        transparent 0 24px, var(--phase-glow) 24px 48px, transparent 48px 72px);
      background-size: 220% 100%;
      animation: caustic var(--caustic-dur, 18s) linear infinite;
      mix-blend-mode: screen;
    }
    .caustic::before {
      content: ''; position: absolute; inset: 0;
      background: repeating-linear-gradient(78deg,
        transparent 0 30px, var(--phase-glow) 30px 52px, transparent 52px 86px);
      background-size: 240% 100%;
      animation: caustic-b calc(var(--caustic-dur, 18s) * 1.37) linear infinite;
    }
    @keyframes caustic { from { background-position: 0 0; } to { background-position: 220% 0; } }
    @keyframes caustic-b { from { background-position: 240% 0; } to { background-position: 0 0; } }

    /* Ambient bubbles drifting up. */
    .bubbles { position: absolute; inset: 0; pointer-events: none; overflow: hidden; }
    .bubble {
      position: absolute; bottom: -10px;
      width: var(--size, 4px); height: var(--size, 4px);
      border-radius: 50%;
      background: var(--phase-accent); opacity: 0.18;
      animation: rise var(--rise, 10s) linear infinite;
    }
    @keyframes rise {
      0%   { transform: translateY(0) translateX(0); opacity: 0; }
      10%  { opacity: 0.22; }
      90%  { opacity: 0.22; }
      100% { transform: translateY(-360px) translateX(calc(8px + var(--flow, 0) * 14px)); opacity: 0; }
    }
    /* Live bubbles (wide tanks): the rAF sim owns position — no keyframes. */
    .bubble.live { animation: none; bottom: auto; }
    /* Silt motes: passive tracers of the flow field. Opacity is data-driven
       (silt_density, set inline); they're dimmest things in the tank. */
    .mote {
      position: absolute; pointer-events: none; user-select: none;
      font-family: var(--font-mono); font-size: 0.7rem;
      color: var(--phase-fish); opacity: 0;
      transition: opacity 3s ease;
    }

    /* Live field guide of what's swimming now. */
    .legend {
      display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; margin-top: 0.9rem;
      font-family: var(--font-mono); font-size: 0.75rem;
    }
    .legend-item { display: inline-flex; align-items: baseline; gap: 0.4rem;
      color: var(--text-muted); cursor: help; }
    .legend-glyph { color: var(--phase-fish); }
    .legend-name { color: var(--text-secondary); }

    #memorial {
      margin-top: 0.6rem; font-family: var(--font-mono); font-size: 0.78rem;
      color: var(--text-muted); letter-spacing: 0.15em;
    }
    #named {
      margin-top: 0.6rem; font-family: var(--font-mono); font-size: 0.78rem;
      color: var(--text-muted);
    }
    #named .named-proj { color: var(--primary); }

    .footer {
      display: flex; justify-content: space-between; align-items: center;
      margin-top: 0.9rem; font-family: var(--font-mono); font-size: 0.8rem;
      color: var(--text-muted); flex-wrap: wrap; gap: 0.5rem;
    }
    .footer .live { color: var(--primary); }
    .footer .sleeping { color: var(--text-secondary); }
    .footer a { color: var(--text-secondary); text-decoration: none; border-bottom: 1px dotted var(--border); }
    .footer a:hover { color: var(--text); }

    .explainer {
      margin-top: 2rem; font-size: 0.85rem; color: var(--text-muted);
      line-height: 1.7; border-top: 1px solid var(--border); padding-top: 1.25rem;
    }
    /* Plain words — the layperson paragraph every technical page here carries.
       Sits above the explainer: what you are looking at, before how it works. */
    .plain-words {
      margin-top: 2rem; padding-top: 1.25rem;
      border-top: 1px solid var(--border);
      font-size: 0.92rem; line-height: 1.75;
      color: var(--text-secondary); max-width: 62ch;
    }
    .plain-words strong {
      display: block; font-family: var(--font-mono); font-size: 0.68rem;
      letter-spacing: 0.18em; text-transform: uppercase;
      color: var(--primary); margin-bottom: 0.5rem; font-weight: 500;
    }
    .explainer b { color: var(--text-secondary); font-weight: 600; }
    .explainer code { font-family: var(--font-mono); color: var(--primary); font-size: 0.8rem; }

    .home { display: inline-block; margin-bottom: 1.5rem; font-family: var(--font-mono);
            font-size: 0.8rem; color: var(--text-secondary); text-decoration: none; }
    .home:hover { color: var(--text); }

    /* Narrow screens: the topbar's title + phase/mood share a space-between
       row that collides under ~560px, so stack them. Tighten paddings and
       trim the tank height so the whole tank sits above the fold on a phone. */
    @media (max-width: 560px) {
      body { padding: 1.25rem 0.85rem 2.5rem; }
      .topbar {
        flex-direction: column; align-items: flex-start; gap: 0.15rem;
        margin-bottom: 0.4rem;
      }
      .label { font-size: 0.98rem; }
      .phase-mood { font-size: 0.8rem; }
      .tagline { font-size: 0.78rem; margin-bottom: 1rem; }
      .tank { height: clamp(300px, 46vh, 420px); }
      .legend { gap: 0.4rem 0.8rem; font-size: 0.72rem; }
      .footer { font-size: 0.74rem; }
      .explainer { margin-top: 1.5rem; font-size: 0.82rem; }
    }

    @media (prefers-reduced-motion: reduce) {
      .fish, .bubble, .caustic, .caustic::before, .rays, [data-phase="witching"] .rays, .weed, .plant, .plant.epi, .surface-line, .koi { animation: none; }
      body { transition: none; }
      /* Night Alive new motion lanes — calm static fallbacks (constraint 4). */
      .streak, .ripple, .fish-reflect, .trail-pt, .feed-ripple { animation: none; opacity: 0 !important; }
      /* Shimmer lane: .plant is already in the animation:none list above, which
         kills BOTH sway and glyph-glint (one shorthand list) and falls the
         plant back to its inline depth opacity. Stone is a new animated surface
         and needs naming here. Glyph cycling needs no rule: REDUCED_MOTION
         forces wideFX false, so registerCycle never fires and every piece stays
         on its authored resting frame. */
      .structure { animation: none; }
      .flake { transition: none; }
      .fish.eel, .fish.anglerfish .lure { animation: none; transition: none; }
      .rays { transform: none !important; opacity: var(--ray-opacity, 0.09) !important; }
      /* Reduced-motion night halo: a dim, NON-pulsing floor (no rAF => no inline glow write). */
      body[data-phase="night"]    .fish { text-shadow: 0 0 6px rgba(125,167,217,0.35), 0 0 2px rgba(0,0,0,0.55) !important; }
      body[data-phase="witching"] .fish { text-shadow: 0 0 8px rgba(167,139,250,0.40), 0 0 2px rgba(0,0,0,0.6) !important; }
      .mote.plankton { text-shadow: 0 0 4px rgba(167,139,250,0.5); }
    }
  </style>
</head>
<body data-phase="__PHASE__">
  <svg width="0" height="0" style="position:absolute" aria-hidden="true">
    <filter id="bb-schlieren">
      <feTurbulence type="fractalNoise" baseFrequency="0.012 0.02" numOctaves="1" seed="7" result="n">
        <animate attributeName="baseFrequency" dur="18s" values="0.012 0.02;0.016 0.014;0.012 0.02" repeatCount="indefinite"/>
      </feTurbulence>
      <feDisplacementMap in="SourceGraphic" in2="n" scale="6" xChannelSelector="R" yChannelSelector="G"/>
    </filter>
  </svg>
  <main class="wrap" id="main-content">
    <a class="home" href="https://www.brokenbranch.dev/lab/">← the lab</a>
    <a class="home" href="https://www.brokenbranch.dev/" style="margin-left:14px">broken branch</a>

    <div class="topbar">
      <h1 class="label"><span class="sky" id="sky">☾</span>tank — this machine</h1>
      <div class="phase-mood"><span id="phase">night</span> · <span class="temp" id="temp">—</span> · feels <span class="mood" id="mood">—</span></div>
    </div>
    <div class="tagline">A live terminal aquarium fed by this machine. CPU heat is the weather; commits and shipped projects spawn fish. Polls <code>/tank.json</code> from <code>tank serve</code>.</div>

    <div class="tank" id="tank" role="img" aria-label="live aquarium">
      <div class="rays" aria-hidden="true"></div>
      <div class="caustic" aria-hidden="true"></div>
      <div class="depth" aria-hidden="true"></div>
      <div class="decor" id="decor" aria-hidden="true"></div>
      <div class="weeds" id="weeds" aria-hidden="true"></div>
      <div class="bubbles" id="bubbles" aria-hidden="true"></div>
      <div class="glass" aria-hidden="true"></div>
      <div class="surface-line" id="surface">· · · · · · · · · · · ·</div>
      <div class="silt" id="silt"></div>
      <div class="fossils" id="fossils"></div>
    </div>

    <noscript>__NOSCRIPT__</noscript>

    <section class="legend" id="legend" aria-label="what's swimming now"></section>
    <section id="named" aria-label="projects swimming now"></section>
    <section id="memorial" aria-label="fossils of fish that have passed"></section>

    <div class="footer">
      <span id="status">connecting to the tank…</span>
      <a href="https://github.com/benskamps/fish-tank" id="repo">how it works ↗</a>
    </div>

    <div class="explainer">
      <p>A real aquarium, not a simulation: the <b>weather</b> tracks CPU and GPU heat,
      the <b>light</b> follows the local clock through
      <code>dawn → day → dusk → night → witching</code>, and fish are born from real
      events — ships, commits, notes. Between midnight and 3am a <b>night-fish</b>
      <code>~(o_o)~</code> surfaces, gone by dawn. The full story — the bestiary, the
      epitaphs, what spawns what — lives at <a href="https://www.brokenbranch.dev/lab/">the lab</a> and in
      <a href="https://github.com/benskamps/fish-tank">the source</a>.</p>
    </div>
  </main>

  <script>__JS__</script>
</body>
</html>
""".replace("__JS__", _JS)
