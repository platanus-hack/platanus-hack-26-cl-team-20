# Correr el Sistema solar en la mesa de SAT

Para el agente de SAT, en su Mac, con la sesión de su consola. Primero el camino **A** (sin motor, unos 5 min). El
**B** (con motor) solo si sobra tiempo. Todo va en worktrees aparte: no se toca el checkout de la consola.

## A. Sin motor: el conductor por el MCP de tu sesión

1. **Worktree desde `main`** (aparte del checkout de la consola, sin rama propia):
   ```sh
   cd <tu clon de satelerd/platanus-hack-26>
   git fetch origin
   git worktree add --detach ../hack26-solar origin/main
   cd ../hack26-solar/escenas/sistema-solar && uv sync
   ```

2. **El MCP de tu sesión por variable de entorno.** El token no se imprime ni se pega en ningún archivo, chat o
   commit: va directo a la variable.
   - Si tu Claude Code ya tiene la mesa como `pcx` (`claude mcp add ... pcx`):
     ```sh
     export PCX_MCP_URL="$(claude mcp get pcx | sed -n 's/^ *URL: *//p' | head -1)"
     export PCX_USER_TOKEN="$(claude mcp get pcx | sed -n 's/.*Bearer \([^" ]*\).*/\1/p' | head -1)"
     ```
   - Si no, con el botón «Copiar» de la consola (queda en el portapapeles):
     ```sh
     export PCX_MCP_URL="$(pbpaste | grep -o 'https\?://[^ "]*/mcp' | head -1)"
     export PCX_USER_TOKEN="$(pbpaste | sed -n 's/.*Bearer \([^" ]*\).*/\1/p' | head -1)"
     ```
   - En los dos casos:
     ```sh
     export PCX_CAMARA=cam-2   # la cenital (usb-webcam)
     echo "url=$PCX_MCP_URL token=${#PCX_USER_TOKEN} caracteres cámara=$PCX_CAMARA"   # el token nunca a la vista
     ```

3. **Revisar cámara y calibración** (no proyecta nada):
   ```sh
   uv run python conductor.py captura
   ```
   Deja las fotos en `prueba-vivo/` de la escena, que está en el `.gitignore`. Tiene que decir `cámara=cam-2 calibrada=True`. Si dice `False`, calibra esa cámara en la consola (botón
   Calibrar) o usa en `PCX_CAMARA` la cámara que sí está calibrada.

4. **Pestaña propia:** `uv run python conductor.py pestana "Sistema solar"`.

5. **Armar** (look `clean:true` → percepción → planetas por tamaño, en el orden en que estén hoy, nada fijo →
   publica `solar:sol`, `solar:<planeta>`, `solar:lamina-0` → proyecta Sol, texturas, órbitas y lámina anclados):
   ```sh
   uv run python conductor.py armar
   ```
   Imprime qué bola quedó como qué planeta y las dudas. Si dice «No veo bolas», mira
   `estado/referencia.jpg`: el canasto tiene que estar entero bajo la cenital y a más de 150 mm del borde izquierdo
   (la barra de pestañas).

6. **Seguir el canasto** (otra terminal, con las mismas variables; queda corriendo):
   ```sh
   uv run python conductor.py seguir 2>&1 | tee -a prueba-vivo/seguir.log
   ```

7. **Verificar:**
   - En la mesa, cada bola tiene su planeta, el engranaje es el Sol, hay órbitas y nada cae sobre la barra
     izquierda.
   - Mueve el canasto entero. El log dice `canasto: +12.3°, (+40, -10) mm · 9/10 bolas · error 1.2 mm` y los
     planetas lo siguen. Al quedar quieto, las órbitas se re-anclan.
   - Deja el dedo quieto sobre Saturno en la lámina. El log dice `lámina: tocaron saturno`, Lumo vuela al Saturno
     del canasto y se abre su ficha.
   - Prueba un pedido como lo haría un agente: `uv run python conductor.py pedir paso_siguiente`. El log dice
     `pedido paso_siguiente: ok` y se abre la ficha del planeta siguiente.

8. **Capturas cada 30 s** (otra terminal, en `escenas/sistema-solar`):
   ```sh
   while true; do uv run python conductor.py captura; sleep 30; done
   ```
   Quedan en `prueba-vivo/`, fuera de git. **Nunca con `git add`, commit ni push**: en las fotos pueden salir personas.
   Si hay que mostrarlas, por un canal directo. Cada vuelta deja:
   - `HHMMSS-camara.jpg`: lo que ve la cenital;
   - `HHMMSS-proyectado.jpg`: la misma foto con lo proyectado dibujado encima;
   - `HHMMSS-mm.jpg`: la vista en mm;
   - una línea en `log.txt`.

   Las variables del paso 2 tienen que estar en esa terminal.
   **Para volver a armar, solo `armar` (nunca `limpiar && armar`).** `armar` mantiene cada planeta en su bola por
   posición y los ids `solar:*` no cambian. Lo que no ve queda un armado más como fantasma y sale si tampoco aparece en
   el siguiente. `limpiar` saca todos los objetos, y Lumo lo oye como «se fueron todos».

9. **Cerrar:** Ctrl-C en `seguir` y en el bucle, después `uv run python conductor.py limpiar` (quita solo lo de
   `solar`) y vuelve a tu pestaña.

## B. Con motor (si alcanza), según el QA de Manu

1. Worktree del motor desde `main`. Hoy (22-nov) `main` no tiene el motor con las escenas registradas (PR #22) y se
   mergea encima. Si `main` ya tiene el #22, se salta el `git merge`.
   ```sh
   cd <tu clon>
   git fetch origin
   git worktree add --detach ../hack26-motor origin/main
   cd ../hack26-motor && git merge --no-edit origin/max/motor-escenas   # solo mientras main no tenga el #22
   ```
2. Pesos de SAM en `~/.pcx/models` (`ls ~/.pcx/models`).
3. Cámaras (la primera compilación tarda de 5 a 10 min):
   `cd ../hack26-motor && cargo run -p pcxd --release --features onnx -- cameras`.
4. Motor:
   `cargo run -p pcxd --release --features onnx -- motor --camera <usb-webcam> --camera-lateral <c920>`.
   Si hay conflicto de cámara con la consola, va sin `--camera`.
5. La consola de Render no trae `motor.js` hasta el #17. Levanta el gateway local desde la misma rama:
   ```sh
   cd cloud/gateway && uv sync && PORT=9321 PCX_OPEN_SESSIONS=1 uv run python -m pcx_gateway
   ```
   Abre `/mesa` con `motor` y `motor_token`, la URL de emparejamiento que imprime el motor. Calibra.
6. La pestaña activa se tiene que llamar exactamente **`solar`**: así el motor activa el módulo `solar`.
7. Repite los pasos A2 a A5 contra ese gateway, con `PCX_MCP_URL=http://localhost:9321/mcp` y el token de esa
   consola. Agrega `export PCX_SIN_OBJETOS=1`: los `solar:*` los publica el motor y el conductor solo proyecta.
   **No corras `seguir`**: el seguimiento lo hace el motor.
8. Medir:
   - de mano fuera a halo re-anclado, menos de 1 s;
   - calidad de los objetos `solar:*` en `scene`;
   - eventos `solar_armado` y `canasto_movido` en `events_wait`.
