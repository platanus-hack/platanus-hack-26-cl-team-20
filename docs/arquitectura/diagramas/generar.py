#!/usr/bin/env python3
"""Genera los diagramas de arquitectura de pcx (v2) como SVG. Sin dependencias.

Uso: python3 generar.py   (o ./render.sh, que además saca los PNG a 2x y el PDF combinado).
Todo se dibuja sobre una grilla de 8 px. Para ajustar un diagrama, edita su función (d1 … d5) y vuelve a correr.
"""

from pathlib import Path
from xml.sax.saxutils import escape

AQUI = Path(__file__).parent
FECHA = "21-nov-2026"
TOTAL = 5

SANS = "-apple-system, 'Helvetica Neue', Helvetica, Arial, sans-serif"
MONO = "'SF Mono', Menlo, Consolas, 'DejaVu Sans Mono', monospace"
INK, TEXT, MUTED, RULE = "#1E2127", "#3B4048", "#6B7280", "#E3E5E8"
LINE, PLAN, MAIN = "#50555E", "#8C9099", "#D9502A"

# Un color por zona: borde, fondo, cara del nodo 3D y tinta del texto.
Z = {
    "agente": dict(s="#7A68C2", f="#F5F2FC", h="#E6E0F6", i="#45358F"),
    "nube": dict(s="#4C7BC0", f="#EEF4FC", h="#DCE7F7", i="#22477E"),
    "motor": dict(s="#4C9867", f="#EDF7F0", h="#D9EEDF", i="#215E37"),
    "max": dict(s="#4C9867", f="#EDF7F0", h="#D9EEDF", i="#215E37"),
    "web": dict(s="#2E8FA3", f="#EAF6F8", h="#D3ECF1", i="#1A5D6B"),
    "pm": dict(s="#4C7BC0", f="#EEF4FC", h="#DCE7F7", i="#22477E"),
    "mesa": dict(s="#C08A3E", f="#FDF6E9", h="#F6E7C8", i="#7A4F0F"),
    "device": dict(s="#9A9A92", f="#FAFAF8", h="#EDEDE8", i="#4A4A44"),
    "ext": dict(s="#85878C", f="#FFFFFF", h="#F1F1F1", i="#3F4147"),
}
LEY_DUENOS = [("agente", "agente de IA"), ("motor", "motor local (Rust)"), ("web", "web app"), ("nube", "nube"), ("mesa", "mesa física"),
              ("ext", "externo u opcional")]
ESTADOS = {"ok": "implementado", "part": "parcial", "plan": "planeado"}


def tw(s, size, bold=False, mono=False):
    """Ancho aproximado de un texto, para fondos de etiquetas."""
    if mono:
        return len(s) * size * 0.602
    n = 0.0
    for ch in s:
        if ch in " .,:;'|!ilj·":
            n += 0.27
        elif ch in "ftrI()[]-«»/":
            n += 0.35
        elif ch in "mwMW@":
            n += 0.84
        elif ch.isupper():
            n += 0.67
        elif ch.isdigit():
            n += 0.556
        else:
            n += 0.535
    return n * size * (1.07 if bold else 1.0)


class Box:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.l, self.r, self.t, self.b = x, x + w, y, y + h
        self.cx, self.cy = x + w / 2, y + h / 2


def _f(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


class D:
    """Un diagrama. Capas, de atrás hacia adelante: zone, line, box, top."""

    def __init__(self, n, nombre, w, h, titulo, sub, tipo, tecnica=False):
        self.n, self.nombre, self.w, self.h = n, nombre, w, h
        self.titulo = titulo
        self.L = {k: [] for k in ("zone", "line", "box", "top")}
        self.color_paso = "#D9502A" if tecnica else MAIN
        self.tecnica = tecnica
        if tecnica:  # lámina técnica: el encabezado va entero a la izquierda (arriba a la derecha hay cajas)
            self.T(32, 40, f"PCX · ARQUITECTURA · LÁMINA TÉCNICA {n} DE 2 · {tipo.upper()} · {FECHA.upper()}", 11.5, 700, MUTED, ls=1.2)
            self.T(32, 78, f"T{n} · {titulo}", 28, 700, INK)
            self.T(32, 104, sub, 14.5, 400, TEXT)
            return
        self.T(32, 40, f"PCX · ARQUITECTURA V2 · {FECHA.upper()}", 11.5, 700, MUTED, ls=1.2)
        self.T(w - 32, 40, f"diagrama {n} de {TOTAL} · {tipo}", 12, 400, MUTED, anchor="end")
        self.T(32, 78, f"{n} · {titulo}", 28, 700, INK)
        self.T(32, 104, sub, 14.5, 400, TEXT)
        self.a("zone", f'<path d="M 32 120 H {w - 32}" stroke="{RULE}" stroke-width="1.2"/>')

    def a(self, layer, frag):
        self.L[layer].append(frag)

    # ------------------------------------------------------------------ texto
    def T(self, x, y, s, size=13, w=400, fill=TEXT, anchor="start", mono=False, ls=None, layer="top", italic=False):
        """Texto. `s` es un string o una lista de tramos (texto, {w, fill, mono})."""
        fam = MONO if mono else SANS
        extra = f' letter-spacing="{ls}"' if ls else ""
        extra += ' font-style="italic"' if italic else ""
        if isinstance(s, str):
            body = escape(s)
        else:
            body = ""
            for t, o in s:
                at = ""
                if "w" in o:
                    at += f' font-weight="{o["w"]}"'
                if "fill" in o:
                    at += f' fill="{o["fill"]}"'
                if o.get("mono"):
                    at += f' font-family="{MONO}" font-size="{_f(size * 0.94)}"'
                body += f"<tspan{at}>{escape(t)}</tspan>"
        self.a(
            layer,
            f'<text x="{_f(x)}" y="{_f(y)}" font-family="{fam}" font-size="{_f(size)}" font-weight="{w}" '
            f'fill="{fill}" text-anchor="{anchor}" xml:space="preserve"{extra}>{body}</text>',
        )

    @staticmethod
    def ancho(s, size, bold=False, mono=False):
        if isinstance(s, str):
            return tw(s, size, bold, mono)
        return sum(tw(t, size * (0.94 if o.get("mono") else 1), o.get("w", 400) >= 600, o.get("mono", False)) for t, o in s)

    # ------------------------------------------------------------------ marcas
    def estado(self, cx, cy, st, r=6, layer="top"):
        """● implementado · ◐ parcial · ○ planeado."""
        if st == "ok":
            self.a(layer, f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{r}" fill="{INK}" stroke="{INK}" stroke-width="1.4"/>')
        elif st == "part":
            self.a(layer, f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{r}" fill="#FFFFFF" stroke="{INK}" stroke-width="1.4"/>')
            self.a(layer, f'<path d="M {_f(cx)} {_f(cy - r)} A {r} {r} 0 0 0 {_f(cx)} {_f(cy + r)} Z" fill="{INK}"/>')
        else:
            self.a(
                layer,
                f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{r}" fill="#FFFFFF" stroke="{INK}" stroke-width="1.4" '
                f'stroke-dasharray="2.4 2.2"/>',
            )

    def quien(self, xr, yc, nombre, layer="top"):
        """Etiqueta de dueño (solo en la vista simple). `xr` es el borde derecho."""
        return 0  # decisión de Manu: los diagramas no muestran dueños
        wpx = tw(nombre, 11, True) + 18
        self.a(layer, f'<rect x="{_f(xr - wpx)}" y="{_f(yc - 9)}" width="{_f(wpx)}" height="18" rx="9" fill="{DUENO.get(nombre.split(" · ")[0], INK)}"/>')
        self.T(xr - wpx / 2, yc + 4, nombre, 11, 700, "#FFFFFF", anchor="middle", layer=layer)
        return wpx

    def paso(self, cx, cy, n, color=None, layer="top"):
        self.a(layer, f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="11" fill="{color or self.color_paso}"/>')
        self.T(cx, cy + 4.5, str(n), 12.5, 700, "#FFFFFF", anchor="middle", layer=layer)

    def icono_comp(self, x, y, color):
        """Ícono UML de componente (esquina superior derecha de la caja)."""
        self.a("box", f'<rect x="{x}" y="{y}" width="13" height="16" fill="#FFFFFF" stroke="{color}" stroke-width="1.2"/>')
        for dy in (3, 9):
            self.a(
                "box",
                f'<rect x="{x - 4}" y="{y + dy}" width="8" height="4" fill="#FFFFFF" stroke="{color}" stroke-width="1.2"/>',
            )

    # ------------------------------------------------------------------ cajas
    def nodo(self, x, y, w, h, zona, estereo, titulo, sub=None, d=12, centro=False, st=None, quien=None):
        """Nodo de despliegue UML (caja 3D)."""
        z = Z[zona]
        st_ = f'fill="{z["h"]}" stroke="{z["s"]}" stroke-width="1.4" stroke-linejoin="round"'
        self.a("zone", f'<polygon points="{x},{y} {x + d},{y - d} {x + w + d},{y - d} {x + w},{y}" {st_}/>')
        self.a("zone", f'<polygon points="{x + w},{y} {x + w + d},{y - d} {x + w + d},{y + h - d} {x + w},{y + h}" {st_}/>')
        self.a("zone", f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{z["f"]}" stroke="{z["s"]}" stroke-width="1.4"/>')
        tx, an = (x + w / 2, "middle") if centro else (x + 16, "start")
        ex = tx
        if st and not centro:
            self.estado(x + 23, y + 19, st, layer="box")
            ex = x + 36
        self.T(ex, y + 23, estereo, 12, 400, z["i"], anchor=an, layer="box")
        self.T(tx, y + 45, titulo, 17.5, 700, z["i"], anchor=an, layer="box")
        if sub:
            self.T(tx, y + 65, sub, 13, 400, MUTED, anchor=an, layer="box")
        if quien:
            self.quien(x + w - 12, y, quien)
        return Box(x, y, w, h)

    def grupo(self, x, y, w, h, zona, estereo, titulo, sub=None, st=None, quien=None, punteado=False):
        """Contenedor plano (un proceso o una aplicación con piezas adentro)."""
        z = Z[zona]
        dash = ' stroke-dasharray="7 5"' if punteado else ""
        self.a(
            "zone",
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{z["f"]}" stroke="{z["s"]}" stroke-width="1.8"{dash}/>',
        )
        ex = x + 16
        if st:
            self.estado(x + 23, y + 20, st, layer="box")
            ex = x + 36
        self.T(ex, y + 24, estereo, 12, 400, z["i"], layer="box")
        self.T(x + 16, y + 47, titulo, 17.5, 700, z["i"], layer="box")
        if sub:
            self.T(x + 16, y + 67, sub, 13, 400, MUTED, layer="box")
        if quien:
            self.quien(x + w - 12, y, quien)
        return Box(x, y, w, h)

    def comp(self, x, y, w, h, zona, titulo, lineas=(), estereo=None, st=None, quien=None, mono=False, icono=True, ts=15.5):
        """Componente: marca de estado + estereotipo, título y una o dos líneas simples."""
        z = Z[zona]
        dash = ' stroke-dasharray="6 4"' if st == "plan" else ""
        self.a(
            "box",
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="#FFFFFF" stroke="{z["s"]}" stroke-width="1.6"{dash}/>',
        )
        ty = y + 27
        if estereo or st:
            ex = x + 14
            if st:
                self.estado(x + 20, y + 19, st, layer="box")
                ex = x + 32
            if estereo:
                self.T(ex, y + 23, estereo, 11.5, 400, z["i"], layer="box")
            ty = y + 45 if estereo else y + 24
            if st and not estereo:
                self.T(x + 32, ty, titulo, ts, 700, INK, mono=mono, layer="box")
            else:
                self.T(x + 14, ty, titulo, ts, 700, INK, mono=mono, layer="box")
        else:
            self.T(x + 14, ty, titulo, ts, 700, INK, mono=mono, layer="box")
        if icono:
            self.icono_comp(x + w - 25, y + 9, z["s"])
        ly = ty + 20
        for ln in lineas:
            self._linea(x + 14, ly, ln, z)
            ly += 18
        if quien:
            self.quien(x + w - 12, y, quien)
        return Box(x, y, w, h)

    def _linea(self, x, y, ln, z, anchor="start", size=13):
        if isinstance(ln, tuple):
            t, k = ln
        else:
            t, k = ln, "n"
        if k == "m":
            self.T(x, y, t, size - 0.5, 400, MUTED, anchor=anchor, layer="box")
        elif k == "mono":
            self.T(x, y, t, size - 1, 400, MUTED, anchor=anchor, mono=True, layer="box")
        elif k == "b":
            self.T(x, y, t, size, 700, INK, anchor=anchor, layer="box")
        elif k == "z":
            self.T(x, y, t, size, 700, z["i"], anchor=anchor, layer="box")
        else:
            self.T(x, y, t, size, 400, TEXT, anchor=anchor, layer="box")

    def cil(self, x, y, w, h, zona, titulo, lineas=(), st=None):
        """Base de datos (cilindro)."""
        z = Z[zona]
        ry = 9
        self.a(
            "box",
            f'<path d="M {x} {y + ry} A {w / 2} {ry} 0 0 1 {x + w} {y + ry} V {y + h - ry} A {w / 2} {ry} 0 0 1 {x} {y + h - ry} Z" '
            f'fill="#FFFFFF" stroke="{z["s"]}" stroke-width="1.6"/>',
        )
        self.a("box", f'<path d="M {x} {y + ry} A {w / 2} {ry} 0 0 0 {x + w} {y + ry}" fill="none" stroke="{z["s"]}" stroke-width="1.6"/>')
        tx = x + 14
        if st:
            self.estado(x + 20, y + 37, st, layer="box")
            tx = x + 32
        self.T(tx, y + 42, titulo, 15, 700, INK, layer="box")
        ly = y + 61
        for ln in lineas:
            self._linea(x + 14, ly, ln, z)
            ly += 17
        return Box(x, y, w, h)

    def nota(self, x, y, w, h, lineas, titulo=None, size=12.5):
        """Nota UML (esquina doblada)."""
        c = 13
        self.a(
            "box",
            f'<path d="M {x} {y} H {x + w - c} L {x + w} {y + c} V {y + h} H {x} Z" fill="#FFFBEA" stroke="#C9B458" stroke-width="1.2"/>',
        )
        self.a("box", f'<path d="M {x + w - c} {y} V {y + c} H {x + w}" fill="none" stroke="#C9B458" stroke-width="1.2"/>')
        ly = y + 21
        if titulo:
            self.T(x + 12, ly, titulo, size, 700, INK, layer="box")
            ly += 18
        for ln in lineas:
            self.T(x + 12, ly, ln, size, 400, TEXT, layer="box")
            ly += 17
        return Box(x, y, w, h)

    def actor(self, cx, y, nombre, color=INK):
        """Persona (actor UML). Ocupa de y a y+76; el nombre va debajo."""
        st_ = f'stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round"'
        self.a("box", f'<circle cx="{cx}" cy="{y + 11}" r="10" fill="#FFFFFF" stroke="{color}" stroke-width="2"/>')
        self.a("box", f'<path d="M {cx} {y + 21} V {y + 50} M {cx - 17} {y + 32} H {cx + 17} M {cx} {y + 50} L {cx - 14} {y + 74} M {cx} {y + 50} L {cx + 14} {y + 74}" {st_}/>')
        self.T(cx, y + 94, nombre, 14.5, 700, INK, anchor="middle", layer="box")
        return Box(cx - 18, y, 36, 76)

    # ------------------------------------------------------------------ líneas
    def flecha(self, pts, k="linea", punta="tri", cola=None, r=8, layer="line"):
        """Polilínea con esquinas redondeadas. k: main | linea | opc. punta: tri | abierta | None."""
        color, sw, dash = {
            "main": (MAIN, 2.6, ""),
            "rojo": ("#D9502A", 2.6, ""),
            "linea": (LINE, 1.7, ""),
            "opc": (PLAN, 1.7, ' stroke-dasharray="6 5"'),
            "ret": (LINE, 1.5, ' stroke-dasharray="5 4"'),
        }[k]
        pts = [tuple(map(float, p)) for p in pts]
        rec = 7 if punta == "tri" else 0.8

        def acorta(p, q, n):
            dx, dy = q[0] - p[0], q[1] - p[1]
            m = (dx * dx + dy * dy) ** 0.5 or 1
            return (q[0] - dx / m * n, q[1] - dy / m * n)

        fin, ini = pts[-1], pts[0]
        if punta:
            pts[-1] = acorta(pts[-2], pts[-1], rec)
        if cola:
            pts[0] = acorta(pts[1], pts[0], 7 if cola == "tri" else 0.8)
        dpath = f"M {_f(pts[0][0])} {_f(pts[0][1])}"
        for i in range(1, len(pts) - 1):
            p, c, n = pts[i - 1], pts[i], pts[i + 1]
            a_ = acorta(p, c, min(r, ((c[0] - p[0]) ** 2 + (c[1] - p[1]) ** 2) ** 0.5 / 2))
            b_ = acorta(n, c, min(r, ((c[0] - n[0]) ** 2 + (c[1] - n[1]) ** 2) ** 0.5 / 2))
            dpath += f" L {_f(a_[0])} {_f(a_[1])} Q {_f(c[0])} {_f(c[1])} {_f(b_[0])} {_f(b_[1])}"
        dpath += f" L {_f(pts[-1][0])} {_f(pts[-1][1])}"
        self.a(layer, f'<path d="{dpath}" fill="none" stroke="{color}" stroke-width="{sw}" stroke-linejoin="round"{dash}/>')

        def cabeza(desde, hasta, tipo):
            dx, dy = hasta[0] - desde[0], hasta[1] - desde[1]
            m = (dx * dx + dy * dy) ** 0.5 or 1
            ux, uy = dx / m, dy / m
            L_, W_ = (11, 5) if tipo == "tri" else (10, 5)
            bx, by = hasta[0] - ux * L_, hasta[1] - uy * L_
            p1 = (bx - uy * W_, by + ux * W_)
            p2 = (bx + uy * W_, by - ux * W_)
            if tipo == "tri":
                self.a(layer, f'<polygon points="{_f(hasta[0])},{_f(hasta[1])} {_f(p1[0])},{_f(p1[1])} {_f(p2[0])},{_f(p2[1])}" fill="{color}"/>')
            else:
                self.a(
                    layer,
                    f'<path d="M {_f(p1[0])} {_f(p1[1])} L {_f(hasta[0])} {_f(hasta[1])} L {_f(p2[0])} {_f(p2[1])}" fill="none" '
                    f'stroke="{color}" stroke-width="{sw}" stroke-linejoin="round" stroke-linecap="round"/>',
                )

        if punta:
            cabeza(pts[-2] if len(pts) > 1 else ini, fin, punta)
        if cola:
            cabeza(pts[1], ini, cola)

    def piruleta(self, x1, y, xc, x2, k="main", r=7.5, st=None):
        """Interfaz ofrecida (bolita) desde x1 hasta xc, y el enchufe del que la usa desde x2."""
        color, sw, dash = {"main": (MAIN, 2.6, ""), "rojo": ("#D9502A", 2.6, ""), "linea": (LINE, 1.7, ""), "opc": (PLAN, 1.7, ' stroke-dasharray="6 5"')}[k]
        s1 = 1 if xc > x1 else -1
        self.a("line", f'<path d="M {x1} {y} H {_f(xc - s1 * r)}" stroke="{color}" stroke-width="{sw}" fill="none"/>')
        self.a("top", f'<circle cx="{xc}" cy="{y}" r="{r}" fill="#FFFFFF" stroke="{color}" stroke-width="{sw}"/>')
        R = r + 5.5
        sweep = 1 if x2 > xc else 0
        self.a(
            "line",
            f'<path d="M {xc} {_f(y - R)} A {R} {R} 0 0 {sweep} {xc} {_f(y + R)}" fill="none" stroke="{color}" stroke-width="{sw}"{dash}/>',
        )
        s2 = 1 if x2 > xc else -1
        self.a("line", f'<path d="M {_f(xc + s2 * R)} {y} H {x2}" stroke="{color}" stroke-width="{sw}" fill="none"{dash}/>')

    def piruleta_v(self, x, y1, yc, y2, k="main", r=7.5):
        """Igual que piruleta, en vertical: la ofrece quien está en y1; la usa quien está en y2."""
        color, sw, dash = {"main": (MAIN, 2.6, ""), "rojo": ("#D9502A", 2.6, ""), "linea": (LINE, 1.7, ""), "opc": (PLAN, 1.7, ' stroke-dasharray="6 5"')}[k]
        s1 = 1 if yc > y1 else -1
        self.a("line", f'<path d="M {x} {y1} V {_f(yc - s1 * r)}" stroke="{color}" stroke-width="{sw}" fill="none"/>')
        self.a("top", f'<circle cx="{x}" cy="{yc}" r="{r}" fill="#FFFFFF" stroke="{color}" stroke-width="{sw}"/>')
        R = r + 5.5
        sweep = 0 if y2 > yc else 1
        self.a(
            "line",
            f'<path d="M {_f(x - R)} {yc} A {R} {R} 0 0 {sweep} {_f(x + R)} {yc}" fill="none" stroke="{color}" stroke-width="{sw}"{dash}/>',
        )
        s2 = 1 if y2 > yc else -1
        self.a("line", f'<path d="M {x} {_f(yc + s2 * R)} V {y2}" stroke="{color}" stroke-width="{sw}" fill="none"{dash}/>')

    def rot(self, x, y, lineas, anchor="middle", bg="#FFFFFF", paso=None, size=13, lh=17, st=None):
        """Rótulo de una flecha. `lineas`: strings o (texto, estilo) con estilo b | n | m | mono | tramos."""
        filas = []
        for ln in lineas:
            if isinstance(ln, tuple):
                t, k = ln
            else:
                t, k = ln, "n"
            if isinstance(t, list):
                wpx = self.ancho(t, size)
            else:
                wpx = tw(t, size - (1 if k == "mono" else 0.5 if k == "m" else 0), k == "b", k == "mono")
            filas.append((t, k, wpx))
        pre = (28 if paso else 0) + (17 if st else 0)
        wmax = max(f[2] + (pre if i == 0 else 0) for i, f in enumerate(filas))
        if anchor == "middle":
            x0 = x - wmax / 2
        elif anchor == "end":
            x0 = x - wmax
        else:
            x0 = x
        if bg:
            self.a(
                "top",
                f'<rect x="{_f(x0 - 5)}" y="{_f(y - size - 3)}" width="{_f(wmax + 10)}" height="{_f(lh * len(filas) + 7)}" rx="3" fill="{bg}"/>',
            )
        for i, (t, k, wpx) in enumerate(filas):
            yy = y + i * lh
            off = pre if i == 0 else 0
            if anchor == "middle":
                tx = x0 + (wmax - wpx - off) / 2 + off
            elif anchor == "end":
                tx = x0 + wmax - wpx
            else:
                tx = x0 + off
            if i == 0 and paso:
                self.paso(tx - pre + 11, yy - 4.5, paso)
            if i == 0 and st:
                self.estado(tx - 10, yy - 4.5, st, r=5)
            if isinstance(t, list):
                self.T(tx, yy, t, size, 400, INK)
            elif k == "b":
                self.T(tx, yy, t, size, 700, INK)
            elif k == "m":
                self.T(tx, yy, t, size - 0.5, 400, MUTED)
            elif k == "mono":
                self.T(tx, yy, t, size - 1, 400, TEXT, mono=True)
            else:
                self.T(tx, yy, t, size, 400, INK)

    # ------------------------------------------------------------------ secuencia
    def vida(self, x, y, y2, zona, titulo, sub=None, persona=False, w=192):
        """Línea de vida: cabecera centrada en x y línea punteada hasta y2."""
        z = Z[zona]
        if persona:
            self.actor(x, y - 22, titulo)
            y0 = y + 84
        else:
            self.a("box", f'<rect x="{x - w / 2}" y="{y}" width="{w}" height="56" rx="6" fill="{z["f"]}" stroke="{z["s"]}" stroke-width="1.8"/>')
            self.T(x, y + (24 if sub else 33), titulo, 15.5, 700, z["i"], anchor="middle", layer="box")
            if sub:
                self.T(x, y + 43, sub, 12, 400, MUTED, anchor="middle", layer="box")
            y0 = y + 56
        self.a("zone", f'<path d="M {x} {y0} V {y2}" stroke="#A9AEB6" stroke-width="1.4" stroke-dasharray="5 5" fill="none"/>')

    def msj(self, x1, x2, y, lineas, k="main", paso=None, st=None, abajo=None, medio=128):
        """Mensaje de x1 a x2. El texto va sobre la flecha, cerca de quien lo manda; `abajo` va debajo."""
        self.flecha([(x1, y), (x2, y)], k, "abierta" if k == "ret" else "tri")
        s = 1 if x2 > x1 else -1
        cx = x1 + s * min(medio, abs(x2 - x1) / 2)
        self.rot(cx, y - 11, lineas, paso=paso, st=st)
        if abajo:
            self.rot(cx, y + 20, [(t, "m") if isinstance(t, str) else t for t in abajo], lh=16)

    def propio(self, x, y, lineas, paso=None, st=None, k="main"):
        """Mensaje a sí mismo: un lazo a la derecha de la línea de vida."""
        self.flecha([(x, y), (x + 34, y), (x + 34, y + 24), (x, y + 24)], k, "tri", r=6)
        self.rot(x + 48, y + 8, lineas, anchor="start", paso=paso, st=st)

    def marco(self, x, y, w, h, etiqueta, st=None):
        """Fragmento UML: un recuadro con pestaña que agrupa varios mensajes."""
        self.a("zone", f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="#FAFAFB" stroke="#C3C7CE" stroke-width="1.3"/>')
        tw_ = tw(etiqueta, 13, True) + 28 + (18 if st else 0)
        self.a("box", f'<path d="M {x} {y + 28} V {y + 6} Q {x} {y} {x + 6} {y} H {x + tw_} V {y + 18} L {x + tw_ - 10} {y + 28} Z" fill="#ECEEF1" stroke="#C3C7CE" stroke-width="1.3"/>')
        tx = x + 12
        if st:
            self.estado(x + 17, y + 14, st, r=5.5)
            tx = x + 30
        self.T(tx, y + 19, etiqueta, 13, 700, INK)

    # ------------------------------------------------------------------ leyenda
    def panel(self, x, y, w, h):
        self.a("zone", f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="#FFFFFF" stroke="#D5D8DC" stroke-width="1.2"/>')

    def ley_estado(self, x, y, titulo="Estado"):
        self.T(x, y, titulo, 13, 700, INK)
        for i, (k, nombre) in enumerate(ESTADOS.items()):
            yy = y + 24 + i * 22
            self.estado(x + 7, yy - 4.5, k)
            self.T(x + 24, yy, nombre, 12.5, 400, TEXT)
        return 150

    def ley_lineas(self, x, y, filas, titulo="Líneas"):
        self.T(x, y, titulo, 13, 700, INK)
        for i, (k, nombre) in enumerate(filas):
            yy = y + 24 + i * 22
            if k == "piruleta":
                self.piruleta(x, yy - 4.5, x + 17, x + 40, "linea", r=5.5)
            elif k == "ret":
                self.flecha([(x, yy - 4.5), (x + 38, yy - 4.5)], "ret", "abierta", layer="top")
            else:
                self.flecha([(x, yy - 4.5), (x + 38, yy - 4.5)], k, "tri", layer="top")
            self.T(x + 50, yy, nombre, 12.5, 400, TEXT)

    def ley_zonas(self, x, y, zonas, titulo="Colores", cols=1, dx=150):
        self.T(x, y, titulo, 13, 700, INK)
        per = -(-len(zonas) // cols)
        for i, (k, nombre) in enumerate(zonas):
            cx_ = x + (i // per) * dx
            yy = y + 24 + (i % per) * 22
            z = Z[k]
            self.a("top", f'<rect x="{cx_}" y="{yy - 12}" width="22" height="14" rx="3" fill="{z["f"]}" stroke="{z["s"]}" stroke-width="1.4"/>')
            self.T(cx_ + 32, yy, nombre, 12.5, 400, TEXT)

    def svg(self):
        out = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}">',
            f"  <title>pcx · arquitectura v2 · {self.n} de {TOTAL} · {escape(self.titulo)}</title>",
            "  <!-- Generado por generar.py: no editar a mano. Regenerar con ./render.sh -->",
            f'  <rect width="{self.w}" height="{self.h}" fill="#FFFFFF"/>',
        ]
        for k in ("zone", "line", "box", "top"):
            out.append(f'  <g id="{k}">')
            out += ["    " + f for f in self.L[k]]
            out.append("  </g>")
        out.append("</svg>")
        return "\n".join(out) + "\n"

    def guardar(self):
        (AQUI / f"{self.nombre}.svg").write_text(self.svg(), encoding="utf-8")


# =============================================================================
# 1 · Vista general
# =============================================================================
def d1():
    d = D(1, "1-vista-general", 1600, 1336, "Vista general",
          "Qué hay y cómo se conecta: la persona, su agente, el computador, la mesa y la nube.", "UML de despliegue, simplificado")
    zn, zd, zm = Z["nube"], Z["device"], Z["mesa"]

    # --- nube
    d.nodo(400, 160, 1160, 168, "nube", "«nube» · Render", "Gateway pcx", "sirve la web app y guarda una sesión por persona",
           st="ok", quien="Daniel")
    chips = [
        ("MCP de la sesión", "tools para el agente", "ok", None),
        ("Sesiones", "una llave por persona", "ok", None),
        ("Entornos (presets)", "clase · lab · escritorio", "plan", "Pablo y Matilda"),
        ("Web app", "se abre en el navegador", "ok", None),
        ("Compartir e historial", "enlace /ver, solo lectura", "ok", None),
    ]
    cx = []
    for i, (t, l, st, q) in enumerate(chips):
        b = d.comp(424 + i * 226, 244, 208, 64, "pm" if q else "nube", t, [(l, "m")], st=st, quien=q, icono=False, ts=14.5)
        cx.append(b)

    # --- landing
    la = d.comp(32, 208, 304, 100, "nube", "Landing", ["link de setup y descarga del", "motor con sus pesos (diagrama 4)"],
                estereo="«sitio web»", st="plan", icono=False, quien="Manu")

    # --- computador
    d.nodo(400, 424, 1160, 440, "device", "«device»", "Computador del usuario",
           "aquí corren el agente y el motor: todo el cómputo es local", centro=True)
    ag = d.comp(432, 512, 320, 104, "agente", "Agente de IA", ["Claude Code · Codex · OpenCode", ("+ plugin pcx: 6 skills (diagrama 3)", "z")],
                estereo="«aplicación»", st="ok")
    mo = d.comp(432, 696, 320, 136, "motor", "Motor pcx", ["mira la cámara y separa los objetos", "calcula dónde proyectar, en mm",
                                                         ("percepción: SAM y tracking", "m")],
                estereo="«proceso» · Rust", st="part", quien="Manu")
    d.quien(mo.r - 12 - (tw("Manu", 11, True) + 18) - 6, mo.t, "Max · percepción")
    wa = d.grupo(1000, 504, 528, 336, "web", "«navegador»", "Web app pcx", "dibuja con WebGPU y HTML", st="ok", quien="Daniel")
    co = d.comp(1024, 592, 480, 72, "web", "Consola", [("eliges cámara y pantalla; ves lo que hace el agente", "m")], st="ok", icono=False, ts=14.5)
    ve = d.comp(1024, 720, 480, 88, "web", "Ventana de proyección", [("a pantalla completa en la segunda pantalla", "m")],
                st="ok", icono=False, ts=14.5)

    # --- opcionales
    cl = d.comp(152, 672, 216, 76, "ext", "Claude API", [("nombra objetos nuevos", "m")], estereo="opcional · nube", st="part", icono=False, ts=14.5)
    ha = d.comp(152, 764, 216, 76, "ext", "Home Assistant", [("domótica, con confirmación", "m")], estereo="opcional · red local", st="part",
                icono=False, ts=14.5)

    # --- mesa
    d.nodo(32, 952, 1528, 152, "mesa", "«lugar»", "Mesa física", "lo que se ve y se toca")
    ca = d.comp(432, 984, 320, 96, "mesa", "Cámara", [("mira la mesa desde arriba", "m")], estereo="«dispositivo»", icono=False)
    ob = d.comp(872, 996, 192, 72, "mesa", "Objetos y manos", [("lo real", "m")], icono=False, ts=14.5)
    pr = d.comp(1184, 984, 344, 96, "mesa", "Proyector", [("es la segunda pantalla del computador", "m")], estereo="«dispositivo»", icono=False)

    # --- persona
    pe = d.actor(88, 512, "Persona")

    # --- flechas principales
    d.flecha([(116, 564), (ag.l, 564)], "main")
    d.rot(274, 552, [("le pide algo", "b")], paso=1)
    d.flecha([(528, ag.t), (528, cx[0].b)], "main")
    d.rot(548, 368, [("llama tools por MCP", "b"), ("con la llave de su sesión", "m")], anchor="start", paso=2)
    d.flecha([(cx[3].cx, cx[3].b), (cx[3].cx, wa.t)], "main", cola="tri")
    d.rot(cx[3].cx + 20, 368, [("pasa las órdenes del agente", "b"), ("y recibe los eventos de la mesa", "m")], anchor="start", paso=3)
    d.flecha([(ca.cx, ca.t), (mo.cx, mo.b)], "main")
    d.rot(ca.cx + 20, 904, [("entrega el video", "b"), ("por USB", "m")], anchor="start", paso=4, bg=None)
    d.flecha([(mo.r, 764), (ve.l, 764)], "main")
    d.rot(876, 734, [("«proyecta esto", "b")], paso=5, bg=zd["f"])
    d.rot(876, 751, [("en estas coordenadas»", "b")], bg=None)
    d.rot(876, 788, [("ws://127.0.0.1:9230", "mono"), ("viajan coordenadas, no video", "m"), ("hoy local; puede ser remoto por proxy", "m")],
          bg=zd["f"])
    d.flecha([(1264, ve.b), (1264, pr.t)], "main")
    d.rot(1284, 904, [("dibuja en la segunda pantalla", "b"), ("por HDMI", "m")], anchor="start", paso=6, bg=None)

    # --- flechas secundarias
    d.flecha([(ca.r, ca.cy), (ob.l, ca.cy)], "linea")
    d.rot((ca.r + ob.l) / 2, ca.cy - 10, ["mira"], bg=zm["f"])
    d.flecha([(pr.l, pr.cy), (ob.r, pr.cy)], "linea")
    d.rot((pr.l + ob.r) / 2, pr.cy - 10, ["ilumina"], bg=zm["f"])
    d.flecha([(88, 628), (88, 952)], "linea")
    d.flecha([(88, 504), (88, la.b)], "opc", "abierta")
    d.rot(104, 412, ["copia el link", "de setup"], anchor="start", bg=None)
    d.rot(104, 900, ["toca y mueve", "los objetos"], anchor="start", bg=None)
    d.flecha([(mo.l, 710), (cl.r, 710)], "opc", "abierta")
    d.flecha([(mo.l, 802), (ha.r, 802)], "opc", "abierta")

    # --- leyenda y estado real
    d.panel(32, 1136, 800, 168)
    d.ley_estado(56, 1166)
    d.ley_lineas(216, 1166, [("main", "flujo principal, en orden"), ("linea", "otra conexión"), ("opc", "opcional")])
    d.ley_zonas(448, 1166, LEY_DUENOS, titulo="Color por capa", cols=2, dx=170)
    d.panel(856, 1136, 704, 168)
    d.T(880, 1166, "Estado real hoy (sáb 21-nov)", 13, 700, INK)
    hoy = [
        ("ok", "Web app, sesiones y MCP del gateway: en vivo en Render.", "Hoy la web app hace todo: capta la cámara, sigue las manos y dibuja."),
        ("part", "Motor Rust: percepción, calibración y manos ya probadas como", "bibliotecas; falta cablearlas a su servidor local."),
        ("plan", "Link de setup, descarga del motor con pesos y entornos (presets).", None),
    ]
    yy = 1192
    for st, l1, l2 in hoy:
        d.estado(887, yy - 4.5, st)
        d.T(904, yy, l1, 12.5, 400, TEXT)
        if l2:
            d.T(904, yy + 17, l2, 12.5, 400, MUTED)
        yy += 42 if l2 else 22
    return d


def _hoy(d, x, y, items, titulo="Estado real hoy (sáb 21-nov)"):
    """Bloque «estado real hoy»: lista de (estado, línea 1, línea 2 o None)."""
    d.T(x, y, titulo, 13, 700, INK)
    yy = y + 26
    for st, l1, l2 in items:
        d.estado(x + 7, yy - 4.5, st)
        d.T(x + 24, yy, l1, 12.5, 400, TEXT)
        if l2:
            d.T(x + 24, yy + 17, l2, 12.5, 400, MUTED)
        yy += 42 if l2 else 24


# =============================================================================
# 2 · El computador por dentro
# =============================================================================
def d2():
    d = D(2, "2-computador", 1600, 1304, "El computador por dentro",
          "El motor (Rust) y la web app (navegador), con las interfaces que los unen.", "UML de componentes")
    zm, zd = Z["motor"], Z["device"]

    gw = d.comp(1072, 152, 464, 84, "nube", "Gateway pcx", ["sirve la web app · sesiones · MCP de la sesión"],
                estereo="«servicio» · Render", st="ok", quien="Daniel")

    d.nodo(32, 296, 1528, 656, "device", "«device»", "Computador del usuario", "aquí corren el agente y el motor")
    ag = d.comp(464, 316, 336, 80, "agente", "Agente de IA", ["Claude Code · Codex · OpenCode"], estereo="«aplicación» + plugin pcx", st="ok")

    # --- MCP de la sesión: lo ofrece el gateway, lo usa el agente
    d.piruleta(gw.l, 194, 1016, 996, "linea")
    d.flecha([(996, 194), (984, 194), (984, 356), (ag.r, 356)], "linea", None)
    d.rot(892, 344, [("MCP de la sesión", "b")], st="ok", bg=zd["f"])
    d.rot(892, 377, [("llama tools con su llave", "m")], bg=zd["f"])

    # --- motor
    mo = d.grupo(64, 432, 808, 488, "motor", "«proceso» · Rust", "Motor pcx", "separa los objetos y calcula dónde proyectar",
                 st="part", quien="Manu")
    d.comp(456, 448, 184, 84, "motor", "Seguridad", [("llaves y confirmación", "m"), ("física de domótica", "m")], st="ok", icono=False, ts=14.5)
    mem = d.cil(664, 448, 184, 84, "motor", "Memoria", [("SQLite", "m")], st="ok")
    cap = d.comp(88, 576, 216, 136, "motor", "Cámara y foto limpia", ["lee la cámara; pide negro", "y toma la foto sin luz", ("pcx-capture", "mono")], st="ok", icono=False)
    per = d.comp(360, 576, 216, 136, "max", "Percepción",
                 ["cascada N0–N2b, MobileSAM", "ids estables (tracking)", "y percepción por escena", ("pcx-objects", "mono")],
                 st="part", icono=False, quien="Max")
    mun = d.comp(632, 576, 216, 136, "motor", "Mundo y coordenadas", ["qué hay y dónde, en mm", "decide qué se dibuja", ("pcx-core", "mono")],
                 st="ok", icono=False)
    mod = d.comp(360, 784, 216, 104, "motor", "Modelos (pesos)", ["MobileSAM y manos", ("ONNX Runtime", "mono")], st="part", icono=False)
    cal = d.comp(632, 784, 216, 104, "motor", "Calibración", ["homografía + lente (k1, k2)", ("pcx-calib", "mono")], st="part", icono=False)

    d.flecha([(cap.r, 644), (per.l, 644)], "main")
    d.rot(332, 632, ["foto"], bg=zm["f"])
    d.flecha([(per.r, 644), (mun.l, 644)], "main")
    d.rot(604, 632, ["objetos"], bg=zm["f"])
    d.flecha([(per.cx, per.b), (mod.cx, mod.t)], "opc", "abierta")
    d.rot(per.cx + 14, 753, ["carga"], anchor="start", bg=None)
    d.flecha([(mun.cx, mun.b), (cal.cx, cal.t)], "linea", "tri", cola="tri")
    d.rot(mun.cx + 14, 753, ["px ↔ mm"], anchor="start", bg=None)
    d.flecha([(756, mun.t), (756, mem.b)], "linea")
    d.rot(770, 559, ["guarda"], anchor="start", bg=None)

    # --- web app
    wa = d.grupo(1072, 432, 464, 488, "web", "«navegador»", "Web app pcx", "la sirve el gateway; corre en tu navegador", st="ok", quien="Daniel")
    dib = d.comp(1096, 592, 216, 104, "web", "Dibujo", ["widgets, efectos y HTML", ("WebGPU (hoy Canvas 2D)", "m")], st="part", icono=False)
    ses = d.comp(1328, 592, 184, 104, "web", "Sesión", [("recibe órdenes", "m"), ("y manda eventos", "m")], st="ok", icono=False)
    ven = d.comp(1096, 784, 216, 104, "web", "Ventana de proyección", [("pantalla completa en", "m"), ("la segunda pantalla", "m")], st="ok", icono=False)
    d.comp(1328, 720, 184, 72, "web", "Consola", [("eliges cámara y pantalla", "m")], st="ok", icono=False, ts=14.5)
    d.comp(1328, 816, 184, 72, "web", "Manos y voz", [("MediaPipe y micrófono", "m")], st="ok", icono=False, ts=14.5)
    d.flecha([(ses.l, 644), (dib.r, 644)], "linea")
    d.flecha([(dib.cx, dib.b), (ven.cx, ven.t)], "main")
    d.rot(dib.cx + 14, 745, ["pinta"], anchor="start", bg=None)
    d.flecha([(1420, gw.b), (1420, ses.t)], "linea", "tri", cola="tri")
    d.rot(1406, 338, [("sirve la web app", "n"), ("pasa órdenes y recibe eventos", "m")], anchor="end", bg=zd["f"])

    # --- servidor local: lo ofrece el motor, lo usa la web app
    for px in (mo.r, wa.l):
        d.a("top", f'<rect x="{px - 6}" y="638" width="12" height="12" fill="#FFFFFF" stroke="{MAIN}" stroke-width="2"/>')
    d.flecha([(mun.r, 644), (mo.r - 6, 644)], "main", None)
    d.piruleta(mo.r + 6, 644, 950, wa.l - 6, "main")
    d.flecha([(wa.l + 6, 644), (dib.l, 644)], "main")
    d.rot(972, 584, [("Servidor local", "b"), ("ws://127.0.0.1:9230", "mono")], st="part", bg=zd["f"])
    d.rot(972, 682, ["«proyecta esto aquí»", "«apágate para la foto»", "«muestra el patrón»"], size=12.5, bg=zd["f"])
    d.nota(888, 752, 168, 132, ["Hoy el motor es local.", "Si el computador es", "lento, la web app puede", "apuntar a un motor", "remoto por un proxy."],
           titulo="Desacoplado", size=12)

    # --- cámara y proyector
    cam = d.comp(88, 1000, 216, 72, "mesa", "Cámara", [("conectada por USB", "m")], icono=False)
    pro = d.comp(1096, 1000, 216, 72, "mesa", "Proyector", [("segunda pantalla, por HDMI", "m")], icono=False)
    d.flecha([(cam.cx, cam.t), (cap.cx, cap.b)], "main")
    d.rot(cap.cx + 14, 832, ["entrega", "el video"], anchor="start", bg=None)
    d.flecha([(ven.cx, ven.b), (pro.cx, pro.t)], "main")
    d.rot(ven.cx + 14, 980, ["proyecta"], anchor="start", bg=None)

    # --- leyenda
    d.panel(336, 992, 728, 280)
    d.ley_estado(360, 1022)
    d.ley_lineas(500, 1022, [("main", "camino del video a la luz"), ("linea", "otra conexión"), ("opc", "usa (carga)"),
                             ("piruleta", "interfaz (bolita: la ofrece)")])
    d.ley_zonas(752, 1022, LEY_DUENOS[:5], titulo="Color por capa")
    d.a("top", f'<path d="M 360 1142 H 1040" stroke="{RULE}" stroke-width="1.2"/>')
    _hoy(d, 360, 1168, [
        ("ok", "La web app hoy hace todo: capta la cámara, sigue las manos y calibra en el navegador.", None),
        ("part", "El servidor del motor ya atiende a un renderer nativo (pcx-renderer). Falta conectar la web app.", None),
        ("part", "El motor también trae un MCP local, hecho y probado. Hoy los agentes usan el de la sesión.", None),
    ])
    return d


# =============================================================================
# 3 · Interfaz para agentes
# =============================================================================
def d3():
    d = D(3, "3-agentes", 1600, 1264, "Interfaz para agentes",
          "Las skills le enseñan al agente cómo usar pcx; las tools MCP son lo que puede hacer.", "UML de componentes + matriz")
    za = Z["agente"]

    # --- fila 1: cómo llega al agente
    ins = d.comp(32, 204, 280, 104, "motor", "Instalador", [("integrations/install.sh", "mono"), ("integrations/install.ps1", "mono")],
                 estereo="«script»", st="ok", icono=False, quien="Manu")
    d.flecha([(ins.r, 256), (392, 256)], "linea")
    d.rot(352, 244, ["instala"])
    d.grupo(392, 144, 720, 208, "agente", "«aplicación»", "Agente de IA", "Claude Code · Codex · OpenCode")
    d.a("box", f'<rect x="408" y="232" width="688" height="104" rx="6" fill="#FFFFFF" stroke="{za["s"]}" stroke-width="1.3" stroke-dasharray="5 4"/>')
    d.T(1084, 224, [("plugin pcx", {"w": 700, "fill": za["i"]}), (" · lo que se instala", {"fill": MUTED})], 12.5, 400, MUTED, anchor="end", layer="box")
    d.comp(420, 252, 212, 68, "agente", "6 skills", [("le enseñan cómo usar pcx", "m")], st="ok", icono=False, ts=14.5)
    d.comp(648, 252, 212, 68, "agente", "/pcx-estado", [("comando: estado en una mirada", "m")], st="ok", icono=False, ts=14, mono=True)
    c3 = d.comp(876, 252, 212, 68, "agente", "Conexión MCP", [("dirección + llave de la sesión", "m")], st="ok", icono=False, ts=14.5)
    gw = d.comp(1288, 228, 280, 116, "nube", "Gateway de la sesión", ["12 tools hoy", ("+ las de cada entorno (planeado)", "m")],
                estereo="«servicio» · Render", st="ok", quien="Daniel")
    d.piruleta(gw.l, 286, 1208, c3.r, "main")
    d.rot(1200, 258, [("MCP", "b")])
    d.rot(1200, 316, [("llama tools", "m")])
    d.T(392, 380, "Claude Code: plugin del marketplace pcx-agentes  ·  Codex y OpenCode: Agent Skills en ~/.agents/skills/ y el MCP registrado",
        12.5, 400, MUTED)

    # --- matriz skills × tools
    g = ("#E9ECEF", INK, "#F8F9FA")
    C = {"ver": g, "act": g, "aut": g, "cal": g, "casa": g}
    grupos = [
        ("Ver", "ver", [("scene", "qué hay", 72), ("look", "foto", 72), ("events_wait", "espera toques", 100), ("devices", "equipos", 80),
                        ("history*", "memoria", 80)]),
        ("Actuar", "act", [("project", "dibuja", 80), ("remove", "borra", 80), ("ask", "pregunta", 72), ("scenes*", "pestañas", 80)]),
        ("Automatizar", "aut", [("run_flow", "se maneja solo", 104)]),
        ("Calibrar", "cal", [("calibrate", "alinea la luz", 96)]),
        ("Casa", "casa", [("home", "domótica", 104)]),
    ]
    filas = [
        ("pcx-operar", "Ver la mesa y proyectar sobre ella: mirar, señalar y preguntar.",
         "scene look events_wait project remove ask", "devices history*", None),
        ("pcx-flujos", "Crear guías, juegos y menús que reaccionan solos al toque.", "run_flow", "scene look events_wait project remove", None),
        ("pcx-calibrar", "Alinear cámara, proyector y mesa cuando la luz cae corrida.", "calibrate", "scene look events_wait devices project", None),
        ("pcx-efectos", "Crear efectos de luz nuevos (shaders) para el proyector.", "", "project", "trabaja en el repo: escribe shaders"),
        ("pcx-domotica", "Prender y apagar cosas de la casa, con confirmación física.", "home", "events_wait run_flow", None),
        ("pcx-extender", "Sumar capacidades: cámaras, detectores y tools nuevas.", "", "", "no llama tools: trabaja en el código de pcx"),
        ("/pcx-estado", "Comando: el estado de la mesa en una mirada (solo lectura).", "scene events_wait devices", "look", None),
    ]
    hoy = [
        ("Hoy, en la sesión", "web app + gateway (lo que está en vivo)",
         dict(scene="ok", look="ok", events_wait="ok", devices="ok", project="ok", remove="ok", ask="ok", run_flow="plan", calibrate="ok",
              home="plan", **{"history*": "ok", "scenes*": "ok"})),
        ("Hoy, en el motor", "Rust (probado, sin cablear a la sesión)",
         dict(scene="ok", look="ok", events_wait="ok", devices="ok", project="ok", remove="ok", ask="ok", run_flow="ok", calibrate="part",
              home="part")),
    ]
    X0, XT, Y0 = 32, 512, 440
    RH, HH = 58, 40
    y_filas = Y0 + 30 + 62
    y_hoy = y_filas + RH * len(filas) + 34
    y_fin = y_hoy + HH * len(hoy)
    d.T(X0, Y0 - 16, "Qué tools usa cada skill", 17, 700, INK)

    col, x = {}, XT
    for nombre, ck, tools in grupos:
        fill, ink, tint = C[ck]
        gw_ = sum(t[2] for t in tools)
        d.a("zone", f'<rect x="{x}" y="{Y0}" width="{gw_}" height="{y_fin - Y0}" rx="6" fill="{tint}"/>')
        d.a("zone", f'<path d="M {x} {Y0 + 30} V {Y0 + 6} Q {x} {Y0} {x + 6} {Y0} H {x + gw_ - 6} Q {x + gw_} {Y0} {x + gw_} {Y0 + 6} V {Y0 + 30} Z" fill="{fill}"/>')
        d.T(x + gw_ / 2, Y0 + 20, nombre, 13, 700, ink, anchor="middle")
        for t, glosa, w in tools:
            col[t] = x + w / 2
            d.T(x + w / 2, Y0 + 56, t, 12.5, 700, INK, anchor="middle", mono=True)
            d.T(x + w / 2, Y0 + 74, glosa, 11.5, 400, MUTED, anchor="middle")
            x += w
        x += 8
    x_fin = x - 8

    for i, (nombre, para, prim, sec, nota) in enumerate(filas):
        y = y_filas + i * RH
        cmd = nombre.startswith("/")
        d.a("zone", f'<path d="M {X0} {y} H {x_fin}" stroke="{"#9AA0A8" if cmd else RULE}" stroke-width="{1.4 if cmd else 1.2}"/>')
        d.estado(X0 + 8, y + 19, "ok", r=5)
        d.T(X0 + 22, y + 24, nombre, 14.5, 700, za["i"], mono=True)
        d.T(X0 + 22, y + 44, para, 13, 400, TEXT)
        for t in prim.split():
            d.a("top", f'<circle cx="{col[t]}" cy="{y + RH / 2}" r="8.5" fill="{za["s"]}"/>')
        for t in sec.split():
            d.a("top", f'<circle cx="{col[t]}" cy="{y + RH / 2}" r="6.5" fill="#FFFFFF" stroke="{za["s"]}" stroke-width="2.2"/>')
        if nota:
            d.T(XT + 12, y + RH / 2 + 4.5, nota, 12.5, 400, MUTED, italic=True)
    d.a("zone", f'<path d="M {X0} {y_filas + RH * len(filas)} H {x_fin}" stroke="{RULE}" stroke-width="1.2"/>')

    for i, (t1, t2, mapa) in enumerate(hoy):
        y = y_hoy + i * HH
        d.a("zone", f'<path d="M {X0} {y} H {x_fin}" stroke="{"#9AA0A8" if i == 0 else RULE}" stroke-width="{1.4 if i == 0 else 1.2}"/>')
        d.T(X0 + 22, y + 25, [(t1, {"w": 700, "fill": INK}), ("  " + t2, {"fill": MUTED})], 13, 400, TEXT)
        for t, cx in col.items():
            if t in mapa:
                d.estado(cx, y + HH / 2, mapa[t], r=6)
            else:
                d.T(cx, y + HH / 2 + 4.5, "—", 13, 400, PLAN, anchor="middle")
    d.a("zone", f'<path d="M {X0} {y_fin} H {x_fin}" stroke="{RULE}" stroke-width="1.2"/>')
    d.T(X0, y_hoy - 10, "¿Funciona hoy?", 12.5, 700, INK)

    # --- leyenda
    yl = y_fin + 32
    d.panel(32, yl, 1536, 132)
    d.T(56, yl + 30, "Cómo leer la matriz", 13, 700, INK)
    d.a("top", f'<circle cx="64" cy="{yl + 50}" r="8.5" fill="{za["s"]}"/>')
    d.T(82, yl + 54.5, "la skill usa esa tool siempre", 12.5, 400, TEXT)
    d.a("top", f'<circle cx="64" cy="{yl + 74}" r="6.5" fill="#FFFFFF" stroke="{za["s"]}" stroke-width="2.2"/>')
    d.T(82, yl + 78.5, "la usa a veces, de apoyo", 12.5, 400, TEXT)
    d.T(56, yl + 104, [("*", {"w": 700, "fill": INK}), (" la suma el gateway o la consola; no está en el contrato", {})], 12.5, 400, TEXT)
    d.ley_estado(440, yl + 30, "Estado")
    d.T(640, yl + 30, "Para tener en cuenta", 13, 700, INK)
    notas = [
        [("home", {"mono": True, "w": 700, "fill": INK}), (" nunca actúa sola: la persona confirma con un toque en la mesa.", {})],
        [("run_flow", {"mono": True, "w": 700, "fill": INK}), (" y ", {}), ("home", {"mono": True, "w": 700, "fill": INK}),
         (" existen en el motor Rust, pero la web app de hoy todavía no los atiende.", {})],
        [("scenes", {"mono": True, "w": 700, "fill": INK}), (" (pestañas) y los entornos (presets) aún no tienen skill que los explique.", {})],
        [("Las 6 skills y el comando están instalables hoy en Claude Code, Codex y OpenCode.", {})],
    ]
    for i, n in enumerate(notas):
        d.a("top", f'<circle cx="646" cy="{yl + 49.5 + i * 22}" r="2.2" fill="{MUTED}"/>')
        d.T(658, yl + 54 + i * 22, n, 12.5, 400, TEXT)
    d.h = yl + 132 + 32
    return d


# =============================================================================
# 4 · Instalación
# =============================================================================
def _ley_seq(d, x, y):
    d.T(x, y, "Flechas", 13, 700, INK)
    d.flecha([(x, y + 19.5), (x + 38, y + 19.5)], "main", "tri", layer="top")
    d.T(x + 50, y + 24, "pide o avisa", 12.5, 400, TEXT)
    d.flecha([(x, y + 41.5), (x + 38, y + 41.5)], "ret", "abierta", layer="top")
    d.T(x + 50, y + 46, "responde", 12.5, 400, TEXT)
    d.flecha([(x + 4, y + 58), (x + 26, y + 58), (x + 26, y + 70), (x + 4, y + 70)], "main", "tri", r=4, layer="top")
    d.T(x + 50, y + 68, "lo hace por su cuenta", 12.5, 400, TEXT)


def d4():
    d = D(4, "4-instalacion", 1600, 1512, "Instalación",
          "De un link a una mesa viva: el agente instala el motor, se abre la sesión y se calibra.", "UML de secuencia")
    P, L, A, M, W, G = 112, 368, 624, 880, 1136, 1392
    Y2 = 1340
    d.vida(P, 172, Y2, "ext", "Persona", persona=True)
    d.vida(L, 172, Y2, "motor", "Landing", "sitio web")
    d.vida(A, 172, Y2, "agente", "Agente de IA", "Claude Code · Codex · OpenCode")
    d.vida(M, 172, Y2, "motor", "Motor pcx", "proceso local, en Rust")
    d.vida(W, 172, Y2, "web", "Web app", "en tu navegador")
    d.vida(G, 172, Y2, "nube", "Gateway", "sesiones, en Render")
    d.a("zone", f'<path d="M {A - 104} 158 V 150 H {W + 104} V 158" fill="none" stroke="{Z["device"]["s"]}" stroke-width="1.4"/>')
    d.rot((A + W) / 2, 154, [("en el computador del usuario (device)", "m")])

    d.marco(32, 264, 1536, 396, "1 · Instalar el motor", st="plan")
    d.msj(P, L, 320, ["copia el link de setup"], paso=1, st="plan")
    d.msj(P, A, 368, ["«instala pcx con este link»"], paso=2, st="plan")
    d.msj(A, L, 416, ["pide el paquete del motor"], paso=3, st="plan")
    d.msj(L, A, 480, [("pcx + ONNX Runtime", "n"), ("+ pesos: MobileSAM y manos", "n")], k="ret")
    d.propio(A, 512, ["comprueba el checksum"], paso=4, st="plan")
    d.propio(A, 564, ["instala el plugin: 6 skills y /pcx-estado"], paso=5, st="ok")
    d.msj(A, M, 628, [("pcx up", "mono")], paso=6, st="part", abajo=["levanta ws://127.0.0.1:9230"])

    d.marco(32, 676, 1536, 340, "2 · Abrir la sesión", st="ok")
    d.msj(P, W, 732, ["abre la web app"], paso=7, st="ok", abajo=["elige cámara y segunda pantalla"])
    d.msj(W, G, 792, ["crea la sesión"], paso=8, st="ok")
    d.msj(G, W, 836, [("su propia llave", "n")], k="ret")
    d.msj(W, M, 884, ["se conecta al motor local"], paso=9, st="plan")
    d.msj(P, A, 932, ["le pega la llave (botón «Copiar»)"], paso=10, st="ok")
    d.propio(A, 960, ["registra el MCP de la sesión"], paso=11, st="ok")

    d.marco(32, 1032, 1536, 292, "3 · Calibrar", st="part")
    d.msj(A, G, 1088, [("calibrate", "mono")], paso=12, st="ok", abajo=["por MCP"])
    d.msj(G, W, 1144, ["«muestra el patrón»"], paso=13, st="ok")
    d.propio(M, 1172, ["lo ve por la cámara y calcula homografía + lente"], paso=14, st="part")
    d.msj(G, A, 1248, [("calibrado: error en mm", "n")], k="ret")
    d.msj(A, P, 1296, [("«listo: ya veo la mesa y puedo proyectar»", "n")], k="ret")

    d.panel(32, 1364, 1536, 116)
    d.ley_estado(56, 1394, "Estado de cada paso")
    _ley_seq(d, 264, 1394)
    _hoy(d, 520, 1394, [
        ("ok", "Abrir la sesión ya funciona así en Render. El instalador del plugin también existe (integrations/).", None),
        ("part", "pcx up existe, pero levanta el nodo nativo. Hoy la calibración corre entera en la web app.", None),
        ("plan", "Link de setup, paquete del motor con sus pesos y la conexión de la web app al motor local.", None),
    ])
    return d


# =============================================================================
# 5 · Percepción
# =============================================================================
def d5():
    d = D(5, "5-percepcion", 1600, 1256, "Percepción: del gesto a la luz",
          "Qué pasa cuando alguien mueve un objeto: foto limpia, cascada de lo barato a lo caro y la luz que lo sigue.", "UML de secuencia")
    P, C, M, W, G, A = 112, 368, 624, 880, 1136, 1392
    Y2 = 1080
    zm = Z["max"]
    d.vida(P, 172, Y2, "ext", "Persona", persona=True)
    d.vida(C, 172, Y2, "mesa", "Cámara", "sobre la mesa")
    d.vida(M, 172, Y2, "motor", "Motor pcx", "proceso local, en Rust")
    d.vida(W, 172, Y2, "web", "Web app", "ventana de proyección")
    d.vida(G, 172, Y2, "nube", "Gateway", "sesión, en Render")
    d.vida(A, 172, Y2, "agente", "Agente de IA", "espera con events_wait")

    d.msj(P, C, 304, ["mueve un objeto y saca la mano"], k="linea", paso=1)
    d.msj(C, M, 352, ["video, todo el tiempo"], paso=2, st="ok")
    d.propio(M, 384, ["nota que la mano se fue", ("espera 0,4 s de calma", "m")], paso=3, st="part")
    d.msj(M, W, 468, ["«apágate un momento»"], paso=4, st="part")
    d.msj(W, M, 516, [("«apagado»", "n")], k="ret")
    d.msj(C, M, 564, ["foto limpia"], paso=5, st="part", abajo=["sin luz proyectada encima"])
    d.msj(M, W, 628, ["«vuelve a dibujar»"], paso=6, st="part")
    d.a("top", f'<rect x="{W - 6}" y="468" width="12" height="160" fill="{INK}"/>')
    d.rot(W + 18, 552, [("proyector en negro", "b"), ("unos 300 ms", "m")], anchor="start")
    d.propio(M, 660, ["cascada: de lo barato a lo caro", ("para en el primer nivel que resuelve", "m")], paso=7, st="part")

    # --- los niveles de la cascada
    niveles = [("N0", "¿cambió algo?", "#F1FCF3"), ("N1", "¿sigue igual?", "#E3F9E7"), ("N2", "¿se movió?", "#D3F9D8"),
               ("N2b", "¿forma nueva?", "#B9EFC1"), ("MobileSAM", "segmenta de cero", "#9BE3A7")]
    x, y, w = 660, 728, 132
    d.a("box", f'<rect x="{x - 12}" y="{y - 12}" width="836" height="92" rx="8" fill="#FFFFFF" stroke="{zm["s"]}" stroke-width="1.4"/>')
    for i, (n, q, fill) in enumerate(niveles + [("Claude API", "le pone nombre", "#FFFFFF")]):
        cx = x + i * (w + 4)
        opc = n == "Claude API"
        borde = Z["ext"]["s"] if opc else zm["s"]
        dash = ' stroke-dasharray="5 4"' if opc else ""
        d.a("box", f'<rect x="{cx}" y="{y}" width="{w - 12}" height="52" rx="6" fill="{fill}" stroke="{borde}" stroke-width="1.3"{dash}/>')
        d.T(cx + (w - 12) / 2, y + 21, n, 13.5, 700, INK, anchor="middle", layer="box")
        d.T(cx + (w - 12) / 2, y + 40, q, 12, 400, TEXT, anchor="middle", layer="box")
        if i:
            d.flecha([(cx - 15, y + 26), (cx - 1, y + 26)], "opc" if opc else "linea", "abierta", layer="top")
    d.T(x, y + 70, "barato y rápido", 11.5, 400, MUTED, layer="box")
    d.T(x + 5 * (w + 4) - 16, y + 70, "caro y lento", 11.5, 400, MUTED, anchor="end", layer="box")
    d.T(x + 6 * (w + 4) - 16, y + 70, "opcional", 11.5, 400, MUTED, anchor="end", layer="box")

    d.msj(M, W, 872, ["«el objeto ahora está aquí»"], paso=8, st="plan", abajo=["posición y contorno, en mm"])
    d.propio(W, 908, ["mueve lo proyectado junto al objeto"], paso=9, st="ok")
    d.msj(W, G, 992, [[("avisa: ", {}), ("object_moved", {"mono": True})]], paso=10, st="ok")
    d.msj(G, A, 1040, [[("responde ", {}), ("events_wait", {"mono": True})]], k="ret")

    # --- meta de tiempo
    d.a("top", f'<path d="M 1520 384 H 1532 V 932 H 1520" fill="none" stroke="{MAIN}" stroke-width="1.6"/>')
    d.a("top", f'<text transform="translate(1554 658) rotate(-90)" font-family="{SANS}" font-size="13" font-weight="700" fill="{MAIN}" '
        f'text-anchor="middle">meta: menos de 1 segundo</text>')

    d.panel(32, 1108, 1536, 116)
    d.ley_estado(56, 1138, "Estado de cada paso")
    _ley_seq(d, 264, 1138)
    _hoy(d, 520, 1138, [
        ("ok", "Hoy la foto limpia la saca la web app (look con clean: true) y los objetos los publica un cliente por MCP.", None),
        ("part", "La cascada N0–N2b y la foto limpia en Rust pasan sus pruebas, sin cablear. MobileSAM: falta incluir los pesos.", None),
        ("plan", "Que el motor le mande las posiciones a la web app por su servidor local.", None),
    ])
    return d


# =============================================================================
# Láminas técnicas y lámina para PPT, con el estilo de la v1: un nodo 3D por capa (color de fondo), componentes con
# ícono UML, estereotipo y nombre en monoespaciada, flujo principal en rojo y ensambles con nombre, protocolo y mensajes.
# El dueño va como etiqueta chica. El interior sale del código: feat/motor-local (node/crates/pcxd/src/motor),
# feat/motor-en-consola (console/*.js) y daniel/consola (pcx_gateway/app.py).
# =============================================================================
ROJO = "#D9502A"
Z.update({
    "c_ag": dict(s="#7A68C2", f="#F5F2FC", h="#E6E0F6", i="#45358F"),
    "c_nube": dict(s="#4C7BC0", f="#EEF4FC", h="#DCE7F7", i="#22477E"),
    "c_pc": dict(s="#4C9867", f="#EDF7F0", h="#D9EEDF", i="#215E37"),
    "c_hw": dict(s="#C08A3E", f="#FDF6E9", h="#F6E7C8", i="#7A4F0F"),
    "c_ext": dict(s="#85878C", f="#FFFFFF", h="#F1F1F1", i="#3F4147"),
})
LEY_CAPAS = [("c_ag", "agente de IA"), ("c_nube", "nube (Render)"), ("c_pc", "computador del usuario"), ("c_hw", "hardware de la mesa"),
             ("c_ext", "servicio externo")]
PASOS = [
    "Le pasas a tu agente el link de setup de la landing.",
    "El agente descarga el bundle (motor + pesos) desde GitHub Releases y lo instala.",
    "pcx up levanta el motor local e imprime el link de emparejamiento.",
    "Abres la web app; el servidor te crea una sesión con su llave.",
    "El agente se conecta por MCP con esa llave.",
    "En uso: cámara → motor → web app → proyector. Las órdenes del agente llegan por el servidor.",
]


def _tec(d, b, nombre):
    """Nombre técnico (archivo, crate, ruta) al pie de un componente."""
    d.T(b.x + 14, b.b - 11, nombre, 11.5, 400, MUTED, mono=True, layer="box")


def _puerto(d, x, y, color=ROJO):
    d.a("top", f'<rect x="{x - 6}" y="{y - 6}" width="12" height="12" fill="#FFFFFF" stroke="{color}" stroke-width="2"/>')


def _if(d, x, y, nombre, proto, estilo, ops, anchor="start", paso=None, st=None, bg="#FFFFFF"):
    """Rótulo de una interfaz sobre una línea horizontal en y: nombre + protocolo arriba, estilo + mensajes abajo."""
    d.rot(x, y - 27, [(nombre, "b"), (proto, "mono")], anchor=anchor, paso=paso, st=st, bg=bg, size=12.5, lh=16)
    abajo = ([(estilo, "m")] if estilo else []) + ([(ops, "mono")] if ops else [])
    if abajo:
        d.rot(x, y + 21, abajo, anchor=anchor, bg=bg, size=12.5, lh=16)


def _ley_v1(d, y, nota):
    d.panel(32, y, d.w - 64, 150)
    x = 56
    d.T(x, y + 30, "Notación UML 2", 13, 700, INK)
    f = [y + 54, y + 78, y + 102, y + 126]
    d.a("top", f'<rect x="{x}" y="{f[0] - 13}" width="34" height="18" rx="3" fill="#FFFFFF" stroke="{LINE}" stroke-width="1.4"/>')
    d.a("top", f'<rect x="{x + 22}" y="{f[0] - 10}" width="7" height="9" fill="#FFFFFF" stroke="{LINE}" stroke-width="1"/>')
    d.T(x + 48, f[0], "componente", 12.5, 400, TEXT)
    d.a("top", f'<polygon points="{x},{f[1] - 9} {x + 5},{f[1] - 14} {x + 34},{f[1] - 14} {x + 29},{f[1] - 9}" fill="#F1F3F5" stroke="{LINE}" stroke-width="1.2"/>')
    d.a("top", f'<polygon points="{x + 29},{f[1] - 9} {x + 34},{f[1] - 14} {x + 34},{f[1] - 1} {x + 29},{f[1] + 4}" fill="#F1F3F5" stroke="{LINE}" stroke-width="1.2"/>')
    d.a("top", f'<rect x="{x}" y="{f[1] - 9}" width="29" height="13" fill="#FFFFFF" stroke="{LINE}" stroke-width="1.2"/>')
    d.T(x + 48, f[1], "nodo de despliegue (una capa)", 12.5, 400, TEXT)
    d.a("top", f'<path d="M {x + 4} {f[2] - 10} A 12 4 0 0 1 {x + 28} {f[2] - 10} V {f[2] + 2} A 12 4 0 0 1 {x + 4} {f[2] + 2} Z" fill="#FFFFFF" stroke="{LINE}" stroke-width="1.2"/>')
    d.a("top", f'<path d="M {x + 4} {f[2] - 10} A 12 4 0 0 0 {x + 28} {f[2] - 10}" fill="none" stroke="{LINE}" stroke-width="1.2"/>')
    d.T(x + 48, f[2], "base de datos", 12.5, 400, TEXT)
    d.a("top", f'<path d="M {x + 4} {f[3] - 13} H {x + 28} L {x + 34} {f[3] - 7} V {f[3] + 4} H {x + 4} Z" fill="#FFFBEA" stroke="#C9B458" stroke-width="1.2"/>')
    d.T(x + 48, f[3], "nota", 12.5, 400, TEXT)
    x2 = 300
    d.piruleta(x2, f[0] - 4.5, x2 + 17, x2 + 40, "linea", r=5.5)
    d.T(x2 + 52, f[0], "ensamble: la bolita ofrece la interfaz, el enchufe la usa", 12.5, 400, TEXT)
    _puerto(d, x2 + 17, f[1] - 4.5, LINE)
    d.T(x2 + 52, f[1], "puerto: entrada en el borde de un componente compuesto", 12.5, 400, TEXT)
    _puerto(d, x2 + 6, f[2] - 4.5, LINE)
    d.flecha([(x2 + 12, f[2] - 4.5), (x2 + 40, f[2] - 4.5)], "linea", None, layer="top")
    d.T(x2 + 52, f[2], "delegación: del puerto a la pieza interna que lo atiende", 12.5, 400, TEXT)
    d.T(x2, f[3], [("Nombre", {"w": 700, "fill": INK}), (" · protocolo · estilo · mensajes", {})], 12.5, 400, TEXT)
    d.ley_estado(720, y + 30)
    d.T(900, y + 30, "Conexiones", 13, 700, INK)
    for i, (k, nombre) in enumerate([("rojo", "flujo principal"), ("linea", "otra conexión"), ("opc", "opcional o planeada")]):
        yy = y + 54 + i * 22
        d.flecha([(900, yy - 4.5), (938, yy - 4.5)], k, "tri", layer="top")
        d.T(950, yy, nombre, 12.5, 400, TEXT)
    d.paso(911, y + 118, 1, ROJO)
    d.T(950, y + 122, "pasos, en orden", 12.5, 400, TEXT)
    d.ley_zonas(1130, y + 30, LEY_CAPAS, titulo="Color por capa")
    d.T(1390, y + 30, "Texto", 13, 700, INK)
    d.T(1390, y + 54, "nombre-tecnico", 12, 700, INK, mono=True)
    d.T(1390, y + 76, "frases simples: qué hace", 12.5, 400, TEXT)
    d.T(1390, y + 98, [("→ ", {"w": 700}), ("lo que entrega", {})], 12.5, 400, TEXT)
    d.T(32, y + 174, nota, 12.5, 400, MUTED)


def _narrativa(d, x, y, w, pasos=PASOS, titulo="Cómo se lee: sigue los números", lh=26):
    d.panel(x, y, w, 40 + lh * len(pasos) + 8)
    d.T(x + 24, y + 28, titulo, 13, 700, INK)
    for i, t in enumerate(pasos):
        yy = y + 56 + i * lh
        d.paso(x + 35, yy - 4.5, i + 1, ROJO)
        d.T(x + 56, yy, t, 13, 400, TEXT)


def t1():
    d = D(1, "tecnico-1-sistema", 1840, 1296, "Vista de sistema",
          "Quién ofrece cada interfaz, quién la usa y con qué protocolo. Se lee siguiendo los números.", "UML de componentes", tecnica=True)

    # --- externos, arriba
    lan = d.comp(800, 36, 240, 100, "c_nube", "pcx-web", ["Landing: página pública", "y setup.md para tu agente"], estereo="«static site» · Render",
                 st="part", quien="Manu", mono=True, icono=False)
    rel = d.comp(1100, 36, 280, 100, "c_ext", "GitHub Releases", ["el bundle: motor + pesos", "(SAM y manos)"], estereo="«artifact»", st="plan",
                 quien="Manu", icono=False)
    cla = d.comp(1560, 36, 248, 100, "c_ext", "Claude API", ["pone nombre a objetos nuevos"], estereo="«service» · opcional", st="part", icono=False)
    d.flecha([(lan.r, 86), (rel.l, 86)], "opc", "abierta")

    # --- nube
    d.nodo(32, 208, 560, 560, "c_nube", "«executionEnvironment»", "Nube · Render", "el servidor de las sesiones")
    gw = d.comp(200, 304, 376, 300, "c_nube", "pcx-gateway",
                [("Servidor de sesiones", "b"), "• cada persona entra y recibe su llave", "• puerta MCP para los agentes",
                 "• reenvía órdenes y eventos de la mesa", "• sirve la web app (/mesa)", "• historial y compartir en vivo (/ver)"],
                estereo="«service»", st="ok", quien="Daniel", mono=True)
    _tec(d, gw, "Python · Starlette · app.py, gateway.py")
    pg = d.cil(240, 664, 176, 88, "c_nube", "Postgres", [("sesiones e historial", "m")], st="ok")
    d.flecha([(328, gw.b), (328, pg.t + 2)], "linea", None)
    d.rot(342, 640, [("SQL", "mono")], anchor="start", bg=None)

    # --- agente
    d.nodo(904, 228, 640, 236, "c_ag", "«node»", "Agente de IA", "en el mismo computador")
    ag = d.comp(1128, 306, 300, 100, "c_ag", "agente", ["Claude Code / Codex / OpenCode /", "Claude Desktop"], estereo="«application»", st="ok",
                mono=True)
    d.nota(1128, 414, 300, 42, ["Con el plugin pcx: 6 skills = manuales de uso"], size=12)
    d.actor(1640, 300, "Persona")
    d.flecha([(1620, 356), (ag.r, 356)], "rojo")
    d.rot(1524, 344, ["le pasa el link"], paso=1)

    # --- computador
    d.nodo(904, 504, 880, 336, "c_pc", "«device»", "Computador del usuario", "macOS · Windows · Linux")
    wa = d.comp(928, 576, 296, 224, "c_pc", "consola web", [("Navegador · web app", "b"), "• consola: cámara, pantalla, calibrar",
                                                           "• ventana del proyector: dibuja la luz", "• manos y voz", ("→ la imagen del proyector", "z")],
                estereo="«application»", st="part", quien="Daniel", mono=True)
    _tec(d, wa, "JS · node.js, motor.js, projector.js")
    mo = d.comp(1456, 576, 304, 224, "c_pc", "pcx motor", [("Motor local", "b"), "• cámara y foto limpia", "• encuentra y sigue los objetos",
                                                         "• usa la calibración de la web app", ("→ objetos en mm", "z")],
                estereo="«process» · Rust", st="part", quien="Manu", mono=True)
    d.quien(mo.r - 12 - (tw("Manu", 11, True) + 18) - 6, mo.t, "Max · percepción")
    _tec(d, mo, "pcxd/src/motor · 127.0.0.1:9230")

    # --- ① Setup y ② Bundle
    d.piruleta_v(920, lan.b, 172, 200, "rojo")
    d.flecha([(920, 200), (920, 212), (1200, 212), (1200, ag.t)], "rojo", None)
    d.rot(940, 172, [("Setup", "b"), ("HTTPS GET · pedido/respuesta", "mono")], anchor="start", paso=1, st="plan", size=12.5, lh=16)
    d.piruleta_v(1300, rel.b, 172, 200, "rojo")
    d.flecha([(1300, 200), (1300, ag.t)], "rojo", None)
    d.rot(1320, 172, [("Bundle", "b"), ("HTTPS GET · descarga + sha256", "mono")], anchor="start", paso=2, st="plan", size=12.5, lh=16)

    # --- ③ pcx up
    d.flecha([(1400, ag.b), (1400, 548), (1600, 548), (1600, mo.t)], "rojo")
    d.rot(1500, 536, [[("pcx up", {"mono": True, "w": 700}), (" · lanza el proceso", {})]], paso=3, st="part", bg=Z["c_pc"]["f"], size=12.5)

    # --- interfaces del gateway (puertos en su borde)
    for yy in (356, 460, 564):
        _puerto(d, gw.r, yy)
    d.piruleta(gw.r + 6, 356, 640, 668, "rojo")
    d.flecha([(668, 356), (ag.l, 356)], "rojo", None)
    _if(d, 672, 356, "MCP tools", "JSON-RPC · Streamable HTTP", "pedido/respuesta · HTTPS + llave", "scene · look · project · ask", paso=5, st="ok")
    d.piruleta(gw.r + 6, 460, 640, 668, "rojo")
    d.flecha([(668, 460), (892, 460), (892, 616), (wa.l, 616)], "rojo", None)
    _if(d, 672, 460, "Sesiones", "REST JSON · HTTPS", "pedido/respuesta", "POST /api/sessions → llave", paso=4, st="ok")
    d.piruleta(gw.r + 6, 564, 640, 668, "rojo")
    d.flecha([(668, 564), (868, 564), (868, 664), (wa.l, 664)], "rojo", None)
    _if(d, 672, 564, "Sesión del nodo", "WSS JSON · /node", "eventos push, ida y vuelta", "tool_call · event_batch", paso=6, st="ok")

    # --- ⑥ protocolo del motor
    d.piruleta(mo.l, 688, 1372, wa.r, "rojo")
    d.rot(1340, 640, [("Protocolo motor v0.1", "b"), ("WS local JSON · /v1/ws", "mono")], paso=6, st="part", bg=Z["c_pc"]["f"], size=12.5, lh=16)
    d.rot(1340, 716, [("eventos push, ida y vuelta", "m"), ("blank · blanked · objects · event", "mono")], bg=Z["c_pc"]["f"], size=12.5, lh=16)

    # --- hardware
    d.nodo(780, 920, 1004, 112, "c_hw", "«location»", "Mesa")
    pr = d.comp(966, 944, 220, 72, "c_hw", "Proyector", [("segunda pantalla", "m")], icono=False)
    ha = d.comp(1250, 944, 190, 72, "c_ext", "Home Assistant", [("domótica · opcional", "m")], st="plan", icono=False, ts=14.5)
    ca = d.comp(1498, 944, 220, 72, "c_hw", "Cámara", [("mira la mesa desde arriba", "m")], icono=False)
    d.flecha([(pr.cx, wa.b), (pr.cx, pr.t)], "rojo")
    d.rot(pr.cx + 14, 870, [("Imagen", "b"), ("HDMI · stream", "mono")], anchor="start", paso=6, size=12.5, lh=16)
    d.flecha([(ca.cx, ca.t), (ca.cx, mo.b)], "rojo")
    d.rot(ca.cx + 14, 870, [("Cuadros", "b"), ("USB UVC · stream", "mono")], anchor="start", paso=6, size=12.5, lh=16)
    d.flecha([(1480, mo.b), (1480, 880), (ha.cx, 880), (ha.cx, ha.t)], "opc", "abierta")
    d.rot(ha.cx - 12, 906, [("REST", "mono")], anchor="end", bg=None)
    d.flecha([(1740, mo.t), (1740, cla.b)], "opc", "abierta")
    d.rot(1728, 180, [("HTTPS · solo objetos nuevos", "mono")], anchor="end", size=12.5)

    _narrativa(d, 32, 808, 716)
    _ley_v1(d, 1072, "Estado: el motor y la web app son parciales hasta integrar los PR #16, #18 y #19. Setup y Bundle son plan, no código. "
                     "El agente nunca habla directo con el motor: solo con el gateway.")
    return d


def t2():
    d = D(2, "tecnico-2-computador", 1920, 1432, "El computador del usuario por dentro",
          "Detalle del paso 6: cómo se hablan las piezas del motor y de la web app (tareas, canales y llamadas reales del código).",
          "UML de componentes", tecnica=True)
    zp = Z["c_pc"]

    gw = d.comp(1464, 132, 400, 80, "c_nube", "pcx-gateway", [("servidor de sesiones (lámina T1)", "m")], estereo="«service» · Render", st="ok",
                quien="Daniel", mono=True)
    d.nodo(32, 248, 1860, 780, "c_pc", "«device»", "Computador del usuario", "el agente de IA también corre aquí (lámina T1)")

    # --- motor
    mo = d.grupo(56, 336, 1144, 660, "c_pc", "«process» · Rust · tokio", "pcx motor", "ve la mesa y entrega objetos en mm", st="part", quien="Manu")
    d.a("zone", f'<rect x="{mo.x}" y="{mo.y}" width="{mo.w}" height="{mo.h}" rx="8" fill="#FFFFFF" stroke="{zp["s"]}" stroke-width="1.8"/>')
    d.a("zone", f'<rect x="362" y="420" width="826" height="228" rx="8" fill="{zp["f"]}" stroke="{zp["s"]}" stroke-width="1.2" stroke-dasharray="6 4"/>')
    d.T(378, 440, [("ciclo de percepción", {"w": 700, "fill": INK}), (" · hilo bloqueante (spawn_blocking), llamadas a traits", {})],
        12, 400, MUTED, layer="box")
    fr = d.comp(80, 460, 168, 168, "c_pc", "frames", [("Cámara", "b"), "lee la cámara y", "guarda el último", "cuadro (buzón)", ("→ cuadros", "z")],
                estereo="«component»", st="ok", mono=True, icono=False)
    _tec(d, fr, "FrameHub")
    cl = d.comp(378, 460, 184, 168, "c_pc", "clean", [("Foto limpia", "b"), "pide negro, espera el", "aviso y toma 3 cuadros", ("→ foto sin luz", "z")],
                estereo="«component»", st="ok", mono=True, icono=False)
    _tec(d, cl, "take_clean · Gate")
    pe = d.comp(692, 460, 176, 168, "c_pc", "Perceptor", [("Percepción", "b"), "cascada N0–N2b y", "MobileSAM si hace falta", ("→ objetos en mm", "z")],
                estereo="«trait»", st="part", mono=True, icono=False)
    _tec(d, pe, "Pipeline")
    sc = d.comp(998, 460, 176, 168, "c_pc", "ScenePerceptor", [("Percepción por escena", "b"), "módulo activo según", "scene{name}", ("→ objetos + event", "z")],
                estereo="«trait»", st="part", mono=True, icono=False, ts=14)
    _tec(d, sc, "scenes::registry()")
    mdl = d.comp(692, 348, 176, 60, "c_pc", "Modelos", [("pesos MobileSAM · ONNX", "m")], st="part", icono=False, ts=14)
    d.flecha([(pe.cx + 40, pe.t), (pe.cx + 40, mdl.b)], "opc", "abierta")
    lat = d.comp(998, 348, 176, 60, "c_hw", "Cámara lateral", [("--camera-lateral", "mono")], st="part", icono=False, ts=14)
    d.flecha([(lat.cx + 40, lat.b), (sc.cx + 40, sc.t)], "linea")
    d.T(lat.l - 10, 372, "on_live", 11, 400, TEXT, mono=True, anchor="end")
    d.T(lat.l - 10, 388, "hilo aparte", 11, 400, MUTED, anchor="end")
    en = d.comp(372, 736, 328, 176, "c_pc", "engine", [("Orquestador", "b"), "decide cuándo mirar: manos fuera,", "cada 45 s o a pedido (perceive)",
                                                      "guarda objetos y calibración", ("→ objects · status · event", "z")],
                estereo="«actor» · tarea tokio", st="ok", mono=True, icono=False)
    _tec(d, en, "motor/engine.rs")
    sv = d.comp(880, 736, 296, 176, "c_pc", "server", [("Servidor local", "b"), "valida el token y el Origin;", "habla JSON con la web app"],
                estereo="«component» · tarea tokio", st="ok", mono=True, icono=False)
    _tec(d, sv, "axum · /v1/ws · /v1/health")

    for prov, cons, xc, letra, nombre, op in ((fr.r, cl.l, 300, "a", "Cuadros", "wait_newer"), (pe.l, cl.r, 640, "c", "Perceptor", "on_clean"),
                                              (sc.l, pe.r, 946, "d", "ScenePerceptor", "on_clean")):
        d.piruleta(prov, 544, xc, cons, "rojo")
        mx = (prov + cons) / 2
        d.paso(mx, 484, letra)
        d.T(mx, 516, nombre, 12, 700, INK, anchor="middle")
        d.T(mx, 574, op, 11, 400, TEXT, anchor="middle", mono=True)
    d.flecha([(440, en.t), (440, 648)], "linea", "tri", cola="tri")
    d.rot(452, 688, [("lanza el ciclo ↑ · resultado ↓", "n"), ("canal tokio (mpsc)", "mono")], anchor="start", size=12.5, lh=16, bg=None)
    d.flecha([(560, cl.b), (560, 668), (1040, 668), (1040, sv.t)], "linea", "tri", cola="tri")
    d.rot(800, 660, [[("BlankControl", {"w": 700}), (" · canal tokio · ", {}), ("blank → blanked", {"mono": True})]], paso="b", size=12.5)
    d.flecha([(en.r, 824), (sv.l, 824)], "rojo", "tri", cola="tri")
    d.rot(790, 796, [("EngineMsg", "b"), ("canal tokio (mpsc)", "mono")], size=12.5, lh=16, bg=None)
    d.rot(790, 850, [("mensajes ⇄ JSON", "m"), ("calib · hands · scene", "mono")], size=12.5, lh=16, bg=None)

    # --- web app
    wa = d.grupo(1440, 336, 428, 660, "c_pc", "«application» · navegador", "consola web", "lo que la persona ve y toca", st="part", quien="Daniel")
    d.a("zone", f'<rect x="{wa.x}" y="{wa.y}" width="{wa.w}" height="{wa.h}" rx="8" fill="#FFFFFF" stroke="{zp["s"]}" stroke-width="1.8"/>')
    nd = d.comp(1464, 424, 216, 104, "c_pc", "node.js", [("Nodo de sesión", "b"), "órdenes del agente ↓", "eventos de la mesa ↑"], st="ok", mono=True,
                icono=False)
    hs = d.comp(1464, 556, 216, 96, "c_pc", "hands.js", [("Manos", "b"), "MediaPipe: manos y toques"], st="ok", mono=True, icono=False)
    mj = d.comp(1464, 728, 216, 112, "c_pc", "motor.js", [("Enlace al motor", "b"), "manda calibración y manos;", "fusiona los objetos"], st="part",
                quien="Manu", mono=True, icono=False)
    pj = d.comp(1464, 876, 216, 104, "c_pc", "projector.js", [("Ventana del proyector", "b"), "dibuja widgets, html", ("→ la imagen", "z")], st="ok",
                mono=True, icono=False)
    ap = d.comp(1704, 424, 148, 556, "c_pc", "app.js", [("Consola", "b")], st="ok", mono=True,
                icono=False)
    for b_, txt in ((nd, ["llamadas JS", "+ eventos"]), (hs, ["eventos", "(EventTarget)"]), (mj, ["eventos", "(EventTarget)"]),
                    (pj, ["BroadcastChannel", "world · blank →", "← press · blanked"])):
        d.flecha([(b_.r, b_.cy), (ap.l, b_.cy)], "linea", "tri", cola="tri")
        y0 = b_.cy - 52 if len(txt) == 3 else b_.cy + 22
        for i, t in enumerate(txt):
            d.T(ap.x + 12, y0 + i * 15, t, 10.5, 400, MUTED, mono=True, layer="box")

    # --- sesión del nodo: la ofrece el gateway
    _puerto(d, 1656, wa.t)
    d.piruleta_v(1656, gw.b, 280, wa.t - 6, "rojo")
    d.flecha([(1656, wa.t + 6), (1656, nd.t)], "rojo", None)
    d.rot(1636, 268, [("Sesión del nodo", "b"), ("WSS JSON · /node", "mono"), ("eventos push · tool_call · event_batch", "m")], anchor="end", size=12.5,
          lh=16)

    # --- protocolo del motor: puertos y delegación
    _puerto(d, mo.r, 784)
    _puerto(d, wa.l, 784)
    d.flecha([(sv.r, 784), (mo.r - 6, 784)], "rojo", None)
    d.piruleta(mo.r + 6, 784, 1300, wa.l - 6, "rojo")
    d.flecha([(wa.l + 6, 784), (mj.l, 784)], "rojo", None)
    d.rot(1320, 706, [("Protocolo motor v0.1", "b"), ("WS local JSON · /v1/ws", "mono"), ("eventos push, ida y vuelta", "m")], paso="e", st="part",
          size=12.5, lh=16)
    d.rot(1320, 816, [("→ blank · objects · status · event", "mono"), ("← calib · blanked · hands · scene", "mono")], size=12, lh=16)

    # --- cámara y proyector
    ca = d.comp(80, 1088, 176, 72, "c_hw", "Cámara", [("sobre la mesa", "m")], icono=False)
    pr = d.comp(1464, 1088, 216, 72, "c_hw", "Proyector", [("segunda pantalla", "m")], icono=False)
    d.flecha([(ca.cx, ca.t), (fr.cx, fr.b)], "rojo")
    d.rot(ca.cx + 14, 1058, [[("USB UVC", {"mono": True}), (" · stream", {})]], anchor="start", bg=None, size=12.5)
    d.flecha([(pj.cx, pj.b), (pr.cx, pr.t)], "rojo")
    d.rot(pj.cx + 14, 1058, [[("HDMI", {"mono": True}), (" · stream", {})]], anchor="start", paso="f", bg=None, size=12.5)
    d.nota(188, 736, 168, 124, ["La web app calibra y", "manda calib (matriz", "px→mm + lente).", "Sin cámara propia:", "need_frame → frame."],
           titulo="Calibración", size=11.5)

    # --- glosario
    d.panel(288, 1056, 1144, 150)
    d.T(312, 1084, "Glosario", 13, 700, INK)
    glos = [("tarea tokio", "trabajo que corre en paralelo dentro del proceso"), ("canal tokio", "cola por la que dos tareas se pasan mensajes"),
            ("trait", "interfaz de Rust: quien la cumple se puede enchufar"), ("SAM", "recorta la silueta exacta de un objeto"),
            ("BroadcastChannel", "mensajes entre dos ventanas del navegador"),
            ("MediaPipe", "detecta manos"), ("homografía", "mapa entre lo que ve la cámara y la mesa"),
            ("WSS / WS", "conexión abierta por la que ambos lados mandan mensajes")]
    for i, (t, q) in enumerate(glos):
        gx, gy = (312, 1108 + i * 22) if i < 4 else (860, 1108 + (i - 4) * 22)
        d.T(gx, gy, [(t, {"w": 700, "fill": INK}), ("  " + q, {})], 12.5, 400, TEXT)

    _ley_v1(d, 1228, "Fuentes: feat/motor-con-percepcion (pcxd/src/motor, pcx-perception), feat/motor-en-consola (console/*.js) y daniel/consola (app.py). "
                     "El registro de escenas parte vacío. Respaldo sin modelos: PCX_MOTOR_PERCEPTOR=diff.")
    return d


def s1():
    """Lámina para PPT, 16:9. Se dibuja en 1280×720 y se exporta a 3x: la letra queda grande al proyectar."""
    d = D(1, "slide-arquitectura", 1280, 720, "", "", "", tecnica=True)
    d.L = {k: [] for k in ("zone", "line", "box", "top")}
    d.titulo = "Arquitectura de pcx"
    d.T(32, 50, "pcx: cómo funciona", 28, 700, INK)
    d.T(32, 76, "Tu agente de IA ve la mesa y proyecta sobre ella. Todo el cómputo corre en tu computador.", 14.5, 400, TEXT)

    d.nodo(32, 124, 880, 128, "c_nube", "«nube»", "Internet")
    lan = d.comp(190, 148, 180, 88, "c_nube", "Landing", [("link de setup", "m")], st="part", quien="Manu", icono=False)
    rel = d.comp(440, 148, 200, 88, "c_ext", "GitHub Releases", [("motor + pesos", "m")], st="plan", quien="Manu", icono=False)
    srv = d.comp(700, 148, 196, 88, "c_nube", "Servidor", [("sesiones con llave · MCP", "m")], st="ok", quien="Daniel", icono=False)

    d.nodo(32, 340, 880, 200, "c_pc", "«device»", "Tu computador")
    d.actor(96, 408, "Tú")
    ag = d.comp(190, 396, 180, 112, "c_ag", "Agente de IA", ["Claude Code, Codex", "u OpenCode", ("+ skills pcx", "m")], st="ok", icono=False)
    mo = d.comp(462, 396, 180, 112, "c_pc", "Motor local", ["ve la mesa:", ("→ objetos en mm", "z"), ("Rust · con SAM", "m")], st="part", quien="Manu",
                icono=False)
    wa = d.comp(734, 396, 162, 112, "c_pc", "Web app", ["dibuja la luz", ("→ imagen", "z"), ("navegador", "m")], st="part", quien="Daniel", icono=False)

    d.nodo(32, 612, 880, 84, "c_hw", "«lugar»", "Mesa")
    ca = d.comp(462, 624, 180, 60, "c_hw", "Cámara", [], icono=False)
    pr = d.comp(734, 624, 162, 60, "c_hw", "Proyector", [], icono=False)

    d.flecha([(116, 452), (ag.l, 452)], "rojo")
    d.paso(152, 438, 1, ROJO)
    d.flecha([(220, ag.t), (220, lan.b)], "rojo")
    d.rot(234, 284, [("Setup", "b"), ("HTTPS", "mono")], anchor="start", paso=1, lh=16)
    d.flecha([(290, ag.t), (290, 322), (500, 322), (500, rel.b)], "rojo")
    d.rot(514, 284, [("Bundle", "b"), ("HTTPS", "mono")], anchor="start", paso=2, lh=16)
    d.flecha([(350, ag.t), (350, 368), (740, 368), (740, srv.b)], "rojo")
    d.rot(640, 296, [("MCP tools", "b")], anchor="start", paso=5)
    d.flecha([(ag.r, 452), (mo.l, 452)], "rojo")
    d.rot((ag.r + mo.l) / 2, 438, [("pcx up", "mono")], bg=Z["c_pc"]["f"])
    d.paso((ag.r + mo.l) / 2, 474, 3, ROJO)
    d.flecha([(840, wa.t), (840, srv.b)], "rojo", "tri", cola="tri")
    d.rot(826, 296, [("Sesión", "b"), ("WSS", "mono")], anchor="end", paso=4, lh=16)
    d.piruleta(mo.r, 452, 696, wa.l, "rojo")
    d.rot(688, 432, [("WS local", "b")], bg=Z["c_pc"]["f"], size=12)
    d.paso(688, 480, 6, ROJO)
    d.flecha([(ca.cx, ca.t), (mo.cx, mo.b)], "rojo")
    d.rot(mo.cx + 14, 580, [("USB", "mono")], anchor="start", paso=6, bg=None)
    d.flecha([(wa.cx, wa.b), (pr.cx, pr.t)], "rojo")
    d.rot(wa.cx + 14, 580, [("HDMI", "mono")], anchor="start", paso=6, bg=None)

    x = 944
    d.panel(x, 112, 304, 584)
    d.T(x + 20, 142, "Sigue los números", 15, 700, INK)
    cortos = [("Link de setup", "se lo pasas a tu agente"), ("Descarga", "motor + pesos desde Releases"), ("pcx up", "levanta el motor local"),
              ("Sesión", "abres la web app: te da una llave"), ("MCP", "el agente se conecta con la llave"),
              ("En uso", "cámara → motor → web app → proyector")]
    for i, (a_, b_) in enumerate(cortos):
        yy = 180 + i * 52
        d.paso(x + 32, yy - 5, i + 1, ROJO)
        d.T(x + 54, yy, a_, 15, 700, INK)
        d.T(x + 54, yy + 19, b_, 12.5, 400, TEXT)
    d.a("top", f'<path d="M {x + 20} 492 H {x + 284}" stroke="{RULE}" stroke-width="1.2"/>')
    for i, (k, nombre) in enumerate(ESTADOS.items()):
        d.estado(x + 28, 514 + i * 22, k)
        d.T(x + 44, 518.5 + i * 22, nombre, 12.5, 400, TEXT)
    d.ley_zonas(x + 150, 494, [("c_ag", "agente"), ("c_nube", "nube"), ("c_pc", "computador"), ("c_hw", "mesa")], titulo="")
    d.T(x + 20, 624, "Por la red viajan coordenadas,", 12.5, 400, TEXT)
    d.T(x + 20, 641, "no video. El agente solo habla", 12.5, 400, TEXT)
    d.T(x + 20, 658, "con el servidor, nunca con el motor.", 12.5, 400, TEXT)
    return d


def main():
    for fn in (d1, d2, d3, d4, d5, t1, t2, s1):
        d = fn()
        d.guardar()
        print(f"{d.nombre}.svg  {d.w}×{d.h}")


if __name__ == "__main__":
    main()
