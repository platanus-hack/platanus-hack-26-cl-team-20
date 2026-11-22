"""Voz sonora de WUI (Pablo, experiences/04-marca/voz-sonora.md): cuatro cues cortos. Un solo cue a la vez, sin música.
   aparicion: entra una señal · guia: la pista se asienta · confirmacion: paso logrado · recuperacion: se perdió el objeto."""
import subprocess
from pathlib import Path
_C = Path(__file__).parent / 'cues'; _p = [None]


def play(cue):
    f = _C / f'{cue}.wav'
    if not f.exists(): return
    if _p[0] is not None and _p[0].poll() is None: _p[0].terminate()     # nada se superpone
    try: _p[0] = subprocess.Popen(['afplay', str(f)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception: pass
