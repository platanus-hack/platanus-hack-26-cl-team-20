# Voz opcional y configuración propia

La demo funciona sin cuenta ni voz. Para usar la secretaria, instala los extras
desde el entorno activo: `python -m pip install -r requirements-optional.txt`.
El README de [secretaria](../playground/secretaria/README.md) describe la integración
original y su historia. Esta copia no incluye credenciales ni configuración del dueño.

Configura localmente `~/.config/sl-scanner/secretaria/config.json`. Usa tus propios
IDs de voz y modelos disponibles en tu cuenta. Los valores siguientes son marcadores,
no una configuración lista para publicar:

```json
{
  "voz_id": "TU_VOZ_ID",
  "voces_respaldo": [],
  "llm": "TU_MODELO_COMPATIBLE",
  "tts_modelo": "TU_MODELO_DE_VOZ",
  "audio_entrada": "",
  "audio_salida": "",
  "runtime_repo": "/RUTA/ABSOLUTA/sl-scanner",
  "orq_cli": "/usr/bin/false",
  "orq_unidad": "",
  "modo": "local"
}
```

La llave se resuelve desde `ELEVENLABS_API_KEY`, un archivo privado de credenciales
o el llavero según `config.py`. No la agregues al proyecto. Conserva la configuración
y credenciales fuera de la carpeta a compartir. La instalación en otra máquina no
necesita Herdr ni el orquestador original; `/usr/bin/false` evita ejecutar su
integración al anotar feedback. Un campo vacío recuperaría el default histórico.

Antes de publicar, revisa la configuración de tu cuenta, permisos y costo de voz.
Una vez configurada por su titular:

```sh
python -m playground.secretaria publicar --dry-run
python -m playground.secretaria publicar
python -m playground.secretaria voz
# Para consultar estado:
python -m playground.secretaria estado
```

`publicar` crea o actualiza un agente en ElevenLabs. `voz` envía audio a ese servicio.
No se ejecutó ninguno de esos pasos para fabricar el ZIP. La vía local no requiere
túnel público. El modo navegador depende además del cliente JS indicado en la guía
original; ese archivo externo no se incluye.
