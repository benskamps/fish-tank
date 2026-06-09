"""tank serve — tiny localhost HTTP for the tank page.

Serves two things, no dependencies:
  GET /tank.json  — the current world as JSON (fish glyphs/species/zone/mood +
                    weather). The animated page polls this.
  GET / or /tank  — a self-contained animated page: fish actually swim, facing
                    their travel direction (no moonwalking), with a gentle tail
                    wiggle. A <noscript> block keeps the static ASCII tank for
                    JS-off viewers, so the terminal soul survives either way.

Bare-bones on purpose: just swimming + correct facing + light motion. The fuller
boids/decor/startle build lives on the author's site, not in the core tool.
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from tank import paths
from tank.render.frame import compose, phase_bg, render
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
    """The current world as a render-ready dict for the animated page."""
    world = world_from_json(paths.world_path().read_text(encoding="utf-8"))
    w = world.weather
    return {
        "phase": w.phase,
        "temperature_c": w.temperature_c,
        "current_strength": w.current_strength,
        "silt_density": w.silt_density,
        "light_level": w.light_level,
        "mood": w.mood,
        "fish": [
            {
                "species": f.species,
                "glyph": f.glyph,
                "zone": getattr(f, "zone", "mid"),
                "mood": getattr(f, "mood", "calm"),
            }
            for f in world.fish
        ],
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
                    payload = {"phase": "night", "fish": [], "mood": "warming up"}
                self._send(json.dumps(payload).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if self.path not in ("/", "/tank"):
                self.send_error(404)
                return
            bg = phase_bg("night")
            noscript = "<pre>tank: no world yet — run `tank tick`</pre>"
            try:
                world = world_from_json(
                    paths.world_path().read_text(encoding="utf-8"))
                noscript = render(compose(world), style="html")
                bg = phase_bg(world.weather.phase)
            except Exception as e:
                noscript = f"<pre>tank: no world yet — {e}</pre>"
            page = _PAGE.replace("__BG__", bg).replace("__NOSCRIPT__", noscript)
            self._send(page.encode("utf-8"), "text/html; charset=utf-8")

        def log_message(self, *args, **kwargs):
            pass

    return TankHandler


# The animated client. Kept as a plain (non-f) string so the JS/CSS braces need
# no escaping. Self-contained, dependency-free, polls /tank.json every 15s.
_JS = r"""
(function () {
  'use strict';
  var T = document.getElementById('tank');
  if (!T) return;
  var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Drawn direction per glyph: +1 faces right, -1 faces left (verified by eye).
  // A fish faces its travel via scaleX = sign(vx) * natFace, so it never
  // swims backwards no matter which way it's sent.
  var FACING = {
    '>°))<': 1, '<°))>': -1, '>o))<': 1,
    '<·><': -1, '><·>': 1,
    '=>°)>': 1, '<(°<=': -1, '=>o)>': 1,
    '^v^>': 1, '<^v^': -1,
    '><≈>': 1, '<≈><': -1,
    '@_': 1, '_@': -1,
    '<#=>': 1, '<=#>': -1,
    '><((°>': 1, '<°)))><': -1, '{·_·}>': 1,
    '<*°)>': 1, '<(*°)<': -1,
    '>~~~>': 1, '~~>>>': 1, '>.>': 1,
    '>≈≈≈>': 1, '<≈≈<': -1,
    '><x>': 1, '><X>': 1,
    '><((((°>': 1, '<°))))><': -1,
    '<°)F)><': -1,
    '><o>': 1, '<o><': -1,
    '<°)W><': -1,
    '~(o_o)~': 1, '·(u_u)·': 1, '°<))°<': -1, '>°))°<': 1
  };
  function natFace(g) {
    if (Object.prototype.hasOwnProperty.call(FACING, g)) return FACING[g];
    if (!g) return 1;
    var e = g.search(/[°o]/);
    if (e >= 0) return e < g.length / 2 ? -1 : 1;
    var l = g.indexOf('<'), r = g.lastIndexOf('>');
    if (l < 0 && r < 0) return 1;
    return r > l ? 1 : -1;
  }

  var ZONE = { surface: [8, 26], mid: [30, 60], bottom: [64, 82] };
  // Seconds to cross the tank, per species — a creature's true pace.
  var CROSS = {
    snail: 150, pleco: 95, cleanershrimp: 85, crab: 22,
    crashstrider: 12, killifish: 13, thermalwisp: 13, emberlung: 14, hatchetfish: 16,
    coldfin: 30, frostneon: 28, 'night-fish': 24,
    guppy: 22, tetra: 20, rummynose: 21, driftfish: 20
  };
  var ANCHOR = { shipfish: 1, founderfish: 1, notefish: 1 };
  var PH = { dawn: '#1b1712', day: '#12161a', dusk: '#1a130e', night: '#0a0a0b', witching: '#140b1a' };
  // Phase -> bioluminescent accent. Only night/witching glow; the lit phases
  // map to null so the halo clears and fish fall back to their base shadow.
  var PHA = { dawn: null, day: null, dusk: null, night: '125,167,217', witching: '167,139,250' };

  var ents = [], sig = null, raf = null, last = 0, phase = 'night';

  // --- living water: a tiny pooled set of drifting motes ----------------
  // Faint particles that slide sideways + sink and wrap, so the tank is never
  // dead even when idle. Simple linear drift only — no flow field, no rays.
  var MOTES = 10, motes = [];
  function buildMotes() {
    if (motes.length) return;
    var med = document.createElement('div');
    med.className = 'medium'; med.setAttribute('aria-hidden', 'true');
    for (var i = 0; i < MOTES; i++) {
      var m = document.createElement('span');
      m.textContent = i % 3 === 0 ? '·' : '.';
      med.appendChild(m);
      motes.push({
        el: m,
        x: Math.random() * 100, y: Math.random() * 100,
        vx: 1.1 + Math.random() * 1.8,            // slow sideways slide (%/s)
        vy: 0.7 + Math.random() * 1.0,            // gentle sink (%/s)
        op: 0.10 + Math.random() * 0.14
      });
    }
    T.appendChild(med);
    for (var j = 0; j < motes.length; j++) {
      motes[j].el.style.left = motes[j].x + '%';
      motes[j].el.style.top = motes[j].y + '%';
      motes[j].el.style.opacity = motes[j].op;
    }
  }
  function stepMotes(dt) {
    for (var i = 0; i < motes.length; i++) {
      var m = motes[i];
      m.x += m.vx * dt; m.y += m.vy * dt;
      if (m.x > 102) { m.x = -2; m.y = Math.random() * 100; }
      if (m.y > 102) { m.y = -2; m.x = Math.random() * 100; }
      m.el.style.left = m.x + '%'; m.el.style.top = m.y + '%';
    }
  }

  // --- a small clear aquascape ----------------------------------------
  // 2-3 ground objects + a couple of plant stems on the floor, drawn ONLY
  // with glyphs that hold 1 cell in the page mono stack (full/half blocks,
  // light shades, light box verticals/horizontals, a plain circle). No arcs,
  // no diagonals, no heavy/mixed box, no quadrants — those shear under pre.
  var SCAPE = false;
  function buildScape() {
    if (SCAPE) return; SCAPE = true;
    var floor = document.createElement('div');
    floor.className = 'scape'; floor.setAttribute('aria-hidden', 'true');
    // each entry: [art, left%, a soft tint]
    var props = [
      // a small plant: two light stems
      ['│\n│\n│', 14, '#5e8f6a'],
      // a low block rock mound
      ['  ░░\n ▒▒▒░\n▓▓▒▒▒░', 30, '#8a8f99'],
      // a small chest: lid, body, a round latch
      ['────\n│●│\n████', 52, '#9a7a4a'],
      // a taller reed cluster
      ['│ │\n│ │\n│││', 74, '#5e8f6a']
    ];
    for (var i = 0; i < props.length; i++) {
      var p = document.createElement('pre');
      p.textContent = props[i][0];
      p.style.left = props[i][1] + '%';
      p.style.color = props[i][2];
      floor.appendChild(p);
    }
    T.appendChild(floor);
  }

  // --- bioluminescent night glow --------------------------------------
  // Write the halo INLINE on the span (inline wins specificity, no stylesheet
  // fight). Night/witching only; lit phases clear it to '' so the base .f
  // legibility shadow takes over (never 'none'). A subtle breathing pulse
  // reuses the fish's own phase seed; reduced-motion gets a static dim halo.
  function applyGlow(f) {
    var accent = PHA[phase];
    if (!accent) { f.sp.style.textShadow = ''; return; }
    var blur, alpha;
    if (reduced) { blur = 6; alpha = 0.55; }
    else {
      var pulse = 0.5 + 0.5 * Math.sin(f.wp * 0.6 + f.t);  // 0..1, gentle breath
      blur = 5 + pulse * 4;                                 // 5..9 px
      alpha = 0.45 + pulse * 0.30;                          // 0.45..0.75
    }
    f.sp.style.textShadow =
      '0 0 ' + blur.toFixed(1) + 'px rgba(' + accent + ',' + alpha.toFixed(2) + ')';
  }

  function build(fish) {
    var nodes = T.querySelectorAll('.f');
    for (var i = 0; i < nodes.length; i++) nodes[i].remove();
    ents = fish.map(function (f, i) {
      var z = ZONE[f.zone] ? f.zone : 'mid', b = ZONE[z];
      var cruise = 110 / (CROSS[f.species] || 22);
      var anchor = !!ANCHOR[f.species];
      var el = document.createElement('div');
      el.className = 'f';
      var sp = document.createElement('span');
      sp.textContent = f.glyph || '><>';
      el.appendChild(sp);
      el.style.fontSize = (0.85 * (0.8 + Math.random() * 0.5)).toFixed(2) + 'rem';
      T.appendChild(el);
      var x = anchor ? (8 + (i * 53) % 84) : (5 + Math.random() * 90);
      var y = b[0] + Math.random() * (b[1] - b[0]);
      return {
        el: el, sp: sp, nat: natFace(f.glyph || '><>'), anchor: anchor,
        x: x, y: y, hx: x, hy: y, lo: b[0], hi: b[1],
        vx: (i % 2 ? -1 : 1) * cruise, wp: Math.random() * 6.28, t: Math.random() * 6.28
      };
    });
  }

  function step(dt) {
    stepMotes(dt);
    for (var i = 0; i < ents.length; i++) {
      var f = ents[i];
      if (f.anchor) {                                   // landmarks hover in place
        f.t += dt;
        f.x = f.hx + Math.sin(f.t * 0.6) * 1.4;
        f.y = f.hy + Math.sin(f.t * 0.9) * 1.0;
        f.el.style.left = f.x + '%'; f.el.style.top = f.y + '%';
        f.sp.style.transform = 'scaleX(' + f.nat + ') rotate(' + (2 * Math.sin(f.t * 3)).toFixed(1) + 'deg)';
        applyGlow(f);
        continue;
      }
      f.x += f.vx * dt;
      if (f.x < 4 && f.vx < 0) f.vx = -f.vx;
      if (f.x > 96 && f.vx > 0) f.vx = -f.vx;
      f.t += dt;
      f.y = f.hy + Math.sin(f.t * 0.8) * ((f.hi - f.lo) * 0.18);   // gentle bob
      var spd = Math.abs(f.vx);
      f.wp += (2 + spd * 0.4) * dt;
      var rot = (3 * Math.sin(f.wp)).toFixed(1);
      var sx = (f.vx >= 0 ? 1 : -1) * f.nat;
      f.el.style.left = f.x + '%'; f.el.style.top = f.y + '%';
      f.sp.style.transform = 'scaleX(' + sx + ') rotate(' + rot + 'deg)';
      applyGlow(f);
    }
  }

  function loop(ts) {
    if (!last) last = ts;
    var dt = (ts - last) / 1000; last = ts;
    if (dt > 0.05) dt = 0.05;
    if (dt > 0) step(dt);
    raf = window.requestAnimationFrame(loop);
  }

  function placeStatic() {
    for (var i = 0; i < ents.length; i++) {
      var f = ents[i];
      f.el.style.left = f.x + '%'; f.el.style.top = f.y + '%';
      f.sp.style.transform = 'scaleX(' + ((f.vx >= 0 ? 1 : -1) * f.nat) + ')';
      applyGlow(f);
    }
  }

  function reglowAll() {                 // phase changed: refresh halos in place
    for (var i = 0; i < ents.length; i++) applyGlow(ents[i]);
  }

  function poll() {
    fetch('/tank.json', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) {
      document.body.style.background = PH[d.phase] || '#0a0a0b';
      buildScape(); buildMotes();
      var phaseChanged = (d.phase || 'night') !== phase;
      phase = d.phase || 'night';
      var fish = d.fish || [];
      var s = fish.map(function (f) { return f.species + ':' + f.glyph; }).join('|');
      if (s !== sig || ents.length !== fish.length) {
        build(fish); sig = s;
        if (reduced) placeStatic();
        else if (raf == null) { last = 0; raf = window.requestAnimationFrame(loop); }
      } else if (phaseChanged && reduced) {
        reglowAll();   // static mode won't re-run step; refresh halos here
      }
      var st = document.getElementById('stat');
      if (st) {
        st.textContent = fish.length + ' fish'
          + (d.mood ? ' · feels ' + d.mood : '')
          + (d.temperature_c != null ? ' · ' + Math.round(d.temperature_c) + '°C' : '');
      }
    }).catch(function () {
      var st = document.getElementById('stat');
      if (st) st.textContent = 'tank unreachable…';
    });
  }

  poll();
  setInterval(poll, 15000);
})();
"""

_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>tank</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: __BG__; color: #cbd5e1;
    font-family: "Cascadia Mono", "JetBrains Mono", "Consolas", monospace;
    transition: background 2s ease; min-height: 100vh;
    display: flex; flex-direction: column; align-items: center; padding: 24px;
  }
  h1 { font-size: 0.95rem; font-weight: 600; opacity: 0.7; margin-bottom: 10px; letter-spacing: 0.02em; }
  #tank {
    position: relative; width: 100%; max-width: 720px; height: 360px;
    border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; overflow: hidden;
    background: linear-gradient(180deg, transparent 0%, rgba(0,0,0,0.25) 100%);
  }
  .f {
    position: absolute; white-space: pre; color: #aeb9c9; z-index: 1;
    text-shadow: 0 0 7px rgba(0,0,0,0.5);
    font-variant-ligatures: none; font-feature-settings: "liga" 0, "calt" 0;
    will-change: left, top;
  }
  .f span { display: inline-block; transform-origin: center center; will-change: transform; }
  /* ambient drifting medium — faint motes so the water is never still */
  .medium { position: absolute; inset: 0; pointer-events: none; z-index: 0; }
  .medium span {
    position: absolute; color: #9fb4cf; font-size: 0.7rem;
    will-change: left, top; user-select: none;
  }
  /* a small clear aquascape resting on the floor */
  .scape { position: absolute; inset: 0; pointer-events: none; z-index: 0; }
  .scape pre {
    position: absolute; bottom: 4px; margin: 0;
    font-family: inherit; font-size: 0.8rem; line-height: 1.0;
    white-space: pre; opacity: 0.5;
    font-variant-ligatures: none; font-feature-settings: "liga" 0, "calt" 0;
  }
  #stat { margin-top: 10px; font-size: 0.78rem; opacity: 0.6; }
  noscript pre { font-size: 13px; line-height: 1.2; white-space: pre; color: #cbd5e1; }
  a { color: #7da7d9; }
</style>
</head><body>
  <h1>~ tank</h1>
  <div id="tank" aria-label="aquarium"></div>
  <div id="stat">connecting…</div>
  <noscript>__NOSCRIPT__</noscript>
  <script>__JS__</script>
</body></html>
""".replace("__JS__", _JS)
