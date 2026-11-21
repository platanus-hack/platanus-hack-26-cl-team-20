// Plataboy 26 — plantilla de calce (fit gauge), ~8 min en A1.
// Sirve para: (1) poner el badge encima y ver si el contorno y los 3 agujeros calzan,
// (2) sacar una foto cenital del badge sobre la plantilla: las reglas de 1 mm en los 4 lados
//     permiten medir la placa corrigiendo la perspectiva.
include <badge_params.scad>

margin   = 9;     // borde alrededor de la placa
plate_t  = 1.0;
mark_h   = 0.4;   // relieve de líneas y marcas
line_w   = 0.6;
$fn = 48;

W = pcb_w + 2*margin;
H = pcb_h + 2*margin;

module rrect(w, h, r) { offset(r) offset(-r) square([w, h]); }

module outline2d() {
  translate([margin, margin]) difference() {
    rrect(pcb_w, pcb_h, pcb_r);
    offset(-line_w) rrect(pcb_w, pcb_h, pcb_r);
  }
}

module ticks2d(len) {
  // marcas cada 1 mm a lo largo de [0,len], largas cada 5 y 10
  for (i = [0:floor(len)]) {
    l = (i % 10 == 0) ? 4 : (i % 5 == 0) ? 2.8 : 1.6;
    translate([i - line_w/2, 0]) square([line_w, l]);
  }
}

difference() {
  union() {
    linear_extrude(plate_t) rrect(W, H, 3);
    translate([0,0,plate_t]) linear_extrude(mark_h) {
      outline2d();
      // reglas en los 4 bordes, cero alineado con el borde de la placa
      translate([margin, 0.6]) ticks2d(pcb_w);                         // abajo
      translate([margin + pcb_w, H - 0.6]) rotate(180) ticks2d(pcb_w); // arriba
      translate([0.6, margin + pcb_h]) rotate(-90) ticks2d(pcb_h);     // izquierda
      translate([W - 0.6, margin]) rotate(90) ticks2d(pcb_h);          // derecha
      translate([margin + pcb_w/2, margin + pcb_h/2])
        text("v0", size = 5, halign = "center", valign = "center");
    }
  }
  // agujeros estimados de la placa
  for (p = holes) translate([margin + p[0], margin + p[1], -1]) cylinder(d = hole_d, h = 5);
}
