// WUI Card · tarjeta stencil con wordmark, markers esquineros y ventanas de información.
//
//   openscad -o wui-card.stl card.scad   (requiere OpenSCAD 2021.01 o superior)
//
// Pensada para imprimir plana en 3D con grosor fino. Todos los agujeros son pasantes y
// ninguno deja islas flotantes, así que la tarjeta sale en una pieza conectada.
//
// Variables en milímetros. Cambia card_w, card_h o thickness arriba y recompila.

// --- parámetros --------------------------------------------------------
card_w     = 200;
card_h     = 150;
thickness  = 1.6;   // "súper delgadito": 1.2–2.0 mm según filamento
corner_r   = 4;     // radio de las esquinas del contorno exterior

logo_w     = 120;   // ancho del wordmark en milímetros
logo_top   = 15;    // distancia del borde superior al tope del wordmark (al punto de la i)

marker_r   = 10;    // los markers viven dentro de un círculo imaginario de este radio
marker_pad = 18;    // distancia del centro del marker al borde

$fn = 72;           // suavidad de arcos

// --- wordmark (variante Encaje de los Estudios a partir de Perfora) ----
//
// Los paths vienen del SVG 193×97. Para los arcos de la U se muestreó el bézier con
// 7 segmentos. Son recomendaciones; cualquier variante cerrada sirve.
svg_w = 193;
svg_h = 97;
logo_scale = logo_w / svg_w;

module w_letter() {
  polygon(points = [
    [0,24], [23,24], [33,63], [44,24], [60,24], [71,63], [82,24],
    [104,24], [84,88], [63,88], [52,56], [41,88], [20,88]
  ]);
}

module u_letter() {
  polygon(points = [
    [108,24], [129,24], [129,60],
    [130,66], [132,70], [139,71], [146,71], [149,69], [150,65],
    [150,59], [150,24], [170,24], [170,88], [151,88], [151,82],
    [146,87], [140,89], [133,89], [118,89], [108,78], [108,61]
  ]);
}

module i_letter() {
  polygon(points = [[175,24], [193,24], [193,88], [175,88]]);   // asta
  polygon(points = [[175,1],  [193,1],  [193,17], [175,17]]);   // punto
}

module wordmark_svg_native() {
  w_letter();
  u_letter();
  i_letter();
}

module wordmark_placed() {
  // SVG: y crece hacia abajo, OpenSCAD: y crece hacia arriba.
  //   logo_top define dónde queda la parte superior del wordmark en coord. de tarjeta.
  translate([(card_w - logo_w) / 2, card_h - logo_top])
    scale([logo_scale, -logo_scale])
      wordmark_svg_native();
}

// --- markers esquineros (una forma de la lámina 06 por esquina) -------
//
// Formas sin islas: el material de la tarjeta queda conectado alrededor de cada marker.
// Cada esquina tiene una geometría única para que el detector CV identifique la posición.
//
//   TL · Punto     (dot + 4 satélites)
//   TR · Línea     (tres barras paralelas)
//   BL · Arco      (media luna abierta hacia arriba)
//   BR · Contorno  (bracket cuadrado abierto a la derecha)

module marker_punto() {
  circle(r = 5);
  for (a = [0, 90, 180, 270])
    rotate(a) translate([8, 0]) circle(r = 1.5);
}

module marker_linea() {
  for (i = [-1, 0, 1])
    translate([0, i * 4])
      square([14, 1.5], center = true);
}

module marker_arco() {
  difference() {
    circle(r = 9);
    circle(r = 6);
    translate([0, 9]) square([22, 18], center = true);   // abre hacia arriba
  }
}

module marker_contorno() {
  difference() {
    square([16, 16], center = true);
    square([12, 12], center = true);
    translate([6, 0]) square([10, 18], center = true);   // abre hacia la derecha
  }
}

// --- ventanas de información -----------------------------------------
//
// Espacios donde el proyector pinta datos (batería, notificación, hora).

module rounded_rect(w, h, r) {
  hull()
    for (sx = [-1, 1]) for (sy = [-1, 1])
      translate([sx * (w/2 - r), sy * (h/2 - r)]) circle(r = r);
}

module slot_bateria()      rounded_rect(24, 12, 1.2);
module slot_notificacion() circle(r = 7);
module slot_tiempo()       rounded_rect(30, 10, 1.2);

// --- composición 2D --------------------------------------------------
module card_2d() {
  difference() {
    rounded_rect_abs();
    // Wordmark
    wordmark_placed();
    // Markers esquineros
    translate([marker_pad, card_h - marker_pad]) marker_punto();
    translate([card_w - marker_pad, card_h - marker_pad]) marker_linea();
    translate([marker_pad, marker_pad]) marker_arco();
    translate([card_w - marker_pad, marker_pad]) marker_contorno();
    // Ventanas de información (bajo el wordmark)
    translate([card_w / 2, card_h - logo_top - 60 - 15]) slot_bateria();
    translate([card_w / 2 - 36, card_h - logo_top - 60 - 40]) slot_notificacion();
    translate([card_w / 2 + 20, card_h - logo_top - 60 - 40]) slot_tiempo();
  }
}

module rounded_rect_abs() {
  translate([corner_r, corner_r])
    hull() {
      square([card_w - 2 * corner_r, card_h - 2 * corner_r]);
      for (sx = [0, 1]) for (sy = [0, 1])
        translate([(card_w - 2 * corner_r) * sx, (card_h - 2 * corner_r) * sy])
          circle(r = corner_r);
    }
}

// --- extrusión --------------------------------------------------------
linear_extrude(height = thickness) card_2d();
