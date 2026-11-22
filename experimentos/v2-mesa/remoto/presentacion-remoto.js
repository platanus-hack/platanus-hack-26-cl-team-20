// Remoto de la presentación WUI: recibe el POV de Pablo por WebRTC y los comandos del control.
// Corre dentro de presentacion.html. Si el servidor (remoto/servidor.py) no está, no hace nada visible.
(function () {
  'use strict';
  if (!window.WUI || window.__wuiRemoto) return;
  window.__wuiRemoto = true;
  const BASE = window.WUI_REMOTO_URL || 'http://127.0.0.1:9330';
  const SALA = window.WUI_REMOTO_SALA || 'demo';
  const url = (p, extra) => `${BASE}/api/${p}?sala=${encodeURIComponent(SALA)}&de=pres${extra || ''}`;
  const dormir = ms => new Promise(r => setTimeout(r, ms));
  const R = { pc: null, stream: null, sid: null, estadoPov: 'sin cámara', servidor: false };
  window.WUI_REMOTO = R;

  async function enviar(a, m) {
    try { await fetch(url('enviar', '&a=' + a), { method: 'POST', body: JSON.stringify(m) }); } catch (e) {}
  }

  function iceListo(pc, ms) {
    return new Promise(res => {
      if (pc.iceGatheringState === 'complete') return res();
      const t = setTimeout(res, ms);
      pc.addEventListener('icegatheringstatechange', () => { if (pc.iceGatheringState === 'complete') { clearTimeout(t); res(); } });
    });
  }

  function poner(stream) {
    try {
      window.WUI.ponerPov(stream);
      const v = document.getElementById('v-pov');
      if (v && v.play) v.play().catch(() => {});
    } catch (e) { console.warn('[remoto] ponerPov', e); }
  }

  async function responder(m) {
    if (R.pc) { try { R.pc.close(); } catch (e) {} }
    const pc = R.pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
    R.sid = m.sid; R.estadoPov = 'conectando';
    pc.ontrack = ev => {
      R.stream = ev.streams[0] || new MediaStream([ev.track]);
      poner(R.stream);
    };
    pc.onconnectionstatechange = () => {
      if (R.pc !== pc) return;
      const s = pc.connectionState;
      R.estadoPov = s === 'connected' ? 'en vivo' : s === 'connecting' || s === 'new' ? 'conectando' : 'caída';
      console.log('[remoto] pov', s);
    };
    await pc.setRemoteDescription({ type: 'offer', sdp: m.sdp });
    await pc.setLocalDescription(await pc.createAnswer());
    await iceListo(pc, 2500);
    if (R.pc !== pc) return;
    await enviar('pov', { tipo: 'respuesta', sid: m.sid, sdp: pc.localDescription.sdp });
  }

  function comando(c, arg) {
    const W = window.WUI;
    if (c === 'siguiente') W.siguiente();
    else if (c === 'anterior') W.anterior();
    else if (c === 'pausa') W.pausa();
    else if (c === 'reiniciar') W.reiniciar();
    else if (c === 'ir') W.ir(Number(arg));
    else if (c === 'fin' || c === 'preparar' || c === 'dale') W.ctl(c);
    else return;
    console.log('[remoto] cmd', c);
    setTimeout(publicar, 60);
  }

  const texto = id => { const e = document.getElementById(id); return e ? e.textContent : ''; };
  async function publicar() {
    let e;
    try { e = window.WUI.estado(); } catch (x) { return; }
    e.pov = R.estadoPov; e.escena = texto('c-e'); e.prep = texto('b-prep');
    try {
      const r = await fetch(url('estado'), { method: 'POST', body: JSON.stringify(e) });
      R.servidor = r.ok;
    } catch (x) { R.servidor = false; }
  }
  (async function latido() {
    for (;;) { await publicar(); await dormir(R.servidor ? 1000 : 5000); }
  })();

  // Si otra cosa pisa el <video> del POV (el selector de cámara al cargar), lo volvemos a poner
  // mientras el selector esté en «(ninguna)». Si el track remoto fue detenido, pedimos una oferta nueva.
  setInterval(() => {
    if (!R.stream || !R.pc || R.pc.connectionState !== 'connected') return;
    const v = document.getElementById('v-pov'), s = document.getElementById('s-pov');
    if (!v || (s && s.value)) return;
    const vivo = R.stream.getVideoTracks().some(t => t.readyState === 'live');
    if (!vivo) { R.stream = null; R.estadoPov = 'caída'; enviar('pov', { tipo: 'hola' }); return; }
    if (v.srcObject !== R.stream) poner(R.stream);
  }, 1500);

  (async function escuchar() {
    let cursor = -1, saludado = false;
    for (;;) {
      try {
        const ab = new AbortController(), t = setTimeout(() => ab.abort(), 40000);
        const r = await fetch(url('poll', `&rol=pres&desde=${cursor}`), { signal: ab.signal });
        clearTimeout(t);
        if (!r.ok) throw new Error('http ' + r.status);
        const j = await r.json();
        cursor = j.cursor;
        if (!saludado) { saludado = true; enviar('pov', { tipo: 'hola' }); }
        for (const x of j.msgs) {
          const m = x.m || {};
          if (j.ahora - x.ts > 6) continue; // mensajes viejos (p. ej. tras una caída de red): se ignoran
          if (m.tipo === 'oferta') responder(m).catch(e => console.warn('[remoto] oferta', e));
          else if (m.tipo === 'cmd') { try { comando(m.cmd, m.arg); } catch (e) { console.warn('[remoto] cmd', e); } }
        }
      } catch (e) {
        saludado = false; cursor = -1;
        await dormir(3000);
      }
    }
  })();
})();
