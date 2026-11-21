"""Manda tres notas al teclado por su puerto de salida USB (si suenan, el enlace PC->teclado funciona)."""
import time, mido
outs = mido.get_output_names(); name = next((n for n in outs if "KORG" in n.upper() or "USB MIDI" in n.upper()), None)
print("salida:", name)
if name:
    with mido.open_output(name) as p:
        for n in (60, 64, 67):
            p.send(mido.Message("note_on", note=n, velocity=100)); time.sleep(0.4); p.send(mido.Message("note_off", note=n))
    print("notas enviadas")
