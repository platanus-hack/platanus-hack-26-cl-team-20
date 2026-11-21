"""Genera el cuaderno visual desde datos.json; no modifica decisiones del equipo."""
from pathlib import Path
import json
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, Color
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from xml.sax.saxutils import escape

BASE = Path(__file__).resolve().parent
DATA = json.loads((BASE / 'datos.json').read_text())
OUT = BASE / 'output/pdf/mapa-de-estudio.pdf'
OUT.parent.mkdir(parents=True, exist_ok=True)
FONT_ROOT = Path('/System/Library/Fonts/Supplemental')
pdfmetrics.registerFont(TTFont('Study', str(FONT_ROOT / 'Arial.ttf')))
pdfmetrics.registerFont(TTFont('StudyBold', str(FONT_ROOT / 'Arial Bold.ttf')))
pdfmetrics.registerFontFamily('Study', normal='Study', bold='StudyBold')
W, H = 1040, 760
INK = HexColor('#172023')
MUTED = HexColor('#53636B')
PAPER = HexColor('#F7F8F5')
LINE = HexColor('#CDD6D4')
ACCENT = HexColor('#197B78')
PALE = HexColor('#E4F0EA')
C = canvas.Canvas(str(OUT), pagesize=(W, H))
C.setTitle('Mapa de estudio - Propósito, valor, ICP y demo')
C.setAuthor('Preparado para Pablo y Matilda - Platanus Hack 26')

def text(x, y, value, size=13, bold=False, color=INK):
    C.setFillColor(color)
    C.setFont('StudyBold' if bold else 'Study', size)
    C.drawString(x, y, value.replace('—', '-').replace('–', '-'))

def wrap(x, y, value, width, size=13, color=INK, bold=False):
    style = ParagraphStyle('body', fontName='StudyBold' if bold else 'Study',
                           fontSize=size, leading=size * 1.38, textColor=color)
    p = Paragraph(escape(value.replace('—', '-').replace('–', '-')), style)
    _, height = p.wrap(width, 2000)
    p.drawOn(C, x, y-height)
    return height

def line(x1,y1,x2,y2,color=LINE,width=1):
    C.setStrokeColor(color); C.setLineWidth(width); C.line(x1,y1,x2,y2)

def arrow(x1,y1,x2,y2):
    line(x1,y1,x2,y2,MUTED,1.2)
    line(x2-7,y2+4,x2,y2,MUTED,1.2)
    line(x2-7,y2-4,x2,y2,MUTED,1.2)

def box(x,y,w,h,fill=PALE,stroke=None):
    C.setFillColor(fill)
    if stroke: C.setStrokeColor(stroke)
    C.roundRect(x,y,w,h,8,fill=1,stroke=int(stroke is not None))

def page(number,title,subtitle):
    C.setFillColor(PAPER); C.rect(0,0,W,H,fill=1,stroke=0)
    text(48,H-39,'PLATANUS HACK 26 / CUADERNO VISUAL',10,bold=True,color=MUTED)
    text(48,H-85,title,28,bold=True)
    wrap(48,H-103,subtitle,W-96,13,color=MUTED)
    line(48,44,W-48,44)
    text(48,25,'21 nov 2026 · hipótesis abiertas · investigación documental',10,color=MUTED)
    text(W-102,25,f'{number} / 7',10,color=MUTED)

def done(): C.showPage()

def cube(x,y,scale=1,active=False):
    C.saveState();C.translate(x,y);C.scale(scale,scale)
    C.setLineWidth(1);C.setStrokeColor(ACCENT if active else MUTED)
    C.setFillColor(PALE if active else PAPER)
    p=C.beginPath();p.moveTo(-17,0);p.lineTo(0,10);p.lineTo(19,0);p.lineTo(1,-10);p.close()
    C.drawPath(p,fill=1,stroke=1)
    line(-17,0,-17,-20);line(-17,-20,1,-31);line(1,-10,1,-31);line(1,-31,19,-20);line(19,-20,19,0)
    C.restoreState()

def scene(x,y,w,h,kind):
    # Proyector, haz y mesa; el dibujo representa una hipótesis de interacción.
    cx=x+w/2
    box(cx-30,y+h-29,60,25,PAPER,LINE)
    C.setStrokeColor(MUTED);C.circle(cx+14,y+h-17,6,fill=0,stroke=1)
    C.setFillColor(Color(.1,.5,.48,.06))
    p=C.beginPath();p.moveTo(cx-18,y+h-29);p.lineTo(x+12,y+35);p.lineTo(x+w-12,y+35);p.lineTo(cx+18,y+h-29);p.close()
    C.drawPath(p,fill=1,stroke=0)
    C.setStrokeColor(LINE);C.setFillColor(PAPER)
    p=C.beginPath();p.moveTo(x+10,y+68);p.lineTo(x+w-58,y+77);p.lineTo(x+w-10,y+26);p.lineTo(x+58,y+17);p.close()
    C.drawPath(p,fill=1,stroke=1)
    ax=x+w*.24;bx=x+w*.76;cy=y+57
    cube(ax,cy,.85,kind=='aprender');cube(bx,cy,.85,kind=='crear')
    if kind=='crear':
        arrow(ax+25,cy+5,bx-25,cy+5)
    elif kind=='aprender':
        C.setStrokeColor(ACCENT);C.ellipse(bx-30,cy-28,bx+30,cy+8,fill=0,stroke=1)
        arrow(ax+25,cy,bx-35,cy)
    elif kind=='pensar':
        line(ax,cy+10,cx,cy+35,ACCENT,2);line(cx,cy+35,bx,cy+10,ACCENT,2)
    else:
        C.setStrokeColor(ACCENT)
        p=C.beginPath();p.moveTo(cx-23,cy+8);p.lineTo(cx,cy+2);p.lineTo(cx+23,cy+8);p.lineTo(cx+23,cy-18);p.lineTo(cx,cy-24);p.lineTo(cx-23,cy-18);p.close()
        C.drawPath(p,stroke=1,fill=0);line(cx,cy+2,cx,cy-24,ACCENT)

# 1. Decisión y responsabilidades
page(1,'Del sistema a una razón para usarlo','La tecnología permite varias historias. La exploración debe conectar intención, persona y consecuencia.')
labels=[('PROPÓSITO','Qué queremos habilitar'),('VALOR','Qué cambia para alguien'),('PERSONAS / ICP','Para quién tiene sentido'),('DEMO','Qué hacemos visible')]
width=208; gap=35
for i,(title,sub) in enumerate(labels):
    x=48+i*(width+gap)
    box(x,470,width,112,PAPER,LINE)
    text(x+16,552,title,14,True,ACCENT)
    wrap(x+16,528,sub,width-32,15)
    if i<3:arrow(x+width+7,523,x+width+gap-7,523)
text(48,426,'La demo representa una situación. El ICP comercial requiere más validación.',16,True)
for i,(title,sub) in enumerate([('CREADOR','Configura y modifica'),('PARTICIPANTE','Interactúa y recibe valor'),('FACILITADOR / COMPRADOR','Habilita el espacio; puede ser otra persona')]):
    x=48+i*324
    box(x,270,296,112,PALE)
    text(x+16,352,title,12,True)
    wrap(x+16,329,sub,263,15)
text(48,224,'Nuestro frente',18,True)
areas=['Propósito','Usuarios y problemas','Casos de uso','Propuesta de valor','Experiencia','Marca e identidad','Demo y pitch','Escenas y pruebas']
for i,a in enumerate(areas):
    x=48+(i%4)*243;y=184-(i//4)*46
    text(x,y,f'{i+1:02d}  {a}',13)
text(48,75,'Siguiente paso: una conversación para formular 2-3 hipótesis, antes de elegir.',12,color=MUTED)
done()

# 2. Cuatro hipótesis
page(2,'La misma mesa, cuatro valores posibles','Esquemas conceptuales. Estas interacciones y los posibles clientes todavía deben comprobarse.')
for i,s in enumerate(DATA['escenarios']):
    x=48+(i%2)*494;top=590-(i//2)*255
    text(x,top,s['titulo'],19,True)
    scene(x,top-137,205,112,s['id'])
    wrap(x+224,top-22,s['persona'],220,12,bold=True)
    wrap(x+224,top-61,s['valor'],220,13)
    wrap(x,top-153,'Gesto: '+s['gesto'],438,12)
    wrap(x,top-192,'Prueba: '+s['prueba'],438,11,color=MUTED)
done()

# 3. Actores y sustitutos
page(3,'¿De quién estamos hablando?','Una experiencia puede generar valores distintos para quien la crea, la utiliza y la facilita.')
xs=[48,380,712]
roles=[('Quien crea','«Quiero probar una idea»','Tiempo hasta la primera interacción utilizable','TouchDesigner, MadMapper, programación o ayuda técnica'),('Quien participa','«Quiero hacer, entender o imaginar»','Duda resuelta, acción comprendida o historia transformada','Manual, teléfono, explicación, video o juego físico'),('Quien habilita','«Quiero una experiencia que pueda sostener»','Preparación, facilitación, continuidad y repetición','Actividad guiada, contenido preparado u otra instalación')]
for x,(title,quote,value,alt) in zip(xs,roles):
    box(x,340,284,255,PAPER,LINE)
    text(x+18,564,title,21,True)
    wrap(x+18,533,quote,248,15,color=ACCENT)
    text(x+18,474,'Valor a observar',11,True,color=MUTED)
    wrap(x+18,454,value,248,14)
    text(x+18,398,'Alternativas',11,True,color=MUTED)
    wrap(x+18,378,alt,248,12)
text(48,289,'Preguntas que separan una posibilidad de una necesidad',18,True)
qs=['¿Cuándo fue la última vez que necesitó hacer esto?','¿Cómo lo resuelve hoy y en qué momento busca ayuda?','¿Qué se perdería si lo lleváramos a una pantalla?','¿Qué haría falta para repetir la experiencia sin nuestro equipo?']
for i,q in enumerate(qs):text(68,247-i*40,f'{i+1}.  {q}',15)
done()

# 4. Panorama
page(4,'Ya existe un territorio. Hay que encontrar una diferencia.','Los referentes se parecen en aspectos distintos: visión, flujo, tecnología o resultado. No son equivalentes entre sí.')
srcs={s['id']:s for s in DATA['fuentes']}
for i,f in enumerate(DATA['familias']):
    y=581-i*96
    text(48,y,f['titulo'],16,True,ACCENT)
    wrap(48,y-18,f['pregunta'],246,12,color=MUTED)
    refs=[srcs[k] for k in f['ids'] if k not in ['F03','F12','F10','F24','F30','F29']]
    names=' · '.join(s['nombre'] for s in refs)
    wrap(330,y+2,names,650,14)
    line(48,y-66,992,y-66)
text(48,75,'Diferencias por comprobar: preparación · adaptación · interacción · participación.',13,True)
done()

# 5. Concurso y evidencia
page(5,'Una demo que haga visible el valor','Rúbrica proporcionada por Pablo: 0-5 en cada dimensión. Los pesos suman 100 %.')
x=48
for i,r in enumerate(DATA['rubrica']):
    width=944*r['peso']/100
    C.setFillColor(ACCENT if i==3 else HexColor(['#BCD2CB','#A9C4BC','#92B8AC','#197B78','#729E92'][i]))
    C.rect(x,535,width-3,44,fill=1,stroke=0)
    text(x+9,549,str(r['peso'])+' %',15,True,color=PAPER if i==3 else INK)
    x+=width
for i,r in enumerate(DATA['rubrica']):
    y=494-i*57
    text(48,y,r['nombre'],14,True)
    text(260,y,r['evidencia'],14)
line(48,212,992,212)
text(48,180,'Persona actúa',19,True);arrow(237,186,344,186)
text(371,180,'Sistema responde',19,True);arrow(598,186,684,186)
text(710,180,'Algo cambia',19,True)
wrap(48,152,'Una intención y un gesto',240,13,color=MUTED)
wrap(371,152,'Percibe, decide y proyecta',260,13,color=MUTED)
wrap(710,152,'Consecuencia reconocible',280,13,color=MUTED)
text(48,77,'Visión ≠ implementación ≠ comportamiento comprobado en el montaje.',14,True)
done()

# 6. Hallazgos y exploración
page(6,'Qué cambió y qué vamos a explorar','La investigación abre opciones. El montaje, la demanda y el mercado todavía no están validados.')
items=[('La combinación tiene antecedentes','Proyección, gestos y LLM aparecen en Tangible AI Mind-map. La diferencia necesita una descripción concreta.','F15, F16'),('El contexto cambia el resultado','La guía proyectada puede ayudar a novatos y entorpecer a expertos. El juego compartido puede requerir facilitación.','F22, F25'),('La preparación sigue siendo parte del valor','El montaje, la alineación y la creación de comportamientos importan tanto como el resultado visual.','F02, F03, F12, F27')]
for i,(title,body,refs) in enumerate(items):
    y=582-i*105
    text(48,y,title,18,True)
    wrap(48,y-17,body,880,14)
    text(48,y-66,refs,10,color=MUTED)
line(48,278,992,278)
text(48,242,'La conversación empieza con una escena',20,True)
wrap(48,214,'Si la demo salió increíble, ¿qué viste hacer a una persona que te hizo pensar: esto tiene sentido?',904,23,bold=True,color=ACCENT)
for i,label in enumerate(['QUIÉN','QUÉ QUIERE','QUÉ HACE','QUÉ CAMBIA','QUÉ PROBAR']):
    x=48+i*190
    text(x,108,label,11,True,color=MUTED)
    line(x,78,x+163,78)
done()

# 7. Referencias con enlaces
page(7,'Fuentes para seguir explorando','30 referencias de la investigación del 21 nov. Los enlaces de cada título llevan al original.')
for i,s in enumerate(DATA['fuentes']):
    x=48+(i//15)*494;y=593-(i%15)*34.2
    label=f"{s['id']}  {s['nombre']}"
    text(x,y,label,11,True,color=ACCENT)
    C.linkURL(s['url'],(x,y-2,x+min(460,pdfmetrics.stringWidth(label,'StudyBold',11)),y+12),relative=0)
    wrap(x,y-6,s['tipo'],448,9,color=MUTED)
text(48,65,'Tipo de fuente ≠ validación independiente. Estudios, proyectos y proveedores tienen alcances distintos.',11,color=MUTED)
done()
C.save()
print(OUT)
