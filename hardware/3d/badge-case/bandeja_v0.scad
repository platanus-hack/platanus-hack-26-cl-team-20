// Plataboy 26 — bandeja trasera v0 (frente abierto). BORRADOR SIN VERIFICAR.
// La placa entra a presión por el frente y queda retenida por 4 dientes; botones y OLED quedan libres,
// así que solo dependen del contorno XY y de la altura del portapilas.
// Imprime boca arriba (fondo en la cama), sin soportes.
include <badge_params.scad>

clear    = 0.3;   // holgura por lado
wall     = 1.8;
floor_t  = 1.6;
back_gap = back_parts_z + 1.0;  // espacio bajo la PCB para el portapilas
lip      = 1.2;   // pared sobre la cara frontal de la PCB
nub      = 0.5;   // cuánto entran los dientes de retención
post_d   = 4.5;   // postes de apoyo bajo los agujeros
pilot_d  = 1.8;   // guía para tornillo M2 autorroscante (opcional)
bat_win_d = 23;   // ventana trasera para sacar la pila
eyelet_d = 9;  eyelet_hole = 4.5;   // ojal de lanyard (abajo al centro, como el agujero original)
$fn = 64;

z_pcb   = floor_t + back_gap;      // cara trasera de la PCB
z_front = z_pcb + pcb_t;           // cara frontal de la PCB
z_top   = z_front + lip;

module rrect(w, h, r) { offset(r) offset(-r) square([w, h]); }
module pcb2d(o = 0) { translate([-o, -o]) rrect(pcb_w + 2*o, pcb_h + 2*o, pcb_r + o); }

module shell() {
  difference() {
    linear_extrude(z_top) pcb2d(clear + wall);
    translate([0, 0, floor_t]) linear_extrude(z_top) pcb2d(clear);
    // ventana de la pila
    translate([bat_cx, bat_cy, -1]) cylinder(d = bat_win_d, h = floor_t + 2);
    // interruptor (izquierda) y header (derecha): desde la cara trasera de la PCB hacia arriba
    translate([-clear - wall - 1, sw_y0 - 2, z_pcb - 0.5]) cube([wall + 2, sw_y1 - sw_y0 + 4, 20]);
    translate([pcb_w + clear - 1, hdr_y0 - 2, z_pcb - 0.5]) cube([wall + 2, hdr_y1 - hdr_y0 + 4, 20]);
    // el módulo OLED sobresale por arriba
    translate([oled_x0 - 1, pcb_h - 1, z_front - 0.2]) cube([oled_x1 - oled_x0 + 2, wall + clear + 2, 20]);
  }
}

module posts() {
  for (p = holes) translate([p[0], p[1], 0]) difference() {
    cylinder(d = post_d, h = z_pcb);
    translate([0, 0, floor_t + 0.6]) cylinder(d = pilot_d, h = z_pcb);
  }
}

// diente: cara inferior plana (retiene), cara superior en rampa (guía al encajar)
module nub_at(x, y, ang, len = 7) {
  translate([x, y, z_front + 0.1]) rotate(ang)
    hull() {
      translate([-len/2, 0, 0]) cube([len, nub, 0.01]);
      translate([-len/2, 0, 0]) cube([len, 0.01, nub + 0.6]);
    }
}

module nubs() {
  // dientes en las paredes izquierda y derecha, abajo de los cortes
  nub_at(-clear,          pcb_h * 0.30,  -90);
  nub_at(pcb_w + clear,   pcb_h * 0.30,   90);
  nub_at(-clear,          pcb_h * 0.62,  -90, 5);
  nub_at(pcb_w + clear,   pcb_h * 0.62,   90, 5);
}

module eyelet() {
  y = -clear - wall - eyelet_d/2 + 1.5;
  h = floor_t + 1.6;
  difference() {
    hull() {
      translate([pcb_w/2, y, 0]) cylinder(d = eyelet_d, h = h);
      translate([pcb_w/2 - eyelet_d/2, -clear - 0.5, 0]) cube([eyelet_d, 0.5, h]);
    }
    translate([pcb_w/2, y, -1]) cylinder(d = eyelet_hole, h = h + 2);
  }
}

union() {
  shell();
  intersection() {                       // postes recortados al bolsillo
    posts();
    linear_extrude(z_top) pcb2d(clear);
  }
  nubs();
  eyelet();
}
