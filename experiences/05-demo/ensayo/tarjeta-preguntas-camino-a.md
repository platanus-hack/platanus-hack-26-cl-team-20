# WUI · Preguntas del jurado (versión para la demo como la corre la hoja del operador)

Estado: borrador para el ensayo; nada probado en la mesa.
Actualizado: domingo 22-nov-2026, 04:30.
Responsable: Matilda Roberts.


Copia de `tarjeta-preguntas.md` con las preguntas 1, 2, 3, 6 y 8 corregidas (ver `contradicciones.md`, G4 y M10). Vale si el sistema solar va por el camino A (programa de Max, sin el motor en Rust), con respuestas preparadas en la libreta y la voz de Lumo lanzada por el operador. Si la consola dice «Motor local: conectado» y el solar va por el camino B, usar la tarjeta original para las preguntas 3 y 8.

Max responde; si es detalle, pasa a quien sabe. Las fuentes de cada respuesta están en las notas de trabajo de Matilda. ⚠️ = ajustar tras el ensayo.

**1 · ¿Estaba grabado o preparado?** — Max
«No es un video: todo corrió en esta mesa, ahora. Seguir los objetos y medir el vaso fue en vivo. Lo preparado: cada escena tiene su programa, la respuesta de la libreta iba lista y la frase de Lumo la lanzó nuestro operador.» ⚠️

**2 · ¿El agente decide o es un script?** — Max · Daniel
«En esta demo, cada escena la conduce un programa que escribimos, que ve y sigue los objetos en vivo; hoy ningún modelo decidió nada. Cualquier agente, como Claude Code, se conecta por MCP y usa lo mismo: ver la mesa, proyectar y esperar un toque.»

**3 · ¿Qué es lo difícil?** — Manu
«Ver la mesa sin confundirnos con nuestra propia luz, y ubicar cada objeto en milímetros. Apagamos lo proyectado un instante y sacamos una foto limpia. Hoy cada escena sigue sus objetos con su propio programa; el seguimiento del motor en Rust no corrió en esta demo.»

**4 · Son tres demos, ¿cuál es el producto?** — Max
«El motor: WUI. Como un motor de videojuegos: las tres escenas son tres juegos hechos con el mismo motor. El primer uso que queremos validar es la formación técnica.»

**5 · ¿En qué se diferencia de lo que existe?** — Max · Pablo
«Dynamicland usa papeles marcados; LightGuide, instrucciones que escriben ingenieros. WUI sigue objetos comunes sin marcadores y se los entrega a cualquier agente de IA por MCP.»

**6 · ¿Qué hicieron en 36 horas? ¿Traían código?** — Max · Manu
«Max traía un escáner que calibra cámara y proyector; entró en un commit y portamos algunas piezas. Aquí hicimos el motor en Rust, la consola y las tres escenas.»
(Si preguntan por la voz: «La que oyeron es la del computador; hicimos otra, conversacional, que hoy no usamos».)

**7 · ¿Quién es el primer usuario?** — Matilda
«Estudiantes de educación técnica, con un profesor para todo el curso: en Chile son más de 160 mil. Es una hipótesis: todavía no lo probamos con ellos.»

**8 · ¿Reconoce cualquier objeto?** — Max
«Seguimos objetos comunes, sin ponerles marcas. Hoy cada escena trae su propio reconocimiento: las bolas por tamaño, el pastillero por sus colores. El motor general en Rust, que separa objetos sin programar cada uno, está en el repo; hoy no corrió en la mesa.»

**No decir:** táctil · autocalibración · todo local · tiempo real · evita accidentes · 0,7 mm · «con MobileSAM» (hoy no corrió).
**Si no sabemos:** «Eso todavía no lo medimos; lo que sí medimos es…» · «Prefiero no inventarlo: se lo mostramos en la mesa después.»

Sin probar: nada de esto se vio en la mesa. Antes de usarla, mirar en la cabecera de la consola si dice «Motor local: conectado» o «sin motor».
