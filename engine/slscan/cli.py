"""CLI: `python -m slscan <comando>`. Cada paso reproducible desde la línea de comandos."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np


def _p(*a):
    print(*a, flush=True)


# ----------------------------------------------------------------------------- F0

def cmd_decode(args):
    from . import graycode, recon
    from .rig import Rig

    scan = Path(args.scan)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    name = args.name or scan.name
    t0 = time.time()
    items = graycode.load_sequence(scan)
    _p(f"[{name}] {len(items)} imágenes en {scan}")

    def prog(i, n, axis, bit, d):
        _p(f"  bit {i:2d}/{n}  eje {'xy'[axis]} bit {bit:2d}  diff promedio {d:7.2f}")

    dec = graycode.decode(items, threshold=args.threshold, channel=args.channel, progress=prog)
    s = dec.stats
    _p(f"  decodificados: x {s['valid_x']} ({100*s['valid_x']/s['total']:.1f}%)  y {s['valid_y']} "
       f"({100*s['valid_y']/s['total']:.1f}%)  ambos {s['valid']} ({100*s['valid']/s['total']:.1f}%)")
    dec.save(out / f"{name}_map.npz")
    for k, img in graycode.visualize(dec).items():
        cv2.imwrite(str(out / f"{name}_{k}.png"), img)

    m = recon.coalesce(dec)
    _p(f"  coalesce: {len(m.cam)} puntos (uno por píxel de proyector)")
    result = {"name": name, "images": len(items), "stats": s, "coalesced": int(len(m.cam)),
              "seconds": round(time.time() - t0, 2)}
    if args.rig:
        rig = Rig.load(args.rig)
        cam, pro = rig[args.camera], rig.projector()
        xyz = recon.triangulate_pair(m, cam, pro, rect=rig.extra, mode=args.mode)
        ply = out / f"{name}_cloud.ply"
        recon.write_ply(ply, xyz, m.color, comment=f"slscan decode {name}")
        _p(f"  nube: {len(xyz)} puntos -> {ply}")
        result["cloud"] = str(ply); result["points"] = int(len(xyz))
        if args.png:
            from .viewer import render_png
            png = out / f"{name}_cloud.png"
            render_png(xyz, m.color[:, ::-1].astype(np.uint8), png, title=f"{name} · {len(xyz)} puntos")
            _p(f"  captura: {png}")
    (out / f"{name}_result.json").write_text(json.dumps(result, indent=2, default=str))
    _p(f"  listo en {result['seconds']} s")
    return 0


def cmd_compare(args):
    from .recon import read_ply, compare_clouds
    a, _ = read_ply(args.ours)
    b, _ = read_ply(args.reference)
    if args.flip_yz:                     # sltk guarda (x, -y, -z) respecto al marco OpenCV de la cámara
        b = b * np.array([1, -1, -1], np.float32)
    r = compare_clouds(a, b)
    r_back = compare_clouds(b, a)
    r["reverse_median_mm"] = r_back["median_mm"]; r["reverse_p95_mm"] = r_back["p95_mm"]
    _p(json.dumps(r, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(r, indent=2))
    ok = r["median_mm"] <= args.tol and abs(r["n_a"] - r["n_b"]) / max(r["n_b"], 1) <= args.count_tol
    _p("OK" if ok else "DIFIERE")
    return 0 if ok else 1


def cmd_view(args):
    from .viewer import write_html
    from .rig import Rig
    clouds = [(Path(p).stem, Path(p)) for p in args.ply]
    rig = Rig.load(args.rig) if args.rig else None
    out = write_html(args.out, clouds, rig, title=args.title, point_size=args.point_size)
    _p(f"visor: {out}")
    return 0


def cmd_rig(args):
    from .rig import Rig
    rig = Rig.load(args.src)
    if args.out:
        rig.save(args.out)
        _p(f"guardado {args.out}")
    for d in rig.devices.values():
        fx, fy = d.fov_deg()
        _p(f"{d.name:6s} {d.kind:9s} {d.size[0]}x{d.size[1]}  f=({d.K[0,0]:.1f},{d.K[1,1]:.1f}) px  "
           f"FOV {fx:.1f}°x{fy:.1f}°  rms {d.rms}  centro {np.round(d.center_world(), 1)} mm")
    return 0


# ----------------------------------------------------------------------------- F1

def cmd_cameras(args):
    from .capture import list_cameras, list_displays
    for d in list_displays():
        _p(f"display {d['index']}: {d['w']}x{d['h']} en ({d['x']},{d['y']})")
    for c in list_cameras(args.max):
        _p(f"cámara [{c['index']}] {c['width']}x{c['height']} @ {c['fps']:.0f} fps  backend {c['backend']}")
    return 0


def cmd_grab(args):
    """Guarda un cuadro de una cámara (para ver con qué estamos trabajando)."""
    from .capture import Camera
    cam = open_camera(args.camera, size=args.cam_size, rotate=args.rotate, downscale=getattr(args, "downscale", 1))
    try:
        cam.flush(args.flush)
        f = cam.read()
    finally:
        cam.close()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(args.out, f)
    _p(f"{args.out} {f.shape[1]}x{f.shape[0]} (cámara {args.camera})")
    return 0


def cmd_show_board(args):
    """Muestra el tablero ChArUco a pantalla completa en un display (el iPad por Sidecar sirve para intrínsecos)."""
    from .calib import CharucoBoard
    from .capture import PatternWindow, list_displays, resolve_screen
    board = CharucoBoard.from_args(args)
    args.screen = resolve_screen(args.screen)
    d = list_displays()[args.screen]
    img = board.image(args.ppmm, args.margin)
    win = PatternWindow((d["w"], d["h"]), screen=args.screen, fullscreen=True, name="slscan-board")
    canvas = np.full((d["h"], d["w"]), 255, np.uint8)
    scale = min(d["w"] / img.shape[1], d["h"] / img.shape[0])
    im = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    y0, x0 = (d["h"] - im.shape[0]) // 2, (d["w"] - im.shape[1]) // 2
    canvas[y0:y0 + im.shape[0], x0:x0 + im.shape[1]] = im
    win.show(canvas)
    sq_px = args.square * args.ppmm * scale
    _p(f"tablero en display {args.screen} ({d['w']}x{d['h']}): casilla = {sq_px:.1f} px de pantalla. "
       f"Mide una casilla con regla en el iPad y pasa ese valor en --square al calibrar. q para cerrar.")
    try:
        while win.wait_key(200) != ord("q"):
            pass
    finally:
        win.close()
    return 0


def cmd_calib_procam(args):
    """Calibración guiada proyector-cámara: en cada pose captura el tablero y la secuencia gray code."""
    from .capture import Camera, open_camera, PatternWindow, auto_exposure_check, capture_sequence
    from .calib import CharucoBoard
    from . import graycode
    size = tuple(int(v) for v in args.size.split("x"))
    board = CharucoBoard.from_args(args)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    win = PatternWindow(size, screen=args.screen, fullscreen=not args.windowed)
    cam = open_camera(args.camera, size=args.cam_size, rotate=args.rotate, downscale=getattr(args, "downscale", 1), name=args.name)
    guide = "slscan-guia"
    cv2.namedWindow(guide, cv2.WINDOW_NORMAL)
    from .capture import place_on_builtin
    place_on_builtin(guide, 960, 600)

    def show_guide(lines, frame=None, ok=True):
        panel = np.full((600, 960, 3), 30, np.uint8)
        if frame is not None:
            h = 330; w = int(frame.shape[1] * h / frame.shape[0]); w = min(w, 940)
            panel[10:10 + h, 10:10 + w] = cv2.resize(frame, (w, h))
        for i, t in enumerate(lines):
            cv2.putText(panel, t, (12, 370 + 32 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0) if ok else (0, 0, 255), 2)
        cv2.imshow(guide, panel)
        return cv2.waitKey(50) & 0xFF

    try:
        win.solid(args.board_level)
        chk = auto_exposure_check(win, cam)
        _p(f"exposición: blanco {chk['white_mean']:.0f} negro {chk['black_mean']:.0f} -> "
           f"{'fija OK' if chk['manual_ok'] else 'PARECE AUTO (igual sigo; fija la exposición si puedes)'}")
        win.solid(args.board_level)
        existing = sorted(p for p in out.glob("pose_*") if p.is_dir())
        pose = len(existing)
        while pose < args.poses:
            # 1. esperar tablero visible y quieto
            while True:
                cam.flush(2); f = cam.read()
                d = board.detect(f)
                n = 0 if d is None else len(d["img"])
                lines = [f"Pose {pose + 1}/{args.poses}: pon la superficie plana con el tablero donde el proyector ilumine",
                         f"y la camara lo vea entero. Esquinas vistas: {n} (necesito >= 12).",
                         "Cambia inclinacion y posicion en cada pose. ESPACIO = capturar  ·  q = terminar"]
                k = show_guide(lines, f, ok=n >= 12)
                if k == ord("q"):
                    pose = args.poses; break
                if k == ord(" ") and n >= 12:
                    break
            if pose >= args.poses:
                break
            folder = out / f"pose_{pose:04d}" / cam.name
            folder.mkdir(parents=True, exist_ok=True)
            cam.flush(3); cv2.imwrite(str(folder / "board.png"), cam.read())
            show_guide([f"Pose {pose + 1}: capturando gray code, no toques nada ({2 * (graycode.bits_for(size[0]) + graycode.bits_for(size[1]))} patrones)..."])
            specs = graycode.sequence(size)
            capture_sequence(win, [cam], specs, lambda s_: graycode.pattern(size, s_.axis, s_.bit, s_.inverse),
                             out / f"pose_{pose:04d}", settle_s=args.settle)
            win.solid(args.board_level)
            _p(f"pose {pose} guardada en {folder.parent}")
            pose += 1
    finally:
        win.close(); cam.close(); cv2.destroyWindow(guide)
    _p(f"{pose} poses en {out}. Siguiente: slscan calib-rig {out} --proj-size {args.size} --out captures/rig.json --html reports/rig.html")
    return 0


def cmd_scan(args):
    """Escaneo completo en un comando: gray code desde el proyector, captura, decodificación, nube PLY, captura y visor."""
    from .capture import Camera, open_camera, PatternWindow, capture_sequence
    from . import graycode, recon
    from .rig import Rig
    from .viewer import render_png, write_html
    size = tuple(int(v) for v in args.size.split("x"))
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    class _N:  # nombre de cámara sin abrirla (modo --no-capture)
        name = args.name
    cam = _N()
    if not args.no_capture:
        win = PatternWindow(size, screen=args.screen, fullscreen=not args.windowed)
        active_cam = None
        try:
            cam = active_cam = open_camera(args.camera, size=args.cam_size, rotate=args.rotate, downscale=getattr(args, "downscale", 1), name=args.name)
            from .metric_session import frame_size, check_frame, write_json
            expected = frame_size(tuple(map(int,args.cam_size.split('x'))),args.rotate,args.downscale)
            check_frame(cam.read(),expected)
            write_json(out/'camera_acquisition.json',{'requested_size':args.cam_size,'effective_size':list(expected),
                'rotate':args.rotate,'downscale':args.downscale,
                'camera':cam.metadata() if hasattr(cam,'metadata') else {'lock_status':'unverified'}})
            specs = graycode.sequence(size)
            _p(f"{len(specs)} patrones, {args.settle:.1f} s cada uno (~{len(specs) * args.settle / 60:.1f} min). No muevas nada.")
            capture_sequence(win, [cam], specs, lambda s_: graycode.pattern(size, s_.axis, s_.bit, s_.inverse), out,
                             settle_s=args.settle, log=None)
            win.solid(args.board_level)
            cam.flush(5); cv2.imwrite(str(out / cam.name / "color.png"), cam.read())
        finally:
            win.close()
            if active_cam is not None: active_cam.close()
    items = graycode.load_sequence(out / cam.name)
    dec = graycode.decode(items, threshold=args.threshold, skip_bits=args.skip_bits)
    st = dec.stats
    _p(f"decodificados: {st['valid']} px de {st['total']} ({100 * st['valid'] / st['total']:.1f}%)")
    dec.save(out / cam.name / "map.npz")
    for k, img in graycode.visualize(dec).items():
        cv2.imwrite(str(out / f"{cam.name}_{k}.png"), img)
    if not args.rig:
        _p("sin --rig: solo mapas (sin nube)."); return 0
    rig = Rig.load(args.rig)
    camdev = rig[cam.name] if cam.name in rig.devices else rig.cameras()[0]
    if tuple(dec.size) != camdev.size or size != rig.projector().size:
        raise ValueError('resolución del escaneo incompatible con el rig; no triangular')
    m = recon.coalesce(dec)
    xyz = recon.triangulate_pair(m, camdev, rig.projector(), mode="world")
    keep = np.isfinite(xyz).all(axis=1)
    if args.zmax > 0:
        keep &= (xyz[:, 2] > 0) & (xyz[:, 2] < args.zmax)
    xyz, col = xyz[keep], m.color[keep]
    ply = out / f"{args.name_out}.ply"; recon.write_ply(ply, xyz, col, comment=f"slscan scan {out.name}")
    png = Path("reports") / f"{args.name_out}.png"; png.parent.mkdir(exist_ok=True)
    render_png(xyz, col[:, ::-1].astype(np.uint8), png, title=f"{args.name_out} · {len(xyz)} puntos")
    html = Path("reports") / f"{args.name_out}_viewer.html"
    write_html(html, [(args.name_out, ply)], rig, title=f"Escaneo {args.name_out}")
    _p(f"nube: {len(xyz)} puntos -> {ply}\ncaptura: {png}\nvisor: {html}")
    return 0


def projected_board(board, size, margin_px: int = 40):
    """Imagen del ChArUco a tamaño del proyector y píxel de proyector de cada esquina interior (por id)."""
    sq_px = int(min((size[0] - 2 * margin_px) / board.cols, (size[1] - 2 * margin_px) / board.rows))
    img = board.board.generateImage((board.cols * sq_px + 2 * margin_px, board.rows * sq_px + 2 * margin_px), marginSize=margin_px)
    canvas = np.full((size[1], size[0]), 255, np.uint8)
    y0, x0 = (size[1] - img.shape[0]) // 2, (size[0] - img.shape[1]) // 2
    canvas[y0:y0 + img.shape[0], x0:x0 + img.shape[1]] = img
    corners_mm = board.corners_mm()                       # (n,3) en mm del tablero "ideal" (square_mm)
    corners_px = corners_mm[:, :2] / board.square_mm * sq_px + np.array([x0 + margin_px, y0 + margin_px])
    return canvas, corners_px, sq_px


def cmd_calib_projected(args):
    """Calibración con el tablero PROYECTADO sobre una superficie plana: por pose, la cámara ve el ChArUco que
    proyecta el proyector; Max mide el ancho proyectado (mm) y eso da la escala. Cámara, proyector y pose relativa."""
    from .capture import Camera, open_camera, PatternWindow
    from .calib import CharucoBoard, calibrate_points
    from .rig import Device, Rig
    from .viewer import write_html
    import json as _json
    size = tuple(int(v) for v in args.size.split("x"))
    board = CharucoBoard.from_args(args)
    canvas, corners_px, sq_px = projected_board(board, size)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "projected_board.png"), canvas)
    win = PatternWindow(size, screen=args.screen)
    win.show(canvas)
    cam = open_camera(args.camera, size=args.cam_size, rotate=args.rotate, downscale=getattr(args, "downscale", 1), name=args.name)
    poses = []
    try:
        for k in range(args.poses):
            input(f"Pose {k + 1}/{args.poses}: superficie plana en la zona proyectada. ENTER cuando esté quieta... ")
            cam.flush(15); f = cam.read()
            d = board.detect(f)
            n = 0 if d is None else len(d["img"])
            cv2.imwrite(str(out / f"pose_{k:02d}.png"), f)
            if n < 8:
                _p(f"  solo {n} esquinas: acerca/aplana la superficie o sube la luz; repite esta pose."); continue
            w_mm = float(input(f"  {n} esquinas. Ancho proyectado de las {board.cols} casillas en mm (regla): "))
            square_mm = w_mm / board.cols
            ids = d["ids"]
            obj = np.column_stack([corners_px[ids] * 0 + board.corners_mm()[ids][:, :2] / board.square_mm * square_mm, np.zeros(len(ids))])
            poses.append({"obj": obj, "cam": d["img"], "pro": corners_px[ids], "square_mm": square_mm, "n": n})
            _p(f"  pose {k} guardada: casilla {square_mm:.1f} mm")
    finally:
        win.close(); cam.close()
    if len(poses) < 3:
        _p("se necesitan >= 3 poses válidas"); return 1
    obj_l = [p_["obj"] for p_ in poses]
    cam_res = calibrate_points(obj_l, [p_["cam"] for p_ in poses], cam.size)
    pro_res = calibrate_points(obj_l, [p_["pro"] for p_ in poses], size, K_guess=np.array([[1.3 * size[0], 0, size[0] / 2], [0, 1.3 * size[0], size[1] * 0.9], [0, 0, 1]], float))
    _p(f"cámara rms {cam_res['rms']:.2f} px, proyector rms {pro_res['rms']:.2f} px")
    rms, K1, d1, K2, d2, R, T, E, F = cv2.stereoCalibrate(
        [o.astype(np.float32).reshape(-1, 1, 3) for o in obj_l], [p_["cam"].astype(np.float32).reshape(-1, 1, 2) for p_ in poses],
        [p_["pro"].astype(np.float32).reshape(-1, 1, 2) for p_ in poses], cam_res["K"], cam_res["dist"], pro_res["K"], pro_res["dist"],
        cam.size, flags=cv2.CALIB_FIX_INTRINSIC)
    _p(f"estéreo rms {rms:.2f} px; baseline {np.linalg.norm(T):.0f} mm")
    rig = Rig({cam.name: Device(cam.name, "camera", cam.size, K1, d1.ravel(), rms=cam_res["rms"]),
               "proj": Device("proj", "projector", size, K2, d2.ravel(), R, T.ravel(), rms=pro_res["rms"])},
              world=cam.name, notes=f"calib-projected {len(poses)} poses, casillas {[round(p_['square_mm'],1) for p_ in poses]} mm (grueso)")
    rig.extra["stereo_rms"] = float(rms)
    rig.save(args.rig_out)
    write_html(args.html, [], rig, title="Rig (patrón proyectado, grueso)")
    _p(f"rig guardado en {args.rig_out}; visor {args.html}")
    return 0


def cmd_board(args):
    from .calib import CharucoBoard
    board = CharucoBoard.from_args(args)
    img = board.image(args.ppmm, args.margin)
    cv2.imwrite(args.out, img)
    w, h = board.size_mm
    _p(f"tablero {args.board} casillas de {args.square} mm (marcador {args.marker} mm): {w:.0f}x{h:.0f} mm, "
       f"imagen {img.shape[1]}x{img.shape[0]} px a {args.ppmm} px/mm -> {args.out}")
    _p("imprimir al 100 % (sin ajustar a página) y pegar en algo rígido; o mostrar en pantalla y medir la casilla real.")
    return 0


def cmd_capture_scan(args):
    """Captura una secuencia gray code (y opcionalmente el tablero) con todas las cámaras."""
    from .capture import Camera, open_camera, PatternWindow, capture_sequence, auto_exposure_check
    from . import graycode
    size = tuple(int(v) for v in args.size.split("x"))
    win = PatternWindow(size, screen=args.screen, fullscreen=not args.windowed)
    cams = [open_camera(i, size=args.cam_size, rotate=args.rotate, downscale=getattr(args, "downscale", 1)) for i in args.cameras.split(",")]
    if args.names:
        for c, n in zip(cams, args.names.split(",")):
            c.name = n
    out = Path(args.out)
    try:
        for c in cams:
            chk = auto_exposure_check(win, c)
            _p(f"{c.name}: blanco {chk['white_mean']:.0f} negro {chk['black_mean']:.0f} "
               f"-> {'exposición fija OK' if chk['manual_ok'] else 'PARECE AUTO-EXPOSICIÓN: fíjala antes de escanear'}")
            if not chk["manual_ok"] and not args.force:
                return 1
        if args.board:
            win.solid(args.board_level); time.sleep(args.settle)
            for c in cams:
                c.flush(); f = c.read()
                (out / c.name).mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(out / c.name / "board.png"), f)
            _p("tablero capturado")
        specs = graycode.sequence(size)
        _p(f"{len(specs)} patrones, {args.settle:.2f} s de espera cada uno")
        capture_sequence(win, cams, specs, lambda s: graycode.pattern(size, s.axis, s.bit, s.inverse), out,
                         settle_s=args.settle, log=_p)
    finally:
        win.close()
        for c in cams:
            c.close()
    _p(f"secuencia guardada en {out}")
    return 0


def cmd_display(args):
    from .capture import PatternWindow
    from . import graycode
    size = tuple(int(v) for v in args.size.split("x"))
    win = PatternWindow(size, screen=args.screen, fullscreen=not args.windowed)
    try:
        if args.test:
            win.show_test()
            _p("patrón de prueba (bordes, centro, grilla). Tecla q para salir.")
            win.wait_key()
            return 0
        specs = graycode.sequence(size)
        _p(f"{len(specs)} patrones gray code, {args.dt:.2f} s cada uno. q para salir.")
        for spec in specs:
            win.show(graycode.pattern(size, spec.axis, spec.bit, spec.inverse))
            if win.wait_key(int(args.dt * 1000)) == ord("q"):
                break
    finally:
        win.close()
    return 0


def cmd_latency(args):
    from .capture import PatternWindow, Camera, measure_latency
    size = tuple(int(v) for v in args.size.split("x"))
    win = PatternWindow(size, screen=args.screen, fullscreen=not args.windowed)
    cam = open_camera(args.camera, size=args.cam_size)
    try:
        r = measure_latency(win, cam, trials=args.trials)
    finally:
        win.close(); cam.close()
    _p(json.dumps(r, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(r, indent=2))
    return 0


def cmd_capture_calib(args):
    from .capture import Camera, open_camera, guided_board_capture
    from .calib import CharucoBoard
    board = CharucoBoard.from_args(args)
    cams = [open_camera(i, size=args.cam_size, rotate=args.rotate, downscale=getattr(args, "downscale", 1)) for i in args.cameras.split(",")]
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    try:
        n = guided_board_capture(cams, board, out, target=args.views, names=args.names.split(",") if args.names else None)
    finally:
        for c in cams:
            c.close()
    _p(f"{n} vistas guardadas en {out}")
    return 0


def cmd_calib_intrinsics(args):
    from .calib import CharucoBoard, calibrate_intrinsics
    from .rig import Device
    board = CharucoBoard.from_args(args)
    imgs = sorted(Path(args.images).glob(args.glob))
    if not imgs:
        _p("sin imágenes"); return 1
    res = calibrate_intrinsics([cv2.imread(str(p)) for p in imgs], board, rational=args.rational)
    _p(f"{len(imgs)} imágenes, {res['used']} usadas, rms {res['rms']:.3f} px, "
       f"por vista max {max(res['per_view']):.3f} px")
    dev = Device(args.name, "camera", res["size"], res["K"], res["dist"], rms=res["rms"])
    Path(args.out).write_text(json.dumps(dev.to_json(), indent=2))
    _p(f"guardado {args.out}  FOV {dev.fov_deg()[0]:.1f}°x{dev.fov_deg()[1]:.1f}°")
    if args.png:
        from .viewer import distortion_map_png
        distortion_map_png(dev.K, dev.dist, dev.size, args.png)
        _p(f"mapa de distorsión: {args.png}")
    return 0


def cmd_calib_rig(args):
    from .calib import CharucoBoard, calibrate_rig
    board = CharucoBoard.from_args(args)
    rig = calibrate_rig(Path(args.captures), board, proj_size=tuple(int(v) for v in args.proj_size.split("x"))
                        if args.proj_size else None, intrinsics_dir=Path(args.intrinsics) if args.intrinsics else None,
                        log=_p)
    rig.save(args.out)
    _p(f"rig guardado en {args.out}")
    if args.html:
        from .viewer import write_html
        write_html(args.html, [], rig, title="Rig calibrado")
        _p(f"visor del rig: {args.html}")
    return 0



# ----------------------------------------------------------------------------- PC Windows

def cmd_remote(args):
    from .remote import Remote, summarize_doctor
    r = Remote(args.host, args.user, args.root)
    if args.action == "ping":
        ok = r.ping(); _p("ok" if ok else "sin respuesta SSH"); return 0 if ok else 1
    if args.action == "doctor":
        d = r.doctor()
        for line in summarize_doctor(d):
            _p(line)
        if args.out:
            Path(args.out).write_text(json.dumps(d, indent=2)); _p(f"guardado {args.out}")
        return 0
    if args.action == "sync":
        res = r.sync(Path(__file__).resolve().parent.parent)
        _p(res.stdout.strip() or "sincronizado"); _p(res.stderr.strip()) if res.stderr.strip() else None
        return res.returncode
    if args.action == "run":
        res = r.run(args.command or "python --version", capture=False)
        return res.returncode
    return 1


# ----------------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="slscan", description="escáner de luz estructurada multicámara")
    sp = p.add_subparsers(dest="cmd", required=True)
    from .metric_recon import add_parser as add_reconstruct_parser
    add_reconstruct_parser(sp)
    from .autocalib import add_parser as add_autocalib_parser
    add_autocalib_parser(sp)
    from .replay_session import add_parser as add_replay_session_parser
    add_replay_session_parser(sp)
    from .approx_rig import add_parser as add_approx_rig_parser
    add_approx_rig_parser(sp)

    def board_args(s):
        s.add_argument("--board", default="7x5", help="casillas ChArUco ancho x alto")
        s.add_argument("--square", type=float, default=40.0, help="lado de casilla en mm")
        s.add_argument("--marker", type=float, default=30.0, help="lado del marcador en mm")
        s.add_argument("--dict", default="DICT_5X5_100")


    s = sp.add_parser("decode", help="secuencia gray code -> mapas, coalesce y nube PLY")
    s.add_argument("scan"); s.add_argument("--out", required=True); s.add_argument("--name")
    s.add_argument("--rig", help="rig.json o procam_*.yaml de sltk")
    s.add_argument("--camera", default="cam0"); s.add_argument("--mode", default="world", choices=["world", "sltk"], help="world: marco de cam0 (OpenCV); sltk: igual que slcrunch")
    s.add_argument("--threshold", type=int, default=5); s.add_argument("--channel", type=int)
    s.add_argument("--png", action="store_true", help="captura PNG de la nube")
    s.set_defaults(fn=cmd_decode)

    s = sp.add_parser("compare", help="distancia entre dos nubes PLY (vecino más cercano)")
    s.add_argument("ours"); s.add_argument("reference"); s.add_argument("--out")
    s.add_argument("--flip-yz", action="store_true", help="la referencia es de sltk (y, z invertidos)")
    s.add_argument("--tol", type=float, default=0.05); s.add_argument("--count-tol", type=float, default=0.02)
    s.set_defaults(fn=cmd_compare)

    s = sp.add_parser("view", help="visor web Three.js con nubes y frustums")
    s.add_argument("ply", nargs="*"); s.add_argument("--rig"); s.add_argument("--out", required=True)
    s.add_argument("--title", default="SL Scanner"); s.add_argument("--point-size", type=float, default=1.5)
    s.set_defaults(fn=cmd_view)

    s = sp.add_parser("rig", help="muestra o convierte una calibración (sltk yaml -> rig.json)")
    s.add_argument("src"); s.add_argument("--out")
    s.set_defaults(fn=cmd_rig)

    s = sp.add_parser("cameras", help="lista displays y cámaras disponibles")
    s.add_argument("--max", type=int, default=8)
    s.set_defaults(fn=cmd_cameras)

    s = sp.add_parser("calib-projected", help="calibración gruesa con el ChArUco proyectado sobre una superficie plana (3+ poses, 1 medida por pose)")
    s.add_argument("--camera", default="osmo"); s.add_argument("--cam-size", default="1920x1080"); s.add_argument("--rotate", type=int, default=270); s.add_argument("--downscale", type=int, default=1, help="reducir el cuadro N veces (Osmo: pedir 3840x2160 y --downscale 2)")
    s.add_argument("--name", default="osmo"); s.add_argument("--size", default="1920x1080"); s.add_argument("--screen", default="1920x1080")
    s.add_argument("--poses", type=int, default=3); s.add_argument("--out", default="captures/projected")
    s.add_argument("--rig-out", default="captures/rig.json"); s.add_argument("--html", default="reports/rig.html")
    board_args(s); s.set_defaults(fn=cmd_calib_projected)

    s = sp.add_parser("scan", help="escaneo completo: gray code -> captura -> nube PLY + captura + visor")
    s.add_argument("--camera", default="osmo"); s.add_argument("--cam-size", default="3840x2160"); s.add_argument("--rotate", type=int, default=270); s.add_argument("--downscale", type=int, default=2, help="reducir el cuadro N veces (Osmo: pedir 3840x2160 y --downscale 2)")
    s.add_argument("--name", default="osmo"); s.add_argument("--size", default="1920x1080"); s.add_argument("--screen", default="1920x1080", help="índice o WxH del proyector (nunca el iPad/terminal)")
    s.add_argument("--windowed", action="store_true"); s.add_argument("--settle", type=float, default=1.5)
    s.add_argument("--board-level", type=int, default=160); s.add_argument("--threshold", type=int, default=5)
    s.add_argument("--skip-bits", type=int, default=0, help="ignora los N bits más finos (cámara con pocos px por px de proyector)")
    s.add_argument("--no-capture", action="store_true", help="solo re-decodifica lo ya capturado en --out")
    s.add_argument("--rig", help="captures/rig.json (sin rig: solo mapas)"); s.add_argument("--zmax", type=float, default=5000)
    s.add_argument("--out", default="captures/scan1"); s.add_argument("--name-out", default="scan1")
    s.set_defaults(fn=cmd_scan)

    s = sp.add_parser("grab", help="guarda un cuadro de una cámara")
    s.add_argument("--camera", default="0", help="índice o 'osmo'"); s.add_argument("--cam-size"); s.add_argument("--flush", type=int, default=10)
    s.add_argument("--rotate", type=int, default=0, help="90/180/270 (la Osmo vertical: 90 o 270)"); s.add_argument("--downscale", type=int, default=1, help="reducir el cuadro N veces (Osmo: pedir 3840x2160 y --downscale 2)")
    s.add_argument("--out", default="reports/grab.png")
    s.set_defaults(fn=cmd_grab)

    s = sp.add_parser("board", help="genera la imagen del tablero ChArUco para imprimir o mostrar")
    s.add_argument("--out", default="board.png"); s.add_argument("--ppmm", type=float, default=8.0)
    s.add_argument("--margin", type=float, default=10.0)
    board_args(s); s.set_defaults(fn=cmd_board)

    s = sp.add_parser("capture-scan", help="captura una secuencia gray code con todas las cámaras")
    s.add_argument("--cameras", default="0"); s.add_argument("--names"); s.add_argument("--cam-size")
    s.add_argument("--size", default="1920x1080", help="resolución del proyector")
    s.add_argument("--screen", default="1920x1080", help="índice o WxH del proyector (nunca el iPad/terminal)"); s.add_argument("--windowed", action="store_true")
    s.add_argument("--settle", type=float, default=0.4, help="segundos entre patrón y captura (ver latency)")
    s.add_argument("--rotate", type=int, default=0); s.add_argument("--downscale", type=int, default=1, help="reducir el cuadro N veces (Osmo: pedir 3840x2160 y --downscale 2)")
    s.add_argument("--board", action="store_true", help="captura además una imagen del tablero con luz plana")
    s.add_argument("--board-level", type=int, default=180); s.add_argument("--force", action="store_true")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_capture_scan)

    s = sp.add_parser("display", help="muestra patrones en un display (prueba del proyector)")
    s.add_argument("--size", default="1920x1080"); s.add_argument("--screen", default="1920x1080", help="índice o WxH del proyector (nunca el iPad/terminal)")
    s.add_argument("--windowed", action="store_true"); s.add_argument("--test", action="store_true")
    s.add_argument("--dt", type=float, default=0.5)
    s.set_defaults(fn=cmd_display)

    s = sp.add_parser("latency", help="mide latencia display->cámara con parpadeos")
    s.add_argument("--camera", default="0", help="índice o 'osmo'"); s.add_argument("--cam-size")
    s.add_argument("--size", default="1920x1080"); s.add_argument("--screen", default="1920x1080", help="índice o WxH del proyector (nunca el iPad/terminal)")
    s.add_argument("--windowed", action="store_true"); s.add_argument("--trials", type=int, default=10)
    s.add_argument("--out")
    s.set_defaults(fn=cmd_latency)

    s = sp.add_parser("capture-calib", help="captura guiada del tablero con una o varias cámaras")
    s.add_argument("--cameras", default="0", help="índices separados por coma"); s.add_argument("--names")
    s.add_argument("--cam-size"); s.add_argument("--out", required=True); s.add_argument("--views", type=int, default=25)
    s.add_argument("--rotate", type=int, default=0); s.add_argument("--downscale", type=int, default=1, help="reducir el cuadro N veces (Osmo: pedir 3840x2160 y --downscale 2)")
    board_args(s); s.set_defaults(fn=cmd_capture_calib)

    s = sp.add_parser("show-board", help="muestra el tablero ChArUco a pantalla completa en un display (iPad por Sidecar)")
    s.add_argument("--screen", required=True, help="índice o WxH de un monitor que NO sea el iPad/terminal"); s.add_argument("--ppmm", type=float, default=4.0); s.add_argument("--margin", type=float, default=10.0)
    board_args(s); s.set_defaults(fn=cmd_show_board)

    s = sp.add_parser("calib-procam", help="calibración guiada proyector-cámara: tablero + gray code por pose")
    s.add_argument("--camera", default="0", help="índice o 'osmo'"); s.add_argument("--cam-size"); s.add_argument("--rotate", type=int, default=0); s.add_argument("--downscale", type=int, default=1, help="reducir el cuadro N veces (Osmo: pedir 3840x2160 y --downscale 2)")
    s.add_argument("--name", default="osmo"); s.add_argument("--size", default="1920x1080"); s.add_argument("--screen", default="1920x1080", help="índice o WxH del proyector (nunca el iPad/terminal)")
    s.add_argument("--windowed", action="store_true"); s.add_argument("--poses", type=int, default=10)
    s.add_argument("--settle", type=float, default=0.5); s.add_argument("--board-level", type=int, default=160)
    s.add_argument("--out", default="captures/procam")
    board_args(s); s.set_defaults(fn=cmd_calib_procam)

    s = sp.add_parser("calib-intrinsics", help="intrínsecos de una cámara desde imágenes del tablero")
    s.add_argument("images"); s.add_argument("--glob", default="*.png"); s.add_argument("--name", default="cam0")
    s.add_argument("--out", required=True); s.add_argument("--png"); s.add_argument("--rational", action="store_true")
    board_args(s); s.set_defaults(fn=cmd_calib_intrinsics)

    s = sp.add_parser("calib-rig", help="extrínsecos de todas las cámaras y el proyector -> rig.json")
    s.add_argument("captures", help="carpeta con pose_NNNN/<cam>/ (tablero + gray code)")
    s.add_argument("--intrinsics", help="carpeta con <cam>.json de calib-intrinsics")
    s.add_argument("--proj-size"); s.add_argument("--out", required=True); s.add_argument("--html")
    board_args(s); s.set_defaults(fn=cmd_calib_rig)
    s = sp.add_parser("remote", help="PC Windows por SSH: ping, doctor, sync, run")
    s.add_argument("action", choices=["ping", "doctor", "sync", "run"]); s.add_argument("command", nargs="?")
    s.add_argument("--host", required=True, help="nombre o IP en la tailnet"); s.add_argument("--user")
    s.add_argument("--root", default="C:/sl-scanner"); s.add_argument("--out")
    s.set_defaults(fn=cmd_remote)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


# ----------------------------------------------------------------------------- métrico (hoja carta)
# `slscan metric paper|capture|calib|mesh`: escala con una hoja carta, calibración con la hoja + el ChArUco
# proyectado, y malla métrica de un escaneo. Se registra envolviendo build_parser (abajo), sin tocar lo de arriba.

def _metric_size(s: str) -> tuple:
    a, b = s.lower().split("x")
    return (int(a), int(b))


def _metric_paper_mm(s: str) -> tuple[float, float]:
    a, b = s.lower().split("x")
    return (float(a), float(b))


def _metric_paper_png(img, plane, out, margin: int = 40, zoom: int = 4):
    """Recorte de la foto alrededor de la hoja, ampliado, con el cuadrilátero subpíxel y el número de cada esquina."""
    q = plane.quad_cam
    x0, y0 = np.maximum(np.floor(q.min(axis=0)).astype(int) - margin, 0)
    x1, y1 = np.ceil(q.max(axis=0)).astype(int) + margin
    crop = np.ascontiguousarray(img[y0:y1, x0:x1])
    vis = cv2.resize(crop, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_NEAREST)
    qq = (q - [x0, y0] + 0.5) * zoom - 0.5
    cv2.polylines(vis, [np.round(qq * 4).astype(np.int32).reshape(-1, 1, 2)], True, (0, 0, 255), 1, cv2.LINE_AA, shift=2)
    for i, (x, y) in enumerate(qq):
        cv2.putText(vis, str(i), (int(x) + 4, int(y) - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2, cv2.LINE_AA)
    txt = f"{plane.mm_per_px_cam:.2f} mm/px cam"
    if plane.mm_per_px_proj:
        txt += f"  {plane.mm_per_px_proj:.2f} mm/px proy"
    cv2.putText(vis, txt, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), vis)


def cmd_metric_paper(args):
    """Detecta la hoja carta en una foto y reporta la escala (mm/px) en la cámara y, con el mapa, en el proyector."""
    from . import metric
    from .graycode import Decoded
    img = cv2.imread(args.image)
    if img is None:
        _p(f"no pude leer {args.image}"); return 1
    size_mm = _metric_paper_mm(args.paper_mm)
    roi = tuple(int(v) for v in args.roi.split(",")) if args.roi else None
    quad, info = metric.detect_paper(img, aspect=max(size_mm) / min(size_mm), roi=roi, return_info=True)
    if quad is None:
        _p("no encontré la hoja (blanca, 4 lados, proporción carta). Acota con --roi x,y,w,h."); return 1
    dec = Decoded.load(args.map) if args.map else None
    plane = metric.paper_scale(quad, dec, size_mm)
    sc = metric._sides(plane.quad_cam)
    _p(f"hoja en la cámara ({args.image}): esquinas {np.round(plane.quad_cam, 1).tolist()}")
    _p(f"  lados {np.round(sc, 1).tolist()} px (0-1 = lado corto de {size_mm[0]} mm) -> {plane.mm_per_px_cam:.3f} mm por px "
       f"de cámara en su centro")
    if plane.quad_proj is not None:
        sp_ = metric._sides(plane.quad_proj)
        f = plane.fit
        _p(f"  en el proyector: esquinas {np.round(plane.quad_proj, 1).tolist()}")
        _p(f"  lados {np.round(sp_, 1).tolist()} px -> {plane.mm_per_px_proj:.3f} mm por px de proyector "
           f"({plane.proj_per_cam_px():.2f} px de proyector por px de cámara); ajuste {f.get('source')}: "
           f"{f.get('inliers')} px, rms {f.get('rms_proj_px', float('nan')):.2f} px (bloque {f.get('block_px', 1)})")
    elif args.map:
        _p("  el mapa no tiene suficientes píxeles decodificados dentro de la hoja")
    if args.json:
        d = plane.to_json(); d["detect"] = info; d["image"] = args.image; d["map"] = args.map
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(d, indent=1)); _p(f"  -> {args.json}")
    if args.png:
        _metric_paper_png(img, plane, args.png); _p(f"  -> {args.png}")
    return 0


def cmd_metric_capture(args):
    """Captura guiada para `metric calib`: por pose, foto con el ChArUco proyectado (board.png), con luz blanca
    (white.png, para ver la hoja) y la secuencia gray code (-> map.npz). Sin hardware no está probado."""
    if not args.legacy:
        from .metric_session import capture_command
        return capture_command(args)
    from .capture import Camera, PatternWindow, capture_sequence
    from .calib import CharucoBoard
    from . import graycode, metric
    size = _metric_size(args.size)
    board = CharucoBoard.from_args(args)
    canvas, corners_px = metric.projected_charuco(board, size, args.margin)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "projected_board.png"), canvas)
    (out / "projected_board.json").write_text(json.dumps(
        {"board": args.board, "dict": args.dict, "margin_px": args.margin, "proj_size": list(size),
         "corners_px": corners_px.tolist()}, indent=1))
    win = PatternWindow(size, screen=args.screen, fullscreen=not args.windowed)
    cam = Camera(args.camera, size=args.cam_size, rotate=args.rotate, downscale=args.downscale, name=args.name)
    k = len([p for p in out.glob("pose_*") if p.is_dir()])
    try:
        while k < args.poses:
            win.show(canvas)
            r = input(f"Pose {k + 1}/{args.poses}: cartulina con la hoja dentro de la proyección, inclinada distinto "
                      f"que antes, y quieta. ENTER = capturar, q = terminar: ")
            if r.strip().lower() == "q":
                break
            folder = out / f"pose_{k:02d}" / cam.name
            folder.mkdir(parents=True, exist_ok=True)
            time.sleep(args.settle); cam.flush(args.flush); cv2.imwrite(str(folder / "board.png"), cam.read())
            win.solid(args.white_level); time.sleep(args.settle); cam.flush(args.flush)
            cv2.imwrite(str(folder / "white.png"), cam.read())
            specs = graycode.sequence(size)
            _p(f"  gray code: {len(specs)} patrones, no toques nada (~{len(specs) * args.settle:.0f} s)")
            capture_sequence(win, [cam], specs, lambda s_: graycode.pattern(size, s_.axis, s_.bit, s_.inverse),
                             folder.parent, settle_s=args.settle)
            items = graycode.load_sequence(folder)
            dec = graycode.decode(items, threshold=args.threshold, skip_bits=args.skip_bits)
            dec.save(folder / "map.npz")
            if not args.keep_images:                      # el Mac anda corto de disco: queda solo el map.npz
                for _, f in items:
                    Path(f).unlink(missing_ok=True)
            try:
                pose = metric.load_pose(folder, board, corners_px, _metric_paper_mm(args.paper_mm),
                                        skip_bits=args.skip_bits, name=folder.parent.name)
                n_ch = 0 if pose.charuco is None else len(pose.charuco["cam"])
                _p(f"  pose {k}: hoja {np.round(metric._sides(pose.plane.quad_cam), 0).tolist()} px, "
                   f"ChArUco {n_ch} esquinas en el plano, {dec.stats['valid']} px decodificados")
            except ValueError as e:
                _p(f"  pose {k}: {e}. Repite la pose (borra {folder.parent}).")
            k += 1
    finally:
        win.close(); cam.close()
    _p(f"{k} poses en {out}. Siguiente: slscan metric calib {out} --proj-size {args.size} --name {args.name}")
    return 0


def cmd_metric_calib(args):
    """Calibración cámara-proyector con la hoja carta (+ ChArUco proyectado) en 3+ poses -> rig.json + reporte."""
    if args.solver == 'joint':
        from .metric_session import calibrate_command
        return calibrate_command(args)
    from . import metric
    from .calib import CharucoBoard
    from .recon import write_ply
    from .viewer import write_html
    folder = Path(args.folder)
    proj_size = _metric_size(args.proj_size)
    size_mm = _metric_paper_mm(args.paper_mm)
    board = CharucoBoard.from_args(args)
    bj = folder / "projected_board.json"
    if bj.exists():
        meta = json.loads(bj.read_text())
        corners_px = np.array(meta["corners_px"], np.float64)
        if tuple(meta.get("proj_size", proj_size)) != tuple(proj_size):
            _p(f"ojo: {bj.name} dice proyector {meta['proj_size']}, uso ese"); proj_size = tuple(meta["proj_size"])
    else:
        _, corners_px = metric.projected_charuco(board, proj_size, args.margin)
    poses = []
    cam_size = None
    for pdir in sorted(p for p in folder.glob("pose_*") if p.is_dir()):
        sub = pdir / args.name if (pdir / args.name).is_dir() else pdir
        try:
            pose = metric.load_pose(sub, board, corners_px, size_mm, skip_bits=args.skip_bits, name=pdir.name)
        except ValueError as e:
            _p(f"  {pdir.name}: se salta ({e})"); continue
        for f in ("white.png", "board.png", "color.png"):
            im = cv2.imread(str(sub / f)) if (sub / f).exists() else None
            if im is not None:
                cam_size = (im.shape[1], im.shape[0]); break
        ch = pose.charuco
        n_ch = 0 if ch is None else len(ch["cam"])
        rej = "" if ch is None else f" ({ch.get('rejected', 0)} fuera del plano o mal detectadas)"
        _p(f"  {pdir.name}: hoja {np.round(metric._sides(pose.plane.quad_cam), 1).tolist()} px, "
           f"{pose.plane.mm_per_px_cam:.2f} mm/px cám, ChArUco {n_ch} esquinas{rej}")
        poses.append(pose)
    if len(poses) < 3 or cam_size is None:
        _p(f"se necesitan >= 3 poses válidas (hay {len(poses)}) en {folder}"); return 1
    cam_pp = tuple(float(v) for v in args.cam_pp.split(",")) if args.cam_pp else None
    proj_pp = tuple(float(v) for v in args.proj_pp.split(",")) if args.proj_pp else None
    rig = metric.calibrate_from_paper(poses, cam_size, proj_size, cam_name=args.name, use_charuco=not args.no_charuco,
                                      cam_pp=cam_pp, proj_pp=proj_pp, repeat=args.repeat, log=_p)
    rep = rig.extra["metric_report"]
    _p(f"método: {rep['method']}; rms cámara {rep['rms_px']['cam']:.3f} px, proyector {rep['rms_px']['proj']:.3f} px, "
       f"estéreo {rep['rms_px']['stereo']:.3f} px")
    for r in rep["paper_check"]:
        pl = f", ChArUco plano a {r['charuco_planarity_mm']} mm" if "charuco_planarity_mm" in r else ""
        _p(f"  {r['pose']}: hoja medida {r['sides_mm']} mm (error {r['err_mm']}){pl}, a {r['dist_mm']} mm")
    rp = rep["repeatability"]
    if "leave_one_out_max" in rp:
        m = rp["leave_one_out_max"]
        _p(f"  repetibilidad (dejando una pose fuera, peor caso): proyector {m['proj_pos_mm']:.1f} mm / "
           f"{m['proj_rot_deg']:.2f}°, f cámara {m['cam_f_pct']:.2f} %, f proyector {m['proj_f_pct']:.2f} %")
    if "noise_std" in rp:
        m = rp["noise_std"]
        _p(f"  repetibilidad (ruido del rms, {rp['noise_runs']} corridas): proyector {m['proj_pos_mm']:.1f} mm / "
           f"{m['proj_rot_deg']:.2f}°, f cámara {m['cam_f_pct']:.2f} %, f proyector {m['proj_f_pct']:.2f} %")
    if "warning" in rep:
        _p(f"  ojo: {rep['warning']}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    rig.save(args.out)
    _p(f"rig guardado en {args.out}")
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(rep, indent=1)); _p(f"reporte: {args.report}")
    if args.html:
        pts, cols = [], []
        palette = [(0, 0, 255), (0, 200, 0), (255, 120, 0), (0, 200, 255), (255, 0, 255), (255, 255, 0)]
        for i, p in enumerate(poses):
            _, c, q = p.points(not args.no_charuco)
            X = metric._triangulate(rig, args.name, c, q)
            pts.append(X); cols.append(np.tile(palette[i % len(palette)], (len(X), 1)))
        ply = Path(args.html).with_name(Path(args.html).stem + "_puntos.ply")
        write_ply(ply, np.concatenate(pts), np.concatenate(cols), comment="puntos de calibracion (hoja + ChArUco)")
        write_html(args.html, [("puntos de calibración", ply)], rig, title="Rig calibrado con hoja carta",
                   point_size=6.0)
        _p(f"visor: {args.html}")
    return 0


def _metric_sample(img, pts):
    """Color BGR de la imagen en px de cámara (bilineal, en numpy: cv2.remap no acepta mapas de > 32767 px)."""
    im = np.asarray(img, np.float32)
    h, w = im.shape[:2]
    p = np.asarray(pts, np.float64).reshape(-1, 2)
    x, y = np.clip(p[:, 0], 0, w - 1.001), np.clip(p[:, 1], 0, h - 1.001)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    return (im[y0, x0] * (1 - fx) * (1 - fy) + im[y0, x0 + 1] * fx * (1 - fy)
            + im[y0 + 1, x0] * (1 - fx) * fy + im[y0 + 1, x0 + 1] * fx * fy)


def cmd_metric_mesh(args):
    """Escaneo (+ rig) -> malla PLY con caras + PNG (+ visor HTML). Sin rig: malla relativa (no métrica)."""
    from . import metric
    from .graycode import Decoded
    from .recon import write_ply
    from .rig import Rig
    from .viewer import write_html
    scan = Path(args.scan)
    camdir = scan / args.name
    dec = Decoded.load(camdir / "map.npz") if (camdir / "map.npz").exists() else None
    color = cv2.imread(str(camdir / "color.png")) if (camdir / "color.png").exists() else None
    p2c = np.load(scan / "proj_to_cam.npz") if (scan / "proj_to_cam.npz").exists() else None
    hp = np.load(scan / "height_proj.npy") if (scan / "height_proj.npy").exists() else None
    out = Path(args.out) if args.out else scan / "mesh.ply"
    png = Path(args.png) if args.png else Path("reports") / f"{scan.name}_mesh.png"
    out.parent.mkdir(parents=True, exist_ok=True); png.parent.mkdir(parents=True, exist_ok=True)
    info = {"scan": str(scan)}
    if args.rig:
        rig = Rig.load(args.rig)
        use_p2c = p2c if args.proj_to_cam else None
        if dec is None and use_p2c is None:
            _p(f"sin {camdir / 'map.npz'} (ni --proj-to-cam): no hay correspondencias"); return 1
        step = args.step or (metric.block_px(dec) if dec is not None else 4)
        res = metric.relative_to_metric(hp, rig, proj_to_cam=use_p2c, dec=dec,
                                        cam_name=args.name if args.name in rig.devices else None, step=step)
        xyz, cam_px, pro = res["xyz"], res["cam"], res["pro"]
        keep = (xyz[:, 2] > 0) & (xyz[:, 2] < args.zmax)
        src = color if color is not None else (dec.color if dec is not None else None)
        col = None if src is None else _metric_sample(src, cam_px)[keep]
        mesh = metric.cloud_to_mesh(xyz[keep], col, pro_xy=pro[keep], method=args.method)
        hm = res["height_mm"]
        info.update({"metric": True, "points": int(keep.sum()), "plane_n": res["plane"][0].tolist(),
                     "plane_d": res["plane"][1], "height_mm_p99": float(np.nanpercentile(hm, 99)),
                     "mm_per_unit": res.get("mm_per_unit"), "relative_corr": res.get("relative_corr")})
        title = f"{scan.name} · malla métrica ({mesh.method}) · {len(mesh.vertices)} vértices, {len(mesh.faces)} caras"
        _p(f"nube métrica: {keep.sum()} puntos; plano dominante a {abs(res['plane'][1]):.0f} mm de la cámara; "
           f"altura p99 {info['height_mm_p99']:.1f} mm" + (f"; 1 unidad relativa = {res['mm_per_unit']:.3f} mm"
                                                          if res.get("mm_per_unit") else ""))
    else:
        if hp is None:
            _p(f"sin --rig hace falta {scan / 'height_proj.npy'} (playground.segment_pov) para la malla relativa"); return 1
        h = hp.astype(np.float64).copy()
        if p2c is not None and "valid" in p2c.files:
            h[~p2c["valid"]] = np.nan
        pov = cv2.imread(str(scan / "scene_pov.png")) if (scan / "scene_pov.png").exists() else None
        mesh = metric.relative_mesh(h, pov, step=args.step or 4, z_scale=args.z_scale)
        info.update({"metric": False, "z_scale": args.z_scale})
        title = f"{scan.name} · malla RELATIVA (sin rig: x,y px de proyector; z altura relativa x{args.z_scale:g})"
        _p("sin --rig: malla relativa (x, y en px de proyector; z = altura relativa por paralaje). No es métrica.")
    metric.write_mesh_ply(out, mesh, comment=f"slscan metric mesh {scan.name}")
    metric.render_mesh_png(mesh, png, title=title)
    info.update({"ply": str(out), "png": str(png), "vertices": int(len(mesh.vertices)), "faces": int(len(mesh.faces)),
                 "method": mesh.method})
    _p(f"malla: {len(mesh.vertices)} vértices, {len(mesh.faces)} caras ({mesh.method}) -> {out}\ncaptura: {png}")
    if args.html:
        n = len(mesh.vertices)
        sel = np.arange(0, n, max(1, n // args.html_points))
        prev = Path(args.html).with_name(Path(args.html).stem + "_puntos.ply")      # no pisar la malla
        write_ply(prev, mesh.vertices[sel], None if mesh.colors is None else mesh.colors[sel],
                  comment="vista previa de la malla")
        write_html(args.html, [(scan.name, prev)], Rig.load(args.rig) if args.rig else None, title=title)
        info["html"] = args.html
        _p(f"visor: {args.html}")
    Path(out).with_suffix(".json").write_text(json.dumps(info, indent=1, default=float))
    return 0


def _add_metric_parser(sp) -> None:
    def board_args(s):
        s.add_argument("--board", default="7x5", help="ChArUco proyectado: casillas ancho x alto")
        s.add_argument("--square", type=float, default=40.0, help="(no se usa para el proyectado; se deja por compatibilidad)")
        s.add_argument("--marker", type=float, default=30.0)
        s.add_argument("--dict", default="DICT_5X5_100")
        s.add_argument("--margin", type=int, default=40, help="margen en px del ChArUco proyectado (como calib-projected)")

    m = sp.add_parser("metric", help="escala métrica con una hoja carta: paper, capture, calib, mesh")
    ms = m.add_subparsers(dest="metric_cmd", required=True)

    s = ms.add_parser("paper", help="detecta la hoja carta en una foto y reporta mm/px (cámara y, con --map, proyector)")
    s.add_argument("image", help="foto con luz blanca (p.ej. captures/scan2/osmo/color.png)")
    s.add_argument("--map", help="map.npz del gray code de esa misma vista (lleva la hoja al proyector)")
    s.add_argument("--paper-mm", default="215.9x279.4", help="lado corto x lado largo en mm (carta)")
    s.add_argument("--roi", help="x,y,w,h donde buscar la hoja"); s.add_argument("--json"); s.add_argument("--png")
    s.set_defaults(fn=cmd_metric_paper)

    s = ms.add_parser("capture", help="captura guiada de poses (ChArUco proyectado + blanco + gray code) para metric calib")
    s.add_argument("--camera", default="osmo"); s.add_argument("--cam-size", default="3840x2160")
    s.add_argument("--downscale", type=int, default=2)
    s.add_argument("--rotate", type=int, default=270); s.add_argument("--name", default="osmo")
    s.add_argument("--size", default="1920x1080", help="resolución del proyector")
    s.add_argument("--screen", default="1920x1080", help="índice o WxH del proyector (nunca el iPad/terminal)")
    s.add_argument("--windowed", action="store_true"); s.add_argument("--poses", type=int, default=10)
    s.add_argument("--holdout", type=int, default=3, help="poses reservadas antes del ajuste")
    s.add_argument("--display-confirmed", action="store_true", help="turno libre, display 1:1 y óptica fija confirmados")
    s.add_argument("--uncertainty-mm", type=float, help="cota de incertidumbre de la referencia; no inventar")
    s.add_argument("--legacy", action="store_true", help="captura histórica, no acredita rig conjunto")
    s.add_argument("--viewer-state", help="carpeta compartida con metric viewer para ver la captura en vivo")
    s.add_argument("--settle", type=float, default=1.0); s.add_argument("--flush", type=int, default=5)
    s.add_argument("--white-level", type=int, default=255); s.add_argument("--threshold", type=int, default=5)
    s.add_argument("--skip-bits", type=int, default=2); s.add_argument("--keep-images", action="store_true")
    s.add_argument("--paper-mm", default="210x297"); s.add_argument("--out", default="reports/p2-calibration")
    board_args(s); s.set_defaults(fn=cmd_metric_capture)

    s = ms.add_parser("calib", help="carpeta con pose_NN/<cam>/{board.png, map.npz, white.png} -> rig.json + reporte")
    s.add_argument("folder"); s.add_argument("--name", default="osmo", help="nombre de la cámara (subcarpeta)")
    s.add_argument("--proj-size", default="1920x1080"); s.add_argument("--paper-mm", default="215.9x279.4")
    s.add_argument("--skip-bits", type=int, default=2, help="si hay que decodificar imágenes gray code")
    s.add_argument("--no-charuco", action="store_true", help="solo las 4 esquinas de la hoja por pose")
    s.add_argument("--cam-pp", help="cx,cy fijo de la cámara (solo hoja)"); s.add_argument("--proj-pp", help="cx,cy fijo del proyector (solo hoja)")
    s.add_argument("--solver", choices=["joint","legacy"], default="joint")
    s.add_argument("--initial-rig", help="inicialización opcional compatible, nunca se fija en el ajuste")
    s.add_argument("--distortion", choices=["brown5","k1"], default="brown5")
    s.add_argument("--restarts", type=int, default=2)
    s.add_argument("--viewer-state", help="actualizar también el visor local con el ajuste final")
    s.add_argument("--uncertainty-mm", type=float, help="cota total de incertidumbre del control reservado")
    s.add_argument("--repeat", type=int, default=8, help="bootstrap por vista (joint); ruido (legacy)")
    s.add_argument("--out", default="captures/rig.json"); s.add_argument("--report", default="reports/metric_calib.json")
    s.add_argument("--html", default="reports/rig_metric.html")
    board_args(s); s.set_defaults(fn=cmd_metric_calib)

    s = ms.add_parser("viewer", help="visor local del rig, simulación o captura observada; nunca abre hardware")
    s.add_argument("--state", default="reports/p2-viewer")
    s.add_argument("--port", type=int, default=8797)
    s.add_argument("--simulate", action="store_true", help="simular sala 3D y sensor, sin cámara ni display físicos")
    s.add_argument("--scene", action="store_true", help="escena amueblada sin hoja; autocalibración multivista")
    from .calibration_viewer import serve_command
    s.set_defaults(fn=serve_command)

    s = ms.add_parser("mesh", help="escaneo + rig -> malla PLY con caras + PNG (+ HTML); sin rig, malla relativa")
    s.add_argument("scan", help="carpeta del escaneo (p.ej. captures/scan2, con <cam>/map.npz y <cam>/color.png)")
    s.add_argument("--rig"); s.add_argument("--name", default="osmo")
    s.add_argument("--proj-to-cam", action="store_true", help="usar proj_to_cam.npz (interpolado) en vez del mapa crudo")
    s.add_argument("--step", type=int, default=0, help="paso de la grilla en px de proyector (0 = el bloque del mapa)")
    s.add_argument("--method", default="auto", choices=["auto", "grid", "poisson", "delaunay"])
    s.add_argument("--zmax", type=float, default=5000); s.add_argument("--z-scale", type=float, default=1.0)
    s.add_argument("--out"); s.add_argument("--png"); s.add_argument("--html")
    s.add_argument("--html-points", type=int, default=150000)
    s.set_defaults(fn=cmd_metric_mesh)


_build_parser_base = build_parser


def build_parser() -> argparse.ArgumentParser:  # noqa: F811
    """El parser de arriba + `slscan metric` (registrado acá para no tocar los comandos existentes)."""
    p = _build_parser_base()
    sp = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
    _add_metric_parser(sp)
    from .pipeline import add_parser as add_pipeline_parser
    add_pipeline_parser(sp)
    from .objects import add_parser as add_objects_parser
    add_objects_parser(sp)
    from .pbr import add_parser as add_pbr_parser
    add_pbr_parser(sp)
    return p


if __name__ == "__main__":
    sys.exit(main())
