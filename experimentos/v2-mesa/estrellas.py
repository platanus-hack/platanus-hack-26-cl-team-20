#!/usr/bin/env python3
"""Escena «cielo»: las estrellas que hay justo encima de la mesa ahora (Santiago, −33,42°, −70,61°).

Mapa cenital: el centro de la mesa es el cénit y el borde corto queda a ~55° del cénit. Se calcula en el navegador con la
hora real y gira solo. Catálogo: ~50 estrellas brillantes y las Nubes de Magallanes, con coordenadas escritas de memoria
(precisión de décimas de grado: sirve para reconocer el cielo, no para medir). Escribe out/widget-estrellas.json; con
--proyectar lo manda a la mesa."""
import json, subprocess, sys, time
import numpy as np

S = '/private/tmp/claude-501/-Users-sat/676a50ed-7238-4b78-9411-17db147f1404/scratchpad/pcxl.py'
LAT, LON = -33.42, -70.61
# nombre, AR (horas), Dec (grados), magnitud
E = [("Sirio", 6.752, -16.72, -1.46), ("Canopus", 6.399, -52.70, -0.74), ("Rigil Kent", 14.660, -60.83, -0.27), ("Arturo", 14.261, 19.18, -0.05), ("Vega", 18.616, 38.78, 0.03),
     ("Capella", 5.278, 46.00, 0.08), ("Rigel", 5.242, -8.20, 0.13), ("Proción", 7.655, 5.22, 0.34), ("Achernar", 1.629, -57.24, 0.46), ("Betelgeuse", 5.919, 7.41, 0.5),
     ("Hadar", 14.064, -60.37, 0.61), ("Altair", 19.846, 8.87, 0.77), ("Acrux", 12.443, -63.10, 0.76), ("Aldebarán", 4.599, 16.51, 0.85), ("Antares", 16.490, -26.43, 1.06),
     ("Spica", 13.420, -11.16, 0.97), ("Pólux", 7.755, 28.03, 1.14), ("Fomalhaut", 22.961, -29.62, 1.16), ("Deneb", 20.690, 45.28, 1.25), ("Mimosa", 12.795, -59.69, 1.25),
     ("Régulo", 10.140, 11.97, 1.35), ("Adhara", 6.977, -28.97, 1.5), ("Cástor", 7.577, 31.89, 1.58), ("Gacrux", 12.519, -57.11, 1.63), ("Shaula", 17.560, -37.10, 1.62),
     ("Bellatrix", 5.419, 6.35, 1.64), ("Elnath", 5.438, 28.61, 1.65), ("Miaplacidus", 9.220, -69.72, 1.68), ("Alnilam", 5.604, -1.20, 1.69), ("Alnair", 22.137, -46.96, 1.74),
     ("Alnitak", 5.679, -1.94, 1.77), ("Mintaka", 5.533, -0.30, 2.23), ("Saiph", 5.796, -9.67, 2.06), ("Peacock", 20.427, -56.74, 1.94), ("Mirzam", 6.378, -17.96, 1.98),
     ("Alphard", 9.460, -8.66, 1.98), ("Hamal", 2.120, 23.46, 2.0), ("Diphda", 0.727, -17.99, 2.02), ("Ankaa", 0.438, -42.31, 2.38), ("Wezen", 7.140, -26.39, 1.84),
     ("Avior", 8.375, -59.51, 1.86), ("Suhail", 9.133, -43.43, 1.96), ("Imai", 12.252, -58.75, 2.8), ("Menkent", 14.111, -36.37, 2.06), ("Atria", 16.811, -69.03, 1.92),
     ("Kaus Australis", 18.403, -34.38, 1.85), ("Nunki", 18.921, -26.30, 2.02), ("Aludra", 7.401, -29.30, 2.45), ("Naos", 8.060, -40.00, 2.25)]
LIN = [("Betelgeuse", "Bellatrix"), ("Bellatrix", "Mintaka"), ("Mintaka", "Alnilam"), ("Alnilam", "Alnitak"), ("Alnitak", "Betelgeuse"), ("Alnitak", "Saiph"), ("Saiph", "Rigel"), ("Rigel", "Mintaka"),
       ("Acrux", "Gacrux"), ("Mimosa", "Imai"), ("Sirio", "Mirzam"), ("Sirio", "Wezen"), ("Wezen", "Adhara"), ("Wezen", "Aludra"), ("Rigil Kent", "Hadar"), ("Cástor", "Pólux")]
NUBES = [("Nube Mayor", 5.39, -69.75, 22), ("Nube Menor", 0.88, -72.80, 14)]
JS = r"""
const E=__E__, LIN=__LIN__, NUB=__NUB__, LAT=__LAT__*Math.PI/180, LON=__LON__;
const c=document.getElementById('c'),x=c.getContext('2d');const W=1000,H=563,K=2;c.width=W*K;c.height=H*K;x.scale(K,K);
const CX=W/2,CY=H/2,R=H/2/55;   // px por grado de distancia cenital
function lst(){const d=(Date.now()/86400000)+2440587.5-2451545.0;let g=18.697374558+24.06570982441908*d;g=((g%24)+24)%24;return (((g+LON/15)%24)+24)%24;}
function pos(ra,dec){const ha=(lst()-ra)*15*Math.PI/180,de=dec*Math.PI/180;const sa=Math.sin(de)*Math.sin(LAT)+Math.cos(de)*Math.cos(LAT)*Math.cos(ha);const alt=Math.asin(sa);
 const az=Math.atan2(-Math.sin(ha)*Math.cos(de),Math.sin(de)*Math.cos(LAT)-Math.cos(de)*Math.sin(LAT)*Math.cos(ha));const z=90-alt*180/Math.PI;
 // carta del cielo para quien mira la mesa: norte arriba (lejos de la persona), este a su izquierda
 return {x:CX-R*z*Math.sin(az),y:CY-R*z*Math.cos(az),z};}
function txt(s,px,py,size,col){x.save();x.translate(px,py);x.fillStyle=col;x.font='600 '+size+'px Helvetica Neue, Arial';x.textAlign='center';x.fillText(s,0,0);x.restore();}
function tick(t){x.clearRect(0,0,W,H);const P={};
 for(const [n,ra,de,m] of E)P[n]=pos(ra,de);
 x.lineWidth=1.3;x.strokeStyle='rgba(160,190,255,.45)';
 for(const [a,b] of LIN){const p=P[a],q=P[b];if(p.z<80&&q.z<80){x.beginPath();x.moveTo(p.x,p.y);x.lineTo(q.x,q.y);x.stroke();}}
 for(const [n,ra,de,r] of NUB){const p=pos(ra,de);if(p.z<85){const g=x.createRadialGradient(p.x,p.y,0,p.x,p.y,r);g.addColorStop(0,'rgba(210,225,255,.5)');g.addColorStop(1,'rgba(210,225,255,0)');x.fillStyle=g;x.beginPath();x.arc(p.x,p.y,r,0,6.283);x.fill();txt(n,p.x,p.y+r+12,10,'rgba(200,215,255,.8)');}}
 E.forEach(([n,ra,de,m],i)=>{const p=P[n];if(p.z>88||p.x<0||p.x>W||p.y<0||p.y>H)return;const r=Math.max(1.6,5.2-1.25*m)*(0.9+0.12*Math.sin(t/380+i*1.7));
  x.shadowColor='#cfe0ff';x.shadowBlur=r*3;x.fillStyle='#fff';x.beginPath();x.arc(p.x,p.y,r,0,6.283);x.fill();x.shadowBlur=0;
  if(m<1.7)txt(n,p.x,p.y+r+13,m<0.6?13:11,'rgba(239,230,214,.92)');});
 // cénit y puntos cardinales
 x.strokeStyle='rgba(239,230,214,.5)';x.lineWidth=1;x.beginPath();x.arc(CX,CY,5,0,6.283);x.stroke();txt('cénit',CX,CY+18,10,'rgba(239,230,214,.7)');
 txt('N',CX,CY-R*52+14,16,'#efe6d6');txt('S',CX,CY+R*52,16,'#efe6d6');txt('E',CX-R*86,CY+6,16,'#efe6d6');txt('O',CX+R*86,CY+6,16,'#efe6d6');
 const h=new Date();txt('El cielo sobre Santiago · '+String(h.getHours()).padStart(2,'0')+':'+String(h.getMinutes()).padStart(2,'0'),CX,H-18,15,'#efe6d6');
 setTimeout(()=>requestAnimationFrame(tick),60);}
requestAnimationFrame(tick);
"""
js = JS.replace('__E__', json.dumps(E, ensure_ascii=False)).replace('__LIN__', json.dumps(LIN, ensure_ascii=False)).replace('__NUB__', json.dumps(NUBES, ensure_ascii=False)).replace('__LAT__', str(LAT)).replace('__LON__', str(LON))
w = {"type": "effect", "id": "estrellas", "shader": "html", "rect_mm": [0, 0, 1000, 563], "params": {"html": '<canvas id="c"></canvas>', "css": "html,body{margin:0;background:transparent;overflow:hidden}canvas{position:absolute;left:0;top:0;width:1000px;height:563px}", "js": js}}
json.dump({"request_id": "est", "widgets": [w]}, open('out/widget-estrellas.json', 'w'), ensure_ascii=False)
if '--proyectar' in sys.argv:
    print(subprocess.run(["/usr/bin/python3", S, "project", json.dumps({"request_id": "est-%d" % time.time(), "widgets": [w]})], capture_output=True, text=True).stdout[:80])
