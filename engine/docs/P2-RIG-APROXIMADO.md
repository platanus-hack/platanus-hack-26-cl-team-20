# Una vista aproximada para visualizar nube y malla

```sh
python -m slscan approx-rig /RUTA/captures/dale-20261017-2053 \
  --out /RUTA/NUEVA/rig.json
```

Usa el `view_00_map.npz` y `autocalib-session.json` existentes. No abre cámara ni
proyector. Fija píxeles cuadrados, centro de cámara en centro del raster y
distorsión cero en ambos dispositivos; estima solo focal Osmo, R,t y XYZ. No
reutiliza K anterior al cambio de zoom. Fundamental RANSAC inicializa el ajuste;
una optimización con XYZ triangulados refina seis parámetros globales, seguida
de bundle completo de reproyección con puntos libres y pérdida robusta.

El proyector identificado por EDID como `T749-fHD720`, fabricante RKS, no tiene
una ficha óptica confirmada. Se usa el barrido autorizado por h1: throw
`1.2,1.3,1.4,1.5`, offset `100%`, ambos configurables por `--throws` y
`--offset-percent`. `fx=fy=1920*throw`. La convención de offset es explícita:
0% centra el eje óptico; 100% coloca `cy=1079.5`, eje en borde inferior del
raster 1920×1080 para imagen de sobremesa vertical. No se afirma que esa sea
la óptica física ni la resolución nativa del panel. Keystone/escalado/offset
distinto pueden sesgar fuertemente el resultado.

Los 650 tracks se dividen antes de ajustar: 390 para ajuste, 130 para seleccionar
el throw por menor RMS y 130 para auditoría final. El filtro fundamental descarta
outliers del entrenamiento, con conteos en el reporte. La auditoría no elige el
modelo. Se exige convergencia, focal interior de límites y profundidad positiva.
Rango numérico completo es **condicional a los parámetros fijados**, no evidencia
de que los parámetros del proyector sean correctos.

Sin `--baseline-mm` se publica `slscan-rig/1`, `units=projector_baseline`,
`approximate=true`, `metric_status=unverified`, con baseline de norma 1. No son
milímetros. Con una distancia externa aproximada se multiplica una vez todo el
rig; el reporte indica que los centros ópticos no fueron verificados. La pregunta
q23 de Creativo ofrece continuar sin escala o aportar esa distancia; no frena.

No se intenta hacer pasar este artefacto por el contrato P6 métrico, que exige
mm. El pipeline debe tener una ruta explícita de aproximación y conservar unidades
y etiqueta sin verificar en nube, malla y visor. P3 métrico sigue separado.

## Ensayo real 17-oct, captura de las 20:53

Salida entregada a h3:
`reports/p2-approx-dale-refined/rig.json` en el worktree P2.

| Throw supuesto | Focal Osmo ajustada | RMS de selección |
|---|---:|---:|
| 1,2 | 895,63 px | 1,0649 px |
| 1,3 | 976,36 px | 1,0669 px |
| 1,4 | 1056,43 px | 1,0662 px |
| 1,5 | 1137,45 px | 1,0640 px |

Los cuatro convergen y dan rango 6/6. Se elige 1,5 por el criterio prefijado.
La diferencia de residuos es minúscula: **el barrido no mide el throw real**.
La variación de focal muestra sensibilidad al supuesto. Auditoría separada:
RMS 1,0619 px, mediana 0,3651 px, p95 2,1272 px, máximo 6,4750 px;
los 130 puntos triangulan con profundidad positiva. No acredita precisión de
forma ni escala, y menos aún ≤2 mm.

La prueba sintética del solver cambia la focal a 1700 px y la recupera con Kp
conocida; las pruebas también fijan la convención de offset. El replay real se
ejecutó sin parches de decoder, correspondencias ni solver.

La luz estructurada aporta correspondencias para triangular; requiere un modelo
geométrico del par para obtener profundidad. Las tres posiciones eran para
estimar más parámetros ópticos, no un requisito de cada escaneo. El proyecto
[neuvision](https://github.com/nikolaseu/neuvision) también describe una etapa
de calibración para cámara y proyector; no se instaló ni se compiló aquí.
