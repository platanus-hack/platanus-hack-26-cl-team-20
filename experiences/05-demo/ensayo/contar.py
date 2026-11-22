#!/usr/bin/env python3
"""Cuenta las palabras que dice Max en cada bloque del guion y arma la tabla de tiempos.
Uso: python3 contar.py <guion.md>
Solo cuenta lo que va entre « » en las líneas que empiezan con **Max dice:**. 140 palabras por minuto."""
import re
import sys

PPM = 140
# Silencio (s) en que la mesa trabaja y Max calla, por bloque normal.
SILENCIO = {"1": 1, "2": 1, "3": 13, "4": 15, "5": 11, "6": 2}
# Duración fija (s) de cada cambio; la voz va dentro.
CAMBIO = {"Cambio 1": 20, "Cambio 2": 25}


def mmss(s):
    s = round(s)
    return f"{s // 60}:{s % 60:02d}"


def main(ruta):
    bloques, actual = [], None
    for linea in open(ruta, encoding="utf-8"):
        m = re.match(r"### (.+)", linea)
        if m:
            actual = {"titulo": m.group(1).strip(), "palabras": 0}
            bloques.append(actual)
        elif actual and linea.startswith("**Max dice:**"):
            for frase in re.findall(r"«([^»]*)»", linea):
                actual["palabras"] += len(re.findall(r"[0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ%]+", frase))
    bloques = [b for b in bloques if b["palabras"]]
    t = total_pal = propia = dentro = 0
    for b in bloques:
        voz = b["palabras"] / PPM * 60
        total_pal += b["palabras"]
        clave = b["titulo"].split(" · ")[0]
        if clave in CAMBIO:
            dura = CAMBIO[clave]
            nota = f"voz {voz:.1f} s DENTRO del cambio; libre {dura - voz:.1f} s"
            dentro += b["palabras"]
            assert voz <= dura, "la voz no cabe en el cambio"
        else:
            dura = voz + SILENCIO[clave]
            nota = f"voz {voz:.1f} s + silencio {SILENCIO[clave]} s"
            propia += b["palabras"]
        print(f"{b['titulo'][:45]:45} {b['palabras']:4} pal  {nota:48} dura {dura:5.1f}  {mmss(t)}-{mmss(t + dura)}")
        t += dura
    print(f"\nPalabras: {total_pal} (voz total {mmss(total_pal / PPM * 60)})")
    print(f"  con tiempo propio: {propia} ({mmss(propia / PPM * 60)}) · dentro de los cambios: {dentro} ({mmss(dentro / PPM * 60)})")
    print(f"Total: {mmss(t)} ({t:.1f} s) · margen a 3:00: {180 - t:.1f} s")
    callado = t + dentro / PPM * 60
    print(f"Si Max callara en los cambios: {mmss(callado)}")


if __name__ == "__main__":
    main(sys.argv[1])
