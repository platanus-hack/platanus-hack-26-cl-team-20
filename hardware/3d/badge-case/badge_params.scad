// Plataboy 26 — medidas de la placa
// ESTADO: ESTIMADAS POR FOTO (frente.jpg / atras.jpg), error ±1,5 mm en XY, Z sin medir.
// Escala: cuerpo de botón táctil 6x6 mm y módulo OLED (~27,4 mm medido, cuadra con 27,3 típico).
// Origen: esquina inferior izquierda de la PCB vista de FRENTE (OLED arriba), X a la derecha, Y hacia arriba.
// Reemplazar cada valor por el medido con pie de metro y cambiar MEDIDAS_VERIFICADAS a true.

MEDIDAS_VERIFICADAS = false;

// --- PCB ---
pcb_w = 34.0;      // ancho (est.)
pcb_h = 46.0;      // alto  (est.)
pcb_t = 1.6;       // espesor (típico FR4, sin medir)
pcb_r = 2.5;       // radio de esquinas (est.)

// --- agujeros de la placa (centros) ---
hole_d = 2.2;      // diámetro (est.; ¿M2?)
holes = [
  [2.0,          pcb_h - 1.6],   // arriba izquierda
  [pcb_w - 2.0,  pcb_h - 1.6],   // arriba derecha (simétrico, tapado en la foto)
  [pcb_w / 2,    2.2]            // abajo al centro
];

// --- módulo OLED 0,96" (va elevado sobre la PCB) ---
oled_x0 = 3.6;  oled_x1 = 31.0;     // extremos del módulo
oled_y0 = 26.2; oled_y1 = pcb_h + 1.8; // sobresale ~1,8 mm sobre el borde superior
oled_glass = [[4.0, 30.2], [29.0, 44.6]]; // [x0,x1],[y0,y1] vidrio aprox.
oled_top_z = 9.0;  // altura del vidrio sobre la cara de la PCB (SIN MEDIR, supuesto)

// --- botones (centros) ---
btn = [
  ["up",    11.9, 20.4],
  ["left",   5.9, 14.3],
  ["right", 17.9, 14.3],
  ["down",  11.9,  5.9],
  ["A",     29.5, 18.5],
  ["B",     22.7,  6.0]
];
btn_body = 6.0;    // cuerpo 6x6
btn_top_z = 5.0;   // altura del émbolo sobre la PCB (SIN MEDIR, típico 4,3–5)

// --- interruptor lateral (borde izquierdo) ---
sw_y0 = 34.8; sw_y1 = 40.3;
// --- header de pines (borde derecho, arriba) ---
hdr_y0 = 34.5; hdr_y1 = 40.0;

// --- portapilas CR2032 (cara trasera) ---
bat_cx = pcb_w / 2;  // centrado (atras.jpg)
bat_cy = 14.0;       // desde abajo (est.)
bat_holder_w = 30.0; // ancho del portapilas con clips (est.)
bat_holder_h = 22.0;
back_parts_z = 5.0;  // altura del portapilas+pila bajo la PCB (SIN MEDIR, supuesto)
