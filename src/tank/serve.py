"""tank serve — tiny localhost HTTP for the tank page.

Serves two things, no dependencies:
  GET /tank.json  — the current world as a sanitized snapshot: a nested
                    ``weather`` block (phase/temp/current/silt/light/mood) plus
                    the fish roster (species/glyph/zone/mood/name), the fossil
                    layer, a ``fish_count`` and a ``tick_at`` stamp. The page
                    polls this every 15s.
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

    Shape mirrors the published (site) snapshot the renderer expects: a nested
    ``weather`` object plus a flat fish roster, fossils, a count and a freshness
    stamp. ``tick_at`` is the world file's mtime — a faithful "last tick" proxy
    that drives the page's live-ish / sleeping indicator with no model changes.
    """
    path = paths.world_path()
    world = world_from_json(path.read_text(encoding="utf-8"))
    w = world.weather
    try:
        tick_at = datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        tick_at = None
    fish = [
        {
            "species": f.species,
            "glyph": f.glyph,
            "zone": getattr(f, "zone", "mid"),
            "mood": getattr(f, "mood", "calm"),
            "name": getattr(f, "name", None),
        }
        for f in world.fish
    ]
    return {
        "weather": {
            "phase": w.phase,
            "temperature_c": w.temperature_c,
            "current_strength": w.current_strength,
            "silt_density": w.silt_density,
            "light_level": w.light_level,
            "mood": w.mood,
        },
        "fish": fish,
        "fish_count": len(fish),
        "fossil_layer": list(w.fossil_layer),
        "tick_at": tick_at,
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
  };

  // Per-species size (rem). Big proud fish read as foreground; fry stay tiny.
  var SIZE = {
    pleco: 1.15, shipfish: 1.12, founderfish: 1.06, 'night-fish': 1.0,
    witnessfish: 0.94, notefish: 0.94, coldfin: 0.92, crashstrider: 0.86, emberlung: 0.84,
    thermalwisp: 0.82, guppy: 0.8, snail: 0.8, frostneon: 0.78, rummynose: 0.78,
    driftfish: 0.76, hatchetfish: 0.74, tetra: 0.68, killifish: 0.66, cleanershrimp: 0.6,
    crab: 0.92,
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

  // FEEDING (the play) — standalone page only; disabled under cfg.minimal so
  // lobby clicks pass through. Click drops sinking food flakes; nearby fish
  // seek + peck + consume them. Pooled, capped, no per-frame allocation.
  var FEED = {
    on: true,         // master knob — flip off to disable the whole lane
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

  var ZONE_BAND = { surface: [8, 28], mid: [30, 64], bottom: [66, 86] };
  // Proud project fish hold their place as landmarks; everyone else swims.
  var ANCHOR = { shipfish: 1, founderfish: 1, witnessfish: 1, notefish: 1 };
  // Loose shoalers — they flock with their own kind.
  var SCHOOL = { tetra: 1, guppy: 1, rummynose: 1, driftfish: 1, killifish: 1, frostneon: 1 };
  // Darters: short, sharp burst-and-coast. Grazers: long, gentle.
  var DARTY = { crashstrider: 1, killifish: 1, thermalwisp: 1, emberlung: 1, hatchetfish: 1 };
  var CALM  = { snail: 1, pleco: 1, cleanershrimp: 1, coldfin: 1, 'night-fish': 1, anglerfish: 1 };

  // Seconds to cross the tank, per species — a creature's true pace. Bottom
  // grazers creep; darters streak; crabs scuttle quick. (Scaled by current.)
  var CROSS = {
    snail: 150, pleco: 95, cleanershrimp: 85,                 // grazers: glacial
    crab: 18,                                                 // quick scuttle
    crashstrider: 12, killifish: 13, thermalwisp: 13,         // darty
    emberlung: 14, hatchetfish: 16,
    coldfin: 30, frostneon: 28, 'night-fish': 24,             // languid
    guppy: 22, tetra: 20, rummynose: 21, driftfish: 20,       // mid school
    eel: 26, anglerfish: 42,                                  // lurker / deep drifter
  };

  // Drawn direction per glyph: +1 = the glyph as written faces RIGHT, -1 = it
  // faces LEFT. Verified by rendering every glyph and reading the pixels (every
  // mirror-pair gets opposite signs, so a species swims both ways correctly).
  // To make a fish face its travel direction we set the span's scaleX sign to
  // sign(vx) * natFace — so the glyph never moonwalks regardless of which way
  // the sim sends it. Unknown glyphs fall back to a heuristic below.
  var FACING = {
    '>°))<': 1, '<°))>': -1, '>o))<': 1,                       // guppy
    '<·><': -1, '><·>': 1,                                     // tetra
    '=>°)>': 1, '<(°<=': -1, '=>o)>': 1,                       // rummynose
    '^v^>': 1, '<^v^': -1,                                     // hatchetfish
    '><≈>': 1, '<≈><': -1,                                     // killifish
    '@_': 1, '_@': -1,                                         // snail
    '<#=>': 1, '<=#>': -1,                                     // pleco
    '><((°>': 1, '<°)))><': -1, '{·_·}>': 1,                   // coldfin
    '<*°)>': 1, '<(*°)<': -1,                                  // frostneon
    '>~~~>': 1, '~~>>>': 1, '>.>': 1,                          // thermalwisp
    '>≈≈≈>': 1, '<≈≈<': -1,                                    // emberlung
    '><x>': 1, '><X>': 1,                                      // crashstrider
    '><((((°>': 1, '<°))))><': -1,                             // shipfish
    '<°)F)><': -1,                                             // founderfish
    '><o>': 1, '<o><': -1,                                     // driftfish
    '<°)W><': -1,                                              // witnessfish
    'V(°°)V': 1, '(\\°°/)': 1, 'v(°°)v': 1,                    // crab (symmetric)
    '°v°': 1, '·v·': 1,                                        // cleanershrimp
    '~(o_o)~': 1, '·(u_u)·': 1, '°<))°<': -1, '>°))°<': 1,     // night-fish
    '~~~∋°>': 1, '<°∈~~~': -1,                                 // eel (serpentine; head/mouth on the > side)
    '∽∽∽°>': 1, '<°∽∽∽': -1,                                   // anglerfish (lure rides ahead of the °> head)
  };

  // Heuristic facing for an unknown glyph: the eye (°/o) sits near the head, so
  // its half tells us the way it faces; else the outermost </> wins.
  function natFace(g) {
    if (Object.prototype.hasOwnProperty.call(FACING, g)) return FACING[g];
    if (!g) return 1;
    var eye = g.search(/[°o]/);
    if (eye >= 0) return eye < g.length / 2 ? -1 : 1;
    var lt = g.indexOf('<'), gt = g.lastIndexOf('>');
    if (lt < 0 && gt < 0) return 1;
    return gt > lt ? 1 : -1;
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
      r.vx = u + s * 2.0;                          // + laminar downstream drift
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
    ' ▄▀▀▀▄\n▐░░░░░▌\n│││││││\n ▀▀▀▀▀',                          // giant clam
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
  // Plants in tiers for layered aquascaping (background → foreground), plus a
  // top-rooted `epi` tier that hangs from high in the column to fill the dead
  // vertical space above the substrate. RE-AUTHORED 2026-06-09 in the same
  // JetBrains-Mono-700 1-cell palette as STRUCTURES — the old thin-wavy specks
  // (⌇ ≀ ∾) and florets (❀ ❁ ✿ ⚘) and box-diagonals (╲ ╱) all measured 0.69–1.82
  // cells and sheared. Stems are light verticals │, leaves are half-blocks ▌▐ or
  // light box tees, blossoms a plain ○ — all exactly one advance, so columns stack.
  var PLANTS = {
    tall: [
      '  │\n │││\n │││\n  ││\n  │',                               // jungle val ribbons
      '  ○\n ▐│▌\n  │\n ▐│▌\n  │',                               // flowering tall stem
      '  │\n │││\n │││\n │││\n  │',                               // rotala bush
      '  │\n ▐│\n │▌\n ▐│\n  │',                                  // corkscrew val — readable twist
      ' │ │\n │││\n │││\n  │',                                    // reedy spire
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
  function tailTransform(fish, dt, scaleSign) {
    if (typeof fish.wigglePhase !== 'number') fish.wigglePhase = Math.random() * Math.PI * 2;
    var speed = fish.speed || 0;
    var freq = Math.min(22, 2 + 0.45 * speed);
    fish.wigglePhase += freq * dt;
    if (fish.wigglePhase > Math.PI * 2) fish.wigglePhase %= Math.PI * 2;
    var ph = fish.wigglePhase;
    var rot = (3 + Math.min(3, speed * 0.2)) * Math.sin(ph);
    var flex = 0.02 * Math.sin(ph + 0.6);
    var sx = (scaleSign * (1 - flex)).toFixed(4), sy = (1 + flex).toFixed(4);
    return 'scaleX(' + sx + ') rotate(' + rot.toFixed(2) + 'deg) scaleY(' + sy + ')';
  }

  // -----------------------------------------------------------------------
  // Factory — one tank bound to a config (DOM scope + optional chrome).
  // -----------------------------------------------------------------------
  function createTank(cfg) {
    cfg = cfg || {};
    var root = cfg.root || document;
    var scopeEl = cfg.scope || document.body;
    var POLL_MS = cfg.pollMs || 45000;
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
    var timer = null;
    var entities = [];          // live fish sim entities
    var rosterSig = null;       // signature of the current roster
    var rafId = null;
    var lastT = 0;
    var mouse = null;           // {x,y} percent, or null when pointer is away

    // The true floor in percent terms. Zones are %-based but the silt strip is
    // a fixed 26px, so on a tall tank "82%" floats well above the substrate —
    // bottom dwellers (snail, crab) need the real floor, not the bottom band.
    var floorPct = 90;
    var wideFX = false;       // rich water effects: wide tank + motion allowed
    function computeFloor() {
      var r = tank.getBoundingClientRect();
      if (r.height) floorPct = Math.max(84, Math.min(94, 100 - 3400 / r.height));
      // Rich water needs full chrome: the minimal lobby embed is a full-bleed
      // hero (wide by geometry) but paints from a stylesheet without the
      // .mote/.bubble.live/etc rules — it keeps the classic CSS water.
      wideFX = r.width > 900 && !REDUCED_MOTION && !cfg.minimal;
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
    var trailFish = [];       // (glow) subset of entities that carry a light-trail
    var brightSwimmers = [];  // (glow) luminous fish plankton can twinkle against
    var lastPhaseObj = PHASES.night;
    var darkPhase = true;     // (eel/glow) night/witching => glow allowed
    var eelHuntPos = null;    // (eel) per-frame pointer to a hunting eel's position
    var flakes = [];          // (feeding) pooled flake records (fixed length)
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
      f.ghost.style.transform = 'scaleX(' + scaleSign + ') scaleY(-1) translateY(-150%)';
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

      if (f.eelCalm) {                      // reduced-motion / lobby-cheap path
        if (Math.random() < 0.004) f.vx = -f.vx;
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
        if (Math.random() < 0.006) f.vx = -f.vx;
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
      var lead = (f.vx >= 0 ? 1 : -1);          // lure sits on the leading edge
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
    // Pointer/touch -> drop food at the cursor x. Not attached under cfg.minimal.
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

    function applyPhase(phase) {
      var p = PHASES[phase] || PHASES.night;
      var s = scopeEl.style;
      s.setProperty('--phase-bg', p.bg);
      s.setProperty('--phase-glow', p.glow);
      s.setProperty('--phase-fish', p.fish);
      s.setProperty('--phase-accent', p.accent);
      var ray = RAY[phase] || RAY.night;
      s.setProperty('--ray-angle', ray.angle);
      s.setProperty('--ray-opacity', ray.op);
      rayOpacity = ray.op;
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
      var cruise = 110 / cross;                 // percent/sec to cross the tank
      var moodK = f.mood === 'darting' ? 1.4 : f.mood === 'sleeping' ? 0.6 : 1;
      cruise *= moodK;
      var x = anchor ? (8 + ((i * 53) % 84)) : (5 + Math.random() * 90);
      var y = band[0] + Math.random() * (band[1] - band[0]);
      // Bottom dwellers start ON the substrate, not somewhere in the band.
      if (species === 'snail' || species === 'crab') y = floorPct - 1;
      if (species === 'eel') y = floorPct - 3;          // the eel lurks LOW
      var dir = (i % 2 === 0) ? 1 : -1;
      var el = document.createElement('div');
      el.className = 'fish' + ' ' + species + (anchor ? ' anchor' : '');
      var span = document.createElement('span');
      span.textContent = f.glyph || '><>';
      el.appendChild(span);
      // Per-fish size jitter — a mixed population (fry to fully-grown), not clones.
      el.style.fontSize = ((SIZE[species] || 0.8) * TANK_SCALE * (0.72 + Math.random() * 0.62)).toFixed(3) + 'rem';
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
        darty: !!DARTY[species], calm: !!CALM[species], crab: species === 'crab',
        snail: species === 'snail', lo: band[0], hi: band[1],
        eel: species === 'eel', eelCalm: (species === 'eel' && (REDUCED_MOTION || cfg.minimal)),
        lure: lureEl, huntPos: { x: 0, y: 0 }, huntActive: false,
        bio: bioWeight(species), bioPhase: Math.random() * 6.28, bioT: 0,
        _dtCache: 0, _lit: false,
        x: x, y: y, homeX: x, homeY: y, vx: dir * cruise * 0.6, vy: 0,
        cruise: cruise, maxSpeed: cruise * 2.2, speed: cruise, t: Math.random() * 6.28,
      };
    }

    function clearEntities() {
      tank.querySelectorAll('.fish').forEach(function (n) { n.remove(); });
      entities = [];
    }

    function buildEntities(fish) {
      computeFloor();
      clearEntities();
      entities = fish.map(makeEntity);
      // Bioluminescent night: rebuild the small precomputed lists on roster change.
      trailFish.length = 0; brightSwimmers.length = 0;
      for (var bi = 0; bi < entities.length; bi++) {
        var be = entities[bi];
        if (be.bio >= 0.7) brightSwimmers.push(be);   // luminous twinkle sources
      }
      seedTrails();   // builds the capped trail ring for driftfish/notefish
    }

    // One simulation step for all fish — plus the water itself.
    function step(dt) {
      flowT += dt;                       // the water's clock, once per frame
      eelHuntPos = null;                 // reset the per-frame hunt pointer
      var _glowOn = isGlowPhase() ? GLOW : 0;
      var _witch = _glowOn && lastPhaseObj === PHASES.witching;
      for (var i = 0; i < entities.length; i++) {
        var f = entities[i];

        if (f.anchor) {
          // Landmarks hover in place: gentle drift around home + idle wiggle.
          f.t += dt;
          f.x = f.homeX + Math.sin(f.t * 0.6) * 1.6;
          f.y = f.homeY + Math.sin(f.t * 0.9 + 1) * 1.2;
          f.speed = 2;
          f.el.style.left = f.x + '%';
          f.el.style.top = f.y + '%';
          f.span.style.transform = tailTransform(f, dt, f.nat);
          f._dtCache = dt; updateHalo(f, _glowOn);
          writeReflection(f, f.nat);
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
              if (Math.random() < 0.004) f.pauseT = 2 + Math.random() * 6;  // stop to graze
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
          var onGlass = f.mode !== 'graze';
          f.span.style.transform = onGlass
            ? 'scaleX(' + f.nat + ') rotate(' + (f.wall < 0 ? 90 : -90) + 'deg)'
            : 'scaleX(' + (f.vx >= 0 ? 1 : -1) * f.nat + ')';
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
          if (Math.random() < 0.012) f.vx = -f.vx;            // random direction flips
          var scoot = (Math.sin(f.t * 9) > 0.4) ? 1 : 0.15;    // stepped gait
          f.x += f.vx * scoot * dt;
          ay += seekZone(f, floorPct - 4, floorPct - 1).ay;
          f.vy += ay * dt; f.vy *= 0.9; f.y += f.vy * dt;
          wallTurn(f, 6, dt);
          f.speed = Math.abs(f.vx);
          f.el.style.left = f.x + '%';
          f.el.style.top = f.y + '%';
          f.span.style.transform = 'scaleX(' + (f.vx >= 0 ? 1 : -1) * f.nat + ') rotate(' + (Math.sin(f.t * 9) * 4).toFixed(1) + 'deg)';
          f._dtCache = dt; updateHalo(f, _glowOn);
          continue;
        }

        var w = wander(f, dt); ax += w.ax * 0.5; ay += w.ay * 0.5;

        if (f.school) {
          var neigh = [];
          for (var j = 0; j < entities.length; j++) {
            var o = entities[j];
            if (o !== f && o.school && o.species === f.species) neigh.push(o);
          }
          var b = boids(f, neigh, { sep: 2.2, ali: 1.0, coh: 0.85, sepRadius: 9, neighborRadius: 18 });
          ax += b.ax * 8; ay += b.ay * 8;
        }

        var z = seekZone(f, f.lo, f.hi); ax += z.ax; ay += z.ay;
        var st = startle(f, mouse, 16, 160); ax += st.ax; ay += st.ay;
        // A hunting eel is a second predator: nearby fish flee, the school
        // shatters, then re-coheres via boids once the eel retreats.
        if (eelHuntPos) {
          var es = startle(f, eelHuntPos, 22, 240 * CREATURE_FX);
          ax += es.ax; ay += es.ay;
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
        integrate(f, { ax: ax, ay: ay }, dt, f.maxSpeed * bc, 1.2, f.cruise * bc);
        if (fs.peck) f.wigglePhase = (f.wigglePhase || 0) + 1.1;  // tail-flick
        wallTurn(f, 8, dt);
        if (f.y < 1) { f.y = 1; if (f.vy < 0) f.vy = 0; }
        if (f.y > 96) { f.y = 96; if (f.vy > 0) f.vy = 0; }

        f.speed = Math.sqrt(f.vx * f.vx + f.vy * f.vy);
        var dirSign = f.vx >= 0 ? 1 : -1;
        f.el.style.left = f.x + '%';
        f.el.style.top = f.y + '%';
        f.span.style.transform = tailTransform(f, dt, dirSign * f.nat);
        f._dtCache = dt; updateHalo(f, _glowOn);
        writeReflection(f, dirSign * f.nat);
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
      if (dt > 0) step(dt);
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
        f.span.style.transform = 'scaleX(' + ((f.vx >= 0 ? 1 : -1) * f.nat) + ')';
        if (f.lure) renderLure(f, 0, darkPhase, CREATURE_FX, true);  // static bait light
      }
    }

    function renderFish(fish) {
      var sig = fish.map(function (f) { return (f.species || '') + ':' + (f.glyph || ''); }).sort().join('|');
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
      var chars = d > 0.5 ? ['▒', '▓'] : ['░', '▒'];
      var s = '';
      for (var i = 0; i < 170; i++) s += chars[(i * 7 + Math.round(d * 9)) % chars.length];
      el.textContent = s;
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
      var w = snap.weather || snap;
      var fish = Array.isArray(snap.fish) ? snap.fish : [];
      applyPhase(w.phase || 'night');
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

    async function poll() {
      try {
        var res = await fetch('/tank.json', { cache: 'no-store' });
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
      if (masses.length && Math.random() < 0.4) {
        var m = masses[Math.floor(Math.random() * masses.length)];
        return Math.max(2, Math.min(97, m.x + (Math.random() * 2 - 1) * 6));
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
    function seedDecor() {
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

    function seedWeeds() {
      var layer = $('weeds');
      if (!layer) return;
      var specs = [
        { left: '6%', fronds: 5, dur: '7s', delay: '0s' },
        { left: '14%', fronds: 4, dur: '8.5s', delay: '-2s' },
        { left: '83%', fronds: 5, dur: '7.6s', delay: '-1s' },
        { left: '92%', fronds: 3, dur: '9s', delay: '-3s' },
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
    }
    function onLeave() { mouse = null; }
    function onVisibility() {
      if (document.hidden) stopLoop();
      else if ((entities.length || wideFX) && !REDUCED_MOTION) startLoop();
    }

    function start() {
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
      // Feeding click — standalone only (cfg.minimal => lobby clicks pass
      // through). Attaches under reduced motion too (calm drop, no fish seek).
      if (!cfg.minimal && FEED.on) {
        tank.addEventListener('pointerdown', onTankDown);
      }
      // Schlieren (D9) — whole-scene refraction, default OFF (SCHLIEREN=0).
      if (SCHLIEREN && wideFX && !REDUCED_MOTION) tank.classList.add('schlieren');
      poll();
      timer = setInterval(poll, POLL_MS);
    }

    function stop() {
      if (timer) { clearInterval(timer); timer = null; }
      stopLoop();
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
      _entities: function () { return entities; },
      ripple: spawnRipple,                    // shared surface-event entry
      medium: function () { return motes; },  // shared tracer pool
    };
  }

  // Public API for embeds (e.g. the front-door lobby).
  window.BBTank = { create: createTank };

  // Auto-initialize the standalone /aquarium/ page when its DOM is present.
  // The instance handle is kept on BBTank.page for offline/visual testing.
  if (document.getElementById('tank') && document.getElementById('legend')) {
    window.BBTank.page = createTank({ pollMs: 15000 });
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
    }
    body[data-phase="night"]    { --accent-rgb: 125,167,217; }
    body[data-phase="witching"] { --accent-rgb: 167,139,250; }
    body[data-phase="day"]      { --accent-rgb: 16,185,129; }
    body[data-phase="dawn"]     { --accent-rgb: 224,169,109; }
    body[data-phase="dusk"]     { --accent-rgb: 217,138,90; }

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
        linear-gradient(180deg, transparent 0%, rgba(0,0,0,0.18) 78%, rgba(0,0,0,0.4) 100%),
        var(--surface);
      box-shadow: inset 0 0 60px rgba(0,0,0,0.35);
    }
    .tank.schlieren { filter: url(#bb-schlieren); }
    @media (prefers-reduced-motion: reduce) { .tank.schlieren { filter: none; } }
    .surface-line {
      position: absolute; top: 14px; left: 0; right: 0;
      text-align: center; font-family: var(--font-mono);
      font-size: 0.78rem; color: var(--phase-accent); opacity: 0.5;
      letter-spacing: 0.45em; pointer-events: none; user-select: none;
      animation: surface-wave 7s ease-in-out infinite alternate;
    }
    @supports (color: color-mix(in srgb, red, blue)) {
      .surface-line {
        color: color-mix(in srgb, var(--phase-accent), #e3645a calc(var(--heat, 0) * 55%));
      }
    }
    @keyframes surface-wave {
      from { transform: translateX(-6px); opacity: 0.4; }
      to   { transform: translateX(6px);  opacity: 0.6; }
    }
    .fish {
      position: absolute;
      font-family: var(--font-mono);
      font-size: 0.8rem;
      color: var(--phase-fish);
      white-space: pre;
      will-change: left, top;
      text-shadow: 0 0 8px rgba(0,0,0,0.5);
    }
    .fish span {
      display: inline-block;
      transform-origin: center center;
      will-change: transform;
    }

    .decor { position: absolute; inset: 0; pointer-events: none; }
    .structure {
      position: absolute; bottom: 20px; font-family: var(--font-mono);
      font-size: 0.9rem; line-height: 0.95; white-space: pre; color: #7a6a55;
      opacity: var(--d-opacity, 0.18); font-variant-ligatures: none;
      transform: translateX(-50%);
    }
    .plant {
      position: absolute; bottom: 20px; font-family: var(--font-mono);
      font-size: 1rem; line-height: 0.82; white-space: pre; color: var(--primary);
      opacity: 0.3; font-variant-ligatures: none; transform-origin: bottom center;
      animation: sway var(--sway-dur, 8s) ease-in-out infinite alternate;
    }
    .plant.epi {
      position: absolute; top: 26%; bottom: auto;
      transform-origin: top center; line-height: 0.82; white-space: pre;
    }

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
      opacity: calc(0.08 + var(--flow, 0) * 0.14);
      transition: opacity 2s ease;
    }
    .fish.night-fish { color: var(--accent); opacity: 0.92; filter: drop-shadow(0 0 6px rgba(139,92,246,0.5)); }
    .fish.shipfish { color: var(--primary); }
    .fish-name {
      position: absolute; top: 100%; left: 50%; transform: translateX(-50%);
      margin-top: 2px; font-size: 0.58rem; line-height: 1; white-space: nowrap;
      color: var(--phase-accent); opacity: 0.75; pointer-events: none;
      text-shadow: 0 0 4px rgba(0,0,0,0.7); font-variant-ligatures: none;
    }

    .fish.eel { color: #8fb89a; white-space: pre; }
    body[data-phase="night"] .fish.eel,
    body[data-phase="witching"] .fish.eel { color: #7fb0a0; }

    .fish.anglerfish .lure {
      display: inline-block; font-family: var(--font-mono); color: #fff;
      pointer-events: none; transition: opacity 1.5s ease;
    }

    .fish-reflect {
      position: absolute; left: 0; top: 0;
      display: inline-block; transform-origin: center center;
      color: var(--phase-fish); opacity: 0; pointer-events: none;
      filter: blur(0.4px); will-change: transform, opacity;
      font-variant-ligatures: none;
    }

    .trail-pt {
      position: absolute; width: 3px; height: 3px; margin: -1.5px 0 0 -1.5px;
      border-radius: 50%; pointer-events: none; opacity: 0;
      background: rgba(var(--accent-rgb), 0.9);
      box-shadow: 0 0 5px rgba(var(--accent-rgb), 0.8);
      will-change: opacity, left, top;
    }

    .mote.plankton {
      color: #c4b6ff;
      text-shadow: 0 0 4px rgba(167,139,250,0.7), 0 0 8px rgba(167,139,250,0.4);
    }

    .streak {
      position: absolute; pointer-events: none; user-select: none;
      font-family: var(--font-mono); font-size: 0.6rem; line-height: 1;
      color: var(--phase-accent);
      text-shadow: 0 0 5px var(--phase-accent);
      white-space: pre; transform-origin: center center;
      opacity: 0; will-change: left, top, opacity, transform;
      font-variant-ligatures: none;
    }

    .ripple {
      position: absolute; top: 22px;
      pointer-events: none; transform: translate(-50%, -50%);
      border: 1px solid var(--phase-accent); border-radius: 50%;
      opacity: 0; will-change: width, height, opacity;
      mix-blend-mode: screen;
    }

    .flake {
      position: absolute; pointer-events: none; user-select: none;
      font-family: var(--font-mono); color: var(--phase-accent); opacity: 0;
      text-shadow: 0 0 4px var(--phase-glow);
      transition: opacity 0.4s ease; will-change: left, top, opacity;
      transform: translate(-50%, -50%); font-variant-ligatures: none;
    }
    body[data-phase="night"] .flake   { text-shadow: 0 0 6px rgba(125,167,217,0.5); }
    body[data-phase="witching"] .flake { text-shadow: 0 0 8px var(--phase-accent); }

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
    body[data-phase="night"]    .fish { text-shadow: 0 0 2px rgba(0,0,0,0.55); }
    body[data-phase="witching"] .fish { text-shadow: 0 0 2px rgba(0,0,0,0.6); }

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
    .rays {
      position: absolute; inset: 0; pointer-events: none;
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
    [data-phase="witching"] .rays {
      background: repeating-linear-gradient(90deg,
        transparent 0 200px, var(--phase-accent) 200px 203px, transparent 203px 420px);
      animation: witch-breathe 9s ease-in-out infinite alternate;
    }
    @keyframes witch-breathe { from { opacity: 0.03; } to { opacity: 0.08; } }

    .depth {
      position: absolute; inset: 0; pointer-events: none;
      mix-blend-mode: multiply;
      background: linear-gradient(180deg,
        transparent 0%, transparent var(--murk-start, 55%),
        var(--depth-tint, rgba(16,24,46,0.5)) 100%);
      transition: opacity 2s ease;
    }

    .weeds { position: absolute; inset: 0; pointer-events: none; }
    .weed {
      position: absolute; bottom: 22px;
      font-family: var(--font-mono); font-size: 1.1rem; line-height: 0.85;
      color: var(--primary); opacity: 0.22; white-space: pre;
      transform-origin: bottom center;
      animation: sway var(--sway-dur, 8s) ease-in-out infinite alternate;
    }
    @keyframes sway {
      from { transform: rotate(calc(var(--sway-amp, 5deg) * -1)) skewX(3deg); }
      to   { transform: rotate(var(--sway-amp, 5deg)) skewX(-3deg); }
    }

    .silt {
      position: absolute; bottom: 0; left: 0; right: 0; height: 26px;
      font-family: var(--font-mono); font-size: 0.9rem; line-height: 26px;
      color: rgba(160,160,165,0.22); white-space: nowrap; overflow: hidden;
      user-select: none; pointer-events: none;
    }
    .fossils {
      position: absolute; bottom: 2px; left: 6px; right: 6px;
      font-family: var(--font-mono); font-size: 0.8rem;
      color: rgba(160,160,165,0.4); white-space: nowrap; overflow: hidden;
      user-select: none; pointer-events: none;
    }

    .caustic {
      position: absolute; inset: 0; pointer-events: none;
      opacity: calc(0.3 + var(--flow, 0) * 0.25);
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
    .bubble.live { animation: none; bottom: auto; }
    .mote {
      position: absolute; pointer-events: none; user-select: none;
      font-family: var(--font-mono); font-size: 0.7rem;
      color: var(--phase-fish); opacity: 0;
      transition: opacity 3s ease;
    }

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
    .explainer b { color: var(--text-secondary); font-weight: 600; }
    .explainer code { font-family: var(--font-mono); color: var(--primary); font-size: 0.8rem; }

    .home { display: inline-block; margin-bottom: 1.5rem; font-family: var(--font-mono);
            font-size: 0.8rem; color: var(--text-secondary); text-decoration: none; }
    .home:hover { color: var(--text); }

    noscript pre {
      display: block; margin-top: 1rem; font-family: var(--font-mono);
      font-size: 13px; line-height: 1.2; white-space: pre; color: var(--text-secondary);
      font-variant-ligatures: none; font-feature-settings: "liga" 0, "calt" 0;
    }

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
      .streak, .ripple, .fish-reflect, .trail-pt, .feed-ripple { animation: none; opacity: 0 !important; }
      .flake { transition: none; }
      .fish.eel, .fish.anglerfish .lure { animation: none; transition: none; }
      .rays { transform: none !important; opacity: var(--ray-opacity, 0.09) !important; }
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
