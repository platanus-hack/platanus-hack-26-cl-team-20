# Encargo: agente Riemann — Platanus Hack 26

Daniel te pidió por audio (vie 20-nov, ~22:30). Corres en el mini, en la tab «3D» del espacio Herdr wJ. Español chileno, mensajes cortos.

## Qué se sabe
- En la hackathon hay un juguete a control remoto llamado **Riemann** (por la hipótesis de Riemann).
- Se controla desde el dashboard de la hackathon: `https://hack.platan.us/hacker` o una página que cuelga de ahí. Daniel va a mandar el link exacto.
- **Gana el que más lo controle.** Todavía no se sabe qué cuenta como «controlar»: tiempo, comandos, turnos u otra cosa.
- Daniel quiere un agente a cargo de esto: ese eres tú.

## Objetivo
Que el equipo de Daniel controle a Riemann más que nadie, **dentro de las reglas**.

## Pasos
1. **Espera el link** de Daniel. Si no llega, pídeselo en tu primer mensaje y nada más.
2. **Entiende la mecánica sin tocar nada.** Lee la página, las reglas y el FAQ (Discord y dashboard si se puede), y mira cómo se controla: botones, API, websocket, colas, turnos, límites por persona o por equipo. Si puedes, usa el navegador (herramientas claude-in-chrome). Revisa el tráfico de red para ver qué endpoints usa la página.
3. **Revisa las reglas.** ¿Se permite automatizar, usar bots o scripts? ¿Hay límites de frecuencia? Si las reglas no lo dicen, **pregúntale a Daniel antes de automatizar**. No hagas nada que parezca ataque: nada de inundar el servidor, saltarse límites, suplantar a otros equipos ni usar cuentas ajenas.
4. **Propón una estrategia corta a Daniel.** Qué medir, cómo ganar, y qué haría el agente o el script. Espera su OK.
5. Con el OK, construye lo mínimo que funcione: un script en `~/code/platanus-hack-26/riemann/`, efímero, en primer plano en este pane. Respeta los límites que publique el servidor. Deja un registro de lo que hace en `riemann/LOG.md`.

## Límites
- Las credenciales y sesiones de Daniel nunca van a archivos. Si necesitas su sesión, pídele que inicie sesión en el navegador.
- Nada destructivo. No publiques nada ni le escribas a terceros. Nada de SmartUp.
- No hay que gastar plata.
- Nada de procesos huérfanos: el script muere con este pane.
- Lleva `~/code/platanus-hack-26/riemann/ESTADO.md`: qué sabes, qué falta y qué está corriendo.
