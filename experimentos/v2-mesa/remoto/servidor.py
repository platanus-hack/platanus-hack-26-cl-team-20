#!/usr/bin/env python3
"""Servidor remoto de la presentación WUI: páginas móviles (/pov, /control) + señalización por long-polling.

Solo biblioteca estándar. Dos escuchas:
  - http://127.0.0.1:PUERTO       (la presentación y `tailscale serve` hablan con este)
  - https://0.0.0.0:PUERTO_LAN    (opcional, certificado autofirmado, exige ?k=CLAVE)
"""
import json, os, ssl, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

AQUI = os.path.dirname(os.path.abspath(__file__))
CFG = {}
try:
    CFG = json.load(open(os.path.join(AQUI, 'config.json')))
except Exception:
    pass
PUERTO = int(CFG.get('puerto', 9330))
PUERTO_LAN = int(CFG.get('puerto_lan', 0) or 0)
CLAVE = str(CFG.get('clave', ''))
ROLES = ('pres', 'pov', 'ctl')
MAX_COLA = 200
ESTATICOS = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/pov': ('pov.html', 'text/html; charset=utf-8'),
    '/control': ('control.html', 'text/html; charset=utf-8'),
    '/prueba': ('prueba.html', 'text/html; charset=utf-8'),
    '/presentacion-remoto.js': ('presentacion-remoto.js', 'application/javascript; charset=utf-8'),
}

cv = threading.Condition()
salas = {}


def sala(nombre):
    s = salas.get(nombre)
    if s is None:
        s = salas[nombre] = {'colas': {r: [] for r in ROLES}, 'sig': 1, 'estado': None, 'estado_ts': 0, 'visto': {}}
    return s


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Cache-Control', 'no-store')

    def _resp(self, cod, cuerpo, tipo='application/json'):
        if not isinstance(cuerpo, (bytes, bytearray)):
            cuerpo = json.dumps(cuerpo).encode()
        self.send_response(cod)
        self._cors()
        self.send_header('Content-Type', tipo)
        self.send_header('Content-Length', str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _q(self):
        u = urlparse(self.path)
        return u.path, {k: v[0] for k, v in parse_qs(u.query).items()}

    def _permitido(self, q):
        if not getattr(self.server, 'exige_clave', False):
            return True
        return bool(CLAVE) and q.get('k') == CLAVE

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        ruta, q = self._q()
        if ruta == '/salud':
            return self._resp(200, {'ok': True, 'ahora': time.time()})
        if not self._permitido(q):
            return self._resp(403, 'Falta la clave (?k=...)'.encode(), 'text/plain; charset=utf-8')
        if ruta == '/' and getattr(self.server, 'exige_clave', False):
            return self._resp(404, b'no', 'text/plain')
        if ruta in ESTATICOS:
            nombre, tipo = ESTATICOS[ruta]
            try:
                datos = open(os.path.join(AQUI, nombre), 'rb').read()
            except OSError:
                return self._resp(404, b'no existe', 'text/plain')
            log('GET', ruta, self.client_address[0], self.headers.get('X-Forwarded-For', ''))
            return self._resp(200, datos, tipo)
        if ruta == '/api/config':
            return self._resp(200, {k: CFG.get(k) for k in ('url_tailscale', 'url_lan', 'clave')})
        if ruta == '/api/poll':
            rol = q.get('rol')
            if rol not in ROLES:
                return self._resp(400, {'error': 'rol'})
            desde = int(q.get('desde', -1))
            espera = min(float(q.get('espera', 25)), 30)
            fin = time.time() + espera
            with cv:
                s = sala(q.get('sala', 'demo'))
                s['visto'][rol] = time.time()
                if desde < 0:
                    return self._resp(200, {'cursor': s['sig'] - 1, 'msgs': [], 'ahora': time.time()})
                while True:
                    msgs = [m for m in s['colas'][rol] if m['id'] > desde]
                    resto = fin - time.time()
                    if msgs or resto <= 0:
                        break
                    cv.wait(min(resto, 5))
                    s['visto'][rol] = time.time()
                cur = msgs[-1]['id'] if msgs else desde
                # si el servidor se reinició, el cursor del cliente queda por delante: lo devolvemos al actual
                if cur > s['sig'] - 1:
                    cur = s['sig'] - 1
            return self._resp(200, {'cursor': cur, 'msgs': msgs, 'ahora': time.time()})
        if ruta == '/api/estado':
            with cv:
                s = sala(q.get('sala', 'demo'))
                s['visto']['ctl'] = time.time()
                ah = time.time()
                r = {'estado': s['estado'], 'edad': (ah - s['estado_ts']) if s['estado'] else None,
                     'visto': {k: round(ah - v, 1) for k, v in s['visto'].items()}}
            return self._resp(200, r)
        return self._resp(404, {'error': 'ruta'})

    def do_POST(self):
        ruta, q = self._q()
        n = int(self.headers.get('Content-Length') or 0)
        crudo = self.rfile.read(n) if n else b''
        if not self._permitido(q):
            return self._resp(403, {'error': 'clave'})
        try:
            cuerpo = json.loads(crudo or b'{}')
        except ValueError:
            return self._resp(400, {'error': 'json'})
        if ruta == '/api/enviar':
            a = q.get('a')
            if a not in ROLES:
                return self._resp(400, {'error': 'a'})
            with cv:
                s = sala(q.get('sala', 'demo'))
                m = {'id': s['sig'], 'ts': time.time(), 'de': q.get('de', ''), 'm': cuerpo}
                s['sig'] += 1
                c = s['colas'][a]
                c.append(m)
                del c[:-MAX_COLA]
                cv.notify_all()
            log('MSG', q.get('sala', 'demo'), q.get('de', '?'), '->', a, cuerpo.get('tipo'), cuerpo.get('cmd', ''))
            return self._resp(200, {'ok': True, 'id': m['id']})
        if ruta == '/api/estado':
            with cv:
                s = sala(q.get('sala', 'demo'))
                s['estado'] = cuerpo
                s['estado_ts'] = time.time()
                s['visto']['pres'] = time.time()
            return self._resp(200, {'ok': True})
        return self._resp(404, {'error': 'ruta'})


class Srv(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def handle_error(self, request, client_address):
        e = sys.exc_info()[1]
        if isinstance(e, (ConnectionError, ssl.SSLError, TimeoutError, OSError)):
            return
        super().handle_error(request, client_address)


def main():
    local = Srv(('127.0.0.1', PUERTO), H)
    local.exige_clave = False
    log(f'escuchando http://127.0.0.1:{PUERTO}')
    cert, llave = os.path.join(AQUI, 'cert.pem'), os.path.join(AQUI, 'key.pem')
    if PUERTO_LAN and os.path.exists(cert) and os.path.exists(llave):
        lan = Srv(('0.0.0.0', PUERTO_LAN), H)
        lan.exige_clave = True
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, llave)
        lan.socket = ctx.wrap_socket(lan.socket, server_side=True, do_handshake_on_connect=False)
        threading.Thread(target=lan.serve_forever, daemon=True).start()
        log(f'escuchando https://0.0.0.0:{PUERTO_LAN} (autofirmado, exige clave)')
    try:
        local.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
