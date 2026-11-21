# Índice de documentación

## Empezar y crear

- [README principal](../README.md): instalación, demo, primera escena, puertos y problemas frecuentes.
- [Guía para artistas](GUIA-ARTISTAS.md): primera práctica, montaje, efectos e ideas.
- [Demo incluida](../examples/demo/README.md): escena inventada y comandos sin hardware.
- [Voz y configuración propia](VOZ-Y-CONFIGURACION.md): preparar la secretaria en otra máquina.
- [Contenido del bundle](BUNDLE.md): procedencia, exclusiones y validación de esta entrega.

## Proyección y efectos

| Documento | Contenido |
|---|---|
| [Playground](../playground/README.md) | Runtime, API, plugins, MCP y flujo original |
| [Dashboard](../playground/dashboard_ui/README.md) | Nueva vista, Continuar, efectos y API |
| [Escena automática](ESCENA-AUTOMATICA.md) | Preparar objetos después de dale; modelos locales y activación |
| [Apariencia y vectores](APARIENCIA-Y-VECTORES.md) | Foto limpia, máscaras, trazos, lápiz y manchas |
| [Tela viva](TELA-VIVA.md) | Cara, mano o esfera detrás del cuadro; parámetros y límites |
| [Texturas de Minto](TEXTURAS-MINTO.md) | Rayas, leopardo y máscara decorativa |
| [Rendimiento](PERFORMANCE-EFECTOS.md) | Resolución, cachés y mediciones de los efectos |

## Música, interacción y voz

- [Audio](../playground/audio/README.md): captura, canales, ritmo y efectos reactivos.
- [Piano Hero](../playground/pianohero/README.md): juego proyectado y puente MIDI.
- [Manos](../playground/hands/README.md): cámara secundaria, detección y calibración táctil.
- [Mesa musical](../playground/touchsynth/README.md): instrumentos, dibujo y luz con gestos.
- [Secretaria](../playground/secretaria/README.md): voz, herramientas, avances y configuración original.
- [Guion de la secretaria](../playground/skill/SKILL.md): vocabulario de operación y herramientas.
- [Pedidos](../requests/README.md): formato de ideas y mejoras. Los pedidos históricos numerados
  conservan decisiones de desarrollo; no son instrucciones para la primera sesión.

## Escáner y desarrollo 3D

Leer en este orden para entender dependencias y distinguir sintéticos de evidencia física:

| Documento | Propósito |
|---|---|
| [Brief](BRIEF-pipeline-3d.md) | Objetivos y criterios de precisión |
| [Análisis](ANALISIS-pipeline-3d.md) | Diagnóstico inicial; antecede a las implementaciones P2–P7 |
| [Pipeline P6](PIPELINE.md) | Contratos, etapas, estados, repetición y monitor 8796 |
| [Rig P2](P2-RIG.md) | Calibración conjunta y protocolo anterior con hoja |
| [Autocalibración P2](P2-AUTOCALIB.md) | Escena sin tablero, escala, movimientos y limitaciones |
| [Visor P2](P2-VISOR.md) | Monitor auxiliar 8797 y simulación |
| [Malla P3](P3-MALLA.md) | Correspondencias medidas, nube, malla y textura |
| [Escena P4](P4-ESCENA.md) | Visor registrado y render desde el proyector calibrado |
| [Objetos P5](P5-OBJETOS.md) | Segmentación sobre geometría y semántica local |
| [Materiales P7](P7-PBR.md) | Lámparas domésticas, respuesta de color y materiales relativos |

## Equipos e historia

- [Windows](../windows/README.md): scripts y arquitectura opcional del PC; administración separada de la demo.
- [SPEC](../SPEC.md): visión del proyecto y ampliaciones de alcance.
- [PLAN](../PLAN.md): bitácora técnica de decisiones y pruebas anteriores.

Los documentos antiguos se conservan como referencia y pueden contener pasos superados,
sesiones que no se distribuyen, mediciones de otro montaje y funciones propuestas.
Para empezar manda el README principal; para contratos manda el código de esta versión.
Los links a capturas privadas omitidas son referencias históricas, no descargas faltantes.
