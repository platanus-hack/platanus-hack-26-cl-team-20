// La sala: mallas (XRMesh) y planos (XRPlane) del casco, o el escaneo de Scaniverse en el simulador.
// Construye dos vistas de lo mismo: la del casco (wireframe tenue + planos translúcidos) y la del proyector
// (efectos en negro de fondo: wireframe por color, aristas, bordes de mesa, pozo, puntero bajo el rayo).
import * as THREE from 'three';
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js';
import { LineSegmentsGeometry } from 'three/addons/lines/LineSegmentsGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';

export const EFECTOS = ['wireframe', 'aristas', 'mesa', 'pozo', 'negro'];

const COL = { piso: 0x00d8ff, techo: 0x3050ff, muroX: 0xff3cc8, muroZ: 0xffc400, incl: 0x40ff70 };
const COL_PLANO = { floor: 0x30ff80, table: 0xffa040, wall: 0x4080ff, ceiling: 0x8060ff, other: 0xc0c0c0 };

function colorPorNormal(n, out) {
  const ay = Math.abs(n.y);
  if (ay > 0.8) return out.setHex(n.y > 0 ? COL.piso : COL.techo);
  if (ay < 0.3) return out.setHex(Math.abs(n.x) > Math.abs(n.z) ? COL.muroX : COL.muroZ);
  return out.setHex(COL.incl);
}

/** Todas las aristas únicas de una malla de triángulos, coloreadas por la normal (en el mundo) de su cara. */
function aristasWire(pos, idx, matriz) {
  const nV = pos.length / 3;
  const nm = new THREE.Matrix3().getNormalMatrix(matriz);
  const vistos = new Set();
  const P = [], Cc = [];
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), n = new THREE.Vector3(), e = new THREE.Vector3(), col = new THREE.Color();
  for (let t = 0; t + 2 < idx.length; t += 3) {
    const i0 = idx[t], i1 = idx[t + 1], i2 = idx[t + 2];
    a.fromArray(pos, i0 * 3); b.fromArray(pos, i1 * 3); c.fromArray(pos, i2 * 3);
    n.subVectors(b, a).cross(e.subVectors(c, a)).applyMatrix3(nm).normalize();
    colorPorNormal(n, col);
    for (const [p, q] of [[i0, i1], [i1, i2], [i2, i0]]) {
      const k = p < q ? p * nV + q : q * nV + p;
      if (vistos.has(k)) continue;
      vistos.add(k);
      P.push(pos[p * 3], pos[p * 3 + 1], pos[p * 3 + 2], pos[q * 3], pos[q * 3 + 1], pos[q * 3 + 2]);
      Cc.push(col.r, col.g, col.b, col.r, col.g, col.b);
    }
  }
  return { pos: new Float32Array(P), col: new Float32Array(Cc) };
}

/** Aristas "duras" (ángulo entre caras > umbral), coloreadas por dirección: verticales magenta, horizontales cian. */
function aristasDuras(geom, matriz, umbral = 35) {
  const eg = new THREE.EdgesGeometry(geom, umbral);
  const p = eg.attributes.position.array;
  const Cc = new Float32Array(p.length);
  const a = new THREE.Vector3(), b = new THREE.Vector3(), col = new THREE.Color();
  for (let i = 0; i < p.length; i += 6) {
    a.fromArray(p, i).applyMatrix4(matriz); b.fromArray(p, i + 3).applyMatrix4(matriz);
    const d = b.sub(a).normalize();
    col.setHex(Math.abs(d.y) > 0.8 ? 0xff40d0 : Math.abs(d.y) < 0.3 ? 0x40e8ff : 0xfff060);
    for (let k = 0; k < 2; k++) { Cc[i + k * 3] = col.r; Cc[i + k * 3 + 1] = col.g; Cc[i + k * 3 + 2] = col.b; }
  }
  const out = { pos: new Float32Array(p), col: Cc };
  eg.dispose();
  return out;
}

function lineasGordas(pos, col, mat) {
  const g = new LineSegmentsGeometry();
  g.setPositions(pos);
  if (col) g.setColors(col);
  const l = new LineSegments2(g, mat);
  l.frustumCulled = false;
  return l;
}

function triangularPoligono(poly) {
  // poly: [[x, z], ...] en el espacio del plano (y = 0, normal +Y)
  const contorno = poly.map(([x, z]) => new THREE.Vector2(x, z));
  if (THREE.ShapeUtils.isClockWise(contorno)) contorno.reverse();
  const tris = THREE.ShapeUtils.triangulateShape(contorno, []);
  const pos = new Float32Array(contorno.length * 3);
  contorno.forEach((p, i) => { pos[i * 3] = p.x; pos[i * 3 + 2] = p.y; });
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  // ShapeUtils triangula en XY; en XZ el orden se invierte para que la cara mire a +Y
  g.setIndex(tris.flatMap((t) => [t[0], t[2], t[1]]));
  g.computeVertexNormals();
  return g;
}

function bordePoligono(poly) {
  const P = [];
  for (let i = 0; i < poly.length; i++) {
    const [x0, z0] = poly[i], [x1, z1] = poly[(i + 1) % poly.length];
    P.push(x0, 0.003, z0, x1, 0.003, z1);
  }
  return new Float32Array(P);
}

const etiquetaPlano = (s) => {
  const l = (s || 'other').toLowerCase();
  return l.includes('table') || l.includes('desk') ? 'table' : l.includes('floor') ? 'floor' : l.includes('wall') ? 'wall' : l.includes('ceiling') ? 'ceiling' : 'other';
};

export class Sala {
  constructor({ escenaCasco, escenaProy, log = console.log }) {
    this.log = log;
    this.casco = new THREE.Group(); this.casco.name = 'sala-casco'; escenaCasco.add(this.casco);
    this.proy = new THREE.Group(); this.proy.name = 'sala-proy'; escenaProy.add(this.proy);
    this.mallas = new Map(); this.planos = new Map();
    this.efecto = 'wireframe';
    this.verCasco = true;
    this.calibrando = false;

    // materiales del proyector (líneas gordas en px de render; resolution se fija en actualizarProy)
    this.mWire = new LineMaterial({ vertexColors: true, linewidth: 1.6, worldUnits: false });
    this.mAristas = new LineMaterial({ vertexColors: true, linewidth: 3, worldUnits: false });
    this.mMesa = new LineMaterial({ color: 0xffffff, linewidth: 6, worldUnits: false });
    this.mPozo = new LineMaterial({ vertexColors: true, linewidth: 4, worldUnits: false });
    this.mats = [this.mWire, this.mAristas, this.mMesa, this.mPozo];
    // casco
    this.mCascoWire = new THREE.LineBasicMaterial({ color: 0x66ccff, transparent: true, opacity: 0.22, depthWrite: false });

    // puntero bajo el rayo
    this.spot = new THREE.Mesh(new THREE.RingGeometry(0.045, 0.06, 48), new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide }));
    this.spot.visible = false; this.proy.add(this.spot);
    this.reticula = new THREE.Mesh(new THREE.RingGeometry(0.02, 0.028, 32), new THREE.MeshBasicMaterial({ color: 0xffd166, side: THREE.DoubleSide, depthTest: false }));
    this.reticula.visible = false; this.reticula.renderOrder = 5; this.casco.add(this.reticula);
    this.resalte = null; // EntradaPlano resaltado

    // pozo
    this.pozo = null;
    this.pozoObj = null;

    this.raycaster = new THREE.Raycaster();
    this._frameRay = 0;
  }

  // ───────── datos de la sala ─────────

  /** Marco estable de la sala (para el modo fijo): pose de la malla global de Space Setup o, si no, del piso. */
  marcoSala() {
    for (const e of this.mallas.values()) if (e.global) return { matriz: e.matriz, fuente: 'global-mesh' };
    for (const e of this.planos.values()) if (e.etiqueta === 'floor') return { matriz: e.matriz, fuente: 'floor-plane' };
    return null;
  }

  stats() {
    let tris = 0, mesas = 0;
    this.mallas.forEach((e) => (tris += e.tris));
    this.planos.forEach((e) => e.etiqueta === 'table' && mesas++);
    return { mallas: this.mallas.size, tris, planos: this.planos.size, mesas };
  }

  _nuevaMalla(clave, pos, idx, etiqueta, matriz) {
    this._quitarMalla(clave);
    const e = { clave, etiqueta, global: /global|sim/i.test(etiqueta || ''), matriz: matriz.clone(), tris: idx.length / 3 };
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos instanceof Float32Array ? pos : new Float32Array(pos), 3));
    g.setIndex(new THREE.BufferAttribute(idx instanceof Uint32Array ? idx : new Uint32Array(idx), 1));
    g.computeBoundingSphere(); g.computeBoundingBox();
    e.geom = g;
    // raycast (fuera de escena)
    e.ray = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
    e.ray.matrixAutoUpdate = false;
    // casco: wireframe tenue
    e.gCasco = new THREE.Group(); e.gCasco.matrixAutoUpdate = false;
    const w = aristasWire(g.attributes.position.array, g.index.array, matriz);
    const wg = new THREE.BufferGeometry(); wg.setAttribute('position', new THREE.BufferAttribute(w.pos, 3));
    e.gCasco.add(new THREE.LineSegments(wg, this.mCascoWire));
    this.casco.add(e.gCasco);
    // proyector
    e.gProy = new THREE.Group(); e.gProy.matrixAutoUpdate = false;
    e.wire = lineasGordas(w.pos, w.col, this.mWire);
    const d = aristasDuras(g, matriz);
    e.aristas = lineasGordas(d.pos, d.col, this.mAristas);
    e.gProy.add(e.wire, e.aristas);
    this.proy.add(e.gProy);
    e.wgCasco = wg;
    this.mallas.set(clave, e);
    this._pose(e, matriz);
    return e;
  }

  _quitarMalla(clave) {
    const e = this.mallas.get(clave);
    if (!e) return;
    this.casco.remove(e.gCasco); this.proy.remove(e.gProy);
    e.geom.dispose(); e.wgCasco.dispose(); e.wire.geometry.dispose(); e.aristas.geometry.dispose();
    this.mallas.delete(clave);
  }

  _nuevoPlano(clave, poly, etiqueta, orientacion, matriz) {
    this._quitarPlano(clave);
    const et = etiquetaPlano(etiqueta);
    const e = { clave, etiqueta: et, orientacion, poly, matriz: matriz.clone() };
    e.geom = triangularPoligono(poly);
    e.ray = new THREE.Mesh(e.geom, new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
    e.ray.matrixAutoUpdate = false;
    e.gCasco = new THREE.Group(); e.gCasco.matrixAutoUpdate = false;
    e.fillCasco = new THREE.Mesh(e.geom, new THREE.MeshBasicMaterial({ color: COL_PLANO[et], transparent: true, opacity: 0.12, side: THREE.DoubleSide, depthWrite: false }));
    e.gCasco.add(e.fillCasco);
    this.casco.add(e.gCasco);
    e.gProy = new THREE.Group(); e.gProy.matrixAutoUpdate = false;
    e.fillProy = new THREE.Mesh(e.geom, new THREE.MeshBasicMaterial({ color: 0x121c28, side: THREE.DoubleSide, depthWrite: false }));
    e.fillProy.visible = false; e.fillProy.renderOrder = -1;
    e.borde = lineasGordas(bordePoligono(poly), null, this.mMesa);
    e.gProy.add(e.fillProy, e.borde);
    this.proy.add(e.gProy);
    this.planos.set(clave, e);
    this._pose(e, matriz);
    return e;
  }

  _quitarPlano(clave) {
    const e = this.planos.get(clave);
    if (!e) return;
    this.casco.remove(e.gCasco); this.proy.remove(e.gProy);
    e.geom.dispose(); e.borde.geometry.dispose();
    if (this.resalte === e) this.resalte = null;
    this.planos.delete(clave);
  }

  _pose(e, m) {
    e.matriz.copy(m);
    e.gCasco.matrix.copy(m); e.gCasco.matrixWorldNeedsUpdate = true;
    e.gProy.matrix.copy(m); e.gProy.matrixWorldNeedsUpdate = true;
    e.ray.matrix.copy(m); e.ray.matrixWorld.copy(m);
  }

  /** Lee mallas y planos del XRFrame (mesh-detection / plane-detection de Quest; requieren Space Setup). */
  actualizarXR(frame, ref) {
    const m4 = new THREE.Matrix4();
    if (frame.detectedMeshes) {
      const vistas = new Set();
      for (const m of frame.detectedMeshes) {
        vistas.add(m);
        const pose = frame.getPose(m.meshSpace, ref);
        if (!pose) continue;
        m4.fromArray(pose.transform.matrix);
        let e = this.mallas.get(m);
        if (!e || e.cambio !== m.lastChangedTime) {
          try {
            e = this._nuevaMalla(m, m.vertices, m.indices, m.semanticLabel || 'mesh', m4);
            e.cambio = m.lastChangedTime;
            this.log(`malla ${m.semanticLabel || '?'}: ${m.vertices.length / 3} vértices, ${m.indices.length / 3} triángulos`);
          } catch (err) { this.log(`malla falló: ${err.message}`, null, 'error'); continue; }
        } else if (!arrIgual(e.matriz.elements, pose.transform.matrix)) this._pose(e, m4);
      }
      for (const k of [...this.mallas.keys()]) if (typeof k !== 'string' && !vistas.has(k)) this._quitarMalla(k);
    }
    if (frame.detectedPlanes) {
      const vistos = new Set();
      for (const p of frame.detectedPlanes) {
        vistos.add(p);
        const pose = frame.getPose(p.planeSpace, ref);
        if (!pose) continue;
        m4.fromArray(pose.transform.matrix);
        let e = this.planos.get(p);
        if (!e || e.cambio !== p.lastChangedTime) {
          const poly = p.polygon.map((q) => [q.x, q.z]);
          if (poly.length < 3) continue;
          e = this._nuevoPlano(p, poly, p.semanticLabel, p.orientation, m4);
          e.cambio = p.lastChangedTime;
        } else if (!arrIgual(e.matriz.elements, pose.transform.matrix)) this._pose(e, m4);
      }
      for (const k of [...this.planos.keys()]) if (typeof k !== 'string' && !vistos.has(k)) this._quitarPlano(k);
    }
  }

  agregarMallaSim(pos, idx, etiqueta = 'sim global mesh') {
    return this._nuevaMalla('sim', pos, idx, etiqueta, new THREE.Matrix4());
  }
  agregarPlanoSim(clave, poly, altura, etiqueta) {
    return this._nuevoPlano(clave, poly, etiqueta, 'horizontal', new THREE.Matrix4().makeTranslation(0, altura, 0));
  }

  // ───────── rayo ─────────

  /** Intersección del rayo con planos y (cada 3 cuadros, o si no hay planos) con las mallas. */
  raycast(origen, dir, { mallas = true } = {}) {
    this.raycaster.set(origen, dir);
    this.raycaster.far = 15;
    let mejor = null;
    for (const e of this.planos.values()) {
      const h = this.raycaster.intersectObject(e.ray, false)[0];
      if (h && (!mejor || h.distance < mejor.distance)) mejor = { ...h, plano: e };
    }
    if (mallas && (this._frameRay++ % 3 === 0 || !this.planos.size)) {
      for (const e of this.mallas.values()) {
        const h = this.raycaster.intersectObject(e.ray, false)[0];
        if (h && (!mejor || h.distance < mejor.distance - 0.02)) mejor = { ...h, plano: null, malla: e };
      }
      this._ultimoMalla = mejor;
    } else if (mallas && this._ultimoMalla && !mejor) mejor = this._ultimoMalla;
    if (!mejor) return null;
    const normal = mejor.plano
      ? new THREE.Vector3(0, 1, 0).transformDirection(mejor.plano.matriz)
      : mejor.face ? mejor.face.normal.clone().transformDirection(mejor.object.matrixWorld) : dir.clone().negate();
    if (normal.dot(dir) > 0) normal.negate();
    return { punto: mejor.point.clone(), normal, plano: mejor.plano || null, distancia: mejor.distance };
  }

  // ───────── efectos (vista proyector) ─────────

  colocarPozo(hit, ojo) {
    if (!hit) { this.pozo = null; return; }
    let n, u;
    if (hit.plano) {
      n = new THREE.Vector3(0, 1, 0).transformDirection(hit.plano.matriz);
      u = new THREE.Vector3(1, 0, 0).transformDirection(hit.plano.matriz);
    } else {
      n = hit.normal.clone();
      u = Math.abs(n.y) > 0.9 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0).cross(n);
      u.sub(n.clone().multiplyScalar(u.dot(n))).normalize();
    }
    if (ojo && ojo.clone().sub(hit.punto).dot(n) < 0) n.negate();
    // eje largo del pozo mirando al ojo, para que la ilusión se lea de frente
    if (ojo) {
      const haciaOjo = ojo.clone().sub(hit.punto); haciaOjo.sub(n.clone().multiplyScalar(haciaOjo.dot(n)));
      if (haciaOjo.lengthSq() > 1e-4) u = haciaOjo.normalize().cross(n).normalize();
    }
    const v = new THREE.Vector3().crossVectors(n, u).normalize();
    this.pozo = { c: hit.punto.clone(), n, u, v, ancho: 0.32, largo: 0.24, prof: 0.3 };
    this.log(`pozo en (${hit.punto.toArray().map((x) => x.toFixed(2)).join(', ')})`);
  }

  _construirPozo(ojo) {
    const P = this.pozo, pos = [], col = [];
    const c = new THREE.Color();
    const punto = (a, b, d) => P.c.clone().addScaledVector(P.u, a).addScaledVector(P.v, b).addScaledVector(P.n, -d);
    // proyección anamórfica: el punto 3D bajo la superficie se dibuja donde el rayo ojo→punto corta la superficie
    const sobrePlano = (X) => {
      const dX = X.clone().sub(ojo), den = dX.dot(P.n);
      if (Math.abs(den) < 1e-6) return X;
      const s = P.c.clone().sub(ojo).dot(P.n) / den;
      return ojo.clone().addScaledVector(dX, s);
    };
    const seg = (A, B, brillo, hex = 0xffffff) => {
      const a = sobrePlano(A), b = sobrePlano(B);
      pos.push(a.x, a.y, a.z, b.x, b.y, b.z);
      c.setHex(hex).multiplyScalar(brillo);
      col.push(c.r, c.g, c.b, c.r, c.g, c.b);
    };
    const a2 = P.ancho / 2, b2 = P.largo / 2, D = P.prof;
    const esq = [[-a2, -b2], [a2, -b2], [a2, b2], [-a2, b2]];
    const anillos = 6;
    for (let k = 0; k <= anillos; k++) {
      const d = (D * k) / anillos;
      const br = 1 - 0.8 * (k / anillos);
      for (let i = 0; i < 4; i++) seg(punto(...esq[i], d), punto(...esq[(i + 1) % 4], d), br, k === 0 ? 0xffffff : 0x40e8ff);
    }
    for (let i = 0; i < 4; i++) {
      // aristas verticales con degradé
      for (let k = 0; k < anillos; k++) seg(punto(...esq[i], (D * k) / anillos), punto(...esq[i], (D * (k + 1)) / anillos), 1 - 0.8 * (k / anillos), 0xff40d0);
    }
    // rejilla del fondo
    for (let i = 1; i < 4; i++) {
      const x = -a2 + (P.ancho * i) / 4, y = -b2 + (P.largo * i) / 4;
      seg(punto(x, -b2, D), punto(x, b2, D), 0.25, 0x40e8ff);
      seg(punto(-a2, y, D), punto(a2, y, D), 0.25, 0x40e8ff);
    }
    if (!this.pozoObj) { this.pozoObj = lineasGordas(new Float32Array(pos), new Float32Array(col), this.mPozo); this.proy.add(this.pozoObj); }
    else {
      // LineSegmentsGeometry no reusa buffers de distinto largo: recrear es simple y son ~80 segmentos
      this.pozoObj.geometry.dispose();
      const g = new LineSegmentsGeometry(); g.setPositions(new Float32Array(pos)); g.setColors(new Float32Array(col));
      this.pozoObj.geometry = g;
    }
  }

  /** Por cuadro: visibilidad por efecto, puntero bajo el rayo, pozo dependiente del ojo. */
  actualizarProy({ ojo, hit, renderW, renderH }) {
    this.mats.forEach((m) => m.resolution.set(renderW, renderH));
    const ef = this.calibrando ? 'negro' : this.efecto;
    for (const e of this.mallas.values()) {
      e.wire.visible = ef === 'wireframe';
      e.aristas.visible = ef === 'aristas' || (ef === 'mesa' && /table|desk/i.test(e.etiqueta));
      e.gCasco.visible = this.verCasco;
    }
    for (const e of this.planos.values()) {
      e.borde.visible = ef === 'mesa' && e.etiqueta === 'table';
      e.fillProy.visible = !this.calibrando && this.resalte === e && ef !== 'negro';
      e.fillCasco.material.opacity = this.resalte === e ? 0.35 : 0.12;
      e.gCasco.visible = this.verCasco;
    }
    // puntero
    if (hit && !this.calibrando && ef !== 'negro') {
      this.spot.visible = true;
      this.spot.position.copy(hit.punto).addScaledVector(hit.normal, 0.004);
      this.spot.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), hit.normal);
    } else this.spot.visible = false;
    if (hit) {
      this.reticula.visible = true;
      this.reticula.position.copy(hit.punto).addScaledVector(hit.normal, 0.003);
      this.reticula.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), hit.normal);
    } else this.reticula.visible = false;
    this.resalte = hit?.plano || null;
    // pozo
    if (this.pozo && ef === 'pozo' && ojo) { this._construirPozo(ojo); this.pozoObj.visible = true; }
    else if (this.pozoObj) this.pozoObj.visible = false;
  }
}

function arrIgual(a, b) { for (let i = 0; i < 16; i++) if (Math.abs(a[i] - b[i]) > 1e-6) return false; return true; }

// ───────── utilidades del simulador (escaneo Scaniverse) ─────────

/** Simplifica por agrupamiento de vértices en celdas (similar en densidad a la malla global del Quest). */
export function simplificarPorCeldas(pos, idx, celda = 0.08) {
  const nV = pos.length / 3;
  const mapa = new Map(), remap = new Int32Array(nV), suma = [];
  const K = 4096, off = 2048;
  for (let i = 0; i < nV; i++) {
    const ix = Math.floor(pos[i * 3] / celda) + off, iy = Math.floor(pos[i * 3 + 1] / celda) + off, iz = Math.floor(pos[i * 3 + 2] / celda) + off;
    const k = (ix * K + iy) * K + iz;
    let j = mapa.get(k);
    if (j === undefined) { j = suma.length / 4; mapa.set(k, j); suma.push(0, 0, 0, 0); }
    suma[j * 4] += pos[i * 3]; suma[j * 4 + 1] += pos[i * 3 + 1]; suma[j * 4 + 2] += pos[i * 3 + 2]; suma[j * 4 + 3]++;
    remap[i] = j;
  }
  const n = suma.length / 4;
  const P = new Float32Array(n * 3);
  for (let j = 0; j < n; j++) { const c = suma[j * 4 + 3]; P[j * 3] = suma[j * 4] / c; P[j * 3 + 1] = suma[j * 4 + 1] / c; P[j * 3 + 2] = suma[j * 4 + 2] / c; }
  const vistos = new Set(), I = [];
  for (let t = 0; t + 2 < idx.length; t += 3) {
    const a = remap[idx[t]], b = remap[idx[t + 1]], c = remap[idx[t + 2]];
    if (a === b || b === c || a === c) continue;
    const s = [a, b, c].sort((x, y) => x - y);
    const k = (s[0] * n + s[1]) * n + s[2];
    if (vistos.has(k)) continue;
    vistos.add(k); I.push(a, b, c);
  }
  return { pos: P, idx: new Uint32Array(I) };
}

/** Planos horizontales (piso y mesas) desde vértices con normal hacia arriba: picos de altura + componentes en rejilla. */
export function planosHorizontales(pos, nrm, { celda = 0.1 } = {}) {
  const alturas = [];
  for (let i = 0; i < pos.length / 3; i++) if (nrm[i * 3 + 1] > 0.92) alturas.push(i);
  const bin = 0.02, hist = new Map();
  alturas.forEach((i) => { const b = Math.round(pos[i * 3 + 1] / bin); hist.set(b, (hist.get(b) || 0) + 1); });
  const bins = [...hist.entries()].sort((a, b) => a[0] - b[0]);
  const picos = bins.filter(([b, c]) => c > 150 && c >= (hist.get(b - 1) || 0) && c >= (hist.get(b + 1) || 0) && c >= (hist.get(b - 2) || 0) && c >= (hist.get(b + 2) || 0));
  const out = [];
  const pisoBin = picos.length ? picos[0][0] : null;
  for (const [b] of picos) {
    const h = b * bin;
    const esPiso = b === pisoBin;
    if (!esPiso && (h < 0.35 || h > 1.3)) continue;
    const sel = alturas.filter((i) => Math.abs(pos[i * 3 + 1] - h) < 0.03);
    const grid = new Map();
    sel.forEach((i) => { const k = `${Math.floor(pos[i * 3] / celda)},${Math.floor(pos[i * 3 + 2] / celda)}`; (grid.get(k) || grid.set(k, []).get(k)).push(i); });
    const visit = new Set();
    for (const k0 of grid.keys()) {
      if (visit.has(k0)) continue;
      const comp = [], pila = [k0]; visit.add(k0);
      while (pila.length) {
        const k = pila.pop(); comp.push(k);
        const [x, z] = k.split(',').map(Number);
        for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
          const kk = `${x + dx},${z + dz}`;
          if (grid.has(kk) && !visit.has(kk)) { visit.add(kk); pila.push(kk); }
        }
      }
      const area = comp.length * celda * celda;
      if (area < (esPiso ? 2 : 0.3)) continue;
      const pts = comp.flatMap((k) => grid.get(k).map((i) => [pos[i * 3], pos[i * 3 + 2]]));
      out.push({ etiqueta: esPiso ? 'floor' : 'table', altura: h, poly: casco2D(pts), area });
    }
  }
  return out;
}

function casco2D(pts) {
  const p = pts.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cr = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lo = [], hi = [];
  for (const q of p) { while (lo.length >= 2 && cr(lo[lo.length - 2], lo[lo.length - 1], q) <= 0) lo.pop(); lo.push(q); }
  for (const q of p.reverse()) { while (hi.length >= 2 && cr(hi[hi.length - 2], hi[hi.length - 1], q) <= 0) hi.pop(); hi.push(q); }
  return lo.slice(0, -1).concat(hi.slice(0, -1));
}
