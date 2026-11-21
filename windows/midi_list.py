"""Lista los puertos MIDI del PC (mido/rtmidi) y, si hay uno, escucha 5 s e imprime los mensajes que lleguen.

    C:\\sl-scanner\\.venv\\Scripts\\python.exe windows\\midi_list.py [--listen SEG]
"""

from __future__ import annotations

import sys
import time

import mido


def main() -> int:
    ins, outs = mido.get_input_names(), mido.get_output_names()
    print("entradas:", ins)
    print("salidas:", outs)
    secs = float(sys.argv[sys.argv.index("--listen") + 1]) if "--listen" in sys.argv else 0
    if secs and ins:
        name = next((n for n in ins if "KORG" in n.upper() or "EK-50" in n.upper()), ins[0])
        print("escuchando", name, "por", secs, "s (toca algo)")
        n = 0
        with mido.open_input(name) as port:
            t0 = time.time()
            while time.time() - t0 < secs:
                for msg in port.iter_pending():
                    n += 1
                    if n <= 20:
                        print("  ", msg)
                time.sleep(0.01)
        print("mensajes:", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
