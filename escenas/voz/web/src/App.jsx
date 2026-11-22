import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ConversationProvider,
  useConversationControls,
  useConversationInput,
  useConversationMode,
  useConversationStatus,
} from "@elevenlabs/react";

// La voz de las escenas de WUI.
// - Las client tools llaman al servidor local (/api/tool/...), que habla con el MCP de la sesión de la mesa.
// - El oído de la mesa llega por SSE (/api/eventos/...): «hablar» → mensaje al agente (reacciona), «contexto» →
//   contextual update (lo sabe sin hablar), «escena» → cambió la pestaña (si «seguir pestaña» está activo, cambia de
//   agente y de voz).
// - Modo texto para probar sin micrófono; modo voz (WebRTC, interrupciones) solo si el servidor corre con --voz.

const api = (ruta, cuerpo) =>
  fetch(ruta, cuerpo === undefined ? {} : {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(cuerpo),
  }).then(async (r) => ({ ok: r.ok, ...(await r.json().catch(() => ({}))) }));

const hora = () => new Date().toLocaleTimeString("es-CL", { hour12: false });

export default function App() {
  const [registro, setRegistro] = useState([]);
  const anotar = useCallback((tipo, texto) => {
    setRegistro((r) => [...r.slice(-200), { id: `${Date.now()}-${Math.random()}`, t: hora(), tipo, texto }]);
  }, []);
  return (
    <ConversationProvider
      onMessage={({ message, role, source }) => anotar((role || source) === "user" ? "persona" : "agente", message)}
      onError={(m) => anotar("error", typeof m === "string" ? m : JSON.stringify(m))}
      onInterruption={() => anotar("sistema", "interrumpida: se calla y escucha")}
      onDisconnect={() => anotar("sistema", "conversación cerrada")}
      onConnect={() => anotar("sistema", "conectada")}
    >
      <Voz registro={registro} anotar={anotar} />
    </ConversationProvider>
  );
}

function Voz({ registro, anotar }) {
  const { startSession, endSession, sendUserMessage, sendContextualUpdate, sendUserActivity, changeInputDevice } =
    useConversationControls();
  const { status, message } = useConversationStatus();
  const { mode } = useConversationMode();
  const { isMuted, setMuted } = useConversationInput();
  const [estado, setEstado] = useState(null);
  const [escena, setEscena] = useState(() => new URLSearchParams(location.search).get("escena") || "solar");
  const [modo, setModo] = useState(() => (new URLSearchParams(location.search).get("modo") === "voz" ? "voz" : "texto"));
  const [seguir, setSeguir] = useState(true);
  const [texto, setTexto] = useState("");
  // Micrófono: en la mesa de SAT es el de la C920 lateral (cam-3). `?mic=C920` lo elige por nombre.
  const pedirMic = new URLSearchParams(location.search).get("mic") || "";
  const [mics, setMics] = useState([]);
  const [mic, setMic] = useState("");
  const leerMics = useCallback(async (pedirPermiso = false) => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    if (pedirPermiso) {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true }).catch(() => null);
      s?.getTracks().forEach((t) => t.stop());
    }
    const lista = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput" && d.deviceId);
    setMics(lista.map((d) => ({ id: d.deviceId, nombre: d.label || "micrófono sin nombre (falta permiso)" })));
    if (pedirMic) {
      const m = lista.find((d) => d.label.toLowerCase().includes(pedirMic.toLowerCase()));
      if (m) setMic(m.deviceId);
    }
  }, [pedirMic]);
  useEffect(() => { leerMics(false); }, [leerMics]);
  const escenaRef = useRef(escena);
  const modoHablaRef = useRef(mode);
  const pendientes = useRef([]);
  const conectado = status === "connected";
  const conectadoRef = useRef(conectado);
  conectadoRef.current = conectado;
  modoHablaRef.current = mode;
  escenaRef.current = escena;

  useEffect(() => {
    api("/api/estado").then(setEstado);
    const id = setInterval(() => api("/api/estado").then(setEstado), 5000);
    return () => clearInterval(id);
  }, []);

  // Las client tools: el mismo nombre que en el agente, la ejecución en el servidor local.
  const clientTools = useMemo(() => {
    const t = {};
    for (const nombre of estado?.herramientas || []) {
      t[nombre] = async (params) => {
        const { tool_call_id, ...args } = params || {};
        anotar("tool", `${nombre} ${JSON.stringify(args)}`);
        const r = await api(`/api/tool/${escenaRef.current}/${nombre}`, args);
        const res = r.resultado ?? r.error ?? "sin respuesta";
        anotar("resultado", res);
        return res;
      };
    }
    return t;
  }, [estado?.herramientas?.join(","), anotar]);

  const iniciar = useCallback(async (clave = escenaRef.current, como = modo) => {
    const r = await api(`/api/sesion/${clave}`, { modo: como });
    if (!r.ok) return anotar("error", r.error || "no pude abrir la sesión");
    anotar("sistema", `${r.agente} · ${como === "voz" ? "voz (WebRTC)" : "texto"}`);
    const comun = { clientTools, onUnhandledClientToolCall: (c) => anotar("error", `tool sin manejar: ${c?.tool_name}`) };
    const micro = mic ? { inputDeviceId: mic } : {};
    const texto = { textOnly: true, overrides: { conversation: { textOnly: true } } };
    if (r.agentId) {   // agente público: sin llave en este computador
      if (como === "voz") startSession({ ...comun, agentId: r.agentId, connectionType: "webrtc", ...micro });
      else startSession({ ...comun, agentId: r.agentId, connectionType: "websocket", ...texto });
    } else if (como === "voz") startSession({ ...comun, conversationToken: r.conversationToken, connectionType: "webrtc", ...micro });
    else startSession({ ...comun, signedUrl: r.signedUrl, connectionType: "websocket", ...texto });
  }, [clientTools, modo, mic, startSession, anotar]);

  const cambiarEscena = useCallback(async (clave, motivo) => {
    if (clave === escenaRef.current) return;
    setEscena(clave);
    escenaRef.current = clave;
    if (conectadoRef.current) {
      anotar("sistema", `cambio a ${clave}${motivo ? ` (${motivo})` : ""}`);
      endSession();
      setTimeout(() => iniciar(clave), 900);
    }
  }, [endSession, iniciar, anotar]);

  // Mensajes «[mesa] …» en espera: no se le habla encima a la persona ni al agente, salvo lo urgente.
  const vaciar = useCallback(() => {
    if (!conectadoRef.current || modoHablaRef.current === "speaking") return;
    const m = pendientes.current.shift();
    if (m) sendUserMessage(m);
  }, [sendUserMessage]);
  useEffect(() => { if (mode === "listening") setTimeout(vaciar, 400); }, [mode, vaciar]);

  // El oído de la mesa.
  useEffect(() => {
    const es = new EventSource(`/api/eventos/${escena}`);
    es.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      anotar("mesa", `${m.tipo}: ${m.texto || m.pestana || ""}`);
      if (m.tipo === "escena") {
        const destino = (estado?.escenas || []).find((e) => e.pestana === m.pestana);
        if (seguir && destino) return cambiarEscena(destino.clave, `la mesa cambió a ${m.pestana}`);
        if (conectadoRef.current) sendContextualUpdate(m.texto);
        return;
      }
      if (!conectadoRef.current) return;
      if (m.tipo === "contexto") return sendContextualUpdate(m.texto);
      if (m.urgente || modoHablaRef.current !== "speaking") return sendUserMessage(m.texto);
      pendientes.current.push(m.texto);
      setTimeout(vaciar, 6000);
    };
    return () => es.close();
  }, [escena, seguir, estado?.escenas?.length, cambiarEscena, sendContextualUpdate, sendUserMessage, vaciar, anotar]);

  // Tecla M: silencia o abre el micrófono (para narrar la demo sin que el agente lo tome como un turno).
  useEffect(() => {
    const f = (e) => {
      if (e.key.toLowerCase() === "m" && e.target.tagName !== "INPUT" && conectadoRef.current && modo === "voz")
        setMuted(!isMuted);
    };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, [isMuted, setMuted, modo]);

  const enviar = (e) => {
    e.preventDefault();
    if (!texto.trim() || !conectado) return;
    sendUserMessage(texto.trim());
    setTexto("");
  };

  const esc = (estado?.escenas || []).find((e) => e.clave === escena);
  const mesa = estado?.mesa || {};
  return (
    <main>
      <header>
        <div>
          <h1>WUI · Voz</h1>
          <p className="tenue">
            Mesa {mesa.gateway || "…"} · {mesa.sesion ? "sesión lista" : "sin sesión"} · oído: {mesa.oido || "…"}
            {mesa.motor?.length ? " + motor" : ""}
          </p>
        </div>
        <div className={`orbe ${conectado ? mode : status}`} title={status}>
          <span>{conectado ? (mode === "speaking" ? "hablando" : "escuchando") : status === "connecting" ? "conectando" : "apagada"}</span>
        </div>
      </header>

      <section className="controles">
        <div className="escenas">
          {(estado?.escenas || []).map((e) => (
            <button key={e.clave} className={e.clave === escena ? "activa" : ""} disabled={!e.publicado}
                    onClick={() => cambiarEscena(e.clave)}>
              {e.agente.replace("Superhuman Desk · ", "")}
              <small>{e.pestana}</small>
            </button>
          ))}
        </div>
        <div className="fila">
          <label><input type="radio" checked={modo === "texto"} onChange={() => setModo("texto")} disabled={conectado} /> Texto</label>
          <label title={estado?.voz_permitida ? "" : "El micrófono necesita el OK de Max: servir --voz"}>
            <input type="radio" checked={modo === "voz"} onChange={() => setModo("voz")}
                   disabled={conectado || !estado?.voz_permitida} /> Voz {estado?.voz_permitida ? "" : "(bloqueada)"}
          </label>
          <label><input type="checkbox" checked={seguir} onChange={(e) => setSeguir(e.target.checked)} /> Seguir la pestaña</label>
          {conectado && modo === "voz" && (
            <button className={isMuted ? "mudo" : ""} onClick={() => setMuted(!isMuted)}>
              {isMuted ? "Micrófono silenciado (M)" : "Micrófono abierto (M)"}
            </button>
          )}
          {conectado || status === "connecting"
            ? <button className="parar" onClick={() => endSession()}>Cortar</button>
            : <button className="partir" onClick={() => iniciar()} disabled={!esc?.publicado}>Hablar con {esc?.agente.replace("Superhuman Desk · ", "") || "…"}</button>}
        </div>
        {modo === "voz" && (
          <div className="fila">
            <label>Micrófono
              <select value={mic} onChange={(e) => {
                setMic(e.target.value);
                if (conectado) changeInputDevice({ inputDeviceId: e.target.value }).catch((er) => anotar("error", String(er)));
              }}>
                <option value="">El del sistema</option>
                {mics.map((m) => <option key={m.id} value={m.id}>{m.nombre}</option>)}
              </select>
            </label>
            <button onClick={() => leerMics(true)}>Ver nombres de micrófonos</button>
            {pedirMic && <span className="tenue">pedido: «{pedirMic}» {mic ? "✓" : "(no encontrado aún)"}</span>}
          </div>
        )}
        {message && <p className="error">{message}</p>}
      </section>

      <form className="escribir" onSubmit={enviar}>
        <input value={texto} onChange={(e) => { setTexto(e.target.value); if (conectado) sendUserActivity(); }}
               placeholder={conectado ? "Escríbele (en modo voz también sirve)" : "Parte la conversación primero"} />
        <button disabled={!conectado}>Enviar</button>
      </form>

      <ol className="registro">
        {[...registro].reverse().map((r) => (
          <li key={r.id} className={r.tipo}><time>{r.t}</time><b>{r.tipo}</b><span>{r.texto}</span></li>
        ))}
      </ol>
    </main>
  );
}
