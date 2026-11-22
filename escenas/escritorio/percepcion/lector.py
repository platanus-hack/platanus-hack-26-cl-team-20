"""Lector de la libreta con memoria: junta las funciones puras y recuerda la última lectura.

Pensado para la cámara secundaria (la que mira la libreta de cerca), pero sirve con cualquier foto donde se vea la libreta.
Todo lo que devuelve en coordenadas va normalizado a la libreta entera (0..1), salvo `quad_px`.
"""

from __future__ import annotations

import json

import cv2
import numpy as np

from .espacio import espacio_libre
from .libreta import pagina_activa, recorte_pagina, rectificar, tamano_rectificado, ubicar_libreta
from .tinta import hay_mano, lo_nuevo, marcar, mascara_tinta, recorte


def a_libreta(pagina, rect):
    """Rectángulo en coordenadas de una página → coordenadas de la libreta entera."""
    px, py, pw, ph = pagina
    x, y, w, h = rect
    return [round(px + x * pw, 4), round(py + y * ph, 4), round(w * pw, 4), round(h * ph, 4)]


class Lector:
    def __init__(self, *, girar: int = 0, cerca_de=None):
        self.girar = girar
        self.cerca_de = cerca_de
        self.tamano = None  # tamaño fijo de la libreta rectificada, para comparar lecturas píxel a píxel
        self.n_paginas = None
        self.leido: dict[int, np.ndarray] = {}  # página → tinta de la última lectura confirmada
        self.respuestas: dict[int, list] = {}  # página → rectángulos (de página) ya usados por respuestas
        self.activa: int | None = None
        self._ultima = None  # lo que se vio en la última llamada a mirar()

    def reiniciar(self):
        self.tamano = self.n_paginas = self.activa = self._ultima = None
        self.leido.clear()
        self.respuestas.clear()

    def mirar(self, foto: np.ndarray) -> dict:
        """Mira una foto y dice qué hay de nuevo respecto de la última lectura confirmada. No cambia la memoria.

        `estado`: «sin_libreta», «mano» (hay una mano encima: esperar), «nuevo» o «igual».
        Con «nuevo» trae `recorte` (BGR, solo lo nuevo) y `contexto` (la página con lo nuevo marcado) para el LLM.
        """
        lib = ubicar_libreta(foto, girar=self.girar, cerca_de=self.cerca_de)
        if lib is None:
            return {"estado": "sin_libreta"}
        if hay_mano(foto, lib["quad_px"]):
            return {"estado": "mano", "libreta": lib}
        if self.n_paginas is not None and len(lib["paginas"]) != self.n_paginas:
            self.reiniciar()  # se abrió o se cerró la libreta: se parte de cero
        if self.tamano is None:
            self.tamano = tamano_rectificado(lib["quad_px"])
            self.n_paginas = len(lib["paginas"])
        hoja = rectificar(foto, lib["quad_px"], self.tamano)
        paginas = lib["paginas"]
        vistas = []
        for i, p in enumerate(paginas):
            img = recorte_pagina(hoja, p)
            tinta = mascara_tinta(img)
            antes = self.leido.get(i)
            if antes is not None and antes.shape != tinta.shape:
                # el lomo se movió unos píxeles y la página quedó de otro ancho: se lleva la lectura anterior a este
                antes = cv2.resize(antes, (tinta.shape[1], tinta.shape[0]), interpolation=cv2.INTER_NEAREST)
            vistas.append({"img": img, "tinta": tinta, "nuevo": lo_nuevo(antes, tinta)})
        activa = pagina_activa(paginas, [v["nuevo"]["pixeles"] if v["nuevo"]["hay_nuevo"] else 0 for v in vistas], self.activa)
        v = vistas[activa]
        n = v["nuevo"]
        self._ultima = {"vistas": vistas, "activa": activa, "paginas": paginas}
        out = {
            "estado": "nuevo" if n["hay_nuevo"] else "igual",
            "libreta": lib,
            "pagina": activa,
            "pagina_rect": paginas[activa],
            "pixeles_nuevos": n["pixeles"],
        }
        if n["hay_nuevo"]:
            out["nuevo_pagina"] = n["caja"]
            out["nuevo_libreta"] = a_libreta(paginas[activa], n["caja"])
            out["renglones_libreta"] = [a_libreta(paginas[activa], r) for r in n["renglones"]]
            out["recorte"] = recorte(v["img"], n["caja"])
            out["contexto"] = marcar(v["img"], n["caja"])
        else:
            out["contexto"] = v["img"]
        return out

    def confirmar(self):
        """Da por leído lo que se vio en la última llamada a mirar(): la próxima lectura compara contra esto."""
        if not self._ultima:
            return
        for i, v in enumerate(self._ultima["vistas"]):
            self.leido[i] = v["tinta"]
        self.activa = self._ultima["activa"]

    def hueco(self, tamano=(0.6, 0.18), *, cerca_de_pagina=None) -> dict | None:
        """Dónde escribir la respuesta en la página activa de la última mirada. Reserva el lugar para la próxima."""
        if not self._ultima:
            return None
        i = self._ultima["activa"]
        pagina = self._ultima["paginas"][i]
        tinta = self._ultima["vistas"][i]["tinta"]
        nuevo = self._ultima["vistas"][i]["nuevo"]
        cerca = cerca_de_pagina or nuevo.get("caja")
        # el tamaño viene en proporción de la libreta; en la página es más ancho si la libreta es doble
        t = (min(0.95, tamano[0] / pagina[2]), min(0.95, tamano[1] / pagina[3]))
        r = espacio_libre(tinta, t, cerca_de=cerca, ocupado=self.respuestas.get(i, []))
        if r is None:
            return None
        self.respuestas.setdefault(i, []).append(r["rect"])
        return {**r, "pagina": i, "rect_pagina": r["rect"], "rect_libreta": a_libreta(pagina, r["rect"])}

    def olvidar_respuestas(self):
        """La proyección se limpió: los huecos usados quedan libres otra vez."""
        self.respuestas.clear()

    def pagina_actual(self) -> np.ndarray | None:
        """La página activa de la última mirada, de frente y sin marcas (None si no hubo mirada o era de la memoria)."""
        if not self._ultima or "img" not in self._ultima["vistas"][self._ultima["activa"]]:
            return None
        return self._ultima["vistas"][self._ultima["activa"]]["img"]

    # -------------------------------------------------------------- memoria en disco (para usarlo desde la terminal)

    def guardar(self, ruta):
        """Guarda la memoria (lecturas confirmadas, huecos usados y la última mirada) en un .npz."""
        meta = {
            "girar": self.girar,
            "tamano": self.tamano,
            "n_paginas": self.n_paginas,
            "activa": self.activa,
            "respuestas": {str(k): v for k, v in self.respuestas.items()},
            "ultima": None,
        }
        arrays = {f"leido_{i}": m for i, m in self.leido.items()}
        if self._ultima:
            meta["ultima"] = {
                "activa": self._ultima["activa"],
                "paginas": self._ultima["paginas"],
                "nuevo": [v["nuevo"] for v in self._ultima["vistas"]],
            }
            arrays.update({f"tinta_{i}": v["tinta"] for i, v in enumerate(self._ultima["vistas"])})
        np.savez_compressed(ruta, meta=np.frombuffer(json.dumps(meta, default=float).encode(), np.uint8), **arrays)

    @classmethod
    def cargar(cls, ruta, **kw) -> "Lector":
        lector = cls(**kw)
        try:
            z = np.load(ruta)
        except OSError:
            return lector
        meta = json.loads(z["meta"].tobytes())
        lector.girar = meta["girar"]
        lector.tamano = tuple(meta["tamano"]) if meta["tamano"] else None
        lector.n_paginas, lector.activa = meta["n_paginas"], meta["activa"]
        lector.respuestas = {int(k): v for k, v in meta["respuestas"].items()}
        lector.leido = {int(k.split("_")[1]): z[k] for k in z.files if k.startswith("leido_")}
        u = meta.get("ultima")
        if u:
            vistas = [{"tinta": z[f"tinta_{i}"], "nuevo": n} for i, n in enumerate(u["nuevo"])]
            lector._ultima = {"activa": u["activa"], "paginas": u["paginas"], "vistas": vistas}
        return lector
