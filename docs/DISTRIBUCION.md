# Distribución del nodo pcx

Cómo se construyen y publican los binarios que instalan `scripts/install/install.sh` e `install.ps1`.
Guía para quien instala: [INSTALAR.md](INSTALAR.md).

## Herramienta: workflow propio, no `dist`

`dist` (antes `cargo-dist`, axodotdev) sigue mantenido: v0.34.0 salió el 2 de noviembre de 2026 y el repo tiene
commits de esta semana. Aun así se eligió un workflow propio (`.github/workflows/release.yml`):

1. Las librerías nativas que deja `ort` (`copy-dylibs`) cambian por target y salen del directorio de compilación;
   `dist` solo empaqueta archivos fijos (`include`), no lo que aparece junto al binario en cada target.
2. Los instaladores que genera `dist` no tienen `--dry-run`, la pregunta antes de tocar el PATH ni el enganche con
   las skills de `integrations/`. Habría que mantener instaladores propios igual.
3. `dist` regenera su workflow y `dist plan` falla si se edita a mano (salvo `allow-dirty`); las dependencias del sistema (fontconfig, v4l)
   y los runners (ARM nativo, Intel cruzado desde Apple Silicon) son más fáciles de controlar en ~150 líneas propias.

## Qué publica un release

Tag `v*` → `release.yml` compila `pcx`, `pcx-renderer` y `pcx-capture` (con la feature `uvc`, la cámara real) en:

| Target | Runner | Nota |
|---|---|---|
| `aarch64-apple-darwin` | `macos-latest` | |
| `x86_64-apple-darwin` | `macos-latest` (cruzado) | experimental: hoy falla, ver ONNX Runtime |
| `x86_64-pc-windows-msvc` | `windows-latest` | zip, con el runtime de Visual C++ en `bin/` |
| `x86_64-unknown-linux-gnu` | `ubuntu-22.04` | glibc 2.35; fontconfig y Vulkan de la distro |
| `aarch64-unknown-linux-gnu` | `ubuntu-22.04-arm` | |

Cada paquete `pcx-<target>.tar.gz` (o `.zip`) trae `pcx-<target>/bin/` (binarios y librerías nativas),
`pcx.toml` (copia de `node/pcx.toml`) y `VERSION`. Además: `pcx-integrations.tar.gz` (si `integrations/` existe en
el commit), los dos instaladores y `SHA256SUMS`. Los nombres no llevan versión, así que
`releases/latest/download/<archivo>` siempre apunta al último.

Publicar: `git tag v0.1.0 && git push origin v0.1.0`. Un tag con guion (`v0.2.0-rc.1`) sale como prerelease.

Las actions están fijadas por SHA completo (la versión va en un comentario): una action comprometida no puede
cambiar binarios ni checksums. Para actualizarlas, resolver el tag nuevo con `gh api repos/<dueño>/<action>/commits/<tag> --jq .sha`.

**Windows y el runtime de Visual C++.** Los `.exe` usan el CRT dinámico (/MD). No se usa `+crt-static` porque el
ONNX Runtime precompilado de pyke es /MD y no enlaza con CRT estático (pykeio/ort#473). En su lugar, el workflow
copia `vcruntime140*.dll` y `msvcp140*.dll` de `Microsoft.VC14x.CRT` del runner a `bin/` (despliegue app-local que
Microsoft permite); el UCRT ya viene en Windows 10 y 11. Falta probar el zip en un Windows limpio.

**Linux y las librerías del sistema.** El paquete no trae `libfontconfig.so.1` (la enlaza el renderer) ni
`libvulkan.so.1` (wgpu la carga con dlopen). `install.sh` corre `ldd` sobre los binarios y avisa qué paquete instalar
(`libfontconfig1`, `libvulkan1` + `mesa-vulkan-drivers` en Debian y Ubuntu). El workflow imprime el `ldd` de cada binario.

## Pesos de los modelos

El job `modelos` de `release.yml` corre `scripts/install/bajar-modelos.sh`, que baja los archivos de
`scripts/install/modelos.json` (URL fija a una revisión de Hugging Face), verifica tamaño y sha256 y avisa si un sha256
no aparece en `node/crates` (el motor ignoraría la copia del paquete). Cada paquete lleva `models/` con los `.onnx`,
`manifest.json`, `SHA256SUMS` y `LICENSES/Apache-2.0.txt`. Los pesos nunca se suben al repo (`*.onnx` en `.gitignore`).

| Archivo | Bytes | Licencia | Fuente |
|---|---|---|---|
| `mobile_sam_image_encoder.onnx` | 28 157 093 | Apache-2.0 (pesos MobileSAM; export Acly etiquetado MIT) | `huggingface.co/Acly/MobileSAM` |
| `sam_mask_decoder_single.onnx` | 16 501 323 | Apache-2.0 (decodificador de SAM) | `huggingface.co/Acly/MobileSAM` |
| `palm_detection_mediapipe_2023feb.onnx` | 3 905 734 | Apache-2.0 | OpenCV Zoo (`huggingface.co/opencv/…`) |
| `handpose_estimation_mediapipe_2023feb.onnx` | 4 099 621 | Apache-2.0 | OpenCV Zoo (`huggingface.co/opencv/…`) |

Comprimidos suman unos 41 MB por paquete. `pcx` se compila con `--features pcxd/onnx` en macOS arm64, Linux x64 y
Windows x64 solo si el crate ya define esa feature (si no, el build avisa y sigue sin ella).

## ONNX Runtime (`ort`)

`pcx` enlaza ONNX Runtime (`pcxd` → `pcx-hands` → `ort`, para `pcx worker hands`):

- Con `download-binaries`, `ort` enlaza ONNX Runtime **estático** (`libonnxruntime.a`) en macOS y Linux: no hay
  dylib que copiar. En Windows el build de pyke es el de DirectML y `copy-dylibs` puede dejar DLLs al lado del
  `.exe`. El workflow copia cualquier `*.dll`, `*.dylib` o `*.so*` de `target/<target>/release/` a `bin/`
  (con `cp -L`, porque son symlinks a la caché de `ort`) y enlaza con rpath `@executable_path` / `$ORIGIN`.
- **pyke no publica ONNX Runtime para `x86_64-apple-darwin`**, así que ese target falla al enlazar. Está en la
  matriz como experimental (`continue-on-error`): el release sale sin ese paquete e `install.sh` avisa en un Mac
  Intel que la versión no lo trae. Para tenerlo: compilar ORT propio y pasar `ORT_LIB_LOCATION`, o dejar
  `pcx-hands` detrás de una feature de `pcxd` que se apague para Intel.

## macOS: Gatekeeper, cuarentena, firma y cámara

**Sin firmar funciona con `curl | sh`.** Gatekeeper solo revisa archivos con el atributo `com.apple.quarantine`, que
ponen los navegadores, AirDrop o Mail. `curl` no lo pone, así que los binarios que baja `install.sh` corren sin aviso.
Si alguien baja el `.tar.gz` con el navegador, macOS dirá «no se puede verificar el desarrollador»; se arregla con
`xattr -dr com.apple.quarantine ~/.pcx/bin` o clic derecho → Abrir. En Apple Silicon todo binario necesita al menos
firma ad hoc; el linker de Rust ya la pone, así que no hay que hacer nada.

**Firma y notarización (opcional).** Solo vale la pena si se distribuye por navegador o en un `.pkg`. Requiere la
cuenta de Apple Developer (99 USD/año) y estos secretos del repo:

| Secreto | Qué es |
|---|---|
| `APPLE_CERT_P12` / `APPLE_CERT_PASSWORD` | certificado «Developer ID Application» en base64 y su clave |
| `APPLE_ID` / `APPLE_TEAM_ID` / `APPLE_APP_PASSWORD` | cuenta para `notarytool` (contraseña de app) |

Pasos en el job de macOS, entre compilar y empaquetar: importar el `.p12` en un keychain temporal
(`security create-keychain` + `security import`), firmar cada binario con
`codesign --force --options runtime --timestamp --entitlements pcx.entitlements -s "Developer ID Application: …"`,
comprimir en zip y `xcrun notarytool submit pcx.zip --wait`. Un binario suelto no se puede «engrapar» (`stapler`
solo sirve para `.app`, `.pkg` y `.dmg`): Gatekeeper consulta el ticket en línea la primera vez.
`pcx.entitlements` debe incluir `com.apple.security.device.camera` para que el runtime endurecido deje usar la cámara.

**Permiso de cámara (TCC).** La primera vez que `pcx-capture` abre la cámara, macOS muestra el diálogo de permiso.
Para un binario de línea de comandos el permiso queda a nombre de la terminal que lo lanzó (Terminal, iTerm, VS Code),
no de `pcx`. Si se niega, hay que activarlo en Ajustes del Sistema → Privacidad y seguridad → Cámara y reiniciar la
terminal. `tccutil reset Camera` lo vuelve a preguntar.

## Repo privado

Mientras el repo sea privado, `github.com/<repo>/releases/...` pide sesión y `curl | sh` no funciona para terceros.
Opciones: hacer público el repo, o subir los archivos del release a un host público (la web en Render, un bucket) y
usar `PCX_BASE_URL=https://host/pcx/v0.1.0` (carpeta con los archivos y `SHA256SUMS`). El `<url>` de los comandos
de instalación es donde se publique `install.sh`; lo natural es servirlo desde la web de pcx.

## Probar los instaladores sin release

```sh
# carpeta con pcx-<target>.tar.gz y SHA256SUMS (shasum -a 256 * > SHA256SUMS), servida en local
python3 -m http.server 8765 &
PCX_BASE_URL=http://127.0.0.1:8765 PCX_HOME=/tmp/pcx-prueba sh scripts/install/install.sh --dry-run
```
