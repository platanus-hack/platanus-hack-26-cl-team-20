# Texturas de Minto y máscara azteca

Plugins con hot reload sobre las máscaras preparadas de `scene_regions`. Primero
debe estar activo el provider `scene_regions` (sin target). No usan cámara ni
crean ventanas. Las fotos, máscaras y hashes se leen del catálogo existente.

| Efecto | Target | Parámetros iniciales |
|---|---|---|
| `tigre_animado` | `oreja_izquierda` | `speed=0.35`, `spacing=38`, `intensity=1`, `capa=20` |
| `tigre_animado` | `oreja_superior` | los mismos; cada patrón sigue el eje de su oreja |
| `leopardo_fucsia` | `conejo` | `speed=0.22`, `spacing=48`, `intensity=1`, `capa=20` |
| `mascara_viva` | `mascara_azteca` | `speed=0.35`, `width=3`, `textura=0.5`, `ojos=1`, `capa=30` |

Todos aceptan `audio="bass"` (por defecto), o `audio=""` para apagarlo.
`speed` controla la animación; `spacing` es el tamaño aproximado del estampado
en píxeles. `intensity` controla brillo. En la máscara, `color` es RGB y controla
los bordes; `textura` conserva parte de la pintura original y `ojos=0` apaga
la animación ocular.

Las rayas son naranja y negro, curvas y con ancho variable. El leopardo usa
rosetas negras interrumpidas sobre fucsia, deriva y respiración; conserva un
sombreado suave según forma y textura. Se restan **las máscaras nativas de ambas
orejas** del cuerpo, incluso si el cuerpo se dibuja después. El recorte final
se aplica a resolución nativa, incluyendo huecos y partes desconectadas.

La máscara anima los contornos segmentados, líneas detectadas en la foto limpia
y dos componentes oscuros emparejados en la zona de ojos. Los ojos parpadean,
mueven las pupilas y reaccionan al bajo. La detección se hace una vez por textura
y se expone en el estado: `eyes_detected`, `eyes_px`, `eye_status`. Si no hay
evidencia suficiente, `eye_status=not_detected`: sigue animando bordes y no
inventa una posición para los ojos. Es una heurística para esta máscara frontal,
no un detector facial general.

El negro reemplaza píxeles dentro de la máscara para mantener el contraste.
Nada se escribe fuera de cada región. La comprobación de píxeles no certifica
la alineación física del proyector ni la lectura del negro sobre el objeto real.

Pruebas: `python -m pytest tests/test_party_textures.py tests/test_appearance.py tests/test_playground.py -q`.
Evidencia privada en el worktree h13: `reports/textures/` (renders y benchmark).
Los efectos se entregaron de forma incremental: orejas, cuerpo y máscara.
