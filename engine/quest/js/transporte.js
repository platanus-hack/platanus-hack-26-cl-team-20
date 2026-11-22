// Transporte de la vista proyector: canvas → pista de video → WebRTC → proyector.html.
// Señalización: WebSocket de server.py (por defecto) o PeerJS cloud (?senal=peerjs, para la versión sin Mac).

// ───────────────────────── fuente de video ─────────────────────────

/**
 * Pista de video desde el canvas del proyector.
 * 'generador': MediaStreamTrackGenerator + VideoFrame(canvas) → un cuadro por render, no depende del ciclo de
 *   pintado de la página (en el casco la página 2D queda oculta durante la sesión inmersiva).
 * 'captura': canvas.captureStream(fps) (estándar; puede congelarse si el navegador no pinta la página).
 */
export function fuenteVideo(canvas, { fps = 30, preferencia = 'auto', hint = 'motion' } = {}) {
  const hayGen = typeof window.MediaStreamTrackGenerator === 'function' && typeof window.VideoFrame === 'function';
  if ((preferencia === 'auto' || preferencia === 'generador') && hayGen) {
    const track = new window.MediaStreamTrackGenerator({ kind: 'video' });
    const writer = track.writable.getWriter();
    let pendiente = false, enviados = 0, saltados = 0;
    try { track.contentHint = hint; } catch { /* opcional */ }
    return {
      tipo: 'generador', track, stream: new MediaStream([track]),
      empujar(tMs) {
        if (pendiente) { saltados++; return false; }
        let vf;
        try { vf = new window.VideoFrame(canvas, { timestamp: Math.round(tMs * 1000) }); } catch (e) { saltados++; return false; }
        pendiente = true;
        writer.write(vf).then(() => { pendiente = false; enviados++; }, () => { pendiente = false; });
        return true;
      },
      get enviados() { return enviados; }, get saltados() { return saltados; },
    };
  }
  const stream = canvas.captureStream(fps);
  const track = stream.getVideoTracks()[0];
  try { track.contentHint = hint; } catch { /* opcional */ }
  let enviados = 0;
  return {
    tipo: 'captura', track, stream,
    empujar() { enviados++; if (typeof track.requestFrame === 'function' && fps === 0) track.requestFrame(); return true; },
    get enviados() { return enviados; }, saltados: 0,
  };
}

// ───────────────────────── SDP / códecs ─────────────────────────

/** Ordena los códecs del transceiver: H.264 primero (perfil constrained baseline / packetization-mode=1). */
export function preferirH264(transceiver) {
  if (!transceiver.setCodecPreferences || !window.RTCRtpSender?.getCapabilities) return false;
  const caps = RTCRtpSender.getCapabilities('video')?.codecs || [];
  const h264 = caps.filter((c) => /h264/i.test(c.mimeType));
  if (!h264.length) return false;
  h264.sort((a, b) => score(b) - score(a));
  const resto = caps.filter((c) => !/h264/i.test(c.mimeType));
  try { transceiver.setCodecPreferences([...h264, ...resto]); return true; } catch { return false; }
  function score(c) {
    const f = c.sdpFmtpLine || '';
    return (/packetization-mode=1/.test(f) ? 2 : 0) + (/profile-level-id=42e0/.test(f) ? 1 : 0);
  }
}

/** Mueve los payloads H.264 al frente de la línea m=video (para PeerJS, que no expone setCodecPreferences). */
export function sdpH264Primero(sdp) {
  const lineas = sdp.split('\r\n');
  const im = lineas.findIndex((l) => l.startsWith('m=video'));
  if (im < 0) return sdp;
  const pts = lineas.filter((l) => /^a=rtpmap:\d+ H264\//i.test(l)).map((l) => l.match(/^a=rtpmap:(\d+)/)[1]);
  if (!pts.length) return sdp;
  const p = lineas[im].split(' ');
  const cab = p.slice(0, 3), lista = p.slice(3);
  lineas[im] = [...cab, ...pts.filter((x) => lista.includes(x)), ...lista.filter((x) => !pts.includes(x))].join(' ');
  return lineas.join('\r\n');
}

/** Bitrate inicial/mínimo alto en el SDP remoto (Chrome parte en ~300 kbps y sube lento). */
export function sdpBitrate(sdp, kbps) {
  return sdp.replace(/^(a=fmtp:\d+ .*)$/gm, (l) => (/x-google-/.test(l) || /apt=/.test(l) ? l
    : `${l};x-google-start-bitrate=${Math.round(kbps * 0.8)};x-google-min-bitrate=${Math.round(kbps * 0.5)};x-google-max-bitrate=${kbps}`));
}

async function ajustarEmisor(sender, { bitrate, fps }) {
  const p = sender.getParameters();
  if (!p.encodings || !p.encodings.length) p.encodings = [{}];
  p.encodings[0].maxBitrate = bitrate;
  p.encodings[0].maxFramerate = fps;
  p.encodings[0].scaleResolutionDownBy = 1;
  p.encodings[0].priority = 'high';
  p.encodings[0].networkPriority = 'high';
  p.degradationPreference = 'maintain-framerate';
  try { await sender.setParameters(p); } catch (e) { console.warn('setParameters', e); }
}

export function bajaLatencia(receiver) {
  try { receiver.playoutDelayHint = 0; } catch { /* Chrome antiguo */ }
  try { receiver.jitterBufferTarget = 0; } catch { /* estándar */ }
}

// ───────────────────────── señalización WebSocket ─────────────────────────

export class Senal {
  constructor({ url, rol, sala = 'sala' }) {
    this.url = url; this.rol = rol; this.sala = sala;
    this.id = null; this.peers = new Map(); this.h = {}; this.abierta = false;
    this.conectar();
  }
  on(t, fn) { (this.h[t] ||= []).push(fn); return this; }
  emit(t, ...a) { (this.h[t] || []).forEach((fn) => fn(...a)); }
  conectar() {
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => { this.abierta = true; ws.send(JSON.stringify({ t: 'hello', rol: this.rol, sala: this.sala })); };
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.t === 'welcome') {
        this.id = m.id; this.peers = new Map(m.peers.map((p) => [p.id, p.rol]));
        this.emit('estado', 'conectada');
        m.peers.forEach((p) => this.emit('join', p.id, p.rol));
      } else if (m.t === 'join') { this.peers.set(m.id, m.rol); this.emit('join', m.id, m.rol); }
      else if (m.t === 'leave') { this.peers.delete(m.id); this.emit('leave', m.id); }
      else if (m.t === 'signal') this.emit('signal', m.from, m.data);
    };
    ws.onclose = () => {
      this.abierta = false; this.emit('estado', 'desconectada');
      [...this.peers.keys()].forEach((id) => this.emit('leave', id));
      this.peers.clear();
      setTimeout(() => this.conectar(), 1500);
    };
    ws.onerror = () => { /* onclose reintenta */ };
  }
  enviar(to, data) { if (this.ws?.readyState === 1) this.ws.send(JSON.stringify({ t: 'signal', to, data })); }
}

export function urlSenalPorDefecto() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}/ws`;
}

// ───────────────────────── emisor (casco) ─────────────────────────

export class Emisor {
  /** Envía la pista a cada proyector que entra a la sala (uno o varios, p. ej. el proyector y un monitor). */
  constructor({ senal, fuente, bitrate = 12e6, fps = 30, iceServers = [], log = console.log }) {
    Object.assign(this, { senal, fuente, bitrate, fps, iceServers, log });
    this.pcs = new Map();
    this.ultimoStats = {};
    senal.on('join', (id, rol) => { if (rol === 'proyector') this.llamar(id); });
    senal.on('leave', (id) => this.cerrar(id));
    senal.on('signal', (from, d) => this.alSenal(from, d));
  }
  async llamar(id) {
    this.cerrar(id);
    const pc = new RTCPeerConnection({ iceServers: this.iceServers });
    pc._n = (this.n = (this.n || 0) + 1);
    this.pcs.set(id, pc);
    const tr = pc.addTransceiver(this.fuente.track, { direction: 'sendonly', streams: [this.fuente.stream] });
    const h264 = preferirH264(tr);
    pc.onicecandidate = (e) => e.candidate && this.senal.enviar(id, { ice: e.candidate.toJSON(), n: pc._n });
    pc.onconnectionstatechange = () => {
      this.log(`webrtc → ${id}: ${pc.connectionState}`);
      if (pc.connectionState === 'failed') setTimeout(() => this.senal.peers.has(id) && this.llamar(id), 1000);
    };
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await ajustarEmisor(tr.sender, this);
    this.senal.enviar(id, { sdp: pc.localDescription.toJSON(), n: pc._n });
    this.log(`oferta a proyector ${id} (H.264 primero: ${h264 ? 'sí' : 'no'}, fuente ${this.fuente.tipo})`);
  }
  async alSenal(from, d) {
    const pc = this.pcs.get(from);
    if (d.pedir) {
      if (!pc || ['failed', 'closed', 'disconnected'].includes(pc.connectionState)) this.llamar(from);
      return;
    }
    if (!pc || (d.n && d.n !== pc._n)) return; // respuesta a una oferta vieja
    if (d.sdp && d.sdp.type === 'answer') {
      await pc.setRemoteDescription({ type: 'answer', sdp: sdpBitrate(d.sdp.sdp, Math.round(this.bitrate / 1000)) });
      const tr = pc.getTransceivers()[0];
      await ajustarEmisor(tr.sender, this);
    } else if (d.ice) {
      try { await pc.addIceCandidate(d.ice); } catch (e) { /* candidato tardío */ }
    }
  }
  cerrar(id) { const pc = this.pcs.get(id); if (pc) { pc.close(); this.pcs.delete(id); } }
  get conectados() { return [...this.pcs.values()].filter((p) => p.connectionState === 'connected').length; }
  async stats() {
    const out = [];
    for (const [id, pc] of this.pcs) {
      const r = await pc.getStats();
      let o = { id, estado: pc.connectionState };
      r.forEach((s) => {
        if (s.type === 'outbound-rtp' && s.kind === 'video') {
          const prev = this.ultimoStats[id];
          const dt = prev ? (s.timestamp - prev.timestamp) / 1000 : 0;
          o = { ...o, fps: s.framesPerSecond, w: s.frameWidth, h: s.frameHeight,
            kbps: prev && dt > 0 ? Math.round(((s.bytesSent - prev.bytesSent) * 8) / dt / 1000) : null,
            encMs: s.framesEncoded ? Math.round((s.totalEncodeTime / s.framesEncoded) * 1000 * 10) / 10 : null,
            limit: s.qualityLimitationReason, codecId: s.codecId };
          this.ultimoStats[id] = s;
        }
        if (s.type === 'candidate-pair' && s.nominated && s.state === 'succeeded') o.rttMs = Math.round((s.currentRoundTripTime || 0) * 1000);
      });
      if (o.codecId) { const c = r.get(o.codecId); o.codec = c?.mimeType; delete o.codecId; }
      out.push(o);
    }
    return out;
  }
}

// ───────────────────────── receptor (proyector) ─────────────────────────

export class Receptor {
  constructor({ senal, video, iceServers = [], log = console.log }) {
    Object.assign(this, { senal, video, iceServers, log });
    this.pc = null; this.de = null;
    senal.on('signal', (from, d) => this.alSenal(from, d));
    senal.on('leave', (id) => { if (id === this.de) { this.log('el casco salió'); this.cerrar(); } });
    // el casco inicia siempre; si en 4 s no hay conexión, se la pedimos de nuevo (recupera fallas)
    setInterval(() => {
      const ok = this.pc && ['connected', 'connecting', 'new'].includes(this.pc.connectionState);
      if (ok) return;
      for (const [id, rol] of senal.peers) if (rol === 'quest') senal.enviar(id, { pedir: 1 });
    }, 4000);
  }
  cerrar() { if (this.pc) { this.pc.close(); this.pc = null; this.de = null; } this.video.srcObject = null; }
  async alSenal(from, d) {
    if (d.sdp && d.sdp.type === 'offer') {
      this.cerrar();
      this.de = from; this.n = d.n;
      const pc = new RTCPeerConnection({ iceServers: this.iceServers });
      this.pc = pc;
      pc.onicecandidate = (e) => e.candidate && this.senal.enviar(from, { ice: e.candidate.toJSON(), n: d.n });
      pc.ontrack = (e) => {
        bajaLatencia(e.receiver);
        this.video.srcObject = e.streams[0] || new MediaStream([e.track]);
        this.video.play().catch(() => {});
      };
      pc.onconnectionstatechange = () => this.log(`webrtc ← ${from}: ${pc.connectionState}`);
      await pc.setRemoteDescription(d.sdp);
      pc.getReceivers().forEach(bajaLatencia);
      const ans = await pc.createAnswer();
      await pc.setLocalDescription(ans);
      this.senal.enviar(from, { sdp: pc.localDescription.toJSON(), n: d.n });
    } else if (d.ice && this.pc && from === this.de && (!d.n || d.n === this.n)) {
      try { await this.pc.addIceCandidate(d.ice); } catch { /* tardío */ }
    }
  }
  async stats() {
    if (!this.pc) return null;
    const r = await this.pc.getStats();
    let o = { estado: this.pc.connectionState };
    r.forEach((s) => {
      if (s.type === 'inbound-rtp' && s.kind === 'video') {
        const prev = this.prev;
        o = { ...o, fps: s.framesPerSecond, w: s.frameWidth, h: s.frameHeight, perdidos: s.packetsLost, caidos: s.framesDropped,
          jitterMs: s.jitterBufferEmittedCount ? Math.round((s.jitterBufferDelay / s.jitterBufferEmittedCount) * 1000) : null,
          decMs: s.framesDecoded ? Math.round((s.totalDecodeTime / s.framesDecoded) * 1000 * 10) / 10 : null,
          kbps: prev ? Math.round(((s.bytesReceived - prev.bytesReceived) * 8) / ((s.timestamp - prev.timestamp) / 1000) / 1000) : null,
          codecId: s.codecId, decoder: s.decoderImplementation };
        this.prev = s;
      }
      if (s.type === 'candidate-pair' && s.nominated && s.state === 'succeeded') o.rttMs = Math.round((s.currentRoundTripTime || 0) * 1000);
    });
    if (o.codecId) { o.codec = r.get(o.codecId)?.mimeType; delete o.codecId; }
    return o;
  }
}

// ───────────────────────── PeerJS cloud (sin Mac, experimental) ─────────────────────────

const PEERJS_URL = 'https://cdn.jsdelivr.net/npm/peerjs@1.5.4/dist/peerjs.min.js';
export async function cargarPeerJS() {
  if (window.Peer) return window.Peer;
  await new Promise((ok, mal) => {
    const s = document.createElement('script');
    s.src = PEERJS_URL; s.onload = ok; s.onerror = () => mal(new Error('no cargó PeerJS'));
    document.head.appendChild(s);
  });
  return window.Peer;
}
export const idPeer = (sala) => `smartup-proyector-${sala}`.replace(/[^A-Za-z0-9_-]/g, '-');

/** Casco: llama al id fijo del proyector en PeerJS cloud; reintenta cada 3 s hasta que conteste. */
export async function emisorPeerJS({ sala, fuente, bitrate = 12e6, fps = 30, log = console.log }) {
  const Peer = await cargarPeerJS();
  const peer = new Peer({ config: { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] } });
  const estado = { conectados: 0, call: null };
  const llamar = () => {
    const call = peer.call(idPeer(sala), fuente.stream, { sdpTransform: (s) => sdpH264Primero(s) });
    estado.call = call;
    call.peerConnection.onconnectionstatechange = async () => {
      const st = call.peerConnection.connectionState;
      log(`peerjs → proyector: ${st}`);
      estado.conectados = st === 'connected' ? 1 : 0;
      if (st === 'connected') call.peerConnection.getSenders().forEach((s) => ajustarEmisor(s, { bitrate, fps }));
      if (st === 'failed' || st === 'closed') setTimeout(llamar, 2000);
    };
    call.on('error', () => setTimeout(llamar, 3000));
  };
  peer.on('open', (id) => { log(`peerjs abierto (${id}), llamando a ${idPeer(sala)}`); llamar(); });
  peer.on('error', (e) => { log(`peerjs error: ${e.type}`); if (e.type === 'peer-unavailable') setTimeout(llamar, 3000); });
  return estado;
}

/** Proyector: se registra con el id fijo de la sala y contesta llamadas. */
export async function receptorPeerJS({ sala, video, log = console.log }) {
  const Peer = await cargarPeerJS();
  const peer = new Peer(idPeer(sala), { config: { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] } });
  const estado = { pc: null };
  peer.on('open', (id) => log(`peerjs: esperando al casco como ${id}`));
  peer.on('error', (e) => log(`peerjs error: ${e.type}`));
  peer.on('call', (call) => {
    call.answer(undefined, { sdpTransform: (s) => sdpH264Primero(s) });
    estado.pc = call.peerConnection;
    call.on('stream', (st) => {
      call.peerConnection.getReceivers().forEach(bajaLatencia);
      video.srcObject = st; video.play().catch(() => {});
      log('peerjs: video del casco recibido');
    });
  });
  return estado;
}
