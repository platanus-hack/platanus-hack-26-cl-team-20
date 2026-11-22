package com.smartup.proyector

import java.io.File
import kotlin.math.sqrt
import kotlin.random.Random
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Candidato con el prior de Max (ArucoMesa.pnpConPrior): proyector de la mesa colgado, casco ~10 cm más
 * abajo y ~20 cm al lado; grilla ArUco de la ventana (1900x1040) sobre la mesa; K desconocida.
 * Geometría tomada de la calibración por luz estructurada del Mac (K fx=3063, cy=952; C=(0.066, 1.461, 0.860)).
 */
class ArucoPriorTest {
  private fun norm(a: DoubleArray): DoubleArray { val n = sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]); return doubleArrayOf(a[0] / n, a[1] / n, a[2] / n) }

  private fun caso(cols: Int, filas: Int, tam: Double, ruidoM: Double, casco: DoubleArray, semilla: Int): String {
    val c = doubleArrayOf(0.066, 1.461, 0.860)
    val r = doubleArrayOf(-0.99667, -0.08153, 0.00016, 0.03157, -0.38418, 0.92272, 0.07516, -0.91965, -0.38548)
    val k = doubleArrayOf(3063.46, 0.0, 951.18, 0.0, 3063.46, 951.92, 0.0, 0.0, 1.0)
    val kr = Mat.mul33(k, r)
    val p = DoubleArray(12)
    for (i in 0..2) { for (j in 0..2) p[i * 4 + j] = kr[i * 3 + j]; p[i * 4 + 3] = -(kr[i * 3] * c[0] + kr[i * 3 + 1] * c[1] + kr[i * 3 + 2] * c[2]) }
    val verdad = ModeloProyector(p, 1900, 1040)
    val mesaY = 0.75
    val rnd = Random(semilla)
    val xs = ArrayList<Double>(); val us = ArrayList<Double>()
    val celW = 1900.0 / cols; val celH = 1040.0 / filas
    for (f in 0 until filas) for (cc in 0 until cols) {
      val u0 = celW * (cc + 0.5) - tam / 2; val v0 = celH * (f + 0.5) - tam / 2
      for ((u, v) in listOf(u0 to v0, u0 + tam to v0, u0 + tam to v0 + tam, u0 to v0 + tam)) {
        val d = verdad.rayo(u, v); val t = (mesaY - verdad.centro[1]) / d[1]
        xs.add(verdad.centro[0] + d[0] * t + rnd.nextDouble(-ruidoM, ruidoM)); xs.add(mesaY + rnd.nextDouble(-ruidoM, ruidoM))
        xs.add(verdad.centro[2] + d[2] * t + rnd.nextDouble(-ruidoM, ruidoM)); us.add(u); us.add(v)
      }
    }
    val pts = (0 until xs.size / 3).map { doubleArrayOf(xs[it * 3], xs[it * 3 + 1], xs[it * 3 + 2]) }
    val pl = ArucoMesa.ajustarPlano(pts, doubleArrayOf(-1.0, 0.0, 0.0), casco)!!
    val cand = ArucoMesa.pnpConPrior(xs.toDoubleArray(), us.toDoubleArray(), pl, 1900, 1040, casco, casco)!!
    val restr = AjusteProyector.ajustar(Pares(xs.toDoubleArray(), us.toDoubleArray()), 1900, 1040, umbralPx = 10.0)
    val restrArriba = restr?.let { ArucoMesa.ladoCorrecto(it.modelo, pl.c, pl.n, casco).first }
    val e = ArucoMesa.dist3(cand.modelo.centro, c)
    val eR = restrArriba?.let { ArucoMesa.dist3(it.centro, c) }
    // un punto 30 cm sobre la mesa: diferencia de proyección con el modelo verdadero
    // (dentro del frustum: sobre el centro de la grilla)
    val pv = DoubleArray(3); verdad.proyectar(pl.c[0], mesaY + 0.3, pl.c[2], pv); val pc = DoubleArray(3); cand.modelo.proyectar(pl.c[0], mesaY + 0.3, pl.c[2], pc)
    val dOff = sqrt((pv[0] - pc[0]) * (pv[0] - pc[0]) + (pv[1] - pc[1]) * (pv[1] - pc[1]))
    return "%dx%d ruido %.0f mm casco %s: prior → %s, fx=%.0f (verdad 3063), centro a %.1f cm (dist. al prior %.2f m), residuo %.2f px, punto a 30 cm: %.1f px | restringido sin prior: centro a %s cm, %s".format(
        cols, filas, ruidoM * 1000, casco.joinToString(",") { "%.2f".format(it) }, cand.tipo, cand.modelo.k[0] / cand.modelo.k[8], e * 100, cand.distPrior, cand.medianaPx, dOff,
        eR?.let { "%.1f".format(it * 100) } ?: "—", restr?.modelo?.resumen() ?: "—")
  }

  @Test fun priorDeMax() {
    val c = doubleArrayOf(0.066, 1.461, 0.860)
    val lineas = ArrayList<String>()
    // casco 10 cm más abajo y 20 cm al lado (dos direcciones), y un prior "malo" (35 cm y 0 cm de altura)
    val cascos = listOf(doubleArrayOf(c[0] - 0.20, c[1] - 0.10, c[2]), doubleArrayOf(c[0], c[1] - 0.10, c[2] + 0.20), doubleArrayOf(c[0] + 0.35, c[1], c[2]))
    val errores = ArrayList<Double>()
    for ((i, casco) in cascos.withIndex()) for ((g, tam) in listOf((3 to 2) to 364.0, (5 to 3) to 228.0)) {
      val l = caso(g.first, g.second, tam, 0.002, casco, 11 + i); lineas.add(l); println(l)
      errores.add(Regex("centro a ([0-9.]+) cm").find(l)!!.groupValues[1].toDouble())
    }
    File("build/sintetico").mkdirs(); File("build/sintetico/aruco_prior.txt").writeText(lineas.joinToString("\n") + "\n")
    // con el prior correcto (los 4 primeros casos) el centro debe quedar a < 15 cm
    assertTrue("prior correcto: ${errores.take(4)}", errores.take(4).all { it < 15.0 })
  }
}
