// Capturas headless con el Chrome instalado (sin descargar navegadores de Playwright).
// Uso: node scripts/shots.mjs [baseURL] [solo1,solo2]
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

const base = process.argv[2] ?? "http://127.0.0.1:9301";
const solo = process.argv[3]?.split(",");
const out = new URL("../propuestas/", import.meta.url).pathname;
mkdirSync(out, { recursive: true });

const tomas = [
  { nombre: "a-1440", ruta: "/a", w: 1440, h: 900 },
  { nombre: "b-1440", ruta: "/b", w: 1440, h: 900 },
  { nombre: "c-1440", ruta: "/c", w: 1440, h: 900 },
  { nombre: "home-1440", ruta: "/", w: 1440, h: 900 },
  { nombre: "home-390", ruta: "/", w: 390, h: 844, movil: true },
  { nombre: "home-scroll-1440", ruta: "/", w: 1440, h: 900, scroll: true },
  { nombre: "home-completa-1440", ruta: "/", w: 1440, h: 900, completa: true },
  { nombre: "home-completa-390", ruta: "/", w: 390, h: 844, completa: true, movil: true },
].filter((t) => !solo || solo.includes(t.nombre));

const browser = await chromium.launch({ channel: "chrome", headless: true });
for (const t of tomas) {
  const ctx = await browser.newContext({
    viewport: { width: t.w, height: t.h },
    deviceScaleFactor: t.movil ? 2 : 1,
    isMobile: !!t.movil,
    hasTouch: !!t.movil,
  });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.error(`[${t.nombre}] pageerror:`, e.message));
  page.on("console", (m) => m.type() === "error" && console.error(`[${t.nombre}] console:`, m.text()));
  await page.goto(base + t.ruta, { waitUntil: "networkidle" });
  await page.waitForTimeout(1800);
  if (!t.movil) {
    await page.mouse.move(t.w * 0.68, t.h * 0.36, { steps: 12 });
    await page.waitForTimeout(1200);
  }
  if (t.scroll) {
    for (let y = 0; y < 2200; y += 300) {
      await page.mouse.wheel(0, 300);
      await page.waitForTimeout(120);
    }
    await page.waitForTimeout(1500);
  }
  if (t.completa) {
    // Recorre la página para disparar revelados y luego captura todo.
    const alto = await page.evaluate(() => document.documentElement.scrollHeight);
    for (let y = 0; y < alto; y += 500) {
      await page.evaluate((yy) => window.scrollTo(0, yy), y);
      await page.waitForTimeout(90);
    }
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(600);
  }
  await page.screenshot({ path: `${out}${t.nombre}.png`, fullPage: !!t.completa });
  console.log("ok", t.nombre);
  await ctx.close();
}
await browser.close();
