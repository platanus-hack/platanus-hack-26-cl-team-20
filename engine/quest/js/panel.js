// Panel flotante en el casco: canvas 2D → textura en un plano, se aprieta apuntando con el rayo derecho + gatillo.
// Los mismos botones se dibujan como HTML en la página 2D y en el simulador (ver app.js).
import * as THREE from 'three';

const COLS = 4;

export class PanelXR {
  /** botones: [{ id, texto: () => string, activo?: () => bool, accion: () => void }] · estado: () => string[] */
  constructor(botones, estado, { ancho = 0.36, px = 1152 } = {}) {
    this.botones = botones; this.estado = estado;
    this.filasBot = Math.ceil(botones.length / COLS);
    this.W = px; this.bh = 92; this.H = this.filasBot * this.bh + 300;
    this.canvas = document.createElement('canvas');
    this.canvas.width = this.W; this.canvas.height = this.H;
    this.ctx = this.canvas.getContext('2d');
    this.tex = new THREE.CanvasTexture(this.canvas);
    this.tex.colorSpace = THREE.SRGBColorSpace;
    const alto = (ancho * this.H) / this.W;
    this.mesh = new THREE.Mesh(new THREE.PlaneGeometry(ancho, alto),
      new THREE.MeshBasicMaterial({ map: this.tex, transparent: true, opacity: 0.94, depthTest: false }));
    this.mesh.renderOrder = 10;
    this.hoverId = null; this.flash = null; this.ultimoDibujo = 0;
  }

  boton(uv) {
    if (!uv) return null;
    const x = uv.x * this.W, y = (1 - uv.y) * this.H;
    const fila = Math.floor((y - 12) / this.bh), col = Math.floor((x - 12) / ((this.W - 24) / COLS));
    if (fila < 0 || fila >= this.filasBot || col < 0 || col >= COLS) return null;
    return this.botones[fila * COLS + col] || null;
  }

  hover(uv) { const b = this.boton(uv); const id = b ? b.id : null; if (id !== this.hoverId) { this.hoverId = id; this.dibujar(); } }

  click(uv) {
    const b = this.boton(uv);
    if (!b) return false;
    this.flash = b.id; setTimeout(() => { this.flash = null; this.dibujar(); }, 150);
    b.accion(); this.dibujar();
    return true;
  }

  /** Redibuja (barato: llamar cada ~250 ms o al cambiar algo). */
  dibujar() {
    const c = this.ctx, W = this.W, bw = (W - 24) / COLS;
    c.clearRect(0, 0, W, this.H);
    c.fillStyle = 'rgba(12,14,18,0.92)';
    redondeado(c, 0, 0, W, this.H, 24); c.fill();
    c.textAlign = 'center'; c.textBaseline = 'middle';
    this.botones.forEach((b, i) => {
      const x = 12 + (i % COLS) * bw, y = 12 + Math.floor(i / COLS) * this.bh;
      const act = b.activo ? b.activo() : false;
      c.fillStyle = this.flash === b.id ? '#ffffff' : act ? '#1f7a4d' : this.hoverId === b.id ? '#3a4a66' : '#262b36';
      redondeado(c, x + 5, y + 5, bw - 10, this.bh - 10, 14); c.fill();
      c.fillStyle = this.flash === b.id ? '#000' : '#e8ecf3';
      c.font = '600 30px system-ui, sans-serif';
      ajustarTexto(c, b.texto(), x + bw / 2, y + this.bh / 2, bw - 22);
    });
    c.textAlign = 'left'; c.textBaseline = 'top';
    c.font = '26px ui-monospace, monospace';
    const y0 = 12 + this.filasBot * this.bh + 10;
    (this.estado() || []).slice(0, 8).forEach((l, i) => {
      c.fillStyle = i === 0 ? '#ffd166' : '#b8c2d6';
      c.fillText(String(l).slice(0, 72), 24, y0 + i * 34);
    });
    this.tex.needsUpdate = true;
    this.ultimoDibujo = performance.now();
  }
}

function redondeado(c, x, y, w, h, r) {
  c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r);
  c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
}
function ajustarTexto(c, t, x, y, maxW) {
  let s = 30;
  while (s > 16 && c.measureText(t).width > maxW) { s -= 2; c.font = `600 ${s}px system-ui, sans-serif`; }
  c.fillText(t, x, y);
}
