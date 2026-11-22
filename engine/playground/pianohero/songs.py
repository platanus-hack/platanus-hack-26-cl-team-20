"""Canciones del Piano Hero: melodías incluidas (generadas por código), carga de .mid y simplificación por nivel.

Una canción es una lista de notas ``Note(t, dur, midi, hand)`` en segundos, con la mano (R derecha, L izquierda) y el
compás para dibujar las líneas de compás. Las incluidas se escriben en negras (1 = una negra) con su arreglo completo
(melodía + acompañamiento con acordes); cada nivel las simplifica con la misma función que a un .mid cualquiera:

    fácil   (1)  solo la melodía (mano derecha), una nota a la vez, lenta, solo blancas, rango de una octava
    medio   (2)  melodía + bajo simple de la izquierda (una nota, espaciada), más rápido
    difícil (3)  todo: acordes de hasta 3 notas por mano, negras, tempo original

Los .mid se leen con mido (mapa de tempos completo), sin la pista de batería (canal 10), se asigna mano por pista
(2 pistas: la más aguda es la derecha) o por altura (>= Do4 derecha), y se transponen por octavas al rango del
teclado (C2..C7 = MIDI 36..96).
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path

LO, HI = 36, 96            # C2..C7: el Korg EK-50 de 61 teclas
NOMBRES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
PC_BLANCAS = (0, 2, 4, 5, 7, 9, 11)


@dataclass
class Note:
    t: float               # inicio (s)
    dur: float             # duración (s)
    midi: int
    hand: str = "R"        # R | L
    vel: int = 90


@dataclass
class Song:
    name: str
    title: str
    notes: list[Note]
    bpm: float = 100.0
    beats_per_bar: float = 4.0       # en negras (3/8 = 1,5)
    source: str = "incluida"
    t0_bar: float = 0.0              # dónde cae el primer compás (s): para las anacrusas
    meta: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max((n.t + n.dur for n in self.notes), default=0.0)

    def bar_times(self) -> list[float]:
        """Inicio de cada compás (s), para las líneas del carril."""
        paso = 60.0 / self.bpm * self.beats_per_bar
        if paso <= 0:
            return []
        out, t = [], self.t0_bar
        while t <= self.duration + 1e-6:
            out.append(round(t, 4)); t += paso
        return out

    def to_json(self) -> dict:
        hands = sorted({n.hand for n in self.notes})
        return {"name": self.name, "title": self.title, "notes": len(self.notes), "duration_s": round(self.duration, 1),
                "bpm": round(self.bpm, 1), "hands": hands, "source": self.source,
                "range": [min((n.midi for n in self.notes), default=0), max((n.midi for n in self.notes), default=0)]}


def es_negra(midi: int) -> bool:
    return midi % 12 not in PC_BLANCAS


def nota(nombre: str) -> int:
    """'C4' -> 60, 'D#5' -> 75, 'Bb3' -> 58 (MIDI 60 = C4)."""
    m = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d)", nombre.strip())
    if not m:
        raise ValueError(f"nota inválida: {nombre}")
    pc = NOMBRES[m.group(1).upper()] + {"#": 1, "b": -1, "": 0}[m.group(2)]
    return 12 * (int(m.group(3)) + 1) + pc


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


# ---------------------------------------------------------------------------------------------- escritura en negras

def seq(texto: str, hand: str = "R", t0: float = 0.0, dur: float = 1.0) -> list[tuple[float, float, int, str]]:
    """'E4 E4 F4:0.5 G4+B4:2 -:1 | ...' -> [(beat, beats, midi, hand)]. ':n' = duración en negras (por defecto `dur`),
    '+' une un acorde, '-' es silencio, '|' se ignora (solo para leer los compases)."""
    out = []; t = t0
    for tok in texto.replace("|", " ").split():
        nom, _, d = tok.partition(":")
        d = float(d) if d else dur
        if nom != "-":
            for p in nom.split("+"):
                out.append((t, d, nota(p), hand))
        t += d
    return out


def _cancion(name, title, bpm, beats_per_bar, eventos, t0_bar_beats=0.0) -> Song:
    s = 60.0 / bpm
    notas = [Note(round(b * s, 5), round(d * s * 0.92, 5), m, h) for b, d, m, h in sorted(eventos, key=lambda e: (e[0], e[2]))]
    return Song(name, title, notas, bpm, beats_per_bar, "incluida", t0_bar_beats * s)


def oda() -> Song:
    """Oda a la Alegría (Beethoven, 9ª sinfonía), Do mayor, 4/4. Izquierda: acordes de la armonía por medio compás."""
    A = "E4 E4 F4 G4 | G4 F4 E4 D4 | C4 C4 D4 E4 |"
    m = seq(A + " E4:1.5 D4:0.5 D4:2 |" + A + " D4:1.5 C4:0.5 C4:2 |"
            " D4 D4 E4 C4 | D4 E4:0.5 F4:0.5 E4 C4 | D4 E4:0.5 F4:0.5 E4 D4 | C4 D4 G3:2 |"
            + A + " D4:1.5 C4:0.5 C4:2")
    C, G, F = "C3+E3+G3", "B2+D3+G3", "C3+F3+A3"
    acordes = [C, G, C, G, C, G, C, (G, C),          # A y A'
               G, G, G, (C, G),                      # B
               C, G, C, (G, C)]                      # A''
    izq = []
    for i, a in enumerate(acordes):
        if isinstance(a, tuple):
            izq += seq(f"{a[0]}:2 {a[1]}:2", "L", t0=4 * i)
        else:
            izq += seq(f"{a}:4", "L", t0=4 * i)
    return _cancion("oda", "Oda a la Alegria", 104, 4, m + izq)


def estrellita() -> Song:
    """Estrellita (Twinkle Twinkle Little Star, tradicional), Do mayor, 4/4."""
    a = "C4 C4 G4 G4 | A4 A4 G4:2 | F4 F4 E4 E4 | D4 D4 C4:2 |"
    b = "G4 G4 F4 F4 | E4 E4 D4:2 |"
    m = seq(a + b + b + a)
    C, F, G = "C3+E3+G3", "C3+F3+A3", "B2+D3+G3"
    compases = [[C], [F, C], [F, C], [G, C], [C, F], [C, G], [C, F], [C, G], [C], [F, C], [F, C], [G, C]]
    izq = []
    for i, cs in enumerate(compases):
        d = 4 / len(cs)
        izq += seq(" ".join(f"{c}:{d}" for c in cs), "L", t0=4 * i)
    return _cancion("estrellita", "Estrellita", 100, 4, m + izq)


def elisa() -> Song:
    """Para Elisa (Beethoven, WoO 59), la menor, 3/8: el tema A dos veces. Semicorcheas = 0,25 negra."""
    s = 0.25
    ev = []
    t = 0.0

    def r(notas, t0):           # derecha: semicorcheas seguidas desde t0
        for i, n in enumerate(notas.split()):
            ev.append((t0 + i * s, s, nota(n), "R"))

    def compas_arpegio(t0, larga, bajo, resp):
        """Larga de la derecha (corchea) + arpegio de 3 semicorcheas en la izquierda + respuesta de 3 en la derecha."""
        ev.append((t0, 2 * s, nota(larga), "R"))
        for i, n in enumerate(bajo.split()):
            ev.append((t0 + i * s, s, nota(n), "L"))
        r(resp, t0 + 3 * s)

    for _ in range(2):
        r("E5 D#5", t); t += 2 * s                                   # anacrusa
        r("E5 D#5 E5 B4 D5 C5", t); t += 6 * s
        compas_arpegio(t, "A4", "A2 E3 A3", "C4 E4 A4"); t += 6 * s
        compas_arpegio(t, "B4", "E2 E3 G#3", "E4 G#4 B4"); t += 6 * s
        compas_arpegio(t, "C5", "A2 E3 A3", "E4 E5 D#5"); t += 6 * s
        r("E5 D#5 E5 B4 D5 C5", t); t += 6 * s
        compas_arpegio(t, "A4", "A2 E3 A3", "C4 E4 A4"); t += 6 * s
        compas_arpegio(t, "B4", "E2 E3 G#3", "E4 C5 B4"); t += 6 * s
        ev.append((t, 4 * s, nota("A4"), "R"))
        for i, n in enumerate("A2 E3 A3".split()):
            ev.append((t + i * s, s, nota(n), "L"))
        t += 4 * s
    return _cancion("elisa", "Para Elisa", 63, 1.5, ev, t0_bar_beats=2 * s)


def escala() -> Song:
    """Escala de Do (arriba y abajo) con la derecha y, en la izquierda, la misma una octava abajo: para calibrar."""
    sube = "C4 D4 E4 F4 G4 A4 B4 C5"
    baja = "C5 B4 A4 G4 F4 E4 D4 C4:2"
    m = seq(sube + " " + baja)
    izq = seq(" ".join(f"{n[:-1]}{int(n[-1]) - 1}" for n in sube.split()) + " "
              + " ".join(f"{n.split(':')[0][:-1]}{int(n.split(':')[0][-1]) - 1}" + (":" + n.split(":")[1] if ":" in n else "")
                         for n in baja.split()), "L")
    return _cancion("escala", "Escala de Do", 90, 4, m + izq)


BUILTIN = {"oda": oda, "estrellita": estrellita, "elisa": elisa, "escala": escala}
ALIASES = {"oda a la alegria": "oda", "ode to joy": "oda", "ode": "oda", "himno": "oda", "twinkle": "estrellita",
           "twinkle twinkle little star": "estrellita", "estrellita donde estas": "estrellita", "para elisa": "elisa",
           "fur elise": "elisa", "fuer elise": "elisa", "fur": "elisa", "scale": "escala", "calibrar": "escala"}


# ---------------------------------------------------------------------------------------------- .mid

def load_midi(path: str | Path, split: int = 60) -> Song:
    """Lee un .mid con mido: notas en segundos (mapa de tempos), sin batería, con mano por pista o por altura."""
    import mido
    mid = mido.MidiFile(str(path))
    tpb = mid.ticks_per_beat or 480
    tempos = []                                     # (tick, tempo µs por negra)
    ts = None
    for tr in mid.tracks:
        tick = 0
        for msg in tr:
            tick += msg.time
            if msg.type == "set_tempo":
                tempos.append((tick, msg.tempo))
            elif msg.type == "time_signature" and ts is None:
                ts = (msg.numerator, msg.denominator)
    tempos.sort()
    if not tempos or tempos[0][0] > 0:
        tempos.insert(0, (0, 500000))
    # segundos acumulados al inicio de cada tramo de tempo
    acum = [0.0]
    for i in range(1, len(tempos)):
        acum.append(acum[-1] + mido.tick2second(tempos[i][0] - tempos[i - 1][0], tpb, tempos[i - 1][1]))
    ticks_t = [t for t, _ in tempos]

    def seg(tick: int) -> float:
        i = bisect.bisect_right(ticks_t, tick) - 1
        return acum[i] + mido.tick2second(tick - tempos[i][0], tpb, tempos[i][1])

    pistas: list[list[Note]] = []
    for tr in mid.tracks:
        tick = 0; abiertas: dict[tuple[int, int], tuple[int, int]] = {}; notas: list[Note] = []
        for msg in tr:
            tick += msg.time
            if msg.type not in ("note_on", "note_off") or msg.channel == 9:
                continue
            k = (msg.channel, msg.note)
            if msg.type == "note_on" and msg.velocity > 0:
                if k in abiertas:                    # re-ataque sin note_off: cierra la anterior
                    t0, v = abiertas.pop(k)
                    notas.append(Note(seg(t0), max(0.05, seg(tick) - seg(t0)), msg.note, "R", v))
                abiertas[k] = (tick, msg.velocity)
            elif k in abiertas:
                t0, v = abiertas.pop(k)
                notas.append(Note(seg(t0), max(0.05, seg(tick) - seg(t0)), msg.note, "R", v))
        for (ch, n), (t0, v) in abiertas.items():
            notas.append(Note(seg(t0), 0.5, n, "R", v))
        if notas:
            pistas.append(notas)
    if not pistas:
        raise ValueError(f"{path}: no tiene notas (fuera de la batería)")
    if len(pistas) == 2:
        medias = [sum(n.midi for n in p) / len(p) for p in pistas]
        izq = pistas[int(medias[0] > medias[1])]
        for n in izq:
            n.hand = "L"
        notas = pistas[0] + pistas[1]
    else:
        notas = [n for p in pistas for n in p]
        for n in notas:
            n.hand = "R" if n.midi >= split else "L"
    notas.sort(key=lambda n: (n.t, n.midi))
    t_ini = notas[0].t
    for n in notas:                                   # sin silencio inicial
        n.t = round(n.t - t_ini, 5)
    bpm = 60_000_000 / tempos[0][1]
    num, den = ts or (4, 4)
    p = Path(path)
    return Song(_norm(p.stem).replace(" ", "_") or "midi", p.stem, notas, bpm, num * 4 / den, str(p),
                t0_bar=0.0, meta={"tracks": len(pistas), "ticks_per_beat": tpb})


def get_song(spec: str) -> Song:
    """Por nombre (oda, estrellita, elisa, escala, o un alias) o por ruta a un .mid."""
    if spec.lower().endswith((".mid", ".midi")):
        p = Path(spec).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"no encuentro {p}")
        return load_midi(p)
    k = _norm(spec)
    k = ALIASES.get(k, k)
    if k in BUILTIN:
        return BUILTIN[k]()
    cands = [n for n in BUILTIN if n.startswith(k)] if k else []
    if len(cands) == 1:
        return BUILTIN[cands[0]]()
    raise KeyError(f"canción desconocida: {spec}. Hay: {sorted(BUILTIN)} o una ruta a un .mid")


def list_songs() -> list[dict]:
    return [BUILTIN[n]().to_json() for n in BUILTIN]


# ---------------------------------------------------------------------------------------------- transposición

def fit_range(notes: list[Note], lo: int = LO, hi: int = HI) -> list[Note]:
    """Corre todo por octavas para que quepa en [lo, hi] (el corrimiento que deja menos fuera y más centrado);
    lo que igual queda fuera se dobla por octavas hacia adentro."""
    if not notes:
        return []
    mejor = None
    for k in range(-5, 6):
        fuera = sum(not (lo <= n.midi + 12 * k <= hi) for n in notes)
        centro = abs(sum(n.midi + 12 * k for n in notes) / len(notes) - (lo + hi) / 2)
        clave = (fuera, abs(k) if fuera == 0 else 0, centro)      # si ya cabe, no se mueve
        if mejor is None or clave < mejor[0]:
            mejor = (clave, k)
    k = mejor[1]
    out = []
    for n in notes:
        m = n.midi + 12 * k
        while m < lo:
            m += 12
        while m > hi:
            m -= 12
        out.append(replace(n, midi=m))
    return out


# ---------------------------------------------------------------------------------------------- niveles

@dataclass(frozen=True)
class Level:
    n: int
    name: str
    tempo: float                     # factor sobre el tempo original (<1 = más lento)
    lead: float                      # segundos que tarda una nota en recorrer el carril
    windows: tuple[float, float, float]   # perfecto, bien, ok (± s)
    hands: tuple[str, ...]
    max_poly: dict                   # notas simultáneas por mano
    min_gap: dict                    # separación mínima entre ataques por mano (s, ya con el tempo del nivel)
    whites_only: bool
    max_span: dict                   # rango máximo por mano (semitonos) o None
    wrong_breaks_combo: bool


LEVELS = {
    1: Level(1, "Facil", 0.6, 4.0, (0.075, 0.15, 0.24), ("R",), {"R": 1}, {"R": 0.30}, True, {"R": 16}, False),
    2: Level(2, "Medio", 0.85, 3.0, (0.055, 0.11, 0.17), ("R", "L"), {"R": 1, "L": 1}, {"R": 0.16, "L": 0.45},
             False, {"R": 24, "L": 19}, True),
    3: Level(3, "Dificil", 1.0, 2.2, (0.035, 0.07, 0.11), ("R", "L"), {"R": 3, "L": 3}, {"R": 0.05, "L": 0.05},
             False, {"R": None, "L": None}, True),
}


def _racimos(notes: list[Note], tol: float = 0.03) -> list[list[Note]]:
    """Agrupa ataques casi simultáneos (acordes)."""
    out: list[list[Note]] = []
    for n in sorted(notes, key=lambda n: (n.t, n.midi)):
        if out and n.t - out[-1][0].t <= tol:
            out[-1].append(n)
        else:
            out.append([n])
    return out


def _a_blanca(m: int, previa: int | None) -> int:
    """Negra -> la blanca vecina distinta de la nota anterior (así E D# E queda E D E y no E E E)."""
    if not es_negra(m):
        return m
    abajo, arriba = m - 1, m + 1
    if previa == abajo:
        return arriba
    return abajo


def _plegar_rango(notes: list[Note], span: int) -> list[Note]:
    """Dobla por octavas lo que se sale de una ventana de `span` semitonos alrededor de la mediana."""
    if not notes:
        return notes
    ms = sorted(n.midi for n in notes)
    med = ms[len(ms) // 2]
    lo = med - span // 2; hi = lo + span
    out = []
    for n in notes:
        m = n.midi
        while m < lo:
            m += 12
        while m > hi:
            m -= 12
        if m < lo:                                    # span < 12: no cabe la octava, queda lo más cerca
            m += 12 if lo - m > m + 12 - hi else 0
        out.append(replace(n, midi=m))
    return out


def simplify(song: Song, level: int | Level, speed: float = 1.0) -> Song:
    """La canción para un nivel: tempo, manos, polifonía, separación mínima, solo blancas, rango, sin choques."""
    L = LEVELS[level] if isinstance(level, int) else level
    f = 1.0 / (L.tempo * max(0.1, speed))
    base = fit_range([replace(n, t=n.t * f, dur=n.dur * f) for n in song.notes])
    out: list[Note] = []
    for hand in L.hands:
        mano = [n for n in base if n.hand == hand]
        if not mano and hand == "R" and L.hands == ("R",):
            mano = list(base)                        # sin derecha marcada: todo es melodía
        sel: list[Note] = []
        ultimo = -1e9
        for grupo in _racimos(mano):
            if grupo[0].t - ultimo < L.min_gap.get(hand, 0.0):
                continue
            # derecha: las de arriba (melodía); izquierda: las de abajo (bajo)
            grupo = sorted(grupo, key=lambda n: -n.midi if hand == "R" else n.midi)[:L.max_poly.get(hand, 1)]
            sel += grupo; ultimo = grupo[0].t
        if L.max_span.get(hand):
            sel = _plegar_rango(sel, L.max_span[hand])
        if L.whites_only:
            previa = None
            for i, n in enumerate(sel):
                sel[i] = replace(n, midi=_a_blanca(n.midi, previa)); previa = sel[i].midi
        out += sel
    out = fit_range(out)
    # sin duplicados (misma tecla y ataque) y sin solapes en la misma tecla
    out.sort(key=lambda n: (n.midi, n.t))
    limpio: list[Note] = []
    for n in out:
        if limpio and limpio[-1].midi == n.midi:
            prev = limpio[-1]
            if n.t - prev.t < 0.03:
                continue
            if prev.t + prev.dur > n.t - 0.04:
                limpio[-1] = replace(prev, dur=max(0.05, n.t - 0.04 - prev.t))
        limpio.append(replace(n, dur=min(max(n.dur, 0.08), 3.0)))
    limpio.sort(key=lambda n: (n.t, n.midi))
    return Song(song.name, song.title, limpio, song.bpm * L.tempo * speed, song.beats_per_bar, song.source,
                song.t0_bar * f, {**song.meta, "level": L.n})
