package com.smartup.proyector

import java.io.File
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.random.Random
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Grilla de marcadores QR: (1) PGM + CSV del patrón para decodificarlo en el Mac con OpenCV
 * (tools/verificar_marcadores.py); (2) cadena sintética proyector → pared inclinada → "detecciones"
 * tipo MRUK (cuadrado con orientación desconocida) → correspondencias → AjusteProyector.
 */
class MarcadoresTest {
  private val dir = File("build/sintetico").apply { mkdirs() }

  private fun escribir(l: Marcadores.Layout, nombre: String) {
    // PGM binario (sin java.awt en los tests de Android); OpenCV lo lee directo
    val px = Marcadores.rasterizar(l)
    File(dir, "$nombre.pgm").outputStream().use { o ->
      o.write("P5\n${l.ancho} ${l.alto}\n255\n".toByteArray())
      o.write(ByteArray(px.size) { (px[it] and 0xFF).toByte() })
    }
    File(dir, "$nombre.csv").writeText("id,payload,cx,cy,tlx,tly,trx,try,brx,bry,blx,bly,modulos,mod_px\n" +
        l.items.joinToString("\n") { "${it.id},${it.payload},${it.cx},${it.cy},${it.esquinas.joinToString(",")},${it.modulos},${it.modPx}" })
  }

  @Test fun patrones() {
    for ((c, f) in listOf(4 to 3, 6 to 4)) {
      val l = Marcadores.layout(c, f)
      escribir(l, "marcadores_${c}x$f")
      val it0 = l.items[0]
      println("grilla ${c}x$f: QR ${it0.modulos} módulos × ${it0.modPx} px = ${it0.modulos * it0.modPx} px")
      assertTrue(it0.modPx >= 4)
      for (it in l.items) assertTrue(it.esquinas.all { v -> v >= 0 } && it.esquinas[4] <= l.ancho && it.esquinas[5] <= l.alto)
    }
  }

  private fun sub(a: DoubleArray, b: DoubleArray) = doubleArrayOf(a[0] - b[0], a[1] - b[1], a[2] - b[2])
  private fun norm(a: DoubleArray): DoubleArray { val n = sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]); return doubleArrayOf(a[0] / n, a[1] / n, a[2] / n) }
  private fun cross(a: DoubleArray, b: DoubleArray) = doubleArrayOf(a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
  private fun dist(a: DoubleArray, b: DoubleArray) = sqrt(sub(a, b).let { it[0] * it[0] + it[1] * it[1] + it[2] * it[2] })

  @Test fun cadenaSintetica() {
    val l = Marcadores.layout(4, 3)
    // proyector "cabeza" en el origen (marco Spatial: y arriba, z adelante), f = 1400 px, centrado
    val f = 1400.0
    val m = ModeloProyector(doubleArrayOf(f, 0.0, 960.0, 0.0, 0.0, -f, 540.0, 0.0, 0.0, 0.0, 1.0, 0.0), 1920, 1080)
    // pared a 2 m, inclinada 20° en yaw y 8° en pitch
    val a = Math.toRadians(20.0); val b = Math.toRadians(8.0)
    val nPared = norm(doubleArrayOf(-sin(a), sin(b), -cos(a) * cos(b)))
    val p0 = doubleArrayOf(0.1, 0.05, 2.0)
    fun corte(u: Double, v: Double): DoubleArray {
      val d = m.rayo(u, v)
      val t = (nPared[0] * p0[0] + nPared[1] * p0[1] + nPared[2] * p0[2]) / (nPared[0] * d[0] + nPared[1] * d[1] + nPared[2] * d[2])
      return doubleArrayOf(d[0] * t, d[1] * t, d[2] * t)
    }
    val rnd = Random(3)
    val dets = ArrayList<Marcadores.Deteccion>()
    val verdad = HashMap<Int, List<DoubleArray>>()
    for (it in l.items) {
      val es = (0 until 4).map { k -> corte(it.esquinas[k * 2], it.esquinas[k * 2 + 1]) }
      verdad[it.id] = es
      // MRUK ve un cuadrado: centroide del cuadrilátero, lado medio, x a lo largo de TL→TR
      val c = DoubleArray(3) { q -> es.sumOf { e -> e[q] } / 4 }
      val lado = (dist(es[0], es[1]) + dist(es[1], es[2]) + dist(es[2], es[3]) + dist(es[3], es[0])) / 4
      var x = norm(sub(es[1], es[0]))
      val z = nPared.copyOf()
      var y = cross(z, x)
      // orientación desconocida: giro de k·90° en el plano y a veces espejo
      repeat(rnd.nextInt(4)) { val nx = y; val ny = doubleArrayOf(-x[0], -x[1], -x[2]); x = nx; y = ny }
      if (rnd.nextBoolean()) x = doubleArrayOf(-x[0], -x[1], -x[2])
      val zz = cross(x, y)
      val pose = doubleArrayOf(x[0], y[0], zz[0], c[0], x[1], y[1], zz[1], c[1], x[2], y[2], zz[2], c[2], 0.0, 0.0, 0.0, 1.0)
      // ruido de seguimiento: ±3 mm en el centro
      pose[3] += rnd.nextDouble(-0.003, 0.003); pose[7] += rnd.nextDouble(-0.003, 0.003); pose[11] += rnd.nextDouble(-0.003, 0.003)
      dets.add(Marcadores.Deteccion(it.id, it.payload, pose, doubleArrayOf(-lado / 2, -lado / 2), doubleArrayOf(lado / 2, lado / 2), true, 0L))
    }
    val corr = Marcadores.correspondencias(l, dets)
    // orientación: cada esquina ordenada debe caer junto a la verdadera
    // orientación: la esquina asignada debe ser la verdadera más cercana (el cuadrado de MRUK no
    // calza exacto con el cuadrilátero proyectado en una pared inclinada: ese error es de forma, no de orden)
    var errMax = 0.0; var malOrden = 0
    for ((k, id) in corr.ids.withIndex()) {
      if (k < corr.nCentros) continue
      val j = (k - corr.nCentros) % 4
      val p = doubleArrayOf(corr.xMundo[k * 3], corr.xMundo[k * 3 + 1], corr.xMundo[k * 3 + 2])
      val ds = verdad[id]!!.map { dist(p, it) }
      if (ds.indexOf(ds.min()) != j) malOrden++
      errMax = maxOf(errMax, ds[j])
    }
    val lado0 = dist(verdad[0]!![0], verdad[0]!![1])
    println("marcadores: ${corr.nCentros} QR, ${corr.ids.size} pares; esquinas mal ordenadas $malOrden; error de forma máx. %.1f mm (lado %.0f mm)".format(errMax * 1000, lado0 * 1000))
    assertTrue("orientación de esquinas", malOrden == 0)
    val res = AjusteProyector.ajustar(Pares(run {
      // mundo = cabeza en esta prueba
      corr.xMundo
    }, corr.uv), 1920, 1080, umbralPx = 12.0)!!
    println("ajuste con centros+esquinas: ${res.resumen()}")
    val soloC = Marcadores.correspondencias(l, dets, conEsquinas = false)
    val resC = AjusteProyector.ajustar(Pares(soloC.xMundo, soloC.uv), 1920, 1080, umbralPx = 12.0)
    println("ajuste solo centros: ${resC?.resumen() ?: "—"}")
    File(dir, "marcadores_ajuste.txt").writeText("esquinas mal ordenadas=$malOrden errForma_mm=${errMax * 1000}\ncentros+esquinas: ${res.resumen()}\nsolo centros: ${resC?.resumen()}\n")
    assertTrue("residuo solo centros", resC != null && resC.mediana < 3.0)
  }
}
