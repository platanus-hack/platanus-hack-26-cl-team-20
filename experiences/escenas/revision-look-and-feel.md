# Revisión de look and feel de los presets de Max

Estado: lista para usar cuando los presets se puedan probar en la mesa.
Actualizado: sábado 21-nov-2026, 23:59.
Base: presets de Max en su PR #28 (`max/escenas-a-main`, que Manu revisó a las 23:48 para la demo) y el [guion de 3 minutos](../05-demo/guion-3min.md).
Rol: Max decidió (19:21) que sus presets son la base y que Matilda afina el look and feel cuando se puedan probar en la mesa.

## 1. Lo que dice el preset y se sigue

Donde el guion v2 de Max y sus presets no coincidían, se sigue el preset. El guion de 3 minutos ya está ajustado.

| Escena | Se sigue el preset |
|---|---|
| Escritorio | Agentes api-cobros, revisor, migración y escritorio; la libreta con el botón «Responde»; sin SÍ y NO junto al mouse |
| Escritorio | Estilo «tinta y papel»: serif, lápiz rojo de editor, respuesta con letra a mano |
| Sistema solar | Si sobra una bola, es Plutón (después la Luna) |
| Laboratorio | Viales A2, B3 y C5; meta de 50 ml con tolerancia de 3 ml; buffer en el vaso (Coca-Cola en la demo) |

Dos cosas para confirmar en la mesa, sin cambiar el preset:
- **Aviso de «objeto contaminado»** (un celular en la mesa): está en el preset, pero no en los 3 minutos. Se deja apagado o fuera del guion.
- **Dónde queda la barra de pestañas** en la consola de la demo: los presets dicen que va a la izquierda (x < 150 mm).

## 2. Qué revisar en la mesa, escena por escena

Para cada escena, mirar de pie, a la distancia del público, y con la cámara de la pantalla grande.

### Común a las tres
- [ ] **Texto de 28 mm o más** en todo lo que deba leerse de pie (regla de `MOTOR.md`).
- [ ] **Negro es no proyectar:** ningún fondo blanco grande que encandile o lave los objetos.
- [ ] **Nada tapa lo que la mano está tocando.**
- [ ] **Un solo halo de foco a la vez.**
- [ ] **Los avisos se entienden sin color** (forma y palabras), por si la cámara cambia los colores.
- [ ] **La cifra proyectada** (72 mm, 4–5 s, en zona libre) se lee desde el público y en la pantalla grande.
- [ ] **Los colores del tema se ven** con la luz real de la sala (sobre todo los tonos tenues).

### Escritorio (tema tinta y papel)
- [ ] El panel «Tus agentes» se lee sin tapar el laptop.
- [ ] La respuesta escrita a mano sobre la libreta se lee (letra de 28 mm: unos 13 caracteres por línea en A4).
- [ ] El rojo de editor (`--sd-foco: #ff5a3c`) se distingue del rojo de peligro: hoy son el mismo color.
- [ ] Ubicar la cifra **+56 %** junto al panel de agentes.

### Sistema solar (tema óleo nocturno)
- [ ] Las órbitas (6 mm, opacidad 0,72) se ven sobre la mesa real y no solo en la vista previa.
- [ ] La placa azul de los paneles (84 % de opacidad) no oscurece la lámina impresa.
- [ ] Lumo y su burbuja se leen; la versión corta tiene hasta 60 letras.
- [ ] Ubicar la cifra **1 de cada 3** junto a la lámina, sin tapar los planetas.

### Laboratorio (tema clínico)
- [ ] El verde agua de foco (`#5fe0d2`) se distingue del verde de ok (`#8be59c`): son parecidos.
- [ ] El medidor de ml y el número grande se leen junto al vaso.
- [ ] «Te pasaste» en rojo se ve con la Coca-Cola en el vaso.
- [ ] Ubicar la cifra **1 de cada 5** al lado del vaso, antes del primer paso.

## 3. Dónde se cambia cada cosa (sin programar)

Cada escena tiene un `tema.css` que solo cambia los colores, la letra, los tamaños y los tiempos del sistema común. Se edita el valor y se vuelve a cargar la escena:
- escritorio: `escenas/escritorio/tema.css`;
- sistema solar: `escenas/sistema-solar/tema.css` (y `conductor.py redibujar`);
- laboratorio: `escenas/laboratorio/tema.css`.

Los textos de cada paso están en el `escena.md` de cada escena (en el laboratorio, en el protocolo YAML).

## 4. Lo que no está verificado

- Ninguno de los tres presets se ha probado en la mesa real (lo dicen los propios archivos de Max y su mensaje de las 21:14).
- La sección 1 viene de leer los archivos del PR #28 a las 23:55; Max puede tener cambios sin subir.
