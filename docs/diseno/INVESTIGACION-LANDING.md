# Landing de pcx: diseño, anti-slop, stack y brief

Investigación del 2026-11-21 para la landing de **pcx** (nombre provisorio): contexto físico para agentes de IA. Cámara y proyector entienden una mesa; Claude Code, Codex u OpenCode se conectan por MCP para ver el espacio y proyectar interfaces sobre objetos reales.

Todas las versiones se verificaron con `npm view` el 2026-11-21. Los pesos los medí yo empaquetando cada librería con esbuild (minificado) y comprimiendo con brotli 11 (script en `/tmp/landing-research/scripts/measure.mjs`). Los contrastes de color también los calculé yo (`/tmp/landing-research/scripts/palette.py`).

## 0. Resumen

- **Stack:** Astro 7.4 con TypeScript, Tailwind 4.3, GSAP 3.15 (ScrollTrigger y SplitText, gratis desde abril de 2025), Lenis 1.3 opcional y OGL 1.0 para un shader propio. Fuentes autohospedadas. Si el constructor ya empezó en Next 16.4, que se quede ahí: no conviene migrar en plena hackathon.
- **Anti-slop:** casi todo cabe en una regla: cada decisión visual sale de los materiales del producto (franjas de gray code, marcadores ArUco, el haz del proyector, la mesa, milímetros). No sale de una lista de cosas que evitar ni de la plantilla de la categoría.
- **Hero recomendado:** «la calibración». Un barrido de gray code calibra la mesa y después el puntero pasa a ser el haz del proyector, con widgets anclados a objetos reales. Une las direcciones B y A de la §6.
- **Movimiento:** «que nada se quede quieto» se resuelve con una sola capa ambiental viva y movimiento de scroll que signifique algo. No se resuelve con animaciones de entrada en cada sección.

---

## 1. Anti-AI-slop

### 1.1 Fuentes leídas

| Fuente | Qué es | Estado verificado |
|---|---|---|
| `anthropics/skills` → `skills/frontend-design/SKILL.md` | Skill oficial de Anthropic | Lo descargué. Es **idéntico byte a byte** al del plugin `anthropics/claude-plugins-official/plugins/frontend-design`. Licencia Apache 2.0. |
| `pbakaus/impeccable` | Skill de Paul Bakaus que parte del de Anthropic: 24 comandos y un detector de unos 60 patrones | Leí `SKILL.md` v4.10.0, `craft-floor.md`, `animate.md`, `typeset.md`, `colorize.md` y `mode-persuade.md`. Licencia Apache 2.0. |
| `vercel-labs/web-interface-guidelines` → `AGENTS.md` | Reglas MUST/SHOULD/NEVER de Vercel para agentes | Lo leí completo. |
| Vercel, «Teaching agents product design» (2026-06-25) | Cómo Vercel versiona sus decisiones de diseño como skill, linters y revisión | Lo leí. |
| `Leonxlnx/taste-skill` (v2.5.1) | Skill comunitario de «taste» | Leí el README. |
| Auditorías comunitarias de 2026 | Patrones de «slop» | Están en la lista de fuentes al final. |

### 1.2 Skill oficial `frontend-design` de Anthropic

**No pego el texto completo.** Es material con copyright de un tercero, aunque tenga licencia Apache 2.0, así que lo dejo resumido sección por sección. El constructor debe cargarlo literal antes de diseñar con uno de estos comandos:

```bash
curl -sL https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md
# o en Claude Code:  /plugin install frontend-design@claude-plugins-official
```

Paráfrasis fiel por sección:

1. **Rol.** Trabajar como director de diseño de un estudio cuyo cliente ya rechazó propuestas genéricas. Tomar decisiones opinadas de paleta, tipografía y layout específicas del brief, con riesgo estético si se justifica.
2. **Partir del tema.** La industria, los materiales y el vocabulario del sujeto son la fuente de lo distintivo. Si el brief no dice qué es el producto, proponerlo y confirmarlo. Usar contenido real.
3. **Hero.** Abrir con lo más característico del mundo del sujeto, en la forma que mejor le sirva: titular, imagen, animación, demo en vivo o momento interactivo. El patrón «número grande, etiqueta chica, stats y acento con gradiente» es el *default*: solo vale si de verdad es lo mejor.
4. **Tipografía.** Es lo que da la personalidad. Una o dos familias; si son dos, que se distingan con claridad. Elegirlas con intención, no las de siempre. Escala clara según *The Elements of Typographic Style*. El titular trabaja como elemento de diseño, no como vehículo neutro. Líneas de menos de 80 caracteres; las serif aguantan algo más de largo y piden algo más de interlineado.
5. **Tres delatores tipográficos:** acentuar una sola palabra del titular (itálica, negrita u otro color); usar MAYÚSCULAS en etiquetas; poner etiquetas innecesarias sobre el contenido.
6. **La estructura es información.** Bordes, numeración, eyebrows y divisores deben codificar algo. «01 / 02 / 03» solo si el contenido es de verdad una secuencia.
7. **Movimiento.** El que no dispara el usuario va con mesura y solo para dirigir la atención. Un único momento orquestado (una secuencia de carga o un revelado) funciona mejor que efectos dispersos. El *fade-and-slide-up* en cada sección y el hover en cada tarjeta se leen como generados por IA. El movimiento que responde a una acción de la persona es bienvenido.
8. **Calibración: los cinco clústeres actuales de diseño generado por IA.**
   1. Fondo crema cálido (cerca de #F4F1EA), serif de alto contraste y acento terracota (cerca de #D97757).
   2. Fondo casi negro con un único acento verde ácido o bermellón.
   3. Layout de periódico: filetes finos, radio cero y columnas densas.
   4. Kit de tarjetas SaaS: tarjetas idénticas con el mismo radio y la misma sombra gris suave, y lavados de gradiente como decoración.
   5. Cromo de plantilla: eyebrow en MAYÚSCULAS espaciadas, metadatos con punto medio («A · B · C»), etiquetas «PALABRA — fragmento», casi-negro teñido (#0B0B0B o #111) en lugar de negro, monoespaciada para etiquetas de datos y «→» al final de los enlaces.

   Todos son legítimos si el brief los pide. Si el eje queda libre, no hay que gastarlo en ellos.
9. **Proceso en dos pasadas.**
   1. Escribir un plan compacto de tokens: 4–6 colores hex con nombre, tipografías y roles, concepto de layout con wireframes ASCII y alineación, y principios.
   2. Revisar el plan contra el brief. Lo que salga igual que en cualquier página parecida se rehace, y se explica qué cambió y por qué.

   Recién después se escribe código. Ojo con la especificidad CSS: las clases que se anulan entre sí, sobre todo en padding y margin entre secciones.
10. **Contención.** Gastar la audacia en un solo lugar: un elemento memorable y todo lo demás tranquilo. Piso de calidad sin anunciarlo: responsive, foco visible, movimiento reducido y contraste. Autocrítica con capturas. El consejo de Chanel: antes de salir, sacarse un accesorio. Anotar lo que ya se probó.
11. **Escritura.** Las palabras son contenido de diseño. Se escribe desde el usuario, en voz activa, y el CTA dice exactamente qué pasa. La acción mantiene el mismo nombre en todo el flujo. Los errores dicen qué pasó y cómo se arregla, sin pedir disculpas. Tono conversacional en minúscula inicial, sin relleno.

### 1.3 Impeccable (Paul Bakaus)

Lo que aplica a una landing, que Impeccable clasifica como **modo Persuade**:

- **Prohibición absoluta:** el eyebrow o *kicker* sobre un titular. Es la única regla que ningún brief puede revertir.
- **Rechazar por defecto:**
  - tarjetas iguales de ícono, título y texto como estructura de la página;
  - tarjetas anidadas;
  - el hero de métrica grande;
  - «01/02/03» sin secuencia real;
  - texto con gradiente;
  - glass o blur decorativo;
  - barra de color a un costado de una caja redondeada;
  - sombras duras desplazadas fuera de un mundo neobrutalista;
  - **monoespaciada como disfraz de «técnico»**, cuando no hay código, datos ni medidas;
  - emojis o glifos Unicode en lugar de íconos;
  - fondos con rayas `repeating-linear-gradient` o grillas sin un objeto real debajo (lienzo, mapa, plano o instrumento de medida);
  - elegir claro u oscuro por categoría y no por la escena de uso.
- **Piso de oficio:**
  - contraste ≥ 4.5:1 en el cuerpo;
  - sombras con desplazamiento y desenfoque;
  - tracking mínimo de −0.04em;
  - display de 6rem como máximo;
  - medida de 65–75ch;
  - elevación declarada una sola vez (borde *o* sombra, no ambos: la «tarjeta fantasma» es un borde de 1 px bajo una sombra ancha);
  - radios de tarjeta de 12–16 px;
  - **superficies del navegador tematizadas:** selección, caret, scrollbar, anillo de foco, `text-underline-offset` y números tabulares. Según Bakaus, es la señal más barata de que la página se construyó y no se ensambló.
- **Movimiento** (`animate.md`):
  - Antes de animar, una «tesis»: momento focal, continuidad, feedback y presupuesto.
  - Duraciones: 100–150 ms para feedback, 150–300 ms para estado, 300–500 ms para layout y 500–800 ms solo para el momento focal.
  - Salir más rápido que entrar. `cubic-bezier(0.16, 1, 0.3, 1)`, sin rebote ni elástico.
  - El contenido es visible por defecto, para que un script que falle no esconda la página.
  - Los loops no esenciales se detienen fuera de pantalla.
  - El hover no mueve al elemento que tiene el puntero: mueve un hijo.
  - El movimiento reducido no apaga todo: quita el desplazamiento espacial y conserva el feedback.
- **La tesis de Bakaus:** no se arregla el slop diciéndole al modelo qué evitar. Cada prohibición lo empuja al siguiente atractor; el slop pasó de morado a beige. Por eso la regla útil es anclar en el mundo del sujeto.

### 1.4 Vercel: Web Interface Guidelines y product-design

Reglas de `AGENTS.md` relevantes para una landing animada:

- **MUST:**
  - respetar `prefers-reduced-motion`;
  - animar solo `transform` y `opacity`;
  - animaciones interrumpibles;
  - un **autoplay de más de 5 s necesita pausa, stop u ocultar**;
  - `transform-origin` correcto;
  - foco visible con `:focus-visible`;
  - `color-scheme: dark` en `<html>` si el tema es oscuro;
  - `font-variant-numeric: tabular-nums` en números que se comparan;
  - prevenir CLS con dimensiones explícitas;
  - precargar las imágenes de arriba del pliegue;
  - enlaces de navegación con `<a>`;
  - usar el carácter «…»;
  - `translate="no"` en marcas y tokens de código.
- **NEVER:** `transition: all`; animar `top`, `left`, `width` o `height`; `outline: none` sin reemplazo; deshabilitar el zoom.
- **SHOULD:**
  - preferir CSS antes que Web Animations y Web Animations antes que librerías JS;
  - precargar las fuentes críticas con `font-display: swap`;
  - sombras en capas (ambiente y directa);
  - radios anidados concéntricos;
  - teñir bordes y sombras hacia el tono del fondo;
  - evitar el *banding* en gradientes oscuros;
  - usar APCA para el contraste.
- **Product-design (blog de Vercel):** resolver estructura y semántica antes que estilo y copy; diseñar todos los estados alcanzables. «Nunca declarar verificación visual a partir del código solo.»

### 1.5 Taste-skill y auditorías de 2026

- **Taste-skill 2.5.1:**
  - Los shaders y las escenas de scroll quedan reservados para páginas de software, tech y gaming, con un tope de cuatro contextos WebGL y fallbacks estáticos en móvil y con movimiento reducido. Nuestra página califica.
  - Una sección por pantalla, bordes izquierdo y derecho iguales, UI de producto real y un checker en navegador a 1440, 1024, 768 y 390 px.
- **Auditorías** (Krebs, con 1.590 sitios de Show HN; 925 Studios; Vibecodekit; no-code.supply):
  - **la huella clásica:** gradiente morado-azul, Inter en todo, hero centrado, píldora «✨ Now with AI», tres tarjetas con ícono de línea, navbar de vidrio, bento por reflejo y orbes morados detrás del hero;
  - la tarjeta con borde izquierdo de color;
  - el texto con gradiente sobre un número;
  - el modo oscuro que nadie pidió.
- **La segunda ola:** el look «anti-slop» ya es slop. Crema con terracota, cromo mono, **Instrument Serif con captions en mono**. Quien diseña esquivando una lista deja que la lista decida.
- **Dato propio:** `create-next-app` usa **Geist y Geist Mono por defecto** (verificado en la plantilla `app-tw/ts/app/layout.tsx`). En 2026, Geist es la nueva «Inter de los sitios hechos con IA».

### 1.6 Delator → qué hacer en pcx

| Delator genérico | Qué hacer en su lugar (específico de pcx) |
|---|---|
| Gradiente morado-azul, orbes, cian sobre oscuro, neón | La única «luz» de la página es la del proyector: blanco frío con caída suave sobre la mesa. El color de acento solo marca eventos (un toque, una acción del agente). |
| Inter o Geist en todo | Un par elegido por su rol (§4). La pixelada solo para el texto que el sistema *proyecta*. |
| Tres tarjetas con íconos de lucide o bento | El pipeline como una sola escena (cámara → mundo → MCP → proyector), una transcripción real de agente y la foto real de la mesa. |
| Misma sombra gris, borde de 1 px y radio único | Una sola elevación. En un mundo de luz proyectada, la profundidad la da la luz (halo, penumbra), no sombras de tarjeta. |
| Glassmorphism | Desenfoque solo si significa algo: un proyector *desenfocado* que entra en foco al calibrar. |
| Texto con gradiente o una palabra en otro color | El énfasis va con peso o tamaño, o con el haz de luz sobre el titular, que es contenido. |
| Eyebrow en MAYÚSCULAS, «A · B · C», «→» en cada botón | Ninguno. El titular habla solo y el botón dice la acción («Copiar comando»). |
| Números 01/02/03 decorativos | Se justifican **una sola vez**: el pipeline *es* una secuencia (ver, entender, proyectar, esperar el toque). |
| Mono como disfraz | Mono solo para comandos, nombres de tools, coordenadas en mm, fps y ms reales. |
| Hero de métrica grande | Si hay números, son medidos y van dentro de la telemetría viva, no como trofeo. |
| Rayas y grillas decorativas | Las franjas son **los patrones de gray code** que el producto proyecta de verdad para calibrar, y los ArUco son los marcadores que usa. Tienen una mesa real debajo. |
| Logos, testimonios o stats inventados | Solo lo real. Los datos ilustrativos se rotulan como tales. |
| Fade-up en cada sección, hover-lift en cada tarjeta | Un momento orquestado (la calibración) y movimiento de scroll con significado. Lo demás, quieto o en respuesta al usuario. |
| Copy vacío («revoluciona», «sin fricción», «potencia») | Verbos y objetos concretos: «Tu agente ve la mesa», «Proyecta un botón sobre la taza y espera el toque». |
| Oscuro «porque es tech» | El oscuro se justifica por la escena (proyectar exige penumbra). Ojo: si la landing se muestra en el proyector del venue, el oscuro pierde contraste, así que conviene probarla ahí o tener el tema claro (paleta B). |
| Superficies del navegador por defecto | `::selection`, `caret-color`, scrollbar y `:focus-visible` con los tokens de la paleta. |

### 1.7 Meta-regla

Antes de escribir código hay que contestar *por qué este elemento existe en el mundo de pcx*. Si la respuesta vale igual para cualquier SaaS, se rehace.

---

## 2. Referencias de calidad (12)

| # | Sitio | Qué hace (técnica visual o de movimiento) | Qué aplica a pcx |
|---|---|---|---|
| 1 | [Linear](https://linear.app) | El hero es una **reproducción guionada de UI de producto**: un issue que se crea desde Slack, se etiqueta solo, un agente abre un PR y aparecen marcas de tiempo como «just now». Todos los mocks comparten un mismo dataset ficticio coherente. Arriba del hero, una nota dirigida a agentes los apunta al MCP. Usa InterVariable con un sistema estricto. | Transcripción guionada de un agente (`scene` → `project` → `ask` → toque) con datos coherentes en toda la página. Agregar una nota para agentes con el MCP. Inter no es el problema; lo es usarla sin sistema. |
| 2 | [Vercel](https://vercel.com) | «Agentic Infrastructure»: tipografía y una sola marca gráfica. El color queda confinado a una región. Panel de terminal con la salida real de `vercel deploy`. | El comando de instalación como bloque de terminal real y copiable. El color vive en una sola región (el haz). |
| 3 | [Raycast](https://www.raycast.com) | El hero es un **teclado construido en DOM**: el objeto físico del producto como protagonista. Píldora «Introducing Raycast MCP». | Construir el hero con el objeto físico de pcx (la mesa vista desde arriba), no con un 3D de stock. |
| 4 | [Teenage Engineering](https://teenage.engineering) | Microcopy en minúscula, specs telegráficas («varispeed», «jog ff/rewind»), fotografía de producto, Univers Next. Precisión sin adornos. | Microcopy tipo ficha técnica con unidades reales (mm, ms, fps, px de error) y foto real del rig. |
| 5 | [Nothing](https://nothing.tech) | Tipografía de puntos Ndot derivada de su hardware de luz (los LED Glyph). Monocromo con un rojo funcional. | La tipografía de display sale de la luz del producto: los píxeles proyectados. Doto o Geist Pixel solo para lo que «proyecta» el sistema. |
| 6 | [Rive](https://rive.app) | 32 `<canvas>` en la home: demos vivas de máquinas de estado, prompts copiables con «Send to my computer» y un compilador en el navegador (WASM) que «recompila mientras escribes». | Prompt copiable para Claude Code más el comando. Si se puede, un demo que el visitante maneje. |
| 7 | [Warp](https://www.warp.dev) | El hero es un **esquema animado** de tareas de agentes («fig. 1 — the factory», «8 × 28 · live») con contadores. Tono de manual técnico. | Telemetría viva y esquemática. Ojo: los captions «fig.» ya son tendencia; no copiarlos. |
| 8 | [Modal](https://modal.com) | 13 canvas, prompt tipeado «modal \|», dashboards mock con un gráfico «Live». | Comando que se tipea en el hero. Rotular los datos ilustrativos con honestidad. |
| 9 | [Terminal Industries](https://terminal-industries.com) | Apertura de cine (un camión real al atardecer) que **cambia a la vista de visión por computadora**: wireframes, puntos y datos. Lenis y Geist Mono; titulares que se tipean con el scroll. SOTD en septiembre de 2025, nominado a los Webby 2026. | **La referencia más cercana.** Mundo real → lo que ve la máquina es exactamente pcx: foto de la mesa → overlay de objetos, manos y toques. |
| 10 | [Lightform](https://lightform.com) (histórico) | La empresa cerró y el sitio quedó como archivo. «Project LFX» era una plataforma de investigación de interfaces proyectadas orientables, sobre escaneo con luz estructurada. Poppins y un gradiente rosado sobre foto de producto. | Linaje y posicionamiento: la proyección AR como hardware de consumo murió y pcx es la capa para agentes. Visualmente es una **anti-referencia** (look genérico de 2018). |
| 11 | [Dynamicland](https://dynamicland.org) | La portada es **una sola foto de un estante físico con hotspots**: el sitio es un lugar. Se describe como un computador que es un lugar donde la gente trabaja junta en el mundo real. | Dirección A: la foto cenital real de la mesa como interfaz, con hotspots sobre los objetos. Mismo linaje (computación en el espacio). |
| 12 | [Lando Norris](https://landonorris.com) (Site of the Year 2025, OFF+BRAND) y [Messenger](https://messenger.abeto.co) (Developer SOTY 2025, abeto) | Webflow con un casco Three.js, 8 archivos `.riv`, Lenis y GSAP, texturas KTX2 y Draco, y precargador. WPO 7.60 en Awwwards. Messenger es un planeta WebGL diminuto. | Muestran el techo de oficio y su costo. Copiar la disciplina (carga diferida, assets comprimidos, una sola idea), **no** el peso. |

Tendencias de 2026 según [The Branx](https://thebranx.com/blog/best-tech-startup-websites) (2026-11-20):

- se dejaron los gradientes y blobs brillantes «porque los generan por defecto los constructores con IA»;
- se muestra el producto real;
- la prueba de producto se presenta como datos que parecen vivos;
- titular enorme con una capa chica de «panel de instrumento» monoespaciado;
- el scroll se usa como línea de tiempo de cine.

Ojo: el titular gigante con panel mono ya es tendencia, así que también es atractor.

---

## 3. Stack 2026

### 3.1 Versiones verificadas (`npm view`, 2026-11-21)

| Paquete | Versión | Fecha | Licencia |
|---|---|---|---|
| `astro` | 7.4.13 | 2026-11-18 | MIT |
| `next` | 16.4.6 | 2026-11-09 | MIT |
| `react` | 19.3.1 | | MIT |
| `tailwindcss` / `@tailwindcss/vite` | 4.3.5 | 2026-11-06 | MIT |
| `motion` | 14.14.0 | 2026-11-13 | MIT (Motion+ es de pago y no hace falta) |
| `gsap` | 3.15.1 | 2026-11-04 | «Standard no-charge license» de Webflow |
| `@gsap/react` | 2.1.2 | | |
| `lenis` | 1.3.27 | 2026-10-15 | MIT |
| `ogl` | 1.0.11 | | Unlicense |
| `three` | 0.187.0 | | MIT |
| `@react-three/fiber` / `drei` | 9.8.2 / 10.7.9 | | MIT |
| `typescript` | 7.0.2 | | Apache-2.0. Si el tooling falla con la 7, usar la versión que trae la plantilla. |
| `@lhci/cli` | 0.16.1 | 2026-11-11 | Apache-2.0 |
| `lighthouse` | 13.6.0 | | |
| `web-vitals` | 6.2.3 | | |
| `treosh/lighthouse-ci-action` | 12.6.2 | | |

**GSAP es gratis, incluidos todos los plugins.** Desde el 30 de abril de 2025 (versión 3.13, tras la compra de Webflow), SplitText, MorphSVG, ScrollTrigger y el resto vienen en el paquete npm público, también para uso comercial. La única restricción es usar GSAP para construir una herramienta visual sin código que compita con Webflow, cosa que no aplica a una landing. SplitText se reescribió: es un 50 % más chico y trae `mask`, `autoSplit` y `aria`.

### 3.2 Peso medido (minificado, gzip y brotli; sin React salvo donde se indica)

| Entrada | min | gzip | **brotli** |
|---|---|---|---|
| `motion/mini` (`animate`) | 8.4 KB | 3.2 KB | **2.9 KB** |
| `motion` vanilla (`animate`, `scroll`, `inView`, `stagger`) | 78.4 | 28.9 | **26.5** |
| `motion/react` `LazyMotion` + `m` + `domAnimation` | 85.8 | 31.3 | **28.7** |
| `motion/react` `motion` + `AnimatePresence` + `useScroll` | 149.8 | 51.8 | **46.5** |
| `gsap` core | 69.5 | 27.1 | **24.6** |
| `gsap` + ScrollTrigger | 113.0 | 44.2 | **39.9** |
| `gsap` + ScrollTrigger + SplitText | 120.6 | 47.2 | **42.7** |
| `lenis` | 18.3 | 5.3 | **4.7** |
| `ogl` (Renderer, Program, Mesh, Triangle) | 45.7 | 13.0 | **10.9** |
| `three` mínimo con tree-shaking (`WebGLRenderer` + `ShaderMaterial`) | 519.6 | 128.9 | **106.9** |
| `@react-three/fiber` + `three` (sin React) | 915.8 | 243.9 | **202.9** |
| `react` + `react-dom/client` | 217.5 | 67.3 | **58.0** |

**Conclusión:** para un hero con un shader 2D (franjas y haz de luz), OGL cuesta unas **10 veces menos** que three y unas **19 veces menos** que R3F. three o R3F solo se justifican con geometría 3D real, como un modelo del rig, y aun así cargados de forma diferida.

### 3.3 Astro o Next: recomiendo Astro

| | Astro 7.4 | Next 16.4 |
|---|---|---|
| JS por defecto | **Cero.** Las islas y los `<script>` vanilla se empaquetan solos. | El runtime de React y Next en cada página (React solo ya pesa 58 KB br). |
| Encaje con una landing estática | Ideal: HTML estático, `<script>` por componente y Vite 8 con Rolldown. | Bueno con `output: 'export'` o `ensureStatic`, pero se paga el runtime. |
| Animación | GSAP y OGL en scripts vanilla. Sin hidratación, sin riesgos de StrictMode ni de doble montaje. | `@gsap/react` (`useGSAP`) o `motion/react`. View Transitions estables en React 19.3. |
| Cuándo elegirlo | Landing de una página, prioridad en LCP y peso. | Si el equipo ya tiene componentes React que reutilizar (por ejemplo, del dashboard) o el constructor ya empezó en Next. |
| Deploy | Estático en Vercel, Render o cualquier CDN. | Vercel (hay créditos). |

**Recomendación:** Astro 7.4, TypeScript y Tailwind 4.3 (con `@tailwindcss/vite`), sin React. **Si el constructor ya está en Next, que no migre:** que aplique el mismo presupuesto, con `next/font/local` para las fuentes, OGL importado dinámicamente en un client component y GSAP con `useGSAP`.

### 3.4 Para qué sirve cada pieza y cuánto cuesta

| Pieza | Para qué | Costo | Regla de uso |
|---|---|---|---|
| **Tailwind 4.3** | Tokens con `@theme` en CSS (colores OKLCH, fuentes y easing) y utilidades. | Solo CSS. Unos 10–20 KB br según uso, sin JS. | Los tokens de la paleta van en `@theme`. Nada de clases arbitrarias que se peleen entre sí. |
| **GSAP 3.15 + ScrollTrigger + SplitText** | La coreografía: timeline de la calibración, la escena fijada del pipeline con scrub y titulares secundarios. | 42.7 KB br. Se carga **después** del LCP. | `gsap.matchMedia()` para separar movimiento completo y reducido. SplitText con `mask: "lines"` y `aria: "auto"`, nunca sobre el H1 del LCP. |
| **Motion 14** | Alternativa a GSAP si se está en React y se necesitan animaciones de layout o `AnimatePresence`. | 28.7 KB br (`LazyMotion` + `m`) o 2.9 KB (`motion/mini`). | **No cargar Motion y GSAP juntos.** En Astro sobra. En Next, uno de los dos. |
| **Lenis 1.3** | Scroll suave sincronizado con ScrollTrigger. | 4.7 KB br. Corre en el hilo principal en cada frame. | Opcional. Se apaga con movimiento reducido y en táctil (por defecto no toca el touch). Si se usa, sincronizarlo con el ticker de GSAP (snippet en §5.3). |
| **OGL 1.0** | El shader del hero: franjas de gray code más el haz del proyector siguiendo el puntero. | 10.9 KB br más unos 2 KB de GLSL. GPU: un triángulo a pantalla completa. | DPR ≤ 1.5, render a 0.5–0.75 de resolución (la luz es difusa), pausa fuera de pantalla y fallback estático. |
| **three / R3F** | Solo si hay un modelo 3D real (GLB del rig). | 107 KB / 203 KB br más los assets. | No para el hero 2D. Si se usa, KTX2 y Draco y carga perezosa bajo el pliegue. |
| **CSS scroll-driven animations** | Barras de progreso y revelados ligados al scroll sin JS. | 0 KB. Corre en el compositor. | Baseline desde el 27 de octubre de 2026 (Chrome 115, Safari 26, Firefox 159). Detectar con `@supports (animation-timeline: scroll())`. |
| **Fuentes autohospedadas** | Sin terceros ni FOIT. | 20–40 KB por woff2 variable (latin). | Precargar como máximo 2. Fallback con `size-adjust` para CLS ≈ 0. |

---

## 4. Tipografía y color

### 4.1 Tres pares gratis (licencias verificadas, todas OFL-1.1)

Tamaños: woff2 latin medido en los paquetes de fontsource.

| Par | Display y cuerpo | Datos y comandos | Por qué | Riesgo |
|---|---|---|---|---|
| **1. «Instrumento» (recomendado)** | **Host Grotesk** variable, wght 300–800, 19.8 KB. Grotesca contemporánea con carácter, poco usada por los generadores. | **Departure Mono** 1.500, monoespaciada pixelada de lo-fi técnico, ~15–25 KB (zip de la release: 83 KB). No está en fontsource: autohospedarla desde la release de GitHub. | La textura de píxel evoca el píxel proyectado y la cámara de baja resolución. La grotesca neutra deja que la luz sea la protagonista. | Departure Mono es nítida **solo en múltiplos de 11 px** (lo dice su README): 11, 22 o 33 px. |
| **2. «Mesa viva»** | **Mona Sans** variable con wdth 75–125 y wght 200–900: 38.9 KB solo con wght y 95.8 KB con ancho. | **Martian Mono** variable con wdth 75–112.5 y wght 100–800, 23.0 KB / 37.6 KB. | El eje de ancho permite un momento de «enfoque»: el titular pasa de condensado a normal mientras el proyector se calibra. Ambas familias comparten esa lógica de ejes. | Animar `font-variation-settings` re-maqueta el texto en cada frame (no corre en el compositor). Hacerlo solo en un titular y una vez. Con el eje de ancho, Mona pesa el doble. |
| **3. «Luz sobre objetos» (editorial)** | **Instrument Serif** 400 e itálica, 20.5 KB, en titulares grandes, con **Instrument Sans** variable (29.4 KB) en el cuerpo. | **Geist Mono** variable, 22.6 KB. | Cinematográfico y editorial, para la dirección C. | **El riesgo de slop más alto:** «Instrument Serif con captions mono» está señalado como el nuevo default anti-slop, y Geist es el default de `create-next-app`. Solo vale con una composición editorial de verdad. |

**Acento opcional, con una regla semántica:** **Doto** (dot-matrix variable, 5.2 KB) o **Geist Pixel** (cinco variantes, publicada el 6 de febrero de 2026, OFL) se usan **solo para el texto que el sistema proyecta** dentro del demo, por ejemplo la etiqueta proyectada sobre la taza. Nunca en titulares de la página. Así el sistema tipográfico codifica algo: lo proyectado frente a la página.

Descartadas: Inter y Space Grotesk (por el pedido); Geist Sans como voz principal (default de Next); JetBrains Mono como voz principal (39.5 KB y muy vista en sitios dev). JetBrains Mono sirve si se quiere mono «de código» sin personalidad pixelada.

### 4.2 Dos paletas (OKLCH a hex calculado; contrastes WCAG medidos)

**Paleta A, «Cuarto oscuro» (recomendada).** Escena: un proyector necesita penumbra. La mesa es grafito; la luz del proyector DLP es blanca fría; el único color cálido es el *toque*.

| Token | OKLCH | Hex | Rol |
|---|---|---|---|
| `mesa` | 0.22 0.012 250 | `#161b20` | Fondo: superficie de la mesa (grafito, no negro genérico) |
| `mesa-alta` | 0.27 0.014 250 | `#21272d` | Superficie elevada: panel de terminal, bloque de comando |
| `luz` | 0.965 0.010 235 | `#edf5f9` | Texto principal y luz proyectada |
| `penumbra` | 0.735 0.020 240 | `#9eabb5` | Texto secundario (derivado del tono de la luz, no gris) |
| `trazo` | 0.36 0.014 245 | `#373e44` | Divisores y ejes de medida (decorativo, 1.6:1) |
| `toque` | 0.80 0.150 72 | `#f8ac3d` | **Solo** eventos: toque detectado, acción del agente, foco |

Contrastes: `luz`/`mesa` 15.7:1; `penumbra`/`mesa` 7.4:1; `toque`/`mesa` 9.0:1; `mesa` sobre `toque` 9.0:1 (sirve como botón).

Por qué no cae en los clústeres: el acento no es verde ácido ni bermellón y tiene un rol semántico, no decorativo. El fondo es un material con tono, no un `#111`.

**Paleta B, «Base de corte» (tema claro y respaldo para el proyector del venue).** Escena: una mesa de trabajo con base de corte verde milimetrada, que es un instrumento de medida real y por eso justifica una grilla. Las hojas y los marcadores ArUco son de tinta negra sobre papel.

| Token | OKLCH | Hex | Rol |
|---|---|---|---|
| `papel` | 0.975 0.003 200 | `#f5f7f7` | Fondo (blanco frío; **no** crema, para alejarse del clúster 1) |
| `base` | 0.43 0.075 168 | `#1a5d48` | Superficie de marca: la base de corte, en regiones grandes |
| `medida` | 0.80 0.055 168 | `#9ccab7` | Líneas de la grilla milimetrada sobre `base` |
| `tinta` | 0.19 0.012 200 | `#0d1516` | Texto y ArUco |
| `grafito` | 0.43 0.015 200 | `#475253` | Texto secundario |
| `señal` | 0.87 0.165 98 | `#f1d438` | Relleno de acento (la línea amarilla de la regla): toques y selección |

Contrastes: `tinta`/`papel` 17.2:1; `grafito`/`papel` 7.5:1; `papel`/`base` 7.3:1; `tinta` sobre `señal` 12.5:1; `señal`/`base` 5.3:1; `medida`/`base` 4.3:1 (solo líneas).

**Motivos visuales propios** (en las dos paletas):

- **Gray code:** franjas binarias cuyo período se reduce a la mitad en cada bit. Es el patrón que pcx proyecta para calibrar.
- **ArUco:** usar marcadores reales del diccionario (`DICT_4X4_50`) generados con OpenCV, no cuadros inventados.
- **El haz:** un disco de luz con caída suave y leve trapecio (keystone).
- **Unidades en mm:** el frame `table_mm` de pcx tiene origen arriba a la izquierda, con x hacia la derecha e y hacia abajo.

---

## 5. Rendimiento y accesibilidad con animación

### 5.1 Presupuesto

| Métrica | Objetivo | Nota |
|---|---|---|
| LCP | **< 2.0 s** en Lighthouse móvil (Slow 4G y CPU ×4) | El umbral «bueno» de web.dev es 2.5 s en el p75. |
| CLS | **≤ 0.02** | Fallbacks de fuente con `size-adjust` y dimensiones explícitas en imágenes y canvas. |
| INP | **< 200 ms** en campo. En laboratorio, TBT < 150 ms como proxy. | Lighthouse no mide INP. En campo, `web-vitals` 6 (`onINP`). |
| JS inicial (antes del LCP) | ≤ 15 KB br en Astro | El shader, GSAP y Lenis se cargan después. |
| JS total | ≤ 90 KB br en Astro (GSAP + ST + Split + Lenis + OGL ≈ 58 KB más el código propio). En Next, ≤ 220 KB. | |
| Fuentes | ≤ 2 woff2 precargadas, ≤ 70 KB | |
| Imagen del hero (foto de la mesa) | AVIF ≤ 80 KB, `fetchpriority="high"` | |

### 5.2 WebGL sin dañar el LCP

1. **El LCP es el H1, que es texto.** `<canvas>` no es candidato a LCP según web.dev. Lo que mata el LCP es esconder el H1: **los elementos con opacidad 0 no cuentan** y el LCP se mueve al momento del revelado. Nada de `SplitText.from({opacity: 0})` sobre el H1. Animar la capa de luz, no el texto.
2. **Póster estático primero:** una imagen AVIF o SVG de las mismas franjas y la misma mesa ocupa el lugar del canvas desde el HTML, con dimensiones fijas.
3. **Carga diferida en este orden:** evento `load`, luego tiempo ocioso, luego visibilidad confirmada con IntersectionObserver, luego `import()` de OGL, luego el primer frame dibujado y por último el fundido del póster. **Safari estable aún no tiene `requestIdleCallback`** (solo en Technology Preview a noviembre de 2026), así que hace falta fallback.
4. **En ejecución:**
   - DPR ≤ 1.5 (1 en móvil) y render a 0.5–0.75 de resolución;
   - `powerPreference: "low-power"`;
   - pausa con `visibilitychange` y fuera de pantalla;
   - manejar `webglcontextlost`;
   - si WebGL falla, se queda el póster;
   - el `pointermove` solo guarda coordenadas y el rAF las lee, sin lecturas de layout.
5. **Fotosensibilidad (WCAG 2.3.1):** las franjas blanco y negro que cambian rápido pueden ser destellos. El cambio de bit va con fundido de al menos 300 ms, la intensidad de las franjas se queda en 10–20 % y **nunca hay más de 3 destellos por segundo**.

```ts
// hero-loader.ts: carga el shader solo cuando no compite con el LCP
const idle = (cb: () => void) =>
  "requestIdleCallback" in window ? requestIdleCallback(cb, { timeout: 1500 }) : setTimeout(cb, 300);
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
const host = document.querySelector<HTMLElement>("[data-hero-gl]")!;
if (!reduce) addEventListener("load", () => idle(() => {
  new IntersectionObserver(async ([e], io) => {
    if (!e.isIntersecting) return; io.disconnect();
    const { mountHero } = await import("./hero-gl"); // OGL + GLSL, ~13 KB br
    mountHero(host).then(() => host.dataset.ready = "1"); // CSS: [data-ready] .poster { opacity: 0 }
  }).observe(host);
}), { once: true });
```

```glsl
#version 300 es
// hero-gl: franjas de gray code (las de la calibración real) + haz del proyector en el puntero
precision highp float;
in vec2 vUv; out vec4 color;
uniform vec2 uRes; uniform vec2 uPointer; uniform float uBit; // 0..9; la parte fraccional cruza al siguiente bit
int grayBit(int col, int k) { int g = col ^ (col >> 1); return (g >> (9 - k)) & 1; }
void main() {
  int col = int(floor(vUv.x * 1024.0));
  int k = int(floor(uBit));
  float s = mix(float(grayBit(col, k)), float(grayBit(col, min(k + 1, 9))), smoothstep(0.0, 1.0, fract(uBit)));
  vec2 p = (vUv - uPointer) * vec2(uRes.x / uRes.y, 1.0);
  float beam = exp(-dot(p, p) * 6.0);
  vec3 mesa = vec3(0.086, 0.106, 0.125), luz = vec3(0.929, 0.961, 0.976); // #161b20, #edf5f9
  color = vec4(mesa + luz * (0.12 * s + 0.55 * beam * (0.6 + 0.4 * s)), 1.0); // franjas al 12 %: sin destellos
}
```

### 5.3 `prefers-reduced-motion`

- **Con movimiento reducido:**
  - el póster estático queda y el shader no se carga;
  - sin Lenis;
  - sin pin ni scrub: las escenas se muestran resueltas;
  - transiciones de opacidad y color de 150–200 ms para el feedback;
  - el haz salta al punto sin seguimiento suave.
- **Botón de pausa** visible para el loop del hero (más de 5 s de autoplay; Vercel MUST y WCAG 2.2.2).

```ts
const mm = gsap.matchMedia();
mm.add({ full: "(prefers-reduced-motion: no-preference)", reduce: "(prefers-reduced-motion: reduce)" }, (ctx) => {
  if (ctx.conditions!.reduce) return; // estados finales visibles por defecto en el CSS
  const lenis = new Lenis({ autoRaf: false });
  lenis.on("scroll", ScrollTrigger.update);
  const tick = (t: number) => lenis.raf(t * 1000);
  gsap.ticker.add(tick); gsap.ticker.lagSmoothing(0);
  // ...timeline de la calibración y escena del pipeline
  return () => { gsap.ticker.remove(tick); lenis.destroy(); };
});
```

### 5.4 Lighthouse en CI

`lighthouserc.json`. Con Astro, `dist/`. Con Next, `out/` si se usa `output: 'export'`; si no, cambiar `staticDistDir` por `startServerCommand` + `url`.

```json
{
  "ci": {
    "collect": { "staticDistDir": "./dist", "numberOfRuns": 3 },
    "assert": { "assertions": {
      "categories:performance": ["error", { "minScore": 0.9 }],
      "categories:accessibility": ["error", { "minScore": 0.95 }],
      "largest-contentful-paint": ["error", { "maxNumericValue": 2000 }],
      "cumulative-layout-shift": ["error", { "maxNumericValue": 0.02 }],
      "total-blocking-time": ["error", { "maxNumericValue": 150 }],
      "resource-summary:script:size": ["error", { "maxNumericValue": 100000 }],
      "resource-summary:font:size": ["warn", { "maxNumericValue": 80000 }]
    }},
    "upload": { "target": "filesystem", "outputDir": ".lighthouseci" }
  }
}
```

- **Local:** `npm run build && npx @lhci/cli@0.16.1 autorun`.
- **GitHub Actions:** `treosh/lighthouse-ci-action@v12` con `configPath: ./lighthouserc.json`, `uploadArtifacts: true` y **`temporaryPublicStorage: false`**, porque el repo es privado y esa opción publica el informe.
- Medir también a mano en Chrome DevTools (panel Performance, interacciones) moviendo el puntero sobre el hero, para revisar el costo del `pointermove`.

---

## 6. Tres direcciones para el hero

### A. «La mesa viva»

- **Qué es:** vista cenital de una mesa oscura con objetos reales. El puntero es el proyector: un haz de luz con caída suave. Al acercarse a un objeto, un widget proyectado (etiqueta, halo, panel con botón) se ancla a él y lo sigue. Es literal a pcx: `project` con `anchor.object_id`, y «si el objeto se mueve, el widget lo sigue». Al costado, una transcripción breve: `scene` → `project` → `ask` → toque.
- **Técnica:**
  - **foto cenital real** sacada de la cámara de pcx (un `look`) como LCP en AVIF;
  - una capa OGL que oscurece la foto y la ilumina con el haz (multiplicación por una máscara de luz);
  - widgets en DOM posicionados en mm sobre la foto (escala `table_mm` a px), con transiciones FLIP en GSAP;
  - en móvil o táctil, el haz recorre un camino automático y el toque fija el haz.
- **Librerías:** OGL y GSAP (núcleo). SplitText no hace falta.
- **Riesgo:** medio.
  - Depende de una buena foto de la mesa real, que hay que pedir hoy.
  - La foto tiene que verse bien bajo el oscurecimiento.
  - El encaje mm→px tiene que ser coherente.
  - Muy distintivo y muy explicativo.

### B. «Instrumento»

- **Qué es:**
  - El hero *es* la calibración: franjas de gray code barren la mesa bit a bit, de período ancho a fino.
  - Cuatro marcadores ArUco en las esquinas son «detectados» con cajas que se ajustan.
  - Un panel de telemetría muestra datos reales a su ritmo real: fps del renderer, latencia en ms, error de reproyección en px y objetos y manos detectados.
  - Precisión al estilo Teenage Engineering: hairlines con unidades reales y microcopy en minúscula.
- **Técnica:**
  - shader de gray code (§5.2);
  - ArUco reales en SVG (generados con OpenCV y exportados);
  - telemetría en DOM con `tabular-nums`;
  - el barrido es **el** momento orquestado (~1.5 s), y después queda una deriva lenta.
- **Librerías:** OGL y GSAP. La telemetría va en CSS y JS vanilla.
- **Riesgo:**
  - Bajo en assets, porque todo es procedural.
  - Alto en estética: puede caer en «hacker/terminal genérico» y en mono como disfraz.
  - **Fotosensibilidad** (§5.2).
  - Los números tienen que ser reales o rotularse como ilustrativos.

### C. «Luz sobre objetos»

- **Qué es:** editorial y cinematográfico. Una foto o video a sangre de objetos sobre la mesa en penumbra. Con el scroll, un haz recorre la escena y revela en cada objeto la interfaz que el agente le proyecta, en cuatro escenas fijadas: «ve», «entiende», «proyecta», «espera el toque». Titulares grandes.
- **Técnica:**
  - ScrollTrigger con pin y scrub;
  - `mask-image` radial con custom properties para el haz (sin WebGL);
  - loops cortos H.264/AV1 con `poster` y `playsinline muted`;
  - Lenis.
- **Librerías:** GSAP con ScrollTrigger y SplitText, más Lenis.
- **Riesgo:** alto.
  - Necesita material filmado de la demo real, que probablemente esté listo tarde.
  - El peso de video.
  - El pin en móvil.
  - La sensación de scroll secuestrado.
  - Es el más cercano al clúster «editorial serif».

### Recomendación: B → A, «la calibración»

- **Al cargar:** el H1 ya está visible. El barrido de gray code (B) calibra la mesa en ~1.5 s, la imagen pasa de desenfocada a enfocada y los ArUco se marcan.
- **Al terminar el barrido:** aparece la mesa real y el puntero pasa a ser el haz (A). Una transcripción de agente corre al costado.
- **El resto de la página va tranquilo.** Una sola escena fijada para el pipeline. Si llega buen video de la demo, se usa como sección C más abajo, no como hero.

---

## 7. Contenido real disponible (no inventar)

- **Tools MCP reales** (`node/crates/pcx-core/src/tools.rs`, `contracts/schema/tools.json`):
  - `scene` (JSON) y `look` (imagen y texto) para ver;
  - `project` (widgets `label`, `halo`, `arrow`, `panel`, `button`, `image`, `effect`) y `remove`;
  - `ask` (pregunta proyectada con botones que espera el toque);
  - `events_wait`;
  - `run_flow` (máquina de estados en la mesa);
  - `calibrate`, `home` y `devices`.
- Las coordenadas van en **mm** (`table_mm`).
- **Números medidos** según `docs/PLAN.md`: renderer en Rust a ~100 fps y ~5 ms. Claude Code y Codex ya ven la mesa, proyectan sobre objetos y esperan un toque.
- **Faltan, y hay que pedirlos al equipo (Manu):** el comando de instalación exacto para macOS, Windows y Linux; el comando para conectar cada agente por MCP; el nombre final; y una captura `look` de la mesa.

---

## 8. Brief para el constructor

```text
PRODUCTO: pcx da contexto físico a agentes. Cámara + proyector entienden una mesa; Claude Code, Codex u OpenCode
se conectan por MCP para ver (`scene`, `look`) y proyectar (`project`, `ask`, `events_wait`). Modo Persuade:
entender qué es en 5 s, querer probarlo, copiar el comando.

PROCESO: carga el skill oficial antes de diseñar:
  curl -sL https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md
Escribe un plan de tokens, revísalo contra este brief, construye, captura a 1440/768/390 y quita un accesorio.

STACK (si ya empezaste en Next 16.4, quédate; no migres):
- Astro 7.4 + TS + Tailwind 4.3 (@tailwindcss/vite). Scripts vanilla, sin React.
- GSAP 3.15 (+ScrollTrigger, SplitText; gratis) para coreografía. Lenis 1.3 opcional. OGL 1.0 para el shader.
- Nada de three/R3F para el hero (107–203 KB br frente a 11 KB de OGL). Nunca Motion y GSAP juntos.
- Fuentes autohospedadas: Host Grotesk (variable) + Departure Mono (solo en 11/22/33 px). Opcional: Doto
  solo para texto "proyectado" dentro del demo, nunca en titulares.

PALETA "cuarto oscuro": mesa #161b20 · mesa-alta #21272d · luz #edf5f9 · penumbra #9eabb5 · trazo #373e44
· toque #f8ac3d (solo eventos: toque, acción del agente, foco). Tema claro de respaldo para el proyector del
venue: papel #f5f7f7 · base #1a5d48 · tinta #0d1516 · señal #f1d438. Selección, caret, scrollbar y foco con tokens.

HERO: "la calibración", una sola idea.
1. H1 visible desde el primer pintado (es el LCP; jamás animar su opacidad desde 0).
2. Barrido de franjas gray code (~1.5 s, intensidad 10–20 %, cruces suaves, <3 destellos/s) que "calibra"
   la mesa; 4 ArUco reales en las esquinas se marcan.
3. Queda la foto cenital real de la mesa (pide una captura `look`); el puntero es el haz del proyector; widgets
   (label, halo, panel con botón) se anclan a los objetos. Al costado, transcripción: scene → project → ask → toque.
4. Móvil: el haz recorre un camino automático. Botón de pausa del loop.
Técnica: póster AVIF/SVG con las mismas franjas; OGL vía import() tras load + idle (rIC con fallback a setTimeout:
Safari no lo tiene) + IntersectionObserver; DPR ≤ 1.5; pausa fuera de pantalla y con pestaña oculta; fallback
si falla WebGL. Shader y loader de ejemplo: informes/landing-diseno.md §5.2.

MOVIMIENTO: "nada quieto" = una capa ambiental viva (haz + telemetría a ritmo real) + UNA escena fijada con
scrub para el pipeline + respuesta al puntero. Prohibido el fade-up por sección y el hover-lift en tarjetas.
Easing cubic-bezier(0.16,1,0.3,1); 150–300 ms en UI; 500–800 ms solo en la calibración; salidas más rápidas.
Movimiento reducido: póster estático, sin Lenis, sin pin ni scrub, solo opacidad y color.

ESTRUCTURA (sin grillas de tarjetas iguales):
1. Hero: H1 + una frase + comando de instalación copiable (el real; pídelo, no lo inventes) + "ver demo".
2. Pipeline como secuencia (aquí sí se numera): ver → entender → proyectar → esperar el toque.
3. Funciona con tu agente: transcripción real de Claude Code/Codex/OpenCode con los nombres reales de las tools.
4. Instalar: macOS / Windows / Linux en pestañas, un comando cada una, botón "Copiar comando".
5. Técnica para el jurado: solo números medidos (~100 fps, ~5 ms) + gray code, ArUco, MCP, mm.

PROHIBIDO: gradientes morado/azul u orbes · Inter o Geist como voz · 3 tarjetas con íconos lucide · bento por
reflejo · glassmorphism · texto con gradiente · una palabra del titular en otro color · eyebrows en MAYÚSCULAS ·
"A · B · C" · "→" en cada botón · emojis o píldora "✨ New" · mono fuera de comandos/mm/ms/fps · borde de 1 px
+ sombra gris en tarjetas · logos, testimonios o métricas inventadas · "revoluciona", "sin fricción", "potencia".
SÍ: motivos del producto (gray code, ArUco, haz, mm); text-wrap: balance en titulares; tabular-nums en
telemetría; foco visible; responsive a 390 px; idioma consistente (identificadores de código en su forma original).

PRESUPUESTO: LCP < 2.0 s (Lighthouse móvil) · CLS ≤ 0.02 · TBT < 150 ms · INP < 200 ms · JS ≤ 90 KB br en Astro
(≤ 15 KB antes del LCP; Next ≤ 220 KB) · ≤ 2 woff2 precargadas. Antes de entregar: npx @lhci/cli@0.16.1 autorun
con el lighthouserc.json de §5.4 y la prueba en un proyector real.
```

---

## Fuentes

**Skills y guías**
- [anthropics/skills: frontend-design SKILL.md](https://github.com/anthropics/skills/blob/main/skills/frontend-design/SKILL.md) · [plugin en claude-plugins-official](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/frontend-design)
- [pbakaus/impeccable](https://github.com/pbakaus/impeccable) · [impeccable.style](https://impeccable.style) · [Bakaus: «slop is a moving target» (BigGo)](https://finance.biggo.com/news/71cfafb357af64ab)
- [vercel-labs/web-interface-guidelines AGENTS.md](https://github.com/vercel-labs/web-interface-guidelines/blob/main/AGENTS.md) · [Web Interface Guidelines](https://vercel.com/design/guidelines) · [Teaching agents product design at Vercel](https://vercel.com/blog/teaching-agents-product-design-at-vercel)
- [Leonxlnx/taste-skill](https://github.com/Leonxlnx/taste-skill)
- [Developers Digest: 16 patrones de slop](https://www.developersdigest.tech/blog/ai-design-slop-and-how-to-spot-it) · [925 Studios: AI slop tells](https://www.925studios.co/blog/ai-slop-design-tells) · [Vibecodekit: AI slop design](https://vibecodekit.dev/ai-slop-design) · [no-code.supply: Is your website AI slop?](https://no-code.supply/blog/is-your-website-ai-slop/) · [avoid-ai-design](https://github.com/funboy322/avoid-ai-design) · [Medium: The Anti-Slop Look Is Now AI Slop](https://medium.com/@anup.karanjkar08/the-anti-slop-look-is-now-ai-slop-11-claude-design-secrets-nobody-tells-you-0c11c2bb2359)
- [Plantilla de create-next-app (Geist por defecto)](https://github.com/vercel/next.js/blob/canary/packages/create-next-app/templates/app-tw/ts/app/layout.tsx)

**Referencias**
- Sitios: [linear.app](https://linear.app) · [vercel.com](https://vercel.com) · [raycast.com](https://www.raycast.com) · [teenage.engineering](https://teenage.engineering) · [nothing.tech](https://nothing.tech) · [rive.app](https://rive.app) · [warp.dev](https://www.warp.dev) · [modal.com](https://modal.com) · [terminal-industries.com](https://terminal-industries.com) · [lightform.com](https://lightform.com) · [dynamicland.org](https://dynamicland.org) · [folk.computer](https://folk.computer)
- [OFF+BRAND: Lando Norris](https://www.itsoffbrand.com/our-work/lando-norris) · [Awwwards Annual Awards 2025](https://www.awwwards.com/annual-awards/winners) · [The Branx: best tech startup websites (2026-11-20)](https://thebranx.com/blog/best-tech-startup-websites) · [Hon Tran: WebGL website examples 2026](https://www.hontran.dev/blog/webgl-website-examples)
- Nothing: [Ndot / tipografía (Crewtangle)](https://crewtangle.com/nothings-carefully-crafted-brand/) · [shadcn.io Nothing design system](https://www.shadcn.io/design/nothing) · Vercel: [getdesign.md Vercel](https://getdesign.md/vercel/design-md)

**Stack**
- [GSAP Standard License](https://gsap.com/standard-license) · [GSAP 3.13: todo gratis](https://gsap.com/blog/3-13/) · [CSS-Tricks: GSAP is now completely free](https://css-tricks.com/gsap-is-now-completely-free-even-for-commercial-use/)
- [Next.js 16.4](https://nextjs.org/blog/next-16-4) · [Astro 7 (resumen de cambios)](https://morello.dev/blog/astro-7) · [Motion: reduce bundle size](https://motion.dev/docs/react-reduce-bundle-size)

**Fuentes tipográficas**
- [Introducing Geist Pixel](https://vercel.com/blog/introducing-geist-pixel) · [Departure Mono](https://github.com/rektdeckard/departure-mono) · [Martian Mono](https://github.com/evilmartians/mono)

**Rendimiento y accesibilidad**
- [web.dev: LCP](https://web.dev/articles/lcp) · [web.dev: Web Vitals](https://web.dev/articles/vitals) · [caniuse: requestIdleCallback](https://caniuse.com/requestidlecallback) · [Scroll-driven animations Baseline](https://scroll-driven-animations.style/demos/baseline-newly-available/css/) · [treosh/lighthouse-ci-action](https://github.com/treosh/lighthouse-ci-action) · [GoogleChrome/lighthouse-ci](https://github.com/GoogleChrome/lighthouse-ci)
