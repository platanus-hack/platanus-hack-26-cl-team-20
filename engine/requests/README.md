# Pedidos de features del playground

Un archivo por pedido: `NNN-slug.md` (NNN correlativo de 3 dígitos). Los escribe la secretaria (h4 o la de voz,
vía `pg_request_feature`); los toma la unidad que programa en vivo (h3), que cambia `status` y agrega una nota al
final. Nadie más los edita.

```
---
id: 001
title: halo que siga al cocodrilo
kind: visual            # visual | sound | tool | fix
from: secretaria        # quién lo pidió
status: open            # open | doing | done | rejected
created: 2026-10-15T18:50
---
Max quiere que el halo del cocodrilo cambie de color cuando lo levanta.
Se parece a contour_glow; falta leer la altura (bbox z) del tracking.
```

Un buen pedido dice: qué objeto, qué pasa y cuándo, cómo se ve o suena, y qué efecto existente se parece.
