"""Arma (SIN proyectar) el widget html de la consola para proyectar un 'skin' sobre la mesa.
uso: proyectar_skin.py salidas/google-scifi.png [--ancho 1340] [--calidad 85] [--opacidad 1] [--out widget-skin.json]

La imagen debe ser la vista normalizada re-estilizada: cubre x in [-10,1330] mm, y in [-10,700] mm (cualquier resolucion
con esa proporcion, 1340:710). Se incrusta como data: URI en un <img> de 1340x710 CSS px (1 px CSS = 1 mm) colocado en
(-10,-10) dentro de un contenedor cuyo sistema local son los mm reales de la mesa, y ese contenedor se lleva al canvas
(1000x563 CSS px) con matrix3d(mm_to_canvas de fp-metric.json), mismo patron que fp_checker.py.

Escribe el JSON {"request_id":..., "widgets":[widget]} y muestra el comando para enviarlo. NO llama a la consola."""
import argparse, base64, json, sys, cv2, numpy as np
from pathlib import Path
HERE=Path(__file__).resolve().parent
X0,Y0,X1,Y1=-10,-10,1330,700                      # zona normalizada en mm (igual que pipeline.py)
ap=argparse.ArgumentParser(); ap.add_argument("imagen"); ap.add_argument("--ancho",type=int,default=1340)
ap.add_argument("--calidad",type=int,default=85); ap.add_argument("--opacidad",type=float,default=1.0)
ap.add_argument("--metric",default=str(HERE.parent/"fp-metric.json")); ap.add_argument("--out",default=None)
a=ap.parse_args()
m=json.load(open(a.metric)); H=np.array(m["mm_to_canvas"],float); H=H/H[2,2]
# homografia 3x3 -> matrix3d CSS (column-major, z identidad)
m3d="matrix3d(%s)"%",".join("%.9g"%v for v in [H[0,0],H[1,0],0,H[2,0], H[0,1],H[1,1],0,H[2,1], 0,0,1,0, H[0,2],H[1,2],0,H[2,2]])
im=cv2.imread(a.imagen)
if im is None: sys.exit("no pude leer "+a.imagen)
h,w=im.shape[:2]; Wmm,Hmm=X1-X0,Y1-Y0
if abs(w/h-Wmm/Hmm)>0.01: print(f"AVISO: proporcion {w/h:.3f} != {Wmm/Hmm:.3f}; se estira a 1340x710 mm",file=sys.stderr)
alto=round(a.ancho*Hmm/Wmm); im=cv2.resize(im,(a.ancho,alto),interpolation=cv2.INTER_AREA if a.ancho<w else cv2.INTER_CUBIC)
ok,buf=cv2.imencode(".jpg",im,[cv2.IMWRITE_JPEG_QUALITY,a.calidad]); uri="data:image/jpeg;base64,"+base64.b64encode(buf.tobytes()).decode()
html=f'<div id="w"><img id="skin" src="{uri}"></div>'
css=f"""html,body{{margin:0;background:#000;overflow:hidden}}
#w{{position:absolute;left:0;top:0;width:{X1}px;height:{Y1}px;transform-origin:0 0;transform:{m3d}}}
#skin{{position:absolute;left:{X0}px;top:{Y0}px;width:{Wmm}px;height:{Hmm}px;display:block;opacity:{a.opacidad}}}"""
widget={"type":"effect","id":"skin","shader":"html","rect_mm":[0,0,1000,563],"params":{"html":html,"css":css}}
out=Path(a.out) if a.out else HERE/f"widget-{Path(a.imagen).stem}.json"
out.write_text(json.dumps({"request_id":"skin-"+Path(a.imagen).stem,"widgets":[widget]}))
# autochequeo: donde caen en el canvas las esquinas de la imagen y el origen de la mesa
def c(x,y): q=H@[x,y,1]; return (round(q[0]/q[2],1),round(q[1]/q[2],1))
print(f"imagen incrustada {a.ancho}x{alto} jpg q{a.calidad} ({len(buf)//1024} KB) -> {out} ({out.stat().st_size//1024} KB)")
print("esquinas de la imagen (mm -> canvas px):",{f"({x},{y})":c(x,y) for x,y in [(X0,Y0),(X1,Y0),(X1,Y1),(X0,Y1)]}," origen mesa (0,0) ->",c(0,0))
print("canvas 1000x563: lo que caiga fuera no se proyecta.")
print(f"Para proyectar (NO ejecutado): /usr/bin/python3 <scratchpad>/pcxl.py project \"$(cat {out})\"")
