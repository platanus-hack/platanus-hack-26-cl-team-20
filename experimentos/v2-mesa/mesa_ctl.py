#!/usr/bin/env python3
"""Control de la demo: tres escenas en orden (escritorio -> solar -> lab), cada una en dos tiempos.

  PREPARAR: la escena arranca en silencio. Detecta, segmenta o rastrea lo que hay en la mesa, sin proyectar nada.
  DALE:     animación de arranque (trazo del logotipo WUI que luego viaja a su esquina) y la escena aparece
            al instante, porque ya estaba lista.
  TERMINAR: apaga la escena y limpia la proyección, para cambiar los objetos de la mesa.

  doppler run -p platanus-hack-26 -c dev -- ../sl-scanner/.venv/bin/python mesa_ctl.py     (servicio en :9301)
  dashboard http://127.0.0.1:9300/dashboard.html: botones Terminar · Preparar siguiente · Dale
       teclas  T = terminar · P = preparar la siguiente · D o Enter = dale · E / S / L = preparar esa escena directo
  HTTP:  /fin · /preparar?escena=escritorio|solar|lab (sin escena: la siguiente) · /dale · /start?escena=… (prepara y da) · /estado
"""
import json, os, subprocess, sys, threading, time
from marca_mesa import apertura
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

H = Path(__file__).parent; PY = sys.executable
S = '/private/tmp/claude-501/-Users-sat/676a50ed-7238-4b78-9411-17db147f1404/scratchpad/pcxl.py'
ORDEN = ['escritorio', 'solar', 'lab']; NOMBRE = {'escritorio': 'Escritorio', 'solar': 'Sistema solar', 'lab': 'Laboratorio'}
PROC = {'escritorio': ['escritorio_auto.py', 'gato.py', 'opciones.py', 'interaccion.py'], 'solar': ['solar_vivo.py'], 'lab': ['lab_vivo.py']}
TODOS = [p for v in PROC.values() for p in v]
WIDGETS = ['boot', 'lab-vivo', 'solar-vivo', 'solar', 'solar-b', 'escritorio', 'escritorio-b', 'manos-capa', 'gato', 'opciones', 'voz', 'auto-negro', 'barra']
EST = {"escena": None, "fase": "apagada", "ultima": None}      # fase: apagada | preparada | encendida
FLAG = H / 'out/visible.flag'
control = threading.RLock()
turno = 0


def pcx(t, a): return subprocess.run(['/usr/bin/python3', S, t, json.dumps(a)], capture_output=True, text=True).stdout
def vivos(): return [p for p in TODOS if subprocess.run(['pgrep', '-f', p], capture_output=True).returncode == 0]
def siguiente(): return ORDEN[(ORDEN.index(EST['ultima']) + 1) % 3] if EST['ultima'] in ORDEN else 'escritorio'


def fin():
    global turno
    turno += 1
    for p in TODOS: subprocess.run(['pkill', '-f', p])
    time.sleep(0.6); pcx('remove', {"request_id": "ctl-rm-%d" % (time.time() * 10), "ids": WIDGETS})
    for f in ('out/esc-id.txt', 'out/gato-juego.flag', 'out/visible.flag', 'out/lista.flag'):
        try: (H / f).unlink()
        except FileNotFoundError: pass
    EST.update(escena=None, fase='apagada')


def preparar(escena):
    """Arranca la escena sin mostrarla: queda detectando o rastreando hasta que llegue «dale»."""
    fin()
    FLAG.parent.mkdir(exist_ok=True)
    for p in PROC[escena]:
        subprocess.Popen([PY, p], cwd=H, env={**os.environ, 'ESCENA': escena, 'ESPERA': '1'}, stdout=open(H / 'out' / (p.replace('.py', '') + '.log'), 'a'), stderr=subprocess.STDOUT, start_new_session=True)
        if p == 'escritorio_auto.py': time.sleep(1.0)
    EST.update(escena=escena, fase='preparada', ultima=escena)


def auto(escena):
    """Mesa lista: prepara la escena y la lanza sola en cuanto avisa que ya detectó o rastrea (out/lista.flag)."""
    preparar(escena)
    actual = turno

    def _esperar():
        limite = time.monotonic() + 60
        while time.monotonic() < limite:
            with control:
                if actual != turno or EST['fase'] != 'preparada': return
                if (H / 'out/lista.flag').exists():
                    dale()
                    return
            time.sleep(0.1)
    threading.Thread(target=_esperar, daemon=True).start()


ARR = {'escritorio': 2.3, 'solar': 1.2, 'lab': 1.6}      # segundos de arranque antes de mostrar la escena (el del sistema solar es el más corto)


def boot():
    pcx('project', {"request_id": "boot-%d" % time.time_ns(), "widgets": [apertura()]})
    try:
        subprocess.Popen(['afplay', str(H / 'cues/aparicion.wav')])
    except OSError:
        pass  # la señal visual también funciona sin salida de audio


def dale():
    if EST['fase'] != 'preparada': return
    EST['fase'] = 'encendida'
    actual = turno
    espera = ARR.get(EST['escena'], 1.9)

    def abrir():
        # Un «Terminar» o «Preparar» intermedio invalida los dos relevos de esta apertura.
        with control:
            if actual != turno: return
            boot()
        time.sleep(espera)
        with control:
            if actual != turno: return
            FLAG.write_text('1')
        time.sleep(2.2)
        with control:
            if actual == turno:
                pcx('remove', {"request_id": "boot-rm-%d" % time.time_ns(), "ids": ["boot"]})

    threading.Thread(target=abrir, daemon=True).start()


class Ctl(BaseHTTPRequestHandler):
    def do_GET(self):
        ruta = self.path.split('?')[0]; q = self.path.split('escena=')[1].split('&')[0] if 'escena=' in self.path else None
        with control:
            if ruta in ('/fin', '/stop'): fin()
            elif ruta == '/preparar': (preparar if 'manual=1' in self.path else auto)(q if q in PROC else siguiente())      # por defecto se lanza sola al estar lista
            elif ruta == '/dale': dale()
            elif ruta == '/start': preparar(q if q in PROC else 'escritorio'); time.sleep(0.2); dale()
        cuerpo = json.dumps({"ok": True, "vivos": vivos(), "escena": EST['escena'], "nombre": NOMBRE.get(EST['escena']), "fase": EST['fase'], "siguiente": NOMBRE[siguiente()]}, ensure_ascii=False).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers(); self.wfile.write(cuerpo)

    def log_message(self, *a): pass


if __name__ == '__main__':
    print('control de la demo en http://127.0.0.1:9301  (Terminar · Preparar siguiente · Dale)', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 9301), Ctl).serve_forever()
