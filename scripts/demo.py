"""Demo local sin hardware. Ctrl+C cierra únicamente los procesos de esta demo."""
from pathlib import Path
import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-port", type=int, default=8790)
    parser.add_argument("--dashboard-port", type=int, default=8795)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    ports = (args.runtime_port, args.dashboard_port)
    if len(set(ports)) != 2 or not all(0 < p < 65536 for p in ports):
        parser.error("Elige dos puertos distintos entre 1 y 65535.")
    root = Path(__file__).resolve().parents[1]
    for port in ports:
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(f"El puerto {port} está ocupado. Cierra la sesión anterior antes de abrir la demo.")
    env = {**os.environ, "TOUCHSYNTH_MIDI": "null", "SLSCAN_SCAN": "",
           "SLSCAN_RUNTIME_URL": f"http://127.0.0.1:{args.runtime_port}"}
    children = []
    try:
        for command in (["-m", "playground", "serve", "--scan", "", "--scene", "examples/demo/scene.json", "--port", str(args.runtime_port)],
                        ["-m", "playground.dashboard", "--port", str(args.dashboard_port)]):
            children.append(subprocess.Popen([sys.executable, *command], cwd=root, env=env))
        for _ in range(100):
            if any(p.poll() is not None for p in children):
                raise RuntimeError("Un proceso no pudo iniciar; revisa el error de arriba.")
            try:
                for port, endpoint in ((args.runtime_port, "/api/state"), (args.dashboard_port, "/")):
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}{endpoint}", timeout=1) as response:
                        response.read(1)
                break
            except OSError:
                time.sleep(.2)
        else:
            raise RuntimeError("La demo no respondió a tiempo.")
        url = f"http://127.0.0.1:{args.dashboard_port}"
        print(f"Demo lista: {url} · usa Efectos. Ctrl+C para cerrar.", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        while all(p.poll() is None for p in children):
            time.sleep(.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in children:
            if p.poll() is None:
                p.terminate()
        for p in children:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()


if __name__ == "__main__":
    main()
