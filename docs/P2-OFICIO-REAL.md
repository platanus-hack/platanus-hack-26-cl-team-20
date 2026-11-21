# Dos hojas oficio: rig aproximado y escena real

Captura del 17-10-2026, mediante el overlay del runtime 8790 y la Osmo.
No se detuvo el runtime ni se abrió una ventana en la pantalla del Mac.
El visor de solo lectura está en `http://127.0.0.1:8797/`.

## Resultado publicado

En este worktree, `reports/p2-oficio-live-2122/` contiene:

- `rig.json`: primer ajuste aproximado en mm nominales, mundo Osmo,
  cámara 1080×1920, proyector 1920×1080. Baseline estimada 335,30 mm,
  focal cámara 1166,58 px, tiro 1,561 y principal vertical 456,87 px.
- `rig-paper-audit.json`: error de los lados triangulados sobre las hojas:
  **26,09 mm y 4,00 mm máximos**. Son referencias utilizadas por el ajuste,
  no controles físicos independientes. El informe inicial daba 26,95/4,62 mm;
  la auditoría convierte correctamente los bordes legacy del proyector a centros
  OpenCV con −0,5 px. No cambia la geometría del rig.
- `capture/`: Gray fresco decodificado, blancos, esquinas de ambas hojas,
  foto de los ArUco 2 y 1, raster exacto y correspondencias identificadas.
  Se eliminaron los PNG temporales Gray después de decodificar.
- `reconstruction/cloud.ply`: **112.513 puntos** medidos por P3.
- `reconstruction/mesh.ply`: malla P3 inicial, **84.240 caras**, UV y apariencia.
- `reconstruction/mesh-preview.ply` y `.npz`: mismos vértices medidos,
  **193.121 caras** con límites de continuidad más amplios para inspección.
  Aristas/salto ≤150 mm, normales vecinas ≤140°. No interpola puntos ni rellena
  celdas sin observaciones. La malla inicial usa 80/100 mm y 75°.
- `reconstruction/appearance.png`, `mesh.json`, `cloud.json`, `cloud_quality.npz`,
  `matches.npz`, `approximation.json`: textura, calidad, procedencia y límites.
- `viewer-8797.png`: captura del visor. La nube de vista previa muestra 16.000
  muestras; el archivo PLY y la malla conservan todos los puntos aceptados.
- `sweep-summary.json`: ocho variantes de ponderación. Ninguna mejoró de forma
  estable el primer rig. Errores máximos ~31–129 mm entre variantes.

**No cumple ni certifica ≤2 mm.** Las hojas se asumieron oficio Chile 216×330 mm,
sin medirlas físicamente. Son pequeñas en cámara y casi paralelas. Los bordes
tocan el sofá; la detección necesitó semillas visuales y ajuste de líneas. El
desplazamiento máximo de esquinas entre blancos fue 1,69 px. Zoom/foco/exposición
de la Osmo no pudieron bloquearse físicamente. Se estimó focal nueva; no se fijó
la K anterior. No se ha verificado que el proyector carezca de corrección digital.

## Ajuste

`slscan.oficio_fit` ajusta conjuntamente focales, principal vertical del proyector,
rotación/traslación del par y las poses de ambas hojas. Fija píxeles cuadrados,
distorsión cero, principal centrado de cámara y cx centrado del proyector. Usa
las dimensiones físicas nominales, ArUco y Gray, con priors explícitos. Informa
rango sin priors, profundidad positiva, cotas ópticas y reproyección reservada.
El conjunto reservado queda fuera de las hojas; no constituye control métrico.

Los residuos de marcadores y Gray se expresan en píxeles de **cámara**; se puede
regular su peso sin confundir unidades de proyector. Se publican todos los pesos.
Las variantes `balanced/`, `final/` y `sweep/` son experimentos; **el visor conserva
el primer `rig.json`**, como pidió Creativo. El nombre `final/` de una corrida
intermedia no indica precisión aceptada ni reemplaza el rig publicado.

```sh
python -m slscan.oficio_fit reports/p2-oficio-live-2122/capture \
  --initial reports/p2-approx-dale-refined/rig.json --out NUEVA/rig.json \
  --paper-sigma 1 --marker-sigma 1 --gray-effective 64
```

`oficio_capture` exige una carpeta nueva; `--reuse-gray` permite repetir solo los
marcadores tras verificar los bordes contra el blanco original. Refresca el
overlay después de abrir la cámara, porque la búsqueda del dispositivo puede
exceder el timeout inicial. Solo usar con turno de hardware expresamente liberado.

## P3 y compatibilidad

El Gray fresco es legacy y no retiene contraste por bit. Para P3 se redecodificaron
los 44 crudos del `dale-20261017-2053/osmo`, con el decoder v2 integrado en master.
Esa adquisición registra rotate=0: se permutaron exactamente los arrays 270° al
frame del rig. No se cambian códigos de proyector ni se interpolan coordenadas.
La aplicación de este rig a esa sesión fue autorizada como aproximación; **no se
inventó un manifiesto `verified_static_session`**. Su inmovilidad entre sesiones
permanece sin verificar. La reconstrucción solo cubre superficies visibles e
iluminadas: cuadro, parte del sofá, peluche, máscara y columna, con huecos.

`scripts/reconstruct_oficio_preview.py` utiliza las funciones integradas de P3;
ejecutar con el repositorio integrado en `PYTHONPATH` (la rama P2 no copia P3).
Requiere `--allow-unverified-session`, `--scan`, `--rig`, `--out` nuevo y
`--paper-audit`. Los filtros amplios de nube son reproyección 2,5 px, ángulo 0,5°,
gap 40 mm, profundidad 100–8000 mm y sigma de correspondencia 120 mm. No son un
criterio de aceptación metrológica. La textura es apariencia bajo iluminación
de adquisición, no albedo/PBR. Remap se divide en bloques de 16.000 muestras por
el límite SHRT_MAX de OpenCV; ese problema se notificó para integración en P3.

```sh
python -m slscan.oficio_preview --rig reports/p2-oficio-live-2122/rig.json \
  --reconstruction reports/p2-oficio-live-2122/reconstruction \
  --mesh-name mesh-preview.npz --state reports/p2-scene-viewer
```

Se verificaron recuperación de escala sintética, IDs y convención de marcadores,
servidor local/controles y el render real en Chrome. Para certificar ≤2 mm siguen
faltando una adquisición óptica mejor condicionada y controles físicos reservados
medidos, según `P2-RIG.md`; reducir el residuo de estas mismas hojas no basta.
