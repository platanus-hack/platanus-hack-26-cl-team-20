// Lenis solo si el usuario no pidió menos movimiento, y cargado después del primer pintado.
export const reducido = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function cuandoOcioso(fn: () => void, timeout = 1200) {
  const ric = (window as any).requestIdleCallback as undefined | ((cb: () => void, o?: { timeout: number }) => number);
  if (ric) ric(fn, { timeout });
  else setTimeout(fn, 200);
}

export function iniciarScrollSuave() {
  if (reducido()) return;
  cuandoOcioso(async () => {
    const { default: Lenis } = await import("lenis");
    const lenis = new Lenis({ lerp: 0.12, smoothWheel: true });
    (window as any).__lenis = lenis;
    const raf = (t: number) => {
      lenis.raf(t);
      requestAnimationFrame(raf);
    };
    requestAnimationFrame(raf);
    document.querySelectorAll<HTMLAnchorElement>('a[href^="#"]').forEach((a) => {
      a.addEventListener("click", (e) => {
        const id = a.getAttribute("href")!;
        if (id.length < 2) return;
        const el = document.querySelector(id);
        if (!el) return;
        e.preventDefault();
        lenis.scrollTo(el as HTMLElement, { offset: -8 });
        history.replaceState(null, "", id);
        (el as HTMLElement).focus?.({ preventScroll: true });
      });
    });
  });
}

// Bucle de animación que se pausa fuera de pantalla y con la pestaña oculta.
export function bucleVisible(el: Element, paso: (t: number, dt: number) => void) {
  let visible = false;
  let id = 0;
  let prev = 0;
  const tick = (t: number) => {
    const dt = prev ? Math.min(64, t - prev) : 16;
    prev = t;
    paso(t, dt);
    id = requestAnimationFrame(tick);
  };
  const arrancar = () => {
    if (!id && visible && !document.hidden) {
      prev = 0;
      id = requestAnimationFrame(tick);
    }
  };
  const parar = () => {
    cancelAnimationFrame(id);
    id = 0;
  };
  new IntersectionObserver(([e]) => {
    visible = e.isIntersecting;
    visible ? arrancar() : parar();
  }).observe(el);
  document.addEventListener("visibilitychange", () => (document.hidden ? parar() : arrancar()));
}
