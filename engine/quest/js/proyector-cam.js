// Cámara del proyector: intrínsecos K (px del proyector nativo) + extrínsecos T_rig→proyector.
// Modo 'cabeza': rig = casco (pose del proyector = pose del casco × T_cabeza→proyector).
// Modo 'fijo': rig = sala (marco de la malla de Space Setup; pose constante en la sala).
import * as THREE from 'three';
import * as C from './calib.js';

const CV2GL = new THREE.Matrix4().makeScale(1, -1, -1); // ejes OpenCV (x der, y abajo, z adelante) → three (y arriba, −z adelante)

/** Matriz de proyección three.js desde K (convención OpenCV: centro del píxel 0 en u = 0). */
export function proyeccionDesdeK(K, w, h, near, far, out = new THREE.Matrix4()) {
  const fx = K[0][0], s = K[0][1], cx = K[0][2], fy = K[1][1], cy = K[1][2];
  out.set(
    (2 * fx) / w, (-2 * s) / w, 1 - (2 * (cx + 0.5)) / w, 0,
    0, (2 * fy) / h, (2 * (cy + 0.5)) / h - 1, 0,
    0, 0, -(far + near) / (far - near), (-2 * far * near) / (far - near),
    0, 0, -1, 0,
  );
  return out;
}

/** 4x4 row-major (arrays) → THREE.Matrix4 y vuelta. */
export const m4 = (A) => new THREE.Matrix4().set(...A.flat());
export const arr4 = (M) => { const e = M.elements; return [0, 1, 2, 3].map((r) => [0, 1, 2, 3].map((c) => e[c * 4 + r])); };

/** Extrínsecos por defecto (antes de calibrar). */
export function extrinsecosPorDefecto(modo) {
  // pose del proyector en el rig (ejes OpenCV) → R,t (rig → cam)
  let centro, pitch;
  if (modo === 'cabeza') { centro = [0, 0.08, -0.05]; pitch = 10; } // 8 cm sobre los ojos, mirando al frente
  else { centro = [0, 1.2, 0]; pitch = 15; } // trípode a 1.2 m en el origen de la sala, mirando a −z
  const a = (pitch * Math.PI) / 180;
  const xc = [1, 0, 0], yc = [0, -Math.cos(a), Math.sin(a)], zc = [0, -Math.sin(a), -Math.cos(a)];
  const R = [xc, yc, zc]; // filas = ejes de la cámara en el rig → R(rig→cam)
  const t = C.mulv(R, centro).map((x) => -x);
  return { R, t };
}

export function kPorDefecto(w, h, throwRatio = 1.2) {
  const f = throwRatio * w;
  return [[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]];
}

export class ProyectorCam {
  constructor({ w = 1920, h = 1080, modo = 'cabeza', throwRatio = 1.2 } = {}) {
    this.w = w; this.h = h; this.modo = modo; this.throwRatio = throwRatio;
    this.near = 0.05; this.far = 30;
    this.camara = new THREE.PerspectiveCamera();
    this.camara.matrixAutoUpdate = false;
    this.camara.matrixWorldAutoUpdate = false;
    this.T_rig_cam = new THREE.Matrix4(); // pose del proyector en el rig (ejes OpenCV), con ajuste
    this.T_world_cam = new THREE.Matrix4();
    this.restablecer();
  }

  restablecer() {
    const { R, t } = extrinsecosPorDefecto(this.modo);
    this.base = { K: kPorDefecto(this.w, this.h, this.throwRatio), R, t };
    this.info = null; // { rms, metodo, n, fecha }
    this.calibrado = false;
    this.ajuste = ProyectorCam.ajusteCero();
    this.recalcular();
  }

  static ajusteCero() { return { yaw: 0, pitch: 0, roll: 0, dx: 0, dy: 0, dz: 0, zoom: 1, dcx: 0, dcy: 0 }; }

  /** K efectiva (base + ajuste manual). */
  get K() {
    const K = this.base.K.map((r) => r.slice());
    const a = this.ajuste;
    K[0][0] *= a.zoom; K[1][1] *= a.zoom; K[0][1] *= a.zoom;
    K[0][2] += a.dcx; K[1][2] += a.dcy;
    return K;
  }
  /** R,t efectivos (rig → cam), con el ajuste aplicado en los ejes del propio proyector. */
  get Rt() {
    const M = new THREE.Matrix4().copy(this.T_rig_cam).invert();
    const A = arr4(M);
    return { R: A.slice(0, 3).map((r) => r.slice(0, 3)), t: A.slice(0, 3).map((r) => r[3]) };
  }

  recalcular() {
    const base = m4(C.poseEnRig(this.base.R, this.base.t));
    const a = this.ajuste, g = Math.PI / 180;
    const d = new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(a.pitch * g, a.yaw * g, a.roll * g, 'XYZ'));
    d.setPosition(a.dx, a.dy, a.dz);
    this.T_rig_cam.multiplyMatrices(base, d);
    proyeccionDesdeK(this.K, this.w, this.h, this.near, this.far, this.camara.projectionMatrix);
    this.camara.projectionMatrixInverse.copy(this.camara.projectionMatrix).invert();
  }

  /** Coloca la cámara three.js: T_world_rig = pose del casco (cabeza) o de la sala (fijo). */
  actualizar(T_world_rig) {
    this.T_world_cam.multiplyMatrices(T_world_rig, this.T_rig_cam);
    this.camara.matrixWorld.multiplyMatrices(this.T_world_cam, CV2GL);
    this.camara.matrixWorldInverse.copy(this.camara.matrixWorld).invert();
    this.camara.matrixWorld.decompose(this.camara.position, this.camara.quaternion, this.camara.scale);
  }

  /** Punto del mundo → píxel del proyector (u, v, z) con la pose actual (para overlays y verificación). */
  proyectarMundo(p) {
    const inv = new THREE.Matrix4().copy(this.T_world_cam).invert();
    const x = p.clone().applyMatrix4(inv);
    const K = this.K;
    return { u: (K[0][0] * x.x + K[0][1] * x.y) / x.z + K[0][2], v: (K[1][1] * x.y) / x.z + K[1][2], z: x.z };
  }

  /** Rayo del mundo por el píxel (u, v). */
  rayo(u, v) {
    const Kinv = C.inv3(this.K);
    const d = C.mulv(Kinv, [u, v, 1]);
    const o = new THREE.Vector3().setFromMatrixPosition(this.T_world_cam);
    const dir = new THREE.Vector3(d[0], d[1], d[2]).transformDirection(this.T_world_cam);
    return new THREE.Ray(o, dir);
  }

  aplicarCalibracion(res, extra = {}) {
    this.base = { K: res.K.map((r) => r.slice()), R: res.R.map((r) => r.slice()), t: res.t.slice() };
    this.info = { rms: res.rms, metodo: res.metodo, n: res.n, fecha: new Date().toISOString(), ...extra };
    this.calibrado = true;
    this.ajuste = ProyectorCam.ajusteCero();
    this.recalcular();
  }

  /** Ajuste manual fino (estilo superficie_manual: a mano, verificando a ojo). */
  ajustar(campo, delta) {
    if (campo === 'zoom') this.ajuste.zoom *= 1 + delta;
    else this.ajuste[campo] += delta;
    this.recalcular();
  }

  /** Hornea el ajuste manual en la base (para exportar una calibración limpia). */
  hornearAjuste() {
    const { R, t } = this.Rt;
    this.base = { K: this.K, R, t };
    this.ajuste = ProyectorCam.ajusteCero();
    this.recalcular();
  }

  toJSON() {
    const { R, t } = this.Rt;
    return {
      formato: 'smartup-quest-proyector/1', modo: this.modo, w: this.w, h: this.h,
      K: this.K, R, t, poseEnRig: arr4(this.T_rig_cam),
      base: this.base, ajuste: this.ajuste, info: this.info, calibrado: this.calibrado,
    };
  }

  fromJSON(j) {
    if (!j || !j.K || !j.R || !j.t) throw new Error('calibración inválida');
    if (j.w && j.h && (j.w !== this.w || j.h !== this.h)) {
      // re-escala a la resolución nominal actual
      const sx = this.w / j.w, sy = this.h / j.h;
      const K = (j.base?.K || j.K).map((r) => r.slice());
      K[0][0] *= sx; K[0][1] *= sx; K[0][2] = (K[0][2] + 0.5) * sx - 0.5;
      K[1][1] *= sy; K[1][2] = (K[1][2] + 0.5) * sy - 0.5;
      j = { ...j, base: { ...(j.base || j), K } };
    }
    if (j.modo) this.modo = j.modo;
    const b = j.base || { K: j.K, R: j.R, t: j.t };
    this.base = { K: b.K.map((r) => r.slice()), R: b.R.map((r) => r.slice()), t: b.t.slice() };
    this.ajuste = { ...ProyectorCam.ajusteCero(), ...(j.base ? j.ajuste || {} : {}) };
    this.info = j.info || { rms: j.rms_px, metodo: j.metodo };
    this.calibrado = j.calibrado !== false;
    this.recalcular();
  }
}

/** Extrapola la pose del casco N ms al futuro con su velocidad lineal y angular (suavizadas). */
export class PredictorPose {
  constructor({ suavizado = 0.5 } = {}) {
    this.a = suavizado;
    this.t = null;
    this.p = new THREE.Vector3(); this.q = new THREE.Quaternion();
    this.v = new THREE.Vector3(); this.w = new THREE.Vector3(); // m/s, rad/s (marco del mundo)
    this._dq = new THREE.Quaternion(); this._qi = new THREE.Quaternion(); this._ax = new THREE.Vector3(); this._vi = new THREE.Vector3();
  }
  push(tMs, p, q) {
    if (this.t !== null) {
      const dt = (tMs - this.t) / 1000;
      if (dt > 0.001 && dt < 0.2) {
        this._vi.subVectors(p, this.p).divideScalar(dt);
        this.v.lerp(this._vi, this.a);
        this._dq.multiplyQuaternions(q, this._qi.copy(this.q).invert());
        if (this._dq.w < 0) { this._dq.x *= -1; this._dq.y *= -1; this._dq.z *= -1; this._dq.w *= -1; }
        const ang = 2 * Math.acos(Math.min(1, this._dq.w));
        const sn = Math.sqrt(Math.max(0, 1 - this._dq.w * this._dq.w));
        if (sn > 1e-9) this._ax.set(this._dq.x / sn, this._dq.y / sn, this._dq.z / sn).multiplyScalar(ang / dt);
        else this._ax.set(0, 0, 0);
        this.w.lerp(this._ax, this.a);
      } else if (dt >= 0.2) { this.v.set(0, 0, 0); this.w.set(0, 0, 0); }
    }
    this.t = tMs; this.p.copy(p); this.q.copy(q);
  }
  /** Pose (matriz 4x4) a `ms` en el futuro. */
  predecir(ms, out = new THREE.Matrix4()) {
    const dt = ms / 1000;
    const p = this.p.clone().addScaledVector(this.v, dt);
    const ang = this.w.length() * dt;
    const q = this.q.clone();
    if (ang > 1e-9) q.premultiply(new THREE.Quaternion().setFromAxisAngle(this.w.clone().normalize(), ang));
    return out.compose(p, q, new THREE.Vector3(1, 1, 1));
  }
}
