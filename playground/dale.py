"""«Dale»: un botón. Libera el proyector → calibración (enchufable: autocalibración de h6 cuando exista, o rig dado)
→ scan gray code con la Osmo → pipeline P6 (decode, rig, nube, malla, objetos, materiales, escena registrada, verificación)
con visor en vivo → escena registrada P4 → runtime con efectos por objeto. Cada paso se narra por la secretaria de voz
(avances.jsonl) y se imprime como JSON por línea.

    python -m playground.dale                                  # en vivo (Osmo + proyector), calib auto si existe
    python -m playground.dale --calib skip --rig captures/rig.json
    python -m playground.dale --synthetic --no-runtime         # ensayo de punta a punta sin hardware
    python -m playground.dale --replay captures/scan5 --no-runtime --calib skip   # ensayo con datos grabados

Reglas: el proyector y la Osmo los usa un solo proceso a la vez (dale apaga el runtime y los trackers antes); nunca se
abre nada en el display interno ni en un Sidecar; capturas privadas en la sesión (captures/, fuera de git).
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PY = sys.executable
ROOT = Path(__file__).resolve().parents[1]
AVANCES = Path.home() / ".local/state/sl-scanner/secretaria/avances.jsonl"


def evento(paso: str, msg: str, ok: bool = True, **extra) -> None:
    d = {"t": dt.datetime.now().isoformat(timespec="seconds"), "paso": paso, "ok": ok, "msg": msg, **extra}
    print(json.dumps(d, ensure_ascii=False), flush=True)
    try:
        ESTADO_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ESTADO_FILE.open("a") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    except OSError:
        pass


def narrar(texto: str) -> None:
    """Avance para la secretaria de voz (si está corriendo lo cuenta; si no, queda en la cola)."""
    try:
        AVANCES.parent.mkdir(parents=True, exist_ok=True)
        with AVANCES.open("a") as f:
            f.write(json.dumps({"id": f"a{int(time.time() * 1000)}", "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                                "nivel": "trabajo", "de": "dale", "texto": texto}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def run(cmd: list[str], paso: str, cwd: Path = ROOT, env: dict | None = None, stream_json: bool = False, on_event=None) -> int:
    """Corre un comando; con stream_json reenvía cada línea JSON como evento y, si hay on_event, lo llama con
    (evento, proceso) para poder responderle por stdin (p. ej. 'continue' al autocalib entre vistas)."""
    evento(paso, "corriendo: " + " ".join(cmd))
    p = subprocess.Popen(cmd, cwd=cwd, env={**os.environ, **(env or {})}, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, bufsize=1)
    if on_event is None:                    # nadie va a responder por stdin: se cierra para que el hijo vea EOF y no se cuelgue
        try:
            p.stdin.close()
        except OSError:
            pass
    tail = []
    for line in p.stdout:
        line = line.rstrip()
        if not line or "objc[" in line or "OpenCV:" in line or "WARN:" in line:
            continue
        tail.append(line); tail = tail[-12:]
        if stream_json and line.startswith("{"):
            try:
                d = json.loads(line); evento(paso, d.get("msg", ""), d.get("ok", True), **{k: v for k, v in d.items() if k not in ("msg", "ok")})
                if on_event:
                    on_event(d, p)
                continue
            except json.JSONDecodeError:
                pass
        print("   " + line[:200], flush=True)
    rc = p.wait()
    evento(paso, f"terminó con código {rc}", ok=rc == 0, cola=tail[-3:])
    return rc


def liberar_proyector() -> None:
    for pat in ("playground serve", "touchsynth live", "playground.dualview", "playground.firstlook", "playground.workbench"):
        subprocess.run(["pkill", "-f", pat], capture_output=True)
    time.sleep(1.5)
    try:
        urllib.request.urlopen("http://127.0.0.1:8790/api/state", timeout=1.5); evento("liberar", "el puerto 8790 sigue ocupado", ok=False)
    except Exception:  # noqa: BLE001
        evento("liberar", "proyector y Osmo libres (runtime y trackers apagados)")


FEEDBACK = Path.home() / ".local/state/sl-scanner/secretaria/feedback.jsonl"


CONTINUE_FILE = Path.home() / ".local/state/sl-scanner/dale.continue"     # el dashboard lo crea al apretar «Continuar»
ESTADO_FILE = Path.home() / ".local/state/sl-scanner/dale.estado.json"     # último evento, para el dashboard


def esperar_confirmacion(timeout_s: float = 180.0) -> bool:
    """Espera a que Max diga por voz 'continuar/listo/dale/ya' (feedback.jsonl), apriete «Continuar» en el dashboard
    (archivo dale.continue) o Enter en la terminal; True si confirmó."""
    import select
    start = FEEDBACK.stat().st_size if FEEDBACK.exists() else 0
    CONTINUE_FILE.unlink(missing_ok=True)
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if CONTINUE_FILE.exists():
            CONTINUE_FILE.unlink(missing_ok=True); return True
        if FEEDBACK.exists() and FEEDBACK.stat().st_size > start:
            with FEEDBACK.open() as f:
                f.seek(start); nuevo = f.read().lower()
            if any(w in nuevo for w in ("contin", "listo", "dale", " ya", "sigue")):
                return True
            start = FEEDBACK.stat().st_size
        if sys.stdin and sys.stdin.isatty():
            r, _, _ = select.select([sys.stdin], [], [], 1.0)
            if r:
                sys.stdin.readline(); return True
        else:
            time.sleep(1.0)
    return False


AUTO_CONTINUE = False


def on_autocalib(d: dict, proc) -> None:
    """Eventos del autocalib (h6): entre vistas pide mover la cámara y espera 'continue'."""
    if d.get("step") == "move_camera":
        # el mensaje lo manda el autocalib (motion=rigid: se mueve TODO el conjunto Osmo+proyector; motion=camera: solo
        # la Osmo); si no viene, texto según el modo
        msg = (d.get("msg") or "").strip()
        if not msg:
            msg = ("Mueve el conjunto proyector y Osmo juntos a otra posición apuntando a la misma escena, sin tocar el zoom ni la escena."
                   if d.get("motion") == "rigid" else "Mueve la Osmo a la siguiente posición, sin tocar el proyector ni la escena.")
        narrar(msg + " Cuando esté quieto, di continuar.")
        ok = True if AUTO_CONTINUE else esperar_confirmacion()
        if not ok:
            narrar("No recibí confirmación; sigo igual con la vista actual.")
        try:
            proc.stdin.write("continue\n"); proc.stdin.flush()
        except (OSError, ValueError):
            pass


def autocalib_soporta(flag: str) -> bool:
    r = subprocess.run([PY, "-m", "slscan", "autocalib", "--help"], capture_output=True, text=True, cwd=ROOT)
    return r.returncode == 0 and flag in r.stdout


def autocalib_disponible() -> bool:
    r = subprocess.run([PY, "-m", "slscan", "autocalib", "--help"], capture_output=True, text=True, cwd=ROOT)
    return r.returncode == 0


def primero(patrones: list[str]) -> Path | None:
    for pat in patrones:
        hits = sorted(glob.glob(pat, recursive=True))
        if hits:
            return Path(hits[0])
    return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sesion", default=None, help="carpeta de la sesión (default captures/dale-<fecha>)")
    p.add_argument("--calib", choices=["auto", "skip", "replay"], default="auto")
    p.add_argument("--rig", default=None, help="rig.json existente (con --calib skip)")
    p.add_argument("--replay", default=None, help="usar un scan grabado en vez de capturar (p. ej. captures/scan5)")
    p.add_argument("--synthetic", action="store_true", help="pipeline con adaptadores sintéticos (ensayo sin hardware)")
    p.add_argument("--port", type=int, default=8796, help="visor del pipeline (P6)")
    p.add_argument("--no-runtime", action="store_true"); p.add_argument("--no-scan", action="store_true")
    p.add_argument("--sin-radiometria", action="store_true", help="saltar el barrido RGB/brillo")
    p.add_argument("--settle", type=float, default=1.2); p.add_argument("--skip-bits", type=int, default=2)
    p.add_argument("--rotate", type=int, default=0, help="rotación del cuadro de la Osmo (0 si está horizontal como en la sala del 17-oct; 270 si está vertical como en la mesa)")
    p.add_argument("--downscale", type=int, default=2, help="reducción del cuadro 4K de la Osmo (1 = 4K completo en todo el flujo)")
    p.add_argument("--cam-size", default="3840x2160")
    p.add_argument("--escena-efectos", default="halos", help="escena de playground.showcase al final")
    p.add_argument("--views", type=int, default=1, choices=[1, 2, 3], help="vistas para el autocalib (h6); >1 pide mover entre vistas")
    p.add_argument("--motion", default="rigid", choices=["rigid", "camera"], help="rigid: se mueve el conjunto Osmo+proyector (montaje fijo); camera: solo la Osmo")
    p.add_argument("--scale", default=None, help="escala externa del autocalib: huincha:MM (baseline óptico) o teclas:MM con --scale-pixels")
    p.add_argument("--scale-pixels", default=None, help="x1,y1,x2,y2 en px del proyector para --scale teclas:MM")
    p.add_argument("--viewer-state", default=None, help="carpeta ABS del visor de calibración (8797) para que autocalib publique su estado")
    p.add_argument("--replay-calib", default=None, help="ensayo: sesión grabada del autocalib (view_XX_map.npz + autocalib-session.json)")
    p.add_argument("--auto-continue", action="store_true", help="ensayo: responder 'continue' a move_camera sin esperar a Max")
    a = p.parse_args(argv)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    try:
        ESTADO_FILE.parent.mkdir(parents=True, exist_ok=True); ESTADO_FILE.write_text("")
    except OSError:
        pass
    sesion = Path(a.replay) if a.replay else Path(a.sesion or f"captures/dale-{stamp}")
    out = ROOT / "reports" / "pipeline" / (sesion.name if not a.synthetic else f"sintetico-{stamp}")
    evento("inicio", f"sesión {sesion} → salida {out}", sintetico=a.synthetic, replay=bool(a.replay))
    narrar("Dale. Empiezo: libero el proyector y la cámara.")
    # 1. liberar
    liberar_proyector()
    # 1b. guía: proyector colocado y quieto (en vivo; en ensayos se salta)
    if not (a.synthetic or a.replay or a.auto_continue):
        evento("guia", "Coloca el proyector apuntando a la escena, apóyalo y déjalo quieto. Luego Continuar.", step="proyector_quieto")
        narrar("Coloca el proyector apuntando a la escena, apóyalo y déjalo quieto. Cuando esté, di continuar o aprieta Continuar en el dashboard.")
        if not esperar_confirmacion(600):
            evento("guia", "sin confirmación en 10 min: sigo igual", ok=False)
    p_est = ESTADO_FILE
    # 2. visor del pipeline en vivo (antes de correr, para ver el progreso)
    out.mkdir(parents=True, exist_ok=True)
    visor = subprocess.Popen([PY, "-m", "slscan", "pipeline", "serve", str(out), "--port", str(a.port)], cwd=ROOT,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    evento("visor", f"visor del pipeline en http://127.0.0.1:{a.port}")
    rig: Path | None = Path(a.rig) if a.rig else None
    try:
        # 3. calibración (enchufable)
        global AUTO_CONTINUE
        AUTO_CONTINUE = bool(a.auto_continue)
        if a.replay_calib:                     # ensayo del autocalib con una sesión grabada (sin hardware), con move_camera
            rc_dir = Path(a.replay_calib)
            cmd = [PY, "-m", "slscan", "autocalib", str(rc_dir), "--replay", "--out", str(rc_dir / f"rig-{stamp}.json"), "--views", str(a.views)]
            if a.scale:
                cmd += ["--scale", a.scale]
            rc = run(cmd, "calib", stream_json=True, on_event=on_autocalib)
            rig = (rc_dir / f"rig-{stamp}.json") if rc == 0 else None
            evento("calib", f"autocalib replay → {'rig candidato ' + str(rig) if rig else 'sin rig (código ' + str(rc) + ')'}", ok=rig is not None)
        elif a.synthetic:
            evento("calib", "ensayo sintético: el rig lo genera el pipeline")
        elif a.calib == "auto" and autocalib_disponible():
            narrar("Autocalibración sobre la escena: voy a proyectar patrones y mirarlos con la cámara. No muevas nada.")
            sesion.mkdir(parents=True, exist_ok=True)
            cmd = [PY, "-m", "slscan", "autocalib", str(sesion), "--out", str(sesion / "rig.json"), "--display-confirmed", "--views", str(a.views),
                   "--settle", str(max(a.settle, 2.0))]
            if autocalib_soporta("--downscale"):
                cmd += ["--rotate", str(a.rotate), "--downscale", str(a.downscale), "--cam-size", a.cam_size]
            if a.views > 1 and autocalib_soporta("--motion"):
                cmd += ["--motion", a.motion]
            elif a.views == 1 and autocalib_soporta("--motion"):
                cmd += ["--motion", "camera"]          # el modelo rígido exige 2+ vistas; con una sola, la aproximada
            if a.scale:
                cmd += ["--scale", a.scale]
            if a.scale_pixels:
                cmd += ["--scale-pixels", a.scale_pixels]
            if a.viewer_state:
                cmd += ["--viewer-state", str(Path(a.viewer_state).resolve())]
            rc = run(cmd, "calib", stream_json=True, on_event=on_autocalib)
            if rc != 0:
                narrar("La autocalibración no convergió. Sigo con el escaneo, pero sin rig no habrá nube en milímetros."); rig = None
            else:
                rig = sesion / "rig.json"
        elif a.calib == "auto":
            evento("calib", "autocalibración todavía no disponible (slscan autocalib): paso enchufable, sigo sin rig o con --rig", ok=rig is not None)
            narrar("La autocalibración sin hoja todavía no está lista; sigo con el rig que haya.")
        else:
            evento("calib", f"calibración omitida; rig = {rig}")
        # 4. scan
        if a.replay:
            evento("scan", f"replay de {sesion}")
        elif a.synthetic or a.no_scan:
            evento("scan", "sin captura")
        else:
            narrar("Escaneo con luz estructurada: cuarenta y cuatro patrones, un minuto. No te cruces frente al proyector.")
            sesion.mkdir(parents=True, exist_ok=True)
            rc = run([PY, "-m", "slscan", "scan", "--camera", "osmo", "--cam-size", a.cam_size, "--downscale", str(a.downscale), "--rotate", str(a.rotate),
                      "--name", "osmo", "--screen", "1920x1080", "--size", "1920x1080", "--settle", str(a.settle), "--skip-bits", str(a.skip_bits),
                      "--out", str(sesion), "--name-out", sesion.name], "scan")
            if rc != 0:
                narrar("El escaneo falló. Reviso y vuelvo."); return 2
        # 4a. barrido radiométrico (negro/base, referencia, rampas R/G/B y gris): para la respuesta de color, el albedo
        #     relativo y la compensación de proyección (P3/P7). Exposición: la Osmo en webcam no la fija; se registra.
        if not a.synthetic and not a.replay and not a.no_scan and not a.sin_radiometria:
            narrar("Barrido de color y brillo: rampas rojas, verdes, azules y grises, un minuto más.")
            rc = run([PY, "-m", "playground.radiometria", str(sesion), "--rotate", str(a.rotate), "--downscale", str(a.downscale), "--cam-size", a.cam_size], "radiometria")
            if rc != 0:
                evento("radiometria", "el barrido falló; sigo sin él", ok=False)
        # 4b. escena 2D de respaldo (mapa proyector→cámara, verificación ArUco, segmentación): es lo que el runtime
        #     proyecta esta noche aunque el pipeline métrico quede bloqueado por falta de rig
        if not a.synthetic and not a.replay and (sesion / "osmo" / "map.npz").is_file():
            narrar("Armo la escena de proyección: mapa, verificación con marcadores y segmentación. Medio minuto.")
            rc = run([PY, "-m", "playground.recalib", "--scan", str(sesion), "--skip-scan", "--no-workbench", "--rotate", str(a.rotate),
                      "--downscale", str(a.downscale), "--cam-size", a.cam_size], "escena2d")
            if rc != 0:
                evento("escena2d", "la escena 2D de respaldo falló; el runtime no tendrá objetos", ok=False)
        # 5. pipeline P6 (decode → rig → nube → malla → objetos → materiales → escena → verificación)
        narrar("Ahora corre el pipeline: correspondencias, nube, malla, objetos y escena registrada. Míralo en el visor.")
        cmd = [PY, "-m", "slscan", "pipeline", "run", str(sesion if not a.synthetic else out / "scan-sintetico"), "--out", str(out)]
        if a.synthetic:
            cmd.append("--synthetic")
        if rig and rig.is_file():
            cmd += ["--rig", str(rig)]
        rc = run(cmd, "pipeline")
        rep = out / "pipeline.json"
        if rep.is_file():
            d = json.loads(rep.read_text()); st = {k: v.get("state") for k, v in d.get("stages", {}).items()}
            evento("pipeline", "etapas", **st); narrar("Pipeline listo. " + ", ".join(f"{k} {v}" for k, v in st.items()))
        # 6. escena registrada P4 (si hay rig + malla)
        mesh = primero([str(out / "artifacts" / "mesh" / "*" / "mesh.json"), str(out / "**" / "mesh.json")])
        cloud = primero([str(out / "artifacts" / "triangulate" / "*" / "cloud.json"), str(out / "**" / "cloud.json")])
        objs = primero([str(out / "artifacts" / "segment" / "*" / "objects.json"), str(out / "**" / "objects.json")])
        rigp = rig if rig and rig.is_file() else primero([str(out / "artifacts" / "validate_rig" / "*" / "rig.json"), str(out / "**" / "rig.json")])
        escena = primero([str(out / "artifacts" / "register_render" / "*" / "scene.json")])
        if escena:
            evento("escena3d", f"escena registrada por el pipeline: {escena} (visor: python -m slscan.registered serve {escena} --port 8798)")
        elif mesh and rigp:
            reg = out / "registered"
            cmd = [PY, "-m", "slscan.registered", "build", "--rig", str(rigp), "--mesh", str(mesh), "--out", str(reg)]
            if cloud:
                cmd += ["--cloud", str(cloud)]
            if objs:
                cmd += ["--objects", str(objs)]
            rc = run(cmd, "escena3d")
            if rc == 0:
                evento("escena3d", f"escena registrada en {reg} (visor: python -m slscan.registered serve {reg / 'scene.json'} --port 8798)")
        else:
            evento("escena3d", "sin rig o sin malla: no hay escena registrada todavía", ok=False)
        # 7. runtime con efectos por objeto
        if a.no_runtime:
            evento("runtime", "omitido (--no-runtime)")
        else:
            if objs:
                shutil.copy(objs, sesion / "objects.json")
            if (sesion / "objects.json").is_file() or (sesion / "segments.json").is_file():
                narrar("Prendo el playground con la escena nueva y efectos por objeto.")
                subprocess.Popen([PY, "-m", "playground", "serve", "--scan", str(sesion), "--projector", "--port", "8790"], cwd=ROOT,
                                 env={**os.environ, "SLSCAN_SCAN": str(sesion), "TOUCHSYNTH_MIDI": "mac"},
                                 stdout=open(Path.home() / ".local/state/sl-scanner/serve.log", "w"), stderr=subprocess.STDOUT)
                time.sleep(9)
                run([PY, "-m", "playground.showcase", "--escena", a.escena_efectos], "efectos")
            else:
                evento("runtime", "la sesión no tiene objects.json ni segments.json: corre playground.segment_pov o P5 primero", ok=False)
        narrar("Listo el dale. Dime qué ves.")
        evento("fin", f"visor pipeline http://127.0.0.1:{a.port} · sesión {sesion}")
    finally:
        if a.no_runtime and a.synthetic:
            time.sleep(1.0)
        # el visor queda vivo para que se siga mirando; se cierra con: pkill -f "pipeline serve"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
