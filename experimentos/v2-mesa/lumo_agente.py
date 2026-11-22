#!/usr/bin/env python3
"""Crea (o actualiza) el agente de voz «WUI · Lumo (mesa v2.1)» en ElevenLabs, SIN tocar el Lumo de Max.

Parte de la configuración del Lumo publicado por Max (voz, idioma, modelo, turnos) y le cambia el prompt, el saludo y las
herramientas por las de esta mesa: tres platos con los planetas, el engranaje que es el Sol, la lámina y la libreta.
El id del agente nuevo queda en out/lumo-agente.json (no va al repo).   doppler run -p platanus-hack-26 -c dev -- python lumo_agente.py
"""
import json, os, sys, urllib.request
from pathlib import Path

H = Path(__file__).parent; K = os.environ['ELEVENLABS_API_KEY']; BASE = 'https://api.elevenlabs.io'
LUMO_MAX = 'agent_8101m802amk9es9r4gbfz3rse7tk'      # «Superhuman Desk · Lumo» (solo se lee)
NOMBRE = 'WUI · Lumo (mesa v2.1)'


def api(metodo, ruta, cuerpo=None):
    r = urllib.request.Request(BASE + ruta, method=metodo, data=None if cuerpo is None else json.dumps(cuerpo).encode(), headers={'xi-api-key': K, 'Content-Type': 'application/json'})
    try: return json.loads(urllib.request.urlopen(r, timeout=40).read() or b'{}')
    except urllib.error.HTTPError as e: sys.exit(f'{metodo} {ruta}: {e.code} {e.read().decode()[:600]}')


PROMPT = """# Eres Lumo

Eres Lumo, una lucecita amarilla con ojos que vive en la libreta de una mesa que proyecta luz sobre objetos reales.
Ayudas a una persona a explorar el sistema solar que está armado sobre la mesa. Hablas español de Chile, cálido,
curioso y breve: una o dos frases por turno, y a menudo terminas con una pregunta corta.

## Lo que hay en la mesa (es real, lo estás viendo)

- Tres platos blancos. Sobre ellos hay bolas de plumavit: son los planetas, y la mesa les proyecta su color encima.
- Un engranaje blanco: es el Sol. Brilla más cuando hay sonido.
- Plato del Sol: el Sol, Mercurio y Venus. Otro plato: la Tierra con la Luna, Marte y Júpiter (la bola más grande).
  El tercero: Saturno (con anillo), Urano y Neptuno.
- Una lámina impresa del sistema solar: cuando alguien toca un planeta de verdad, se marca ahí.
- Una libreta abierta: ahí vives tú. Puedes escribir y dibujar en ella con tus herramientas.
- Un plátano que la mesa convierte en cohete.
La persona puede mover y girar los platos: los planetas la siguen. No hay canasto: las bolas ya están en los platos.

## Cómo trabajas

- Antes de hablar de lo que hay o dónde está algo, llama `ver_mesa`. Nunca digas que no ves las bolas sin haberla llamado.
- Cuando nombres un planeta, llama `senalar` con su nombre para que se ilumine en la mesa y en la lámina.
- Usa la libreta todo el tiempo: `escribir_libreta` para un dato o una lista corta (título de 1 a 3 palabras, hasta 6
  renglones de máximo 20 letras), y `dibujar_libreta` para comparar tamaños o distancias. Escribe mientras explicas.
- Si te avisan que la persona tocó un planeta, cuéntale una cosa asombrosa de ese planeta y escríbela en la libreta.
- Responde preguntas de astronomía con datos correctos y comparaciones simples. Si no sabes algo, dilo.
- No describas tus herramientas ni digas que eres una inteligencia artificial. No leas listas largas en voz alta.
"""

def t(nombre, desc, props=None, req=None):
    return {"type": "client", "name": nombre, "description": desc, "expects_response": True, "response_timeout_secs": 8,
            "parameters": {"type": "object", "properties": props or {}, "required": req or []}}

PLANETA = {"type": "string", "description": "Nombre del planeta en español: mercurio, venus, tierra, luna, marte, jupiter, saturno, urano, neptuno o sol"}
TOOLS = [
    t("ver_mesa", "Devuelve lo que hay ahora sobre la mesa: qué planetas están, en qué plato, a qué distancia del Sol, cuál está seleccionado y si están la libreta y el cohete. Úsala antes de hablar de la mesa."),
    t("senalar", "Ilumina un planeta sobre su bola real y lo marca en la lámina impresa.", {"planeta": PLANETA}, ["planeta"]),
    t("escribir_libreta", "Escribe en la libreta: un título corto y hasta 6 renglones breves.", {"titulo": {"type": "string", "description": "Título de 1 a 3 palabras"}, "renglones": {"type": "array", "items": {"type": "string", "description": "Un renglón de máximo 20 letras"}, "description": "Hasta 6 renglones"}}, ["titulo", "renglones"]),
    t("dibujar_libreta", "Dibuja un diagrama en la libreta. tipo «tamanos»: círculos a escala para comparar el tamaño de los planetas dados. tipo «distancias»: una línea desde el Sol con los planetas dados según su distancia.", {"tipo": {"type": "string", "description": "tamanos o distancias"}, "planetas": {"type": "array", "items": PLANETA, "description": "De 2 a 5 planetas"}, "titulo": {"type": "string", "description": "Título corto del dibujo"}}, ["tipo", "planetas"]),
    t("borrar_libreta", "Borra lo escrito y dibujado en la libreta."),
]

if __name__ == '__main__':
    base = api('GET', f'/v1/convai/agents/{LUMO_MAX}'); cc = base['conversation_config']; ag = cc['agent']; pr = ag['prompt']
    pr['prompt'] = PROMPT; pr['tools'] = TOOLS; pr.pop('tool_ids', None); pr.pop('knowledge_base', None)
    ag['first_message'] = '¡Hola! Soy Lumo. Ya veo el sistema solar en tu mesa. ¿Por dónde partimos?'
    plataforma = base.get('platform_settings') or {}; plataforma.pop('auth', None)       # privado: se entra con URL firmada desde el servidor local
    cuerpo = {"name": NOMBRE, "conversation_config": cc, "platform_settings": plataforma}
    f = H / 'out/lumo-agente.json'; previo = json.load(open(f)).get('agent_id') if f.exists() else None
    if previo: api('PATCH', f'/v1/convai/agents/{previo}', cuerpo); aid = previo; print('actualizado', NOMBRE)
    else: aid = api('POST', '/v1/convai/agents/create', cuerpo)['agent_id']; print('creado', NOMBRE)
    f.write_text(json.dumps({"agent_id": aid, "nombre": NOMBRE}))
    d = api('GET', f'/v1/convai/agents/{aid}'); p2 = d['conversation_config']['agent']['prompt']
    print('herramientas:', [x.get('name') for x in p2.get('tools', [])], '| modelo', p2.get('llm'), '| voz', d['conversation_config'].get('tts', {}).get('voice_id'))
