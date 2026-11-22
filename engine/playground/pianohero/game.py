"""Lógica del Piano Hero, sin dibujo ni hardware: reloj de la canción, juicio de tiempo, puntaje, combo, práctica.

El reloj es de pared (``time.time()`` o el que se le pase): cada evento MIDI trae su hora de llegada y se juzga en el
tiempo de canción de ese instante, no en el del cuadro (así los fps no cambian el juicio). ``offset_ms`` compensa la
latencia de proyector + puente: si el proyector muestra el cuadro L ms tarde, Max toca L ms tarde; ese desfase se
resta antes de juzgar. El resumen sugiere el offset con el promedio de los aciertos.

    g = Game(get_song("oda"), level=1)
    g.start(now)
    g.update(now, [("on", 60, t_evento), ("off", 60, t_evento2)])     # cada cuadro
    g.status()      # puntaje, combo, precisión, progreso, estado
    g.feedback      # aciertos/fallos/errores recientes para que el render ilumine teclas

Práctica (``practice=True``): el reloj se detiene en la siguiente nota hasta que se toque (no hay fallos por tiempo).
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from .songs import LEVELS, Level, Song, simplify

POINTS = {"perfecto": 300, "bien": 200, "ok": 100}
WEIGHT = {"perfecto": 1.0, "bien": 0.7, "ok": 0.4, "fallo": 0.0}
CHORD_TOL = 0.04                 # notas a menos de esto se esperan juntas en práctica


@dataclass
class PlayNote:
    i: int
    t: float
    dur: float
    midi: int
    hand: str
    state: str = "pending"       # pending | hit | miss
    judge: str = ""              # perfecto | bien | ok | fallo
    dt: float = 0.0              # adelanto (-) o atraso (+) del toque, s
    t_judged: float = 0.0        # tiempo de canción del juicio
    wall_judged: float = 0.0     # hora de pared del juicio (para animar)


class Game:
    def __init__(self, song: Song, level: int | Level = 1, practice: bool = False, offset_ms: float = 0.0,
                 speed: float = 1.0, lead_in: float | None = None):
        self.level: Level = LEVELS[level] if isinstance(level, int) else level
        self.source_song = song
        self.song = simplify(song, self.level, speed)
        self.practice = bool(practice)
        self.offset = float(offset_ms) / 1000.0
        self.speed = speed
        self.lead = self.level.lead
        self.lead_in = max(self.lead, 2.5) if lead_in is None else lead_in
        self.notes = [PlayNote(i, n.t, n.dur, n.midi, n.hand) for i, n in enumerate(self.song.notes)]
        self._times = [n.t for n in self.notes]
        self.bars = self.song.bar_times()
        self.reset()

    # ------------------------------------------------------------------ ciclo
    def reset(self) -> None:
        for n in self.notes:
            n.state, n.judge, n.dt, n.t_judged, n.wall_judged = "pending", "", 0.0, 0.0, 0.0
        self.state = "ready"                 # ready | playing | paused | finished
        self.song_t = -self.lead_in
        self._last = None
        self._first_pending = 0
        self.score = 0
        self.combo = 0
        self.max_combo = 0
        self.counts = {"perfecto": 0, "bien": 0, "ok": 0, "fallo": 0}
        self.wrong = 0
        self.dts: list[float] = []
        self.feedback: list[dict] = []       # {"kind": hit|miss|wrong, "midi", "judge", "wall", "note"}
        self.held: dict[int, float] = {}     # tecla -> hora de pared en que se apretó
        self.finished_at: float | None = None
        self.waiting = False                 # práctica: reloj detenido esperando notas

    def start(self, now: float) -> None:
        if self.state == "finished":
            self.reset()
        if self.state in ("ready", "paused"):
            self.state = "playing"
            self._last = now

    def pause(self, now: float) -> None:
        if self.state == "playing":
            self._advance(now)
            self.state = "paused"

    def resume(self, now: float) -> None:
        if self.state == "paused":
            self.state = "playing"
            self._last = now

    def toggle(self, now: float) -> None:
        if self.state == "playing":
            self.pause(now)
        else:
            self.start(now)

    def restart(self, now: float | None = None) -> None:
        self.reset()
        if now is not None:
            self.start(now)

    # ------------------------------------------------------------------ reloj
    def _next_pending_t(self) -> float | None:
        while self._first_pending < len(self.notes) and self.notes[self._first_pending].state != "pending":
            self._first_pending += 1
        return self.notes[self._first_pending].t if self._first_pending < len(self.notes) else None

    def _advance(self, to_wall: float) -> None:
        if self._last is None:
            self._last = to_wall
        dt = to_wall - self._last
        if self.state != "playing" or dt <= 0:
            self._last = max(self._last, to_wall) if self.state == "playing" else to_wall
            return
        self._last = to_wall
        nuevo = self.song_t + dt
        self.waiting = False
        if self.practice:
            nxt = self._next_pending_t()
            if nxt is not None and nuevo >= nxt:
                nuevo = nxt; self.waiting = True
        self.song_t = nuevo

    def song_time_at(self, wall: float) -> float:
        """Tiempo de canción en una hora de pared (pasada o del cuadro actual)."""
        if self.state != "playing" or self._last is None or self.waiting:
            return self.song_t
        return self.song_t + (wall - self._last)

    def song_time_to_wall(self, t_song: float, now: float) -> float:
        """Hora de pared en que el reloj marca (o marcó) `t_song`, si sigue corriendo como en `now`."""
        return now + (t_song - self.song_time_at(now))

    # ------------------------------------------------------------------ entrada
    def note_on(self, midi: int, wall: float) -> dict | None:
        self.held[midi] = wall
        if self.state != "playing":
            return None
        if wall > (self._last or wall):
            self._advance(wall)
        ts = self.song_time_at(wall) - self.offset
        w0, w1, w2 = self.level.windows
        best = None
        lo = bisect.bisect_left(self._times, ts - w2 - 1e-9)
        hi = bisect.bisect_right(self._times, ts + w2 + 1e-9)
        for n in self.notes[lo:hi]:
            if n.state == "pending" and n.midi == midi and (best is None or abs(n.t - ts) < abs(best.t - ts)):
                best = n
        if best is None and self.practice:
            # práctica: vale la nota que está esperando en la línea aunque el reloj esté detenido
            nxt = self._next_pending_t()
            if nxt is not None:
                for n in self.notes[self._first_pending:]:
                    if n.t > nxt + CHORD_TOL:
                        break
                    if n.state == "pending" and n.midi == midi:
                        best = n; break
        if best is None and (not self.notes or ts < self.notes[0].t - w2 - 0.3):
            return None                          # en la cuenta regresiva no hay errores
        if best is None:
            self.wrong += 1
            if self.level.wrong_breaks_combo and not self.practice:
                self.combo = 0
            ev = {"kind": "wrong", "midi": midi, "judge": "error", "wall": wall, "note": None}
            self.feedback.append(ev)
            return ev
        dt = ts - best.t
        a = abs(dt)
        judge = "perfecto" if (a <= w0 or self.practice) else ("bien" if a <= w1 else "ok")
        best.state, best.judge, best.dt, best.t_judged, best.wall_judged = "hit", judge, dt, ts, wall
        self.counts[judge] += 1
        self.combo += 1
        self.max_combo = max(self.max_combo, self.combo)
        mult = 1 + min(self.combo // 10, 3)
        self.score += POINTS[judge] * mult
        if not self.practice:
            self.dts.append(dt)
        ev = {"kind": "hit", "midi": midi, "judge": judge, "wall": wall, "note": best.i, "dt": dt}
        self.feedback.append(ev)
        return ev

    def note_off(self, midi: int, wall: float) -> None:
        self.held.pop(midi, None)

    # ------------------------------------------------------------------ cuadro
    def update(self, now: float, events=()) -> None:
        """Procesa los eventos (("on"|"off", midi, hora de pared)) en orden y avanza el reloj hasta `now`."""
        for kind, midi, wall in sorted(events, key=lambda e: e[2]):
            if kind == "on":
                self.note_on(int(midi), float(wall))
            else:
                self.note_off(int(midi), float(wall))
        self._advance(now)
        if self.state == "playing" and not self.practice:
            limite = self.song_t - self.offset - self.level.windows[2]
            i = self._first_pending
            while i < len(self.notes) and self.notes[i].t < limite:
                n = self.notes[i]
                if n.state == "pending":
                    n.state, n.judge, n.t_judged, n.wall_judged = "miss", "fallo", self.song_t, now
                    self.counts["fallo"] += 1
                    self.combo = 0
                    self.feedback.append({"kind": "miss", "midi": n.midi, "judge": "fallo", "wall": now, "note": n.i})
                i += 1
            self._next_pending_t()
        if self.state == "playing" and self._next_pending_t() is None and self.song_t > self.song.duration + 1.0:
            self.state = "finished"
            self.finished_at = now
        # el feedback viejo no sirve para dibujar
        if len(self.feedback) > 400:
            self.feedback = [f for f in self.feedback if now - f["wall"] < 3.0]

    # ------------------------------------------------------------------ lectura
    @property
    def judged(self) -> int:
        return sum(self.counts.values())

    @property
    def accuracy(self) -> float:
        """Promedio ponderado de lo ya juzgado (perfecto 1, bien 0,7, ok 0,4, fallo 0); en práctica, aciertos / toques."""
        if self.practice:
            hits = self.judged
            return hits / (hits + self.wrong) if (hits + self.wrong) else 1.0
        n = self.judged
        return sum(WEIGHT[k] * v for k, v in self.counts.items()) / n if n else 1.0

    @property
    def progress(self) -> float:
        d = self.song.duration
        return 0.0 if d <= 0 else min(1.0, max(0.0, self.song_t / d))

    def stars(self) -> int:
        a = self.accuracy
        return 3 if a >= 0.95 else 2 if a >= 0.8 else 1 if a >= 0.6 else 0

    def mean_dt_ms(self) -> float | None:
        return round(1000 * sum(self.dts) / len(self.dts), 1) if len(self.dts) >= 3 else None

    def suggested_offset_ms(self) -> float | None:
        if len(self.dts) < 8:
            return None
        s = sorted(self.dts)
        med = s[len(s) // 2]
        return round(1000 * (self.offset + med))

    def visible(self, ahead: float, behind: float = 1.0) -> list[PlayNote]:
        """Notas que tocan la ventana [song_t - behind, song_t + ahead] (para dibujar)."""
        lo = bisect.bisect_left(self._times, self.song_t - behind - 4.0)
        hi = bisect.bisect_right(self._times, self.song_t + ahead)
        return [n for n in self.notes[lo:hi] if n.t + n.dur >= self.song_t - behind]

    def status(self) -> dict:
        L = self.level
        d = {
            "state": self.state, "song": self.song.name, "title": self.song.title, "level": L.n, "level_name": L.name,
            "practice": self.practice, "waiting": self.waiting, "t": round(self.song_t, 2),
            "duration": round(self.song.duration, 1), "progress": round(self.progress, 3), "score": self.score,
            "combo": self.combo, "max_combo": self.max_combo, "accuracy": round(self.accuracy, 3),
            "counts": dict(self.counts), "wrong": self.wrong, "notes_total": len(self.notes),
            "notes_done": self.judged, "offset_ms": round(self.offset * 1000), "stars": self.stars(),
            "mean_dt_ms": self.mean_dt_ms(), "suggested_offset_ms": self.suggested_offset_ms(),
            "windows_ms": [round(w * 1000) for w in L.windows], "lead_s": self.lead,
        }
        return d

    def summary(self) -> dict:
        s = self.status()
        return {k: s[k] for k in ("title", "level_name", "practice", "score", "accuracy", "stars", "max_combo",
                                  "counts", "wrong", "notes_total", "mean_dt_ms", "suggested_offset_ms")}
