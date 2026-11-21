// Capa en píxeles del proyector (encima de la vista 3D): cruces de calibración numeradas, puntos tocados con su
// error de reproyección, y un patrón de encuadre (borde + rejilla) para revisar overscan/keystone del proyector.
import * as THREE from 'three';
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js';
import { LineSegmentsGeometry } from 'three/addons/lines/LineSegmentsGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';

export class Overlay {
  constructor(w, h) {
    this.w = w; this.h = h;
    this.escena = new THREE.Scene();
    this.camara = new THREE.OrthographicCamera(0, w, h, 0, -10, 10); // x = u + 0.5, y = h − (v + 0.5)
    this.grupo = new THREE.Group(); this.escena.add(this.grupo);
    this.mats = {
      cruz: new LineMaterial({ color: 0xffffff, linewidth: 6, worldUnits: false }),
      ok: new LineMaterial({ color: 0x30ff80, linewidth: 3, worldUnits: false }),
      err: new LineMaterial({ color: 0xff4040, linewidth: 3, worldUnits: false }),
      patron: new LineMaterial({ color: 0x9aa4b4, linewidth: 2, worldUnits: false }),
      borde: new LineMaterial({ color: 0xffffff, linewidth: 4, worldUnits: false }),
    };
    this.texturas = new Map();
    this.activo = false;
  }

  xy(u, v) { return [u + 0.5, this.h - (v + 0.5)]; }

  _lineas(segs, mat) {
    const g = new LineSegmentsGeometry(); g.setPositions(new Float32Array(segs));
    const l = new LineSegments2(g, mat); l.frustumCulled = false; this.grupo.add(l);
  }
  _circulo(segs, x, y, r, n = 40) {
    for (let i = 0; i < n; i++) {
      const a0 = (i / n) * Math.PI * 2, a1 = ((i + 1) / n) * Math.PI * 2;
      segs.push(x + r * Math.cos(a0), y + r * Math.sin(a0), 0, x + r * Math.cos(a1), y + r * Math.sin(a1), 0);
    }
  }
  _texto(txt, x, y, px = 64, color = '#ffffff') {
    const clave = `${txt}|${px}|${color}`;
    let tex = this.texturas.get(clave);
    if (!tex) {
      const c = document.createElement('canvas'); const ctx = c.getContext('2d');
      ctx.font = `700 ${px}px system-ui, sans-serif`;
      c.width = Math.ceil(ctx.measureText(txt).width + 16); c.height = Math.ceil(px * 1.3);
      ctx.font = `700 ${px}px system-ui, sans-serif`; ctx.fillStyle = color; ctx.textBaseline = 'middle';
      ctx.fillText(txt, 8, c.height / 2);
      tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
      tex.userData.size = [c.width, c.height];
      this.texturas.set(clave, tex);
    }
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false }));
    s.scale.set(tex.userData.size[0], tex.userData.size[1], 1);
    s.position.set(x + tex.userData.size[0] / 2, y, 0);
    this.grupo.add(s);
  }

  limpiar() {
    for (const o of [...this.grupo.children]) {
      this.grupo.remove(o);
      if (o.isSprite) o.material.dispose(); else o.geometry.dispose();
    }
    this.activo = false;
  }

  /** Estado de calibración: cruz activa grande; tocados en verde; con solución, error (rojo) hacia la reproyección. */
  calibracion({ cruces, idx, puntos, reproy }) {
    this.limpiar();
    this.activo = true;
    const hechos = new Set(puntos.map((p) => p.i));
    const ok = [], err = [];
    puntos.forEach((p, k) => {
      const [x, y] = this.xy(...p.uv);
      this._circulo(ok, x, y, 14, 20);
      if (reproy && reproy[k] && reproy[k].every(Number.isFinite)) {
        const [rx, ry] = this.xy(...reproy[k]);
        err.push(x, y, 0, rx, ry, 0);
        this._circulo(err, rx, ry, 6, 12);
      }
    });
    // pendientes: punto chico gris
    const pend = [];
    cruces.forEach((uv, i) => { if (i !== idx && !hechos.has(i)) { const [x, y] = this.xy(...uv); this._circulo(pend, x, y, 5, 10); } });
    if (ok.length) this._lineas(ok, this.mats.ok);
    if (err.length) this._lineas(err, this.mats.err);
    if (pend.length) this._lineas(pend, this.mats.patron);
    if (idx >= 0 && idx < cruces.length) {
      const [x, y] = this.xy(...cruces[idx]);
      const L = 150, s = [x - L, y, 0, x - 10, y, 0, x + 10, y, 0, x + L, y, 0, x, y - L, 0, x, y - 10, 0, x, y + 10, 0, x, y + L, 0];
      this._circulo(s, x, y, 40, 48);
      this._lineas(s, this.mats.cruz);
      const dx = x > this.w - 260 ? -230 : 60, dy = y < 200 ? 90 : -70;
      this._texto(String(idx + 1), x + dx, y + dy, 90);
    }
  }

  /** Patrón de encuadre: borde del cuadro, rejilla en cuartos, centro y resolución. */
  patron() {
    this.limpiar();
    this.activo = true;
    const w = this.w, h = this.h, b = [];
    const r = (x0, y0, x1, y1) => b.push(x0, y0, 0, x1, y0, 0, x1, y0, 0, x1, y1, 0, x1, y1, 0, x0, y1, 0, x0, y1, 0, x0, y0, 0);
    r(3, 3, w - 3, h - 3);
    this._lineas(b, this.mats.borde);
    const g = [];
    for (let i = 1; i < 8; i++) { const x = (w * i) / 8, y = (h * i) / 8; g.push(x, 0, 0, x, h, 0, 0, y, 0, w, y, 0); }
    this._circulo(g, w / 2, h / 2, h / 4, 64);
    this._lineas(g, this.mats.patron);
    const c = [w / 2 - 60, h / 2, 0, w / 2 + 60, h / 2, 0, w / 2, h / 2 - 60, 0, w / 2, h / 2 + 60, 0];
    this._lineas(c, this.mats.cruz);
    this._texto(`${w}x${h}`, w / 2 + 20, h / 2 - 50, 40, '#9aa4b4');
  }

  render(renderer, rw, rh) {
    if (!this.activo) return;
    for (const m of Object.values(this.mats)) m.resolution.set(rw, rh);
    const ac = renderer.autoClear;
    renderer.autoClear = false;
    renderer.clearDepth();
    renderer.render(this.escena, this.camara);
    renderer.autoClear = ac;
  }
}
