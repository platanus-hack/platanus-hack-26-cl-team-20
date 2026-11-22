// Instrument Sans via a local asset. The sandboxed widgets receive a data URL so their CSP can stay offline.
export const SANS = '"Instrument Sans", system-ui, sans-serif';
export const LIGHT = "#F4F4F2";
export const INK = "#171717";
export const DIM = "#B3B3B1";

const font = new URL("./brand/instrument-sans.woff2", import.meta.url);
export function loadWuiFont() {
  const face = new FontFace("Instrument Sans", `url(${font})`, { weight: "400 700" });
  document.fonts.add(face);
  face.load().catch(() => {}); // el canvas sigue con la fuente del sistema si el recurso no carga
}

let iframeFont;
export function widgetFont() {
  return iframeFont ??= (async () => {
    try {
      const response = await fetch(font, { signal: AbortSignal.timeout(1800) });
      if (!response.ok) return "";
      const bytes = new Uint8Array(await response.arrayBuffer());
      let binary = "";
      for (const byte of bytes) binary += String.fromCharCode(byte);
      return `@font-face{font-family:"Instrument Sans";font-weight:400 700;font-display:swap;src:url(data:font/woff2;base64,${btoa(binary)}) format("woff2")}`;
    } catch {
      return "";
    }
  })();
}
