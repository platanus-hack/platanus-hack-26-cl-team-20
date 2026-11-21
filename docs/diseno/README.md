# Diseño (landing y consola) · pendiente de decisión del equipo

**Estado:** investigación lista; decisiones de paleta, tipografía y stack **pendientes** (las toma el equipo de diseño). No se avanza UI hasta decidir.

| Archivo | Qué es |
|---|---|
| [INVESTIGACION-LANDING.md](INVESTIGACION-LANDING.md) | Investigación completa: reglas anti-«AI slop» (resumen del skill `frontend-design` de Anthropic y otras guías), 12 referencias, stack 2026 con versiones y pesos, tipografía y color, rendimiento y accesibilidad con animación, 3 direcciones de hero y brief para el constructor |
| `web/` | Prototipo exploratorio (Astro): heroes `/a` `/b` `/c`, tema cálido/frío comparables, Lighthouse 99–100. **No es la decisión final**; sirve para comparar en vivo |

## Opciones a decidir
- **Paleta:** cálida (#0b0c0a / #f1eee4 / naranjo #ff5b24) vs fría «cuarto oscuro» (#161b20 / #edf5f9 / #f8ac3d).
- **Tipografía:** Archivo + Martian Mono vs Host Grotesk + Departure Mono (todas OFL, autohospedadas).
- **Hero:** A «mesa viva», B «instrumento» (calibración), o B → A (recomendado por la investigación).
- **Stack:** Astro + Tailwind v4 + GSAP + WebGL crudo/OGL (prototipo actual) vs Next.js.

## Reglas que ya están acordadas
Sin gradientes morado-azul, glass ni texto con gradiente; sin Inter/Geist; sin tarjetas iguales con íconos ni emojis; un solo momento animado orquestado; `prefers-reduced-motion` + botón de pausa; LCP < 2 s, CLS ≈ 0; datos reales (no inventados).
