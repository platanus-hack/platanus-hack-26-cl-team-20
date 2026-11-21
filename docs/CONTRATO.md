# Contrato engine ↔ agente (borrador)

Objetivo: que un agente pueda ver qué hay en la superficie y decidir qué proyectar, sin conocer los detalles del engine.

Propuesta inicial para discutir con Max:

**Lo que el engine expone (lectura)**
- `GET /scene` → superficies y objetos detectados: `id`, `label`, `bbox`/polígono en coordenadas de la superficie, `confidence`, `updated_at`.
- `GET /events` (stream) → objeto aparece/desaparece/se mueve, toque en una zona.

**Lo que el agente pide (escritura)**
- `POST /layers` → crea o reemplaza una capa: anclada a una zona fija de la superficie o a un objeto (`anchor: object_id`), con contenido (texto, imagen, HTML o un componente de la librería).
- `DELETE /layers/{id}`.
- `POST /mode` → cambia el modo completo de la mesa.

Pendiente: unidades y sistema de coordenadas, latencia esperada, formato del contenido, cómo se declara una zona tocable.
