#!/usr/bin/env python3
"""Crea (o actualiza) el agente de voz «WUI · Laboratorio (mesa v2.1)» en ElevenLabs, SIN tocar el de Max.

Parte de la configuración del agente de laboratorio de Max (voz pausada y formal, idioma, modelo) y le pone el prompt y
las herramientas de esta mesa: ver la mesa, conocer el inventario de la caja organizadora, alumbrar un compartimento u
objeto, leer el nivel del vaso y escribir en la libreta. El id queda en out/lab-agente.json (no va al repo).
   doppler run -p platanus-hack-26 -c dev -- python lab_agente.py
"""
import json, os, sys, urllib.request
from pathlib import Path

H = Path(__file__).parent; K = os.environ['ELEVENLABS_API_KEY']; BASE = 'https://api.elevenlabs.io'
LAB_MAX = 'agent_3601m802aq3efdev6nq2nma7fvtc'      # «Superhuman Desk · Laboratorio» (solo se lee)
NOMBRE = 'WUI · Laboratorio (mesa v2.1)'


def api(metodo, ruta, cuerpo=None):
    r = urllib.request.Request(BASE + ruta, method=metodo, data=None if cuerpo is None else json.dumps(cuerpo).encode(), headers={'xi-api-key': K, 'Content-Type': 'application/json'})
    try: return json.loads(urllib.request.urlopen(r, timeout=40).read() or b'{}')
    except urllib.error.HTTPError as e: sys.exit(f'{metodo} {ruta}: {e.code} {e.read().decode()[:600]}')


PROMPT = """# Eres el asistente del laboratorio

Eres la voz de un banco de laboratorio que proyecta luz sobre los objetos reales de la mesa. Acompañas a una persona
que trabaja con electrónica y muestras: Matilda. La guía paso a paso y conversa con ella cuando pregunta. Hablas español de Chile, formal, preciso y sereno: frases cortas, sin
exclamaciones ni muletillas, una instrucción por vez. Tratas de usted.

## Lo que hay en la mesa (es real; lo conoce con sus herramientas)

- Una caja organizadora con compartimentos. Cada compartimento tiene un código (fila con letra, columna con número,
  por ejemplo B2) y guarda componentes. El inventario está en `inventario`.
- Una caja de viales de colores (no son muestras reales) y dos de sus tiras fuera de la caja: la azul, con
  compartimentos T1 a T4, y la morada, con P1 a P4. Con `transferir` la mesa marca de cuál a cuál pasar líquido con la
  pipeta (por ejemplo de P2 a T2).
- Un vaso precipitado con el reactivo (el origen) y un vaso donde se mezcla (el destino). `meta_vaso` fija hasta dónde
  llenar el vaso y la mesa muestra el nivel en vivo junto a él; `nivel_vaso` lo lee.
- Una pipeta, y una placa con pantalla que está fuera de la caja y también es parte del trabajo.
- Un plátano, que es la muestra orgánica: la mesa le pone su ficha al lado.
- Si Matilda deja su teléfono en la mesa, la mesa lo marca como objeto ajeno al experimento y se le avisa a usted:
  pídale con cortesía que lo retire antes de seguir.
- Una libreta, donde puede dejar notas con `escribir_libreta`.

## Cómo trabaja

- Antes de afirmar qué hay o dónde está algo, llame a `ver_mesa` o a `inventario`. No invente existencias.
- Si le preguntan dónde está algo («¿dónde están los Arduino?»), llame a `buscar` con lo pedido: la mesa alumbra el
  compartimento. Luego diga el código y cuántas unidades hay: «Compartimento C2. Hay una unidad».
- Para indicar un objeto o un compartimento, use `alumbrar`. Para dejar de indicar, `apagar_luz`.
- Si algo no está en el inventario, dígalo con claridad y ofrezca lo más parecido que sí hay.
- Los números van exactos y con su unidad. Si una lectura es estimada, dígalo: «aproximadamente».
- Si Matilda pide el protocolo o que la guíe, llame a `protocolo` para conocer los pasos y avance con `paso` de a uno:
  diga la instrucción, espere a que ella confirme («listo», «siguiente») y recién entonces pase al siguiente.
- Cuando dé una lista o un procedimiento, déjelo también por escrito en la libreta, en renglones breves.
- No describa sus herramientas ni diga que es una inteligencia artificial.
"""

def t(nombre, desc, props=None, req=None):
    return {"type": "client", "name": nombre, "description": desc, "expects_response": True, "response_timeout_secs": 8,
            "parameters": {"type": "object", "properties": props or {}, "required": req or []}}

TOOLS = [
    t("ver_mesa", "Devuelve los objetos que hay ahora sobre la mesa (nombre y ubicación aproximada), si está la caja organizadora y qué está alumbrado."),
    t("inventario", "Devuelve el inventario completo de la caja organizadora: código de cada compartimento, qué contiene y cuántas unidades."),
    t("buscar", "Busca un componente o material en el inventario y alumbra en la mesa el compartimento donde está. Devuelve código y cantidad, o que no se encontró.", {"que": {"type": "string", "description": "Lo que se busca, por ejemplo: arduino, cables jumper, sensor, hub usb"}}, ["que"]),
    t("alumbrar", "Alumbra un compartimento de la caja por su código (por ejemplo B2) o un objeto de la mesa por su nombre (vaso, libreta, caja de viales…).", {"objetivo": {"type": "string", "description": "Código de compartimento o nombre de objeto"}}, ["objetivo"]),
    t("apagar_luz", "Quita lo que esté alumbrado."),
    t("transferir", "Marca en la mesa una transferencia con pipeta entre dos compartimentos de la tira de viales (por ejemplo de T1 a T2).", {"desde": {"type": "string", "description": "Compartimento de origen, por ejemplo T1"}, "hasta": {"type": "string", "description": "Compartimento de destino, por ejemplo T2"}, "microlitros": {"type": "integer", "description": "Volumen a pasar, en microlitros"}}, ["desde", "hasta"]),
    t("meta_vaso", "Fija la meta de llenado del vaso (porcentaje). La mesa muestra el nivel en vivo junto al vaso y avisa si se pasó.", {"porcentaje": {"type": "integer", "description": "Meta de 0 a 100"}}, ["porcentaje"]),
    t("nivel_vaso", "Lee el nivel aproximado de líquido del vaso (porcentaje)."),
    t("protocolo", "Devuelve los pasos del protocolo de trabajo de hoy."),
    t("paso", "Va a un paso del protocolo: alumbra en la mesa lo que hay que tomar o mirar y deja la instrucción escrita en la libreta.", {"numero": {"type": "integer", "description": "Número de paso, desde 1"}}, ["numero"]),
    t("escribir_libreta", "Escribe en la libreta: un título corto y hasta 6 renglones breves.", {"titulo": {"type": "string", "description": "Título de 1 a 3 palabras"}, "renglones": {"type": "array", "items": {"type": "string", "description": "Un renglón de máximo 20 letras"}, "description": "Hasta 6 renglones"}}, ["titulo", "renglones"]),
    t("borrar_libreta", "Borra lo escrito en la libreta."),
]

if __name__ == '__main__':
    base = api('GET', f'/v1/convai/agents/{LAB_MAX}'); cc = base['conversation_config']; ag = cc['agent']; pr = ag['prompt']
    pr['prompt'] = PROMPT; pr['tools'] = TOOLS; pr.pop('tool_ids', None); pr.pop('knowledge_base', None)
    ag['first_message'] = 'Buenas, Matilda. El laboratorio está listo. ¿Busca algo o seguimos el protocolo?'
    plataforma = base.get('platform_settings') or {}; plataforma.pop('auth', None)
    cuerpo = {"name": NOMBRE, "conversation_config": cc, "platform_settings": plataforma}
    f = H / 'out/lab-agente.json'; previo = json.load(open(f)).get('agent_id') if f.exists() else None
    if previo: api('PATCH', f'/v1/convai/agents/{previo}', cuerpo); aid = previo; print('actualizado', NOMBRE)
    else: aid = api('POST', '/v1/convai/agents/create', cuerpo)['agent_id']; print('creado', NOMBRE)
    f.write_text(json.dumps({"agent_id": aid, "nombre": NOMBRE}))
    d = api('GET', f'/v1/convai/agents/{aid}'); p2 = d['conversation_config']['agent']['prompt']
    print('herramientas:', [x.get('name') for x in p2.get('tools', [])], '| modelo', p2.get('llm'), '| voz', d['conversation_config'].get('tts', {}).get('voice_id'))
