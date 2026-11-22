// El reloj pertenece a la pista, no a cada actualización de poses del rastreador.
const pistasWui = new Map();
const quietudWui = matchMedia('(prefers-reduced-motion: reduce)');
document.fonts.load('500 14px "WUI Sans"').catch(() => {});

function pistaWui(ctx, canal, clave, lineas, x, y, altoLinea, ancho, ahora) {
  let paso = pistasWui.get(canal);
  if (!paso || paso.clave !== clave) {
    paso = { clave, inicio: ahora };
    pistasWui.set(canal, paso);
  }
  const ms = quietudWui.matches ? 1720 : Math.max(0, ahora - paso.inicio);
  const fraccion = (a, b) => Math.max(0, Math.min(1, (ms - a) / (b - a)));
  const entrar = fraccion(0, 420), revelar = fraccion(420, 1280), asentar = fraccion(880, 1720);
  const guion = Math.min(10, altoLinea * .6);
  const recorrido = guion + Math.min(ancho * .28, 60) * (1 - asentar);
  const base = y - altoLinea * .3;
  ctx.save();
  ctx.strokeStyle = ctx.fillStyle;
  ctx.lineWidth = 1.6;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.moveTo(x, base);
  ctx.bezierCurveTo(x + recorrido * .3 * entrar, base - 2.2 * (1 - asentar),
    x + recorrido * .66 * entrar, base + 2.2 * (1 - asentar), x + recorrido * entrar, base);
  ctx.stroke();
  ctx.textAlign = 'left';
  const inicio = x + guion + 6;
  const util = Math.max(1, ancho - guion - 6);
  if (ms >= 1720) {
    lineas.forEach((linea, i) => ctx.fillText(linea, inicio, y + i * altoLinea, util));
  } else if (revelar > 0) {
    // Seis segmentos desplazan el frente de tinta; la línea recupera su geometría al asentarse.
    const pasoX = util / 6;
    for (let j = 0; j < 6; j++) {
      const avance = Math.min(1, Math.max(0, revelar * 6 - j));
      if (!avance) break;
      const onda = Math.sin((ms - 420) / 165 - j * .65) * 2.2 * (1 - asentar);
      ctx.save();
      ctx.beginPath();
      ctx.rect(inicio + j * pasoX, y - altoLinea, pasoX * avance + .2, (lineas.length + 1) * altoLinea);
      ctx.clip();
      lineas.forEach((linea, i) => ctx.fillText(linea, inicio, y + i * altoLinea + onda, util));
      ctx.restore();
    }
  }
  ctx.restore();
}
