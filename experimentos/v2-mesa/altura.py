"""Corrección de objetos altos para proyectar sin derrame.

La cámara está baja y adelante: un objeto alto se ve «acostado» hacia atrás en la vista normalizada. El proyector está
más alto, así que para él el objeto se acuesta MENOS. Si se proyecta sobre toda la silueta de la cámara, la parte de
atrás cae en la mesa (derrame). Aquí se estima la altura desde cuánto se acuesta la silueta y se devuelve la silueta
que ve el proyector: la base igual, la parte elevada acortada por k.
Centros ópticos aproximados (mm, marco de la mesa): cámara por homografía; proyector estimado (afinar con PROY_K)."""
import numpy as np
import os

CAM = np.array([563.0, 857.0, 785.0]); PROY = np.array([585.0, 1750.0, 2050.0])
AJUSTE = float(os.environ.get('PROY_K', '1.28'))   # medido en la mesa con discos de prueba: k real ≈ 0,85 en la lata   # <1 recorta más (si aún hay derrame), >1 recorta menos


ALTOS = ('lata', 'taza', 'vaso', 'botella')   # solo estas clases se tratan como objetos altos


def corregir(poly, etiqueta=''):
    """poly: Nx2 mm (silueta de cámara). -> (poly_proyector Nx2, altura_mm, k) ; si no es alto, (poly, 0, 1)."""
    p = np.asarray(poly, float); c = p.mean(axis=0); r = c - CAM[:2]; Dc = float(np.linalg.norm(r)); r /= Dc; tg = np.array([-r[1], r[0]])
    import cv2
    t = (p - CAM[:2]) @ r; s = (p - c) @ tg; L = float(t.max() - t.min())
    ancho = float(min(cv2.minAreaRect(p.astype(np.float32))[1]))   # lado corto: el diámetro de la base, sin asas ni inclinación
    inclin = L - ancho                       # cuánto se alarga hacia atrás respecto de su ancho (base ~ redonda/cuadrada)
    if inclin < 22 or ancho < 20 or not any(etiqueta.startswith(a) for a in ALTOS): return p, 0.0, 1.0
    h = inclin * CAM[2] / (Dc + inclin)      # altura que explica ese alargamiento visto desde la cámara
    base = p[np.argmin(t)]; Dp = float(np.linalg.norm(base - PROY[:2]))
    k = ((Dp / (PROY[2] - h)) / (Dc / (CAM[2] - h))) * AJUSTE; k = float(np.clip(k, 0.2, 1.0))
    t0 = t.min(); d = min(ancho, L)           # hasta `d` es base; más allá es superficie elevada
    tn = np.where(t - t0 <= d, t - t0, d + (t - t0 - d) * k)
    out = CAM[:2] + np.outer(t0 + tn, r) + np.outer((p - CAM[:2]) @ tg, tg)
    return out, float(h), k


# ------------------------------------------------------------------ encaje medido con luz estructurada
# fp-parallax.npz (fp_height.py) trae, por cada píxel de cámara decodificado: A = dónde lo ve la cámara (mm del plano)
# y B = a dónde hay que apuntar el proyector para iluminarlo (mm del plano). Sobre un objeto con altura A≠B. Con las
# muestras que caen dentro de la silueta se ajusta una transformación afín cámara→proyector: es el encaje real, sin
# suponer alturas ni posiciones de cámara y proyector. Vale mientras el objeto no se mueva desde el escaneo.
_SL = [None]


def afin_sl(poly, min_n=120):
    """-> matriz 2x3 (cámara mm -> proyector mm) o None si no hay muestras con relieve dentro de la silueta."""
    import cv2
    from pathlib import Path
    f = Path(__file__).parent / 'fp-parallax.npz'
    if _SL[0] is None:
        if not f.exists(): return None
        z = np.load(f); _SL[0] = (z['A'].astype(np.float32), z['B'].astype(np.float32))
    A, B = _SL[0]; p = np.asarray(poly, np.float32); x0, y0 = p.min(0); x1, y1 = p.max(0)
    s = (A[:, 0] >= x0) & (A[:, 0] <= x1) & (A[:, 1] >= y0) & (A[:, 1] <= y1)
    if s.sum() < min_n: return None
    a, b = A[s], B[s]; m = np.zeros((int(y1 - y0) + 2, int(x1 - x0) + 2), np.uint8); cv2.fillPoly(m, [np.round(p - [x0, y0]).astype(np.int32)], 1)
    m = cv2.erode(m, np.ones((5, 5), np.uint8)); d = m[np.clip((a[:, 1] - y0).astype(int), 0, m.shape[0] - 1), np.clip((a[:, 0] - x0).astype(int), 0, m.shape[1] - 1)] > 0
    a, b = a[d], b[d]
    if len(a) < min_n or float(np.median(np.linalg.norm(b - a, axis=1))) < 4: return None      # sin relieve: el objeto ya no está ahí (o es plano)
    if len(a) > 6000: k = np.random.default_rng(0).choice(len(a), 6000, replace=False); a, b = a[k], b[k]
    M, inl = cv2.estimateAffine2D(a, b, method=cv2.RANSAC, ransacReprojThreshold=3.0)
    if M is None or inl.sum() < 0.35 * len(a): return None
    return M


def poli_sl(poly, min_n=150):
    """Encaje cámara→proyector de segundo grado dentro de la silueta: sigue la curvatura del objeto (p. ej. un plátano).
    -> {'c':[cx,cy],'s':escala,'k':[[6],[6]]} o None."""
    import cv2
    if _SL[0] is None and afin_sl(poly) is None and _SL[0] is None: return None
    A, B = _SL[0]; p = np.asarray(poly, np.float32); x0, y0 = p.min(0); x1, y1 = p.max(0)
    s_ = (A[:, 0] >= x0) & (A[:, 0] <= x1) & (A[:, 1] >= y0) & (A[:, 1] <= y1)
    if s_.sum() < min_n: return None
    a, b = A[s_], B[s_]; m = np.zeros((int(y1 - y0) + 2, int(x1 - x0) + 2), np.uint8); cv2.fillPoly(m, [np.round(p - [x0, y0]).astype(np.int32)], 1)
    d = m[np.clip((a[:, 1] - y0).astype(int), 0, m.shape[0] - 1), np.clip((a[:, 0] - x0).astype(int), 0, m.shape[1] - 1)] > 0; a, b = a[d].astype(float), b[d].astype(float)
    if len(a) < min_n or float(np.median(np.linalg.norm(b - a, axis=1))) < 2.5: return None
    c = a.mean(0); sc = float(max(a.std(0).max(), 1.0)); F_ = lambda q: np.stack([np.ones(len(q)), q[:, 0], q[:, 1], q[:, 0] ** 2, q[:, 0] * q[:, 1], q[:, 1] ** 2], 1)
    X = F_((a - c) / sc); ok = np.ones(len(a), bool)
    for _ in range(3):
        k, *_r = np.linalg.lstsq(X[ok], b[ok], rcond=None); res = np.linalg.norm(X @ k - b, axis=1); ok = res < max(2.5, np.percentile(res, 80))
    return {'c': c.round(2).tolist(), 's': round(sc, 3), 'k': np.round(k.T, 4).tolist(), 'n': int(ok.sum()), 'relieve_mm': round(float(np.median(np.linalg.norm(b - a, axis=1))), 1)}


def aplica_poli(pl, x):
    x = (np.asarray(x, float).reshape(-1, 2) - pl['c']) / pl['s']; k = np.array(pl['k'])
    return np.stack([np.ones(len(x)), x[:, 0], x[:, 1], x[:, 0] ** 2, x[:, 0] * x[:, 1], x[:, 1] ** 2], 1) @ k.T

def aplicar_sl(objs):
    """Agrega afin_proy y poligono_proy_mm (medidos) a los objetos altos que tienen relieve en el escaneo."""
    n = 0
    for o in objs:
        if not any(o.get('etiqueta', '').startswith(k) for k in ALTOS + ('mouse', 'plátano')): continue
        M = afin_sl(o['poligono_mm'])
        if o.get('etiqueta', '').startswith('plátano'):
            pl = poli_sl(o['poligono_mm'])
            if pl: o['poli_proy'] = pl; o['poligono_proy_mm'] = np.round(aplica_poli(pl, o['poligono_mm']), 1).tolist(); n += 1; continue
        if M is None: continue
        p = np.asarray(o['poligono_mm'], float); o['afin_proy'] = np.round(M, 5).tolist(); o['poligono_proy_mm'] = np.round(p @ M[:, :2].T + M[:, 2], 1).tolist(); n += 1
    return n


if __name__ == '__main__':
    import json
    objs = json.load(open('out/objetos.json')); print('encaje medido en', aplicar_sl(objs), 'objetos')
    for o in objs:
        if 'afin_proy' in o: print(' ', o['etiqueta'], np.round(o['afin_proy'], 3).tolist())
    json.dump(objs, open('out/objetos.json', 'w'), ensure_ascii=False)
