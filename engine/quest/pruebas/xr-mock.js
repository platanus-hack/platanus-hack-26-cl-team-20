// WebXR falso para probar el camino del casco en Chrome headless (lo inyecta pruebas/headless.py, escenario "xr").
// Sala de caja 4 x 4 x 2.6 m con mesa (malla global + planos), cabeza y dos controles movibles desde la prueba.
(() => {
  const M = (window.__xrMock = { cuadros: 0 });
  const norm = (v) => { const l = Math.hypot(...v); return v.map((x) => x / l); };
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  /** Matriz (column-major) con origen en c y eje −Z apuntando a d. */
  M.mirando = (c, d, arriba = [0, 1, 0]) => {
    const z = norm(d.map((x) => -x));
    let x = cross(arriba, z);
    if (Math.hypot(...x) < 1e-6) x = cross([0, 0, 1], z);
    x = norm(x);
    const y = cross(z, x);
    return new Float32Array([...x, 0, ...y, 0, ...z, 0, ...c, 1]);
  };
  const trasl = (x, y, z) => new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1]);
  const pitch = (-30 * Math.PI) / 180;
  M.cabeza = M.mirando([0, 1.6, 0], [0, Math.sin(pitch), -Math.cos(pitch)]);
  const pose = (m) => ({ matrix: m, position: { x: m[12], y: m[13], z: m[14], w: 1 }, orientation: { x: 0, y: 0, z: 0, w: 1 } });

  class Espacio {}
  class Fuente {
    constructor(lado) {
      this.handedness = lado; this.targetRayMode = 'tracked-pointer'; this.profiles = ['oculus-touch-v3'];
      this.targetRaySpace = new Espacio(); this.gripSpace = new Espacio();
      this.gamepad = { mapping: 'xr-standard', axes: [0, 0, 0, 0], buttons: Array.from({ length: 7 }, () => ({ pressed: false, touched: false, value: 0 })) };
    }
  }
  M.der = { src: new Fuente('right'), m: M.mirando([0.2, 1.3, -0.3], [0, -0.3, -1]) };
  M.izq = { src: new Fuente('left'), m: M.mirando([-0.2, 1.3, -0.3], [0, -0.3, -1]) };

  // ── sala falsa ──
  function malla() {
    const V = [], I = [];
    const quad = (o, u, v, nu, nv) => {
      const b = V.length / 3;
      for (let j = 0; j <= nv; j++) for (let i = 0; i <= nu; i++) V.push(...[0, 1, 2].map((k) => o[k] + (u[k] * i) / nu + (v[k] * j) / nv));
      for (let j = 0; j < nv; j++) for (let i = 0; i < nu; i++) {
        const a = b + j * (nu + 1) + i;
        I.push(a, a + 1, a + nu + 2, a, a + nu + 2, a + nu + 1);
      }
    };
    quad([-2, 0, 1], [4, 0, 0], [0, 0, -4], 16, 16);          // piso
    quad([-2, 2.6, -3], [4, 0, 0], [0, 0, 4], 8, 8);          // techo
    quad([-2, 0, -3], [4, 0, 0], [0, 2.6, 0], 16, 10);        // muro del fondo
    quad([-2, 0, 1], [0, 0, -4], [0, 2.6, 0], 16, 10);        // muro izq
    quad([2, 0, -3], [0, 0, 4], [0, 2.6, 0], 16, 10);         // muro der
    quad([-0.6, 0.75, -0.8], [1.2, 0, 0], [0, 0, -0.8], 6, 4); // cubierta de la mesa
    quad([-0.6, 0, -0.8], [1.2, 0, 0], [0, 0.75, 0], 6, 3);    // frente de la mesa
    return { V: new Float32Array(V), I: new Uint32Array(I) };
  }
  const g = malla();
  const mallas = new Set([{ meshSpace: new Espacio(), vertices: g.V, indices: g.I, lastChangedTime: 1, semanticLabel: 'global mesh', _m: trasl(0, 0, 0) }]);
  const P = (x, z) => new DOMPointReadOnly(x, 0, z, 1);
  // planos: espacio del plano con Y = normal; polígono en x,z
  const planos = new Set([
    { planeSpace: new Espacio(), polygon: [P(-2, -2), P(2, -2), P(2, 2), P(-2, 2)], orientation: 'horizontal', semanticLabel: 'floor', lastChangedTime: 1, _m: trasl(0, 0, -1) },
    { planeSpace: new Espacio(), polygon: [P(-0.6, -0.4), P(0.6, -0.4), P(0.6, 0.4), P(-0.6, 0.4)], orientation: 'horizontal', semanticLabel: 'table', lastChangedTime: 1, _m: trasl(0, 0.75, -1.2) },
    // muro del fondo: normal +Z (hacia la sala) → Y del plano = +Z, X = +X, Z = −Y
    { planeSpace: new Espacio(), polygon: [P(-2, -1.3), P(2, -1.3), P(2, 1.3), P(-2, 1.3)], orientation: 'vertical', semanticLabel: 'wall', lastChangedTime: 1,
      _m: new Float32Array([1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 1.3, -3, 1]) },
  ]);

  class Cuadro {
    constructor(s) { this.session = s; }
    getViewerPose() {
      const m = M.cabeza;
      const ojo = (dx, eye) => {
        const t = new Float32Array(m); t[12] += m[0] * dx; t[13] += m[1] * dx; t[14] += m[2] * dx;
        const f = 1 / Math.tan((50 * Math.PI) / 180), n = 0.05, fa = 100;
        const proj = new Float32Array([f, 0, 0, 0, 0, f, 0, 0, 0, 0, -(fa + n) / (fa - n), -1, 0, 0, (-2 * fa * n) / (fa - n), 0]);
        return { eye, projectionMatrix: proj, transform: pose(t) };
      };
      return { transform: pose(m), views: [ojo(-0.032, 'left'), ojo(0.032, 'right')], emulatedPosition: false };
    }
    getPose(esp) {
      if (esp === M.der.src.targetRaySpace || esp === M.der.src.gripSpace) return { transform: pose(M.der.m) };
      if (esp === M.izq.src.targetRaySpace || esp === M.izq.src.gripSpace) return { transform: pose(M.izq.m) };
      for (const x of mallas) if (x.meshSpace === esp) return { transform: pose(x._m) };
      for (const x of planos) if (x.planeSpace === esp) return { transform: pose(x._m) };
      return null;
    }
    get detectedMeshes() { return mallas; }
    get detectedPlanes() { return planos; }
    getHitTestResults() { return []; }
  }

  class Sesion extends EventTarget {
    constructor(init) {
      super();
      const pedidas = [...(init.requiredFeatures || []), ...(init.optionalFeatures || [])];
      // concede todo, incluido depth-sensing sin layers (prueba el parche para three r170)
      this.enabledFeatures = Object.freeze(pedidas);
      this.depthUsage = 'gpu-optimized'; this.depthDataFormat = 'luminance-alpha';
      this.inputSources = [M.der.src, M.izq.src];
      this.renderState = { baseLayer: null, depthNear: 0.1, depthFar: 1000, inlineVerticalFieldOfView: null };
      this.visibilityState = 'visible';
    }
    updateRenderState(s) { Object.assign(this.renderState, s); }
    requestReferenceSpace() { return Promise.resolve(new Espacio()); }
    requestAnimationFrame(cb) { return setTimeout(() => { M.cuadros++; cb(performance.now(), new Cuadro(this)); }, 14); }
    cancelAnimationFrame(id) { clearTimeout(id); }
    requestHitTestSource() { return Promise.resolve({ cancel() {} }); }
    end() { this.dispatchEvent(new Event('end')); return Promise.resolve(); }
  }

  window.XRWebGLLayer = class {
    constructor() { this.framebuffer = null; this.framebufferWidth = 1600; this.framebufferHeight = 800; this.antialias = true; this.ignoreDepthValues = false; }
    getViewport(v) { return v.eye === 'left' ? { x: 0, y: 0, width: 800, height: 800 } : { x: 800, y: 0, width: 800, height: 800 }; }
    static getNativeFramebufferScaleFactor() { return 1; }
  };
  window.XRRigidTransform = class { constructor(p, o) { this.position = p; this.orientation = o; } };
  const xr = { isSessionSupported: async (m) => m === 'immersive-ar', requestSession: async (mode, init) => (M.sesion = new Sesion(init || {})), addEventListener() {}, removeEventListener() {} };
  Object.defineProperty(navigator, 'xr', { value: xr, configurable: true });
  // el makeXRCompatible nativo de Chrome headless queda colgado (no hay dispositivo XR): reemplazarlo siempre
  for (const C of [window.WebGL2RenderingContext, window.WebGLRenderingContext]) if (C) C.prototype.makeXRCompatible = () => Promise.resolve();
  M.apretar = async (lado, boton = 0, ms = 60) => {
    const b = M[lado].src.gamepad.buttons[boton];
    b.pressed = true; b.value = 1;
    await new Promise((r) => setTimeout(r, ms));
    b.pressed = false; b.value = 0;
    await new Promise((r) => setTimeout(r, ms));
  };
})();
