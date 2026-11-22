"""Live Mapping Playground: runtime con hot reload, galería de efectos, cola de pedidos y MCP.

Cadena (como BIOME, un sensor nunca toca el render directo):

    cámaras / gestos / MIDI / secretaria
      → eventos + estado de escena (objetos con geometría, contornos, texturas, cuadros, personas)
        → efectos (plugins con hot reload) que leen la escena y dibujan en el lienzo de cada proyector
          → render por proyector (hoy: lienzo numpy; en el PC: moderngl con la calibración del rig)
    pedidos de features → requests/NNN-slug.md → la unidad que programa en vivo (hot reload)
"""

__version__ = "0.1.0"
