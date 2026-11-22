# Demo 2 · Sistema solar (2:08 a 3:02)

Del guion del equipo (`entrega/guion.md`), con el estado de cada momento al sábado 21-nov, 19:00.

## Lo que se dice

> En la tarde llega su sobrina con la tarea del sistema solar: un canasto con bolas de plumavit y una lámina impresa.
> *(pausa 2,5 s)*
>
> El motor reconoce cada bola. El engranaje pasa a ser el Sol, y cada planeta aparece con su órbita. *(pausa 5 s)*
>
> Matilda mueve el canasto entero y el sistema solar se va con él, en tiempo real. *(pausa 5 s)*
>
> La sobrina toca Saturno en la lámina, y la guía vuela al Saturno del canasto y le cuenta, con voz, por qué tiene
> anillos. *(pausa 7 s)*

## Lo que se ve, momento a momento

| Tiempo | Qué pasa en la mesa | Estado |
|---|---|---|
| 2:08 a 2:19 | Salen la libreta y el laptop, entran el canasto y la lámina. La mesa pasa a óleo nocturno: azul ultramar, amarillo y blanco, con remolinos lentos. | Estilo listo (`tema.css`). |
| 2:19 a 2:33 | El engranaje se enciende como Sol. Cada bola recibe su planeta (textura y sombra del lado opuesto al Sol), uno tras otro, y las órbitas se pintan como pinceladas. | Corre en simulación: reconoce el Sol y las 9 bolas en las 4 fotos reales. |
| 2:33 a 2:44 | Matilda gira y desliza el canasto. Sol, planetas y órbitas lo siguen pegados. | Corre en simulación: giro de 20° recuperado con 0,4 mm de error. |
| 2:44 a 3:02 | El dedo se queda sobre Saturno en la lámina y se llena un anillo. Lumo sale volando hasta el Saturno del canasto, lo marca con un halo, abre su ficha y dice: «Los anillos de Saturno son millones de pedacitos de hielo y de roca. Cuando les llega la luz del Sol, brillan.» | Toque, vuelo, halo y ficha corren en simulación. La voz de ElevenLabs la está haciendo otro agente. |

## Qué bola es cada planeta

Por tamaño: la más grande es Júpiter, la segunda Saturno, y así hacia abajo. Con 9 bolas entra Plutón (planeta enano).
Las bolas que miden casi lo mismo (15, 15, 14 y 13 mm de radio) quedan como duda: se ven con borde punteado y se
pueden fijar a mano. La duda también puede ser parte del juego: «Estas tres bolitas son casi iguales. ¿Cuál será
Marte?»

## Falta

- Probar con el proyector y la cámara reales (todo lo de arriba está medido en simulación).
- Conectar la voz de Lumo (ElevenLabs, con interrupción).
- Pasar la percepción a Rust dentro del motor (decisión de Manu, 21-nov).

## Por confirmar con Matilda

1. ¿La sobrina va a estar en el escenario o Matilda hace de sobrina?
2. ¿Qué edad tiene? Lumo habla para unos 8 años.
3. ¿Los datos de cada planeta y las frases de Lumo están bien? Están en `escena.md`, sección «La voz: Lumo».
