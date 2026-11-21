// Soporte de escritorio — Waveshare ESP32-S3-Touch-LCD-7 Rev 1.2
// Dos escuadras laterales idénticas (imprimir ×2), una por cada par de pestañas M3.
// La separación horizontal entre pares (126,20) no afecta el calce: cada escuadra es independiente.
// Fuentes y cotas supuestas: ver NOTAS.md. OpenSCAD 2021.01.
//
// Uso:  openscad -D 'part="bracket"' -o bracket.stl waveshare-stand.scad
//       part = "bracket" | "test" | "assembly"

part = "assembly";

/* ---------- Pantalla (mm) ---------- */
glass_w      = 192.96;   // PDF + PNG Rev1.2
glass_h      = 110.76;
glass_r      = 7.88;     // PDF
stack_t      = 5.00;     // TP+LCD (vidrio → espalda chasis), PDF
m3_dx        = 126.20;   // PNG Rev1.2 (no influye en el calce)
m3_dy        = 65.65;    // PNG Rev1.2; foto real ≈ 65,6
glass_bot_m3 = 21.58;    // borde inferior vidrio → M3 inferior (PDF girado 180°, foto real ≈ 21,9) — SIN VERIFICAR
glass_left_m3_back = 34.91; // vista trasera, borde izq. vidrio → M3 izq. (192,96−31,85−126,20) — SIN VERIFICAR
tab_h        = 2.0;      // altura de la pestaña M3 sobre la espalda del chasis — SUPUESTO, MEDIR
pcb_gap      = 3.5;      // espalda chasis → PCB (PDF 8,50−5,00) — borrador
pcb_w = 108.6; pcb_h = 73.8;  // Rev1.2 aprox. (escalado del PNG) — solo fantasma
pcb_x0 = -52.6; pcb_u0 = -13.6; // esquina PCB en vista frontal (X) y u — borrador
usb_u  = [22.2, 37.1];          // centros USB-C (u) escalados del PNG — borrador

/* ---------- Soporte ---------- */
tilt     = 18;     // grados desde la vertical
lift     = 25;     // altura del canto inferior del vidrio sobre la mesa (dedo a RESET/BOOT)
T        = 12;     // espesor de cada escuadra (eje X)
bar_z    = 15;     // cara inferior de la barra, medida desde la espalda del chasis (libra enchufes USB-C)
bar_t    = 7;      // grosor de barras del perfil
pad_w    = 12;     // largo del taco de apoyo a lo largo de la pantalla
toe      = 12;     // cuánto avanza la pata delantera más allá del canto frontal del vidrio
heel     = 80;     // pata trasera, desde la base de la columna hacia atrás
base_t   = 7;      // grosor de la barra de apoyo en la mesa
fillet   = 3;

/* ---------- Tornillería / tolerancias ---------- */
tol      = 0.3;            // por lado
screw_d  = 3 + 2*tol;      // 3,6
slot     = 1.0;            // ranura ± a lo largo de la pantalla
head_d   = 5.5 + 2*0.5;    // cabeza M3 DIN912 + holgura = 6,5
screw_len= 12;             // M3×12 cabeza cilíndrica
engage   = 3;              // rosca útil en la pestaña (SUPUESTO)
floor_z  = tab_h + (screw_len - engage); // asiento de la cabeza (desde espalda chasis)

$fn = 48;
eps = 0.01;

/* ---------- Geometría derivada ---------- */
s = sin(tilt); c = cos(tilt);
// marco local: u = hacia arriba de la pantalla, w = normal hacia atrás; origen = M3 inferior en la espalda del chasis
// mundo: X ancho, Y profundidad (+ atrás), Z arriba. Canto frontal-inferior del vidrio en Y=0, Z=lift.
P0 = [glass_bot_m3*s + stack_t*c, lift + glass_bot_m3*c - stack_t*s];
function L2W(u, w) = [P0[0] + u*s + w*c, P0[1] + u*c - w*s];
w_spine = bar_z + bar_t/2;
u_top   = m3_dy + pad_w/2 - bar_t/2;   // tope de columna al ras del taco
u_foot  = (bar_t/2 - P0[1] + w_spine*s) / c;   // u donde la columna toca la mesa
A  = L2W(u_top,  w_spine);          // tope de la columna
B  = L2W(u_foot, w_spine);          // pie de la columna
Ty = -toe;                          // punta delantera
Hy = B[0] + heel;                   // talón
Mu = m3_dy*0.55;
M  = L2W(Mu, w_spine);              // arranque del puntal trasero

echo(str("pie columna Y=", B[0], "  talón Y=", Hy, "  alto total≈", A[1]+bar_t/2,
         "  huella Y=", Hy - Ty + bar_t, "  tornillo M3×", screw_len, " asiento a z=", floor_z));

module seg(a, b, r) { hull() { translate(a) circle(r); translate(b) circle(r); } }

// pasa del marco local (u,w) al plano del perfil (Y,Z)
module to_local() {
    multmatrix([[c, s, 0, P0[0]],
                [-s, c, 0, P0[1]],
                [0, 0, 1, 0],
                [0, 0, 0, 1]]) children();
}
// en el plano del perfil: x2d=w, y2d=u  → Y = P0y + w c + u s ; Z = P0z − w s + u c  ✓

module profile2d() {
    r = bar_t/2;
    offset(r=-fillet) offset(delta=fillet) union() {
        seg(A, B, r);                               // columna paralela a la pantalla
        seg([Ty, r], [Hy, r], r);                   // apoyo en la mesa
        seg(M, [Hy, r], r);                         // puntal trasero
        to_local() for (u = [0, m3_dy])             // tacos sobre las pestañas
            translate([tab_h, u - pad_w/2]) square([w_spine - tab_h, pad_w]);
    }
}

// agujero en gota (punta hacia +X = arriba al imprimir de lado) ranurado ±slot en u
module tear(d) { hull() { circle(d/2); rotate(45) square(d/2); } }
module bracket() {
    difference() {
        translate([0, 0, -T/2]) linear_extrude(T) profile2d();
        for (u = [0, m3_dy]) translate([0, 0, 0]) {
            hole_at(u, screw_d, tab_h - 1, w_spine + bar_t);   // pasada
            hole_at(u, head_d,  floor_z,   w_spine + bar_t);   // avellanado cabeza
        }
    }
}
module hole_at(u, d, w0, w1) {
    to_local() translate([w0, u, 0]) rotate([0, 90, 0]) linear_extrude(w1 - w0)
        hull() for (k = [-slot, slot]) translate([0, k]) rotate(90) tear(d);   // punta hacia +Z de impresión
}

// pieza de prueba (<15 min en A1): los dos tacos completos (agujero, ranura, asiento de cabeza)
// unidos por una costilla delgada que fija la separación de 65,65
rib = 5;
module test_piece() {
    intersection() {
        bracket();
        union() {
            for (u = [0, m3_dy]) to_local() translate([tab_h - 1, u - pad_w/2 - 0.5, -T]) cube([w_spine + bar_t, pad_w + 1, 2*T]);
            to_local() translate([w_spine + bar_t/2 - rib, 0, -T/2]) cube([rib, m3_dy, rib]);
        }
    }
}

/* ---------- Fantasma de la pantalla (mundo) ---------- */
m3_x = [glass_w/2 - glass_left_m3_back, glass_w/2 - glass_left_m3_back - m3_dx]; // vista frontal
module rrect(w, h, r) { offset(r) square([w - 2*r, h - 2*r], center = true); }
module ghost() {
    // marco local 3D: X mundo, u, w
    multmatrix([[1, 0, 0, 0],
                [0, s, c, P0[0]],
                [0, c, -s, P0[1]],
                [0, 0, 0, 1]]) {
        // vidrio + LCD (w de -stack_t a 0)
        color([0.08, 0.08, 0.1, 0.55]) translate([0, -glass_bot_m3 + glass_h/2, -stack_t])
            linear_extrude(stack_t) rrect(glass_w, glass_h, glass_r);
        // PCB aprox. (vista frontal: desplazada hacia +X, USB-C apuntando a +X)
        color([0.1, 0.25, 0.6, 0.5]) translate([pcb_x0, pcb_u0, pcb_gap]) cube([pcb_w, pcb_h, 1.6]);
        // enchufes USB-C (sobremolde 12×8.5×6.5 + cable), salen por +X
        for (u = usb_u) color([0.2, 0.2, 0.2, 0.6])
            translate([pcb_x0 + pcb_w, u - 4.25, pcb_gap + 3.2 - 3.25]) cube([30, 8.5, 6.5]);
        // pestañas M3
        for (x = m3_x, u = [0, m3_dy]) color([0.75, 0.75, 0.75, 0.8])
            translate([x - 4.5, u - 6, 0]) cube([9, 12, tab_h]);
    }
}

module placed_brackets() {
    for (x = m3_x) translate([x, 0, 0]) rotate([90, 0, 90]) bracket();
}

if (part == "bracket")   translate([0, 0, T/2]) bracket();     // perfil plano sobre la cama; imprimir ×2
if (part == "test")      translate([0, 0, T/2]) test_piece();
if (part == "assembly") { color("WhiteSmoke") placed_brackets(); ghost(); }
