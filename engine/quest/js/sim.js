// Simulador sin casco (?sim=1): el escaneo de la sala (Scaniverse) hace de "mundo real" y de malla del Quest.
// Un proyector "verdadero" (pose y K desconocidas para la app) proyecta el canvas del proyector sobre el escaneo
// con textura proyectiva + sombra, así se ve en pantalla lo mismo que vería Max: alineado o desalineado.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { ProyectorCam } from './proyector-cam.js';
import { simplificarPorCeldas, planosHorizontales } from './sala.js';

const CV2GL = new THREE.Matrix4().makeScale(1, -1, -1);

/** Pose de un proyector en el rig (ejes OpenCV) desde centro + yaw/pitch/roll en grados (pitch < 0 = mira abajo). */
export function poseProyector(centro, yaw, pitch, roll = 0) {
  const g = Math.PI / 180;
  const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(pitch * g, yaw * g, roll * g, 'YXZ'));
  const Mgl = new THREE.Matrix4().compose(new THREE.Vector3(...centro), q, new THREE.Vector3(1, 1, 1));
  return Mgl.multiply(CV2GL);
}
export function rtDesdePose(T_rig_cam) {
  const inv = T_rig_cam.clone().invert().elements;
  const R = [0, 1, 2].map((r) => [0, 1, 2].map((c) => inv[c * 4 + r]));
  const t = [inv[12], inv[13], inv[14]];
  return { R, t };
}

const VERT = /* glsl */`
  uniform mat4 projVP;
  varying vec2 vUv; varying vec4 vProj; varying vec3 vN;
  void main() {
    vec4 wp = modelMatrix * vec4(position, 1.0);
    vProj = projVP * wp; vUv = uv; vN = normalize(mat3(modelMatrix) * normal);
    gl_Position = projectionMatrix * viewMatrix * wp;
  }`;
const FRAG = /* glsl */`
  uniform sampler2D mapa; uniform sampler2D proyTex; uniform sampler2D sombra;
  uniform float base; uniform float ganancia; uniform float bias; uniform float hayMapa;
  varying vec2 vUv; varying vec4 vProj; varying vec3 vN;
  void main() {
    vec3 c = (hayMapa > 0.5 ? texture2D(mapa, vUv).rgb : vec3(0.5 + 0.3 * vN.y)) * base;
    if (vProj.w > 0.0) {
      vec3 ndc = vProj.xyz / vProj.w;
      if (abs(ndc.x) < 1.0 && abs(ndc.y) < 1.0 && abs(ndc.z) < 1.0) {
        vec2 st = ndc.xy * 0.5 + 0.5;
        float zf = ndc.z * 0.5 + 0.5;
        float zs = texture2D(sombra, st).r;
        if (zf <= zs + bias) c += texture2D(proyTex, st).rgb * ganancia;
      }
    }
    gl_FragColor = vec4(c, 1.0);
  }`;

export class Simulador {
  constructor({ renderer, escena, camCasco, canvasProy, w, h, modo, log = console.log }) {
    Object.assign(this, { renderer, escena, camCasco, canvasProy, log });
    this.w = w; this.h = h;
    // cabeza simulada: posición + yaw/pitch (grados)
    this.cabeza = { pos: new THREE.Vector3(-1.2, 1.65, 0.3), yaw: 0, pitch: -30 }; // de pie frente a la mesa grande
    // proyector "verdadero": desconocido para la app; lo que la calibración debe recuperar
    this.verdad = new ProyectorCam({ w, h, modo });
    this.ponerVerdad(modo);
    this.teclas = new Set();
    this.mouse = new THREE.Vector2(0, 0);
    this.escenaSombra = new THREE.Scene();
    this.rtSombra = new THREE.WebGLRenderTarget(1024, 576, { depthBuffer: true });
    this.rtSombra.depthTexture = new THREE.DepthTexture(1024, 576);
    this.texProy = new THREE.CanvasTexture(canvasProy);
    this.texProy.minFilter = THREE.LinearFilter; this.texProy.generateMipmaps = false;
    this.raycaster = new THREE.Raycaster();
  }

  ponerVerdad(modo) {
    this.verdad.modo = modo;
    const K = [[2150, 0, 975], [0, 2150, 930], [0, 0, 1]]; // throw ≈ 1.12, eje óptico bajo el centro (offset)
    const pose = modo === 'cabeza'
      ? poseProyector([0.03, 0.10, -0.02], 2, -14, 1)     // en la cabeza: 10 cm arriba, mirando un poco abajo
      : poseProyector([0.9, 1.7, 0.4], 63, -30, 0);        // trípode a ~2.5 m apuntando a la mesa grande
    this.verdad.base = { K, ...rtDesdePose(pose) };
    this.verdad.ajuste = ProyectorCam.ajusteCero();
    this.verdad.calibrado = true;
    this.verdad.recalcular();
  }

  async cargar(url, celda = 0.08) {
    const t0 = performance.now();
    const gltf = await new GLTFLoader().loadAsync(url);
    let malla = null;
    gltf.scene.updateMatrixWorld(true);
    gltf.scene.traverse((o) => { if (o.isMesh && !malla) malla = o; });
    if (!malla) throw new Error('el .glb no trae malla');
    const geom = malla.geometry.clone();
    geom.applyMatrix4(malla.matrixWorld); // al mundo (Y arriba, piso ~0)
    geom.computeBoundingBox();
    const mapa = malla.material.map || null;
    this.mat = new THREE.ShaderMaterial({
      vertexShader: VERT, fragmentShader: FRAG,
      uniforms: {
        mapa: { value: mapa }, hayMapa: { value: mapa ? 1 : 0 }, proyTex: { value: this.texProy },
        sombra: { value: this.rtSombra.depthTexture }, projVP: { value: new THREE.Matrix4() },
        base: { value: 0.32 }, ganancia: { value: 1.0 }, bias: { value: 0.0015 },
      },
      side: THREE.DoubleSide,
    });
    this.escaneo = new THREE.Mesh(geom, this.mat);
    this.escena.add(this.escaneo);
    this.sombraMesh = new THREE.Mesh(geom, new THREE.MeshBasicMaterial({ side: THREE.DoubleSide, colorWrite: false }));
    this.escenaSombra.add(this.sombraMesh);
    // malla "del Quest": versión gruesa del escaneo; planos horizontales (piso, mesas) detectados del escaneo
    const pos = geom.attributes.position.array, idx = geom.index.array;
    const simp = simplificarPorCeldas(pos, idx, celda);
    const planos = planosHorizontales(pos, geom.attributes.normal.array);
    const bb = geom.boundingBox;
    this.log(`escaneo: ${pos.length / 3} vértices, ${idx.length / 3} tri → malla sim ${simp.pos.length / 3} v / ${simp.idx.length / 3} tri (celda ${celda * 100} cm); ` +
      `planos: ${planos.map((p) => `${p.etiqueta}@${p.altura.toFixed(2)}m ${p.area.toFixed(1)}m²`).join(', ')}; ` +
      `caja ${bb.min.toArray().map((x) => x.toFixed(2))}..${bb.max.toArray().map((x) => x.toFixed(2))}; ${(performance.now() - t0).toFixed(0)} ms`);
    return { simp, planos };
  }

  matrizCabeza(out = new THREE.Matrix4()) {
    const g = Math.PI / 180;
    const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(this.cabeza.pitch * g, this.cabeza.yaw * g, 0, 'YXZ'));
    return out.compose(this.cabeza.pos, q, new THREE.Vector3(1, 1, 1));
  }

  /** Teclado y mouse: WASD mover, R/F subir/bajar, arrastrar = mirar. Las demás teclas las maneja la app. */
  controles(el, onTecla) {
    window.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT') return;
      this.teclas.add(e.code);
      onTecla(e);
    });
    window.addEventListener('keyup', (e) => this.teclas.delete(e.code));
    let arr = null;
    el.addEventListener('pointerdown', (e) => { arr = { x: e.clientX, y: e.clientY, mov: 0 }; });
    window.addEventListener('pointerup', (e) => {
      if (arr && arr.mov < 4 && e.target === el) onTecla({ code: 'Click', clientX: e.clientX, clientY: e.clientY });
      arr = null;
    });
    el.addEventListener('pointermove', (e) => {
      const r = el.getBoundingClientRect();
      this.mouse.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
      if (arr) {
        const dx = e.clientX - arr.x, dy = e.clientY - arr.y;
        arr.mov += Math.abs(dx) + Math.abs(dy);
        this.cabeza.yaw -= dx * 0.15; this.cabeza.pitch = Math.max(-89, Math.min(89, this.cabeza.pitch - dy * 0.15));
        arr.x = e.clientX; arr.y = e.clientY;
      }
    });
  }

  mover(dt, bloquearFlechas) {
    const k = this.teclas, v = 1.2 * dt, g = Math.PI / 180;
    const f = new THREE.Vector3(-Math.sin(this.cabeza.yaw * g), 0, -Math.cos(this.cabeza.yaw * g));
    const r = new THREE.Vector3(-f.z, 0, f.x);
    if (k.has('KeyW')) this.cabeza.pos.addScaledVector(f, v);
    if (k.has('KeyS')) this.cabeza.pos.addScaledVector(f, -v);
    if (k.has('KeyD')) this.cabeza.pos.addScaledVector(r, v);
    if (k.has('KeyA')) this.cabeza.pos.addScaledVector(r, -v);
    if (k.has('KeyR')) this.cabeza.pos.y += v;
    if (k.has('KeyF')) this.cabeza.pos.y -= v;
    if (!bloquearFlechas) {
      if (k.has('ArrowLeft')) this.cabeza.yaw += 60 * dt;
      if (k.has('ArrowRight')) this.cabeza.yaw -= 60 * dt;
      if (k.has('ArrowUp')) this.cabeza.pitch = Math.min(89, this.cabeza.pitch + 45 * dt);
      if (k.has('ArrowDown')) this.cabeza.pitch = Math.max(-89, this.cabeza.pitch - 45 * dt);
    }
  }

  rayoMouse() {
    this.raycaster.setFromCamera(this.mouse, this.camCasco);
    return { origen: this.raycaster.ray.origin.clone(), dir: this.raycaster.ray.direction.clone() };
  }

  /** Intersección con el escaneo completo (el "mundo real"). */
  golpeReal(origen, dir) {
    this.raycaster.set(origen, dir); this.raycaster.far = 20;
    const h = this.raycaster.intersectObject(this.sombraMesh, false)[0];
    return h ? h.point : null;
  }

  /** Render del simulador: sombra desde el proyector verdadero + vista del casco con la luz proyectada. */
  render(T_world_rig_verdad, ancho, alto) {
    const v = this.verdad;
    v.actualizar(T_world_rig_verdad);
    const r = this.renderer;
    // profundidad desde el proyector verdadero
    r.setRenderTarget(this.rtSombra);
    r.clear();
    r.render(this.escenaSombra, v.camara);
    r.setRenderTarget(null);
    this.mat.uniforms.projVP.value.multiplyMatrices(v.camara.projectionMatrix, v.camara.matrixWorldInverse);
    this.texProy.needsUpdate = true;
    this.camCasco.aspect = ancho / alto; this.camCasco.updateProjectionMatrix();
    r.render(this.escena, this.camCasco);
  }
}
