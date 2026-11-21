# Entrega local para compartir · 18 de octubre de 2026

El paquete se construye con `git archive` de la rama de documentación y añade una
copia de los cambios locales de código autorizados. La revisión y los SHA-256 de
cada archivo exportado quedan en `BUNDLE-MANIFEST.json`, generado dentro del ZIP.
No se incluye historial Git y no se publica ni envía ningún archivo.
La base es la última versión integrada del checkout principal más sus cinco
archivos de código locales pendientes. La rama ajena `codex/p2-rig` contiene tres
commits posteriores todavía sin integrar y queda fuera de esta entrega; el bundle
no fusiona trabajo de otras unidades ni toma archivos de sus worktrees.

## Contenido

- Código completo de `slscan`, `playground`, tests y herramientas Windows admitidas.
- Documentación del proyecto, índice y guías nuevas.
- `requirements.txt`, opcionales y `scripts/demo.py` para partir en el Mac.
- `examples/demo/scene.json`: escena inventada, sin fotografías ni personas.
- Dos imágenes revisadas de `reports/scene_regions`, copiadas sin modificación a
  `docs/imagenes`: manchas de un cuadro y composición de tela/estampados/contornos.
  Son renders de objetos, no fotos de personas ni capturas del escritorio.

## Exclusiones y reemplazos

- `.git`, `.claude`, worktrees, `.venv`, cachés Python, caches de pruebas y archivos del sistema.
- Todo `captures/`: crudos, fotos de la casa, calibraciones y mapas de sesiones privadas.
- Todo `reports/` original, incluso archivos que estaban rastreados por Git; solo se
  incluyen las dos imágenes seleccionadas bajo `docs/imagenes`.
- Correos de preparación Windows (`windows/mail-*.txt`), logs y diagnósticos de equipos.
- Menciones a un cliente y su proyecto en la bitácora, reemplazadas por una nota genérica de fuera de alcance.
- `.env`, claves, certificados, credenciales y configuración/estado personal externos.
- Modelos y pesos externos, carpeta `sltk/` y sus datos de ejemplo.
- Usuarios/rutas personales, hosts, IPs privadas concretas, identificadores de voz
  propios y llaves SSH públicas embebidas se reemplazan por marcadores en la copia exportada.
  Localhost y redes genéricas usadas por validadores/firewalls se conservan.
  `192.0.2.10` es un destino ilustrativo; para usar PC/MIDI configura tu propio host.
- La integración de voz con el orquestador se deja apuntando a `/usr/bin/false` en
  la copia. Voz/modelos se deben configurar con la cuenta de quien recibe el proyecto.

La rama conserva el código original; estos reemplazos ocurren solo al exportar.
`BUNDLE-MANIFEST.json` enumera omisiones rastreadas, archivos sanitizados y cambios
locales incorporados. No se incluyen hashes de secretos ni valores retirados.
Los archivos históricos de `requests/` permanecen como documentación sanitizada.

`sltk` tiene licencia **MIT**, copyright 2014 John De Witt, comprobada en su copia
local. Su licencia permite redistribuir conservando el aviso; se omite porque es
referencia externa, pesa alrededor de 103 MB y no es necesaria para ejecutar la demo.
El aviso MIT se conserva en [THIRD-PARTY-NOTICES](../THIRD-PARTY-NOTICES.txt), dado que
el proyecto documenta algoritmos reimplementados a partir de esa referencia.
Esto no asigna una licencia nueva al resto del proyecto.

## Verificación

La entrega se comprueba descomprimiendo el ZIP en una carpeta nueva: lectura del
manifiesto, hashes, límite de 50 MB comprimido y expandido, compilación Python,
demo por HTTP, creación de un efecto, cambio de parámetro, imagen PNG y apagado.
También se ejecuta el pipeline sintético y pruebas focales del runtime/dashboard/dale.
Resultado en la copia exportada: **51 pruebas focales pasan**, el pipeline sintético
completa sus etapas con `physical_verified=false`, y el dashboard permite crear un
efecto, cambiar color, obtener una imagen 1920×1080 y apagar hasta negro. Se revisó
esa imagen localmente. Los enlaces de las guías nuevas, sintaxis Python/JSON, CRC y
hashes de archivos pasan. El informe final se entrega junto al ZIP como `.verificacion.txt`.

Se reutiliza el entorno local para no descargar dependencias pesadas. Versiones
observadas: Python 3.13, NumPy 2.5.4, SciPy 1.18.1, OpenCV 5.0.0.93, FastAPI 0.143.2,
Uvicorn 0.54.0, JSON Schema 4.26.0, pygame 2.6.1, PyYAML 6.0.3.
No se afirma instalación desde cero en otra máquina, prueba de hardware, voz en la
nube, rendimiento GPU Windows ni precisión física. El ejemplo no requiere esos pasos.

## Repetir la exportación

Desde la rama con los archivos nuevos ya comprometidos:

```sh
python scripts/build_bundle.py --source /RUTA/checkout-original --output /RUTA/sl-scanner-bundle-FECHA.zip
```

El script exige el mismo commit base registrado, usa una lista explícita de cambios
locales y dos imágenes ya revisadas, rechaza binarios no admitidos y posibles secretos,
valida sintaxis/JSON, escribe inventario y SHA-256. No modifica el checkout original.
Una futura entrega requiere volver a revisar imágenes, cambios y dependencias.
