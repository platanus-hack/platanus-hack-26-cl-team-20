# Cómo cargar las cifras (operador, 2 minutos) · v2

Estado: borrador para el ensayo; nada probado en la mesa.
Actualizado: domingo 22-nov-2026, 04:30.
Responsable: Matilda Roberts.


**Sin probar en la mesa real.** Es el paso 3 de la hoja del operador (`hoja-operador.md`).

**Cuándo:** una sola vez, **después** de crear las pestañas «Laboratorio» y la del sistema solar (paso 2 de la hoja) y **antes** de lanzar cualquier escena. Las cifras quedan guardadas y sobreviven a `armar`, `seguir`, `limpiar`, al laboratorio y al escritorio. No hay que recargarlas.

1. En una terminal, con el mismo bloque de «Copiar» que las demás (deja `PCX_MCP_URL` y `PCX_USER_TOKEN`):

   ```sh
   cd [CARPETA]/experiences/05-demo/ensayo/cifras
   python3 cargar.py estado     # solo lee. Debe listar tus pestañas
   python3 cargar.py --crear    # crea Inicio, Escritorio y Cierre, y carga todo
   ```

2. Debe terminar con **`LISTO: todas las cifras están en su pestaña.`** y cinco líneas «cargada y comprobada». Durante 1 segundo la mesa muestra las pestañas nuevas y vuelve sola a la que estaba.
3. Mira cada pestaña en la mesa: que el texto no salga cortado ni encima de un objeto.

**Sistema solar, los dos casos (el script busca la pestaña solo):**

- **Camino A** (el de la hoja): la pestaña se llama «Sistema solar». Debe decir `solar «Sistema solar»: cargada y comprobada`.
- **Camino B** (motor): la pestaña se llama `solar`. Debe decir `solar «solar»: cargada y comprobada`.
- Si existen las dos, carga en las dos. Si la pestaña tiene otro nombre: `python3 cargar.py --solar "Nombre exacto"`.

**Si algo sale distinto:**

| Dice… | Qué hacer |
|---|---|
| `NO SE TOCÓ NADA` + token o dirección | Apretar «Copiar» en la consola, pegar el bloque de nuevo y repetir |
| `NO SE TOCÓ NADA. Faltan las pestañas…` | Repetir con `--crear` |
| `SIN CARGAR: no existe la pestaña «Sistema solar» ni «solar»` (o «Laboratorio») | Crear esa pestaña (paso 2 de la hoja) y repetir `python3 cargar.py` |
| `INCOMPLETO` o `FALLÓ` | Repetir `python3 cargar.py`. Se puede repetir las veces que haga falta: no duplica nada |
| `OJO: NO pude volver a «…»` | Hacer clic en esa pestaña en la consola |
| `AVISO: pisa la barra o el botón de voz` | La consola está girada. Avisar: esa cifra queda bajo la barra |

**Reglas:**

- `--crear` solo con el sistema solar (T3) y el laboratorio (T4) **detenidos**: mientras se crea una pestaña, lo que dibujen caería en ella.
- Sin `--crear` el script no cambia de pestaña: se puede correr en cualquier momento, incluso con escenas andando.
- Antes de la demo: `python3 cargar.py estado` debe decir «cifra puesta» en las 5.
- Quitar una cifra que estorba: `python3 cargar.py quitar solar` (o `inicio`, `escritorio`, `laboratorio`, `cierre`, `todo`). Solo borra las cifras, nunca lo de las escenas.
- Sin cifras la demo corre igual.
