"""Piano Hero: las notas bajan por el carril de cada tecla real (keys.json) y se tocan en el Korg EK-50.

Carril = polígono exacto de la tecla prolongado hacia atrás (hacia el Buda). La nota llega a la línea de las teclas a
su hora; si se toca a tiempo, la tecla entera se ilumina con el color de la precisión (verde perfecto, lima bien,
ámbar ok) y la nota sube y se desvanece; si no, rojo. HUD con puntaje, combo, precisión y progreso sobre la hoja
blanca (o la torre); al final, resumen con estrellas. Lee las teclas tocadas de ``scene.inputs`` (eventos del
dispositivo ``ek50``: puente UDP desde el PC o MIDI local). La lógica vive en ``playground/pianohero`` y el estado
del juego sobrevive al hot reload de este archivo.

Dibuja en píxeles del proyector (keys.json), no en mm: el lienzo del runtime es 1920x1080 = el proyector.
"""

from __future__ import annotations

import math
import time
import unicodedata
import uuid

import cv2
import numpy as np

from .base import Effect, Canvas, bgr
from .. import pianohero as ph
from ..pianohero.game import Game
from ..pianohero.layout import from_scene, load_layout, scene_keys
from ..pianohero.songs import LEVELS, get_song, list_songs

HANDS = {"R": ([70, 170, 255], [40, 90, 230]), "L": ([255, 110, 220], [200, 50, 170])}   # (blanca, negra) en RGB
JUDGE = {"perfecto": [60, 255, 120], "bien": [190, 255, 50], "ok": [255, 190, 0], "fallo": [255, 30, 30],
         "error": [255, 30, 30]}
LABEL = {"perfecto": "PERFECTO", "bien": "BIEN", "ok": "OK", "fallo": "FALLO", "error": "ERROR"}
FONT = cv2.FONT_HERSHEY_DUPLEX


def ascii_(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()


def _scale(rgb, k: float) -> tuple[int, int, int]:
    b, g, r = bgr(rgb)
    k = max(0.0, min(1.0, k))
    return (int(b * k), int(g * k), int(r * k))


def _poly(img, pts, color, fill=True, thick=2):
    p = np.round(np.asarray(pts, float) * 4).astype(np.int32).reshape(-1, 1, 2)
    if fill:
        cv2.fillPoly(img, [p], color, cv2.LINE_AA, 2)
    else:
        cv2.polylines(img, [p], True, color, thick, cv2.LINE_AA, 2)


def _text(img, s, org, scale, color, thick=2, center=False):
    s = ascii_(s)
    (tw, th), _ = cv2.getTextSize(s, FONT, scale, thick)
    x, y = org
    if center:
        x -= tw / 2
    cv2.putText(img, s, (int(x), int(y)), FONT, scale, color, thick, cv2.LINE_AA)
    return tw, th


def _star(img, c, r, color, fill):
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((c[0] + rr * math.cos(a), c[1] + rr * math.sin(a)))
    _poly(img, pts, color, fill=fill, thick=2)


class PianoHero(Effect):
    name = "pianohero"
    label = "Piano Hero"
    description = ("Notas que bajan por el carril de cada tecla del Korg EK-50 hacia la línea de las teclas; acierto = la "
                   "tecla se ilumina con el color de la precisión, fallo = rojo. 3 niveles, práctica, HUD con puntaje/"
                   "combo/precisión/progreso y resumen final. Teclas: las de la escena (objeto keys, extra.keys) o "
                   "keys.json. demo=true se toca solo. cmd = start|pause|resume|toggle|restart. También pg_pianohero.")
    needs = ("geometry", "controls")
    targets = ("*",)
    params = {"level": 1, "song": "oda", "practice": False, "keys": "", "offset_ms": 0.0, "speed": 1.0,
              "lane_px": 0.0, "hud": "auto", "device": "ek50", "autostart": False, "demo": False, "brightness": 1.0}
    # keys: "" = la escena si trae teclas (scene.objects["keys"].extra["keys"]), si no keys.json; "scene"; o una ruta.
    # `_live` (lo agrega la instancia): ficha del juego vivo; viaja en los params cuando el hot reload recrea la instancia
    REBUILD = ("level", "song", "practice", "speed")
    RELAYOUT = ("keys", "lane_px", "hud")
    DEMO_SKIP = 12                                    # en demo deja pasar 1 de cada 12 notas (para que se vean fallos)

    def __init__(self, target, params=None, instance_id=""):
        super().__init__(target, params, instance_id)
        self.now = time.time()
        self._scaled: dict = {}
        self.layout = None
        self._scene_sig = None
        self._ctl_t: dict[str, float] = {}
        self.p.setdefault("_live", uuid.uuid4().hex[:12])
        self._build(reuse=True)
        if self._keys_src() not in ("", "scene"):
            self._set_layout(self._file_layout())

    # ------------------------------------------------------------------ construcción
    def _firma(self) -> tuple:
        return tuple(str(self.p.get(k)) for k in self.REBUILD)

    def _keys_src(self) -> str:
        return str(self.p.get("keys") or "").strip()

    def _lane(self):
        return float(self.p.get("lane_px") or 0) or None

    def _file_layout(self):
        src = self._keys_src()
        k = ("file", "" if src == "scene" else src, self._lane() or 0.0, str(self.p.get("hud")))
        if k not in ph.LAYOUTS:
            ph.LAYOUTS[k] = load_layout(None if src in ("", "scene") else src, lane_px=self._lane(), hud=self.p.get("hud") or "auto")
        return ph.LAYOUTS[k]

    def _set_layout(self, lay) -> None:
        self.layout = lay
        self._scaled = {}

    def _scene_layout(self, scene) -> bool:
        """Teclas desde la escena (si las trae). Rehace el layout si cambiaron (nudge, reload_scene). True si las usa."""
        objs = getattr(scene, "objects", None) or {}
        found = scene_keys(objs)
        if found is None:
            return False
        oid, ks = found
        sig = (oid, len(ks), tuple(map(tuple, ks[0].get("poligono", []))), tuple(map(tuple, ks[-1].get("poligono", []))),
               self._lane(), str(self.p.get("hud")))
        if sig != self._scene_sig or self.layout is None:
            self._scene_sig = sig
            lay = from_scene(objs, self._lane(), self.p.get("hud") or "auto")
            if lay is not None:
                self._set_layout(lay)
        return True

    def _build(self, reuse: bool = False) -> None:
        vivo = ph.LIVE.get(self.p["_live"])
        if reuse and vivo and vivo[0] == self._firma():
            self.game = vivo[1]
        else:
            lv = int(self.p.get("level", 1))
            if lv not in LEVELS:
                raise ValueError(f"nivel {lv}: hay 1, 2 y 3")
            self.game = Game(get_song(str(self.p.get("song") or "oda")), lv, bool(self.p.get("practice")),
                             float(self.p.get("offset_ms") or 0), float(self.p.get("speed") or 1.0))
            if self.p.get("autostart") or self.p.get("demo"):
                self.game.start(time.time())
        self.game.offset = float(self.p.get("offset_ms") or 0) / 1000.0
        ph.LIVE[self.p["_live"]] = (self._firma(), self.game)

    def set(self, key, value):
        if key == "cmd":                              # pg_set_param <id> cmd '"pause"' en cualquier runtime
            self.control(str(value))
            return
        super().set(key, value)
        if key in self.REBUILD:
            if key == "level":
                self.p["level"] = int(value)
            self._build()
        elif key in self.RELAYOUT:
            self._scene_sig = None
            self.layout = None if self._keys_src() in ("", "scene") else self._file_layout()
            self._scaled = {}
        elif key == "offset_ms":
            self.game.offset = float(value) / 1000.0
        elif key == "demo" and value and self.game.state in ("ready", "finished"):
            self.game.restart(time.time())

    def kill(self):
        super().kill()
        ph.LIVE.pop(self.p.get("_live"), None)

    # ------------------------------------------------------------------ control (comando pianohero del runtime)
    def control(self, action: str, args: dict | None = None) -> dict:
        a = args or {}
        now = time.time()
        changed = {k: a[k] for k in self.REBUILD + self.RELAYOUT
                   if k in a and a[k] not in (None, "") and str(a[k]) != str(self.p.get(k))}
        for k, v in changed.items():
            self.set(k, int(v) if k == "level" else v)
        if "demo" in a and a["demo"] is not None:
            self.p["demo"] = bool(a["demo"])
        if "offset_ms" in a and a["offset_ms"] is not None:
            self.set("offset_ms", float(a["offset_ms"]))
        g = self.game
        if action in ("start", "play"):
            g.start(now)
        elif action == "pause":
            g.pause(now)
        elif action == "resume":
            g.resume(now)
        elif action == "toggle":
            g.toggle(now)
        elif action == "restart":
            g.restart(now)
        elif action == "songs":
            return {"songs": list_songs(), "levels": {n: L.name for n, L in LEVELS.items()}}
        return self.status()

    def status(self) -> dict:
        s = self.game.status()
        s["demo"] = bool(self.p.get("demo"))
        s["layout"] = (self.layout.to_json() if self.layout is not None
                       else {"source": "pendiente (escena o keys.json en el próximo cuadro)"})
        if self.game.state == "finished":
            s["summary"] = self.game.summary()
        return s

    def state(self) -> dict:
        d = super().state()
        d["game"] = {k: v for k, v in self.game.status().items()
                     if k in ("state", "title", "level", "practice", "score", "combo", "accuracy", "progress")}
        return d

    # ------------------------------------------------------------------ cuadro
    def _events(self, scene) -> list[tuple[str, int, float]]:
        """Teclas tocadas desde el cuadro anterior: scene.inputs (con hora de llegada) o, en un runtime sin inputs,
        los cambios de scene.controls (último evento por tecla)."""
        dev = self.p.get("device")
        evs = []
        inputs = getattr(scene, "inputs", None)
        if inputs is not None:
            for ev in inputs:
                c = str(ev.get("control", ""))
                if ev.get("device") != dev or not c.startswith("note"):
                    continue
                try:
                    midi = int(c[4:])
                except ValueError:
                    continue
                evs.append(("on" if ev.get("state") == "down" else "off", midi, float(ev.get("t") or self.now)))
            return evs
        for cid, c in ((getattr(scene, "controls", None) or {}).get(dev) or {}).items():
            t = float(c.get("t") or 0)
            if not str(cid).startswith("note") or t <= self._ctl_t.get(cid, 0):
                continue
            self._ctl_t[cid] = t
            try:
                midi = int(str(cid)[4:])
            except ValueError:
                continue
            evs.append(("on" if c.get("state") in ("down", "on") else "off", midi, t))
        return evs

    def _demo(self, now: float) -> list[tuple[str, int, float]]:
        """demo: toca cada nota a su hora (y suelta 0,12 s después); deja pasar 1 de cada DEMO_SKIP."""
        g = self.game
        if g.state in ("ready", "finished"):
            g.restart(now)
        if g.state != "playing":
            return []
        ts = g.song_time_at(now) - g.offset
        evs = []
        for n in g.notes[g._first_pending:g._first_pending + 32]:
            if n.t > ts:
                break
            if n.state == "pending" and n.i % self.DEMO_SKIP != self.DEMO_SKIP - 1 and ts - n.t < g.level.windows[0]:
                evs.append(("on", n.midi, g.song_time_to_wall(n.t + g.offset, now)))
        for m, t_on in list(g.held.items()):
            if now - t_on > 0.12:
                evs.append(("off", m, now))
        return evs

    def update(self, dt, scene):
        super().update(dt, scene)
        self.now = time.time()
        if self._keys_src() in ("", "scene") and not self._scene_layout(scene) and self.layout is None:
            self._set_layout(self._file_layout())
        evs = self._events(scene)
        g = self.game
        if self.p.get("demo"):
            evs = [e for e in evs if e[0] == "off"] + self._demo(self.now)
        elif g.state in ("ready", "finished") and any(k == "on" for k, _, _ in evs):
            first = min(w for k, _, w in evs if k == "on")
            if g.state == "finished":
                g.reset()
            g.start(first)                       # tocar cualquier tecla empieza (esa tecla no se juzga)
            evs = [e for e in evs if e[2] > first or e[0] == "off"]
        g.update(self.now, evs)

    def draw(self, canvas: Canvas):
        img = canvas.img
        if self.layout is None:
            return
        if canvas.size not in self._scaled:
            self._scaled[canvas.size] = self.layout.scaled(canvas.size)
        lay = self._scaled[canvas.size]
        g = self.game
        now = self.now
        br = float(self.p.get("brightness", 1.0))
        L = lay.lane_px
        lead = g.lead
        notas_cancion = {n.midi for n in g.notes}

        def s_of(t):
            return (t - g.song_t) / lead * L

        # 1. carriles de las teclas que usa la canción (tenues)
        for m in sorted(notas_cancion, key=lambda m: lay.keys[m].black if m in lay.keys else False):
            k = lay.keys.get(m)
            if k is None:
                continue
            col = HANDS["R"][1] if k.black else HANDS["R"][0]
            _poly(img, k.lane_quad(0, L, 0.08), _scale(col, 0.07 * br))

        # 2. líneas de compás
        for tb in g.bars:
            s = s_of(tb)
            if 0 < s < L:
                pts = np.round(lay.bar_line(s) * 4).astype(np.int32).reshape(-1, 1, 2)
                cv2.polylines(img, [pts], False, _scale([255, 255, 255], 0.22 * br), 1, cv2.LINE_AA, 2)

        # 3. notas: blancas primero, negras encima
        vis = g.visible(lead, behind=1.0)
        for black_pass in (False, True):
            for n in vis:
                k = lay.keys.get(n.midi)
                if k is None or k.black != black_pass:
                    continue
                cw, cb = HANDS.get(n.hand, HANDS["R"])
                col = cb if k.black else cw
                inset = 0.06 if not k.black else 0.0
                h = max(10.0, n.dur / lead * L - 3)
                if n.state == "pending":
                    s0 = max(0.0, s_of(n.t)); s1 = min(L, max(s0 + 10, s_of(n.t) + h))
                    if s0 >= L:
                        continue
                    k_br = 1.0
                    if g.waiting and abs(n.t - g.song_t) < 0.05:
                        k_br = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(now * 2 * math.pi * 2.5))
                    _poly(img, k.lane_quad(s0, s1, inset), _scale(col, k_br * br))
                    if k.black:
                        _poly(img, k.lane_quad(s0, s1, 0.0), (0, 0, 0), fill=False, thick=1)
                elif n.state == "hit":
                    age = now - n.wall_judged
                    if age < 0.6:                                   # sube y se desvanece
                        up = age / 0.6 * L * 0.8
                        s0 = min(L, up); s1 = min(L, up + min(h, L * 0.3))
                        if s1 > s0:
                            _poly(img, k.lane_quad(s0, s1, inset), _scale(JUDGE[n.judge], (1 - age / 0.6) * br))
                elif n.state == "miss":
                    age = now - n.wall_judged
                    if age < 0.5:
                        _poly(img, k.lane_quad(0, min(L, h), inset), _scale(JUDGE["fallo"], (1 - age / 0.5) * br))

        # 4. teclas: pistas de lo que viene, aciertos, fallos
        ultimo: dict[int, dict] = {}
        for fb in g.feedback:
            if now - fb["wall"] < 1.5:
                ultimo[fb["midi"]] = fb
        pista: dict[int, tuple[float, str]] = {}
        for n in vis:
            if n.state == "pending":
                falta = n.t - g.song_t
                if -0.1 < falta < 0.9 and n.midi not in pista:
                    pista[n.midi] = (falta, n.hand)
        for black_pass in (False, True):
            for m, k in lay.keys.items():
                if k.black != black_pass:
                    continue
                forma = k.contour if not k.black else k.poly
                fb = ultimo.get(m)
                lit = 0.0; col = None
                if fb is not None:
                    age = now - fb["wall"]
                    if fb["kind"] == "hit":
                        lit = 1.0 if (m in g.held and g.held[m] >= fb["wall"] - 1e-6) else max(0.0, 1 - age / 0.35)
                        col = JUDGE[fb["judge"]]
                    else:
                        lit = max(0.0, 1 - age / 0.45); col = JUDGE["fallo"]
                if lit > 0.02 and col is not None:
                    _poly(img, forma, _scale(col, lit * br))
                    continue
                if m in pista:
                    falta, hand = pista[m]
                    hc = HANDS.get(hand, HANDS["R"])[0]
                    if g.waiting:
                        k_br = 0.35 + 0.35 * (0.5 + 0.5 * math.sin(now * 2 * math.pi * 2.5))
                        _poly(img, forma, _scale(hc, k_br * br))
                    else:
                        _poly(img, forma, _scale(hc, (0.25 + 0.75 * (1 - max(0.0, falta) / 0.9)) * br), fill=False, thick=2)
                elif m in g.held:
                    _poly(img, forma, _scale([255, 255, 255], 0.5 * br), fill=False, thick=1)

        # 5. línea de las teclas con el progreso
        hl = lay.hit_line()
        seg = np.linalg.norm(np.diff(hl, axis=0), axis=1); total = float(seg.sum()) or 1.0
        cut = g.progress * total
        acc = 0.0; pts_done = [hl[0]]
        for i, d in enumerate(seg):
            if acc + d <= cut:
                pts_done.append(hl[i + 1]); acc += d
            else:
                f = (cut - acc) / d if d > 0 else 0
                pts_done.append(hl[i] + (hl[i + 1] - hl[i]) * f); break
        cv2.polylines(img, [np.round(hl * 4).astype(np.int32).reshape(-1, 1, 2)], False, _scale([200, 200, 200], 0.6 * br), 2, cv2.LINE_AA, 2)
        if len(pts_done) > 1:
            cv2.polylines(img, [np.round(np.array(pts_done) * 4).astype(np.int32).reshape(-1, 1, 2)], False,
                          _scale([0, 220, 255], br), 3, cv2.LINE_AA, 2)

        # 6. juicio del último toque, sobre su carril
        if g.feedback:
            fb = g.feedback[-1]
            age = now - fb["wall"]
            k = lay.keys.get(fb["midi"])
            if age < 0.7 and k is not None:
                p = k.at(L * 0.45, 0.5)
                sc = max(0.5, lay.size[0] / 1920 * 0.9)
                _text(img, LABEL.get(fb["judge"], ""), (p[0], p[1]), sc, _scale(JUDGE.get(fb["judge"], [255, 255, 255]), (1 - age / 0.7) * br), 2, center=True)
                if fb["kind"] == "hit" and g.combo >= 5:
                    _text(img, f"x{g.combo}", (p[0], p[1] + 30 * sc), sc * 0.8, _scale([255, 255, 255], (1 - age / 0.7) * br), 2, center=True)

        # 7. HUD
        self._hud(img, lay, g, now, br)

    def _hud(self, img, lay, g, now, br):
        x, y, w, h = lay.hud
        if w < 20 or h < 20:
            return
        sc = max(0.35, w / 330)
        blanco = _scale([255, 255, 255], br)
        cv2.rectangle(img, (int(x), int(y)), (int(x + w), int(y + h)), _scale([120, 120, 140], br), 1, cv2.LINE_AA)
        pad = 10 * sc
        cx = x + w / 2
        nivel = LEVELS[g.level.n].name.upper() + (" PRACTICA" if g.practice else "")
        if g.state == "finished":
            s = g.summary()
            _text(img, "FIN", (cx, y + pad + 34 * sc), 1.3 * sc, _scale([255, 230, 120], br), 3, center=True)
            for i in range(3):
                _star(img, (cx + (i - 1) * 46 * sc, y + pad + 70 * sc), 18 * sc, _scale([255, 210, 0], br), i < g.stars())
            lineas = [g.song.title, nivel, f"{s['score']} pts", f"precision {round(100 * g.accuracy)}%",
                      f"combo max {s['max_combo']}",
                      f"P {s['counts']['perfecto']}  B {s['counts']['bien']}  OK {s['counts']['ok']}",
                      f"fallos {s['counts']['fallo']}  errores {s['wrong']}"]
            if s["mean_dt_ms"] is not None:
                lineas.append(f"promedio {s['mean_dt_ms']:+.0f} ms")
            yy = y + pad + 110 * sc
            for ln in lineas:
                if yy > y + h - 6:
                    break
                _text(img, ln, (cx, yy), 0.55 * sc, blanco, 1, center=True); yy += 24 * sc
            _text(img, "toca una tecla: otra vez", (cx, y + h - 8 * sc), 0.45 * sc, _scale([150, 150, 150], br), 1, center=True)
            # FIN grande sobre los carriles
            mid = lay.keys[sorted(lay.keys)[len(lay.keys) // 2]].at(lay.lane_px * 0.55, 0.5)
            _text(img, f"FIN  {round(100 * g.accuracy)}%", (mid[0], mid[1]), 1.4 * lay.size[0] / 1920, _scale([255, 230, 120], br), 3, center=True)
            return
        _text(img, f"{nivel}", (x + pad, y + pad + 16 * sc), 0.5 * sc, _scale([170, 200, 255], br), 1)
        _text(img, g.song.title, (x + pad, y + pad + 38 * sc), 0.5 * sc, blanco, 1)
        _text(img, f"{g.score}", (cx, y + pad + 95 * sc), 1.6 * sc, _scale([255, 230, 120], br), 3, center=True)
        mult = 1 + min(g.combo // 10, 3)
        _text(img, f"combo {g.combo}" + (f"  x{mult}" if mult > 1 else ""), (cx, y + pad + 135 * sc), 0.65 * sc, blanco, 2, center=True)
        _text(img, f"precision {round(100 * g.accuracy)}%", (cx, y + pad + 168 * sc), 0.6 * sc, blanco, 1, center=True)
        # estado
        msg = ""
        if g.state == "ready":
            msg = "toca una tecla"
        elif g.state == "paused":
            msg = "PAUSA"
        elif g.state == "playing" and g.song_t < 0:
            msg = f"{math.ceil(-g.song_t)}"
        elif g.waiting:
            msg = "toca la nota"
        if msg:
            k = 0.6 + 0.4 * (0.5 + 0.5 * math.sin(now * 2 * math.pi * 1.2))
            _text(img, msg, (cx, y + pad + 205 * sc), 0.7 * sc, _scale([0, 220, 255], k * br), 2, center=True)
        # barra de progreso
        by = y + h - pad - 10 * sc
        cv2.rectangle(img, (int(x + pad), int(by)), (int(x + w - pad), int(by + 10 * sc)), _scale([120, 120, 140], br), 1)
        cv2.rectangle(img, (int(x + pad), int(by)), (int(x + pad + (w - 2 * pad) * g.progress), int(by + 10 * sc)),
                      _scale([0, 220, 255], br), -1)
