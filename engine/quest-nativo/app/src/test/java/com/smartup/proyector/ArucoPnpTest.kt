package com.smartup.proyector

import java.io.File
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.random.Random
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Proyector de la mesa colgado del techo (pose fija en la sala), grilla ArUco 6x4 proyectada sobre la
 * mesa (plano y = 0,75): esquinas 3D con ruido de 2 mm → AjusteProyector (caso plano) → la solución
 * espejo se corrige al lado de la cámara (ArucoMesa.ladoCorrecto). El centro debe volver a ±3 cm.
 */
class ArucoPnpTest {
  private fun norm(a: DoubleArray): DoubleArray { val n = sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]); return doubleArrayOf(a[0] / n, a[1] / n, a[2] / n) }

  @Test fun proyectorColgado() {
    // proyector en (0.1, 2.3, 0.6) mirando hacia abajo y algo adelante (pitch −70°), f = 1500 px, cy desplazado (lens shift)
    val c = doubleArrayOf(0.1, 2.3, 0.6)
    val pitch = Math.toRadians(-70.0)
    // marco del proyector (mundo Spatial: x der, y arriba, z adelante): filas de R = ejes x, y(arriba de la imagen), z (óptico)
    val z = norm(doubleArrayOf(0.0, sin(pitch), cos(pitch)))
    val x = doubleArrayOf(1.0, 0.0, 0.0)
    val y = ArucoMesa.cross(z, x)
    val f = 1500.0; val cx = 960.0; val cy = 900.0
    // P = K·[R | −R·C] con v hacia abajo: u = cx + f·(x·d)/(z·d), v = cy − f·(y·d)/(z·d)
    val r = doubleArrayOf(x[0], x[1], x[2], y[0], y[1], y[2], z[0], z[1], z[2])
    val kr = DoubleArray(9)
    for (j in 0..2) { kr[j] = f * r[j] + cx * r[6 + j]; kr[3 + j] = -f * r[3 + j] + cy * r[6 + j]; kr[6 + j] = r[6 + j] }
    val p = DoubleArray(12)
    for (i in 0..2) { for (j in 0..2) p[i * 4 + j] = kr[i * 3 + j]; p[i * 4 + 3] = -(kr[i * 3] * c[0] + kr[i * 3 + 1] * c[1] + kr[i * 3 + 2] * c[2]) }
    val verdad = ModeloProyector(p, 1920, 1080)
    val mesaY = 0.75
    val g = ArucoMesa.Grilla(1920, 1080, (0 until 24).associateWith { id ->
      val u0 = 201.0 + (id % 6) * 270; val v0 = 51.0 + (id / 6) * 270
      doubleArrayOf(u0, v0, u0 + 167, v0, u0 + 167, v0 + 167, u0, v0 + 167)
    }, 6, 4)
    val rnd = Random(5)
    val xs = ArrayList<Double>(); val us = ArrayList<Double>()
    for ((_, e) in g.esquinas) for (k in 0 until 4) {
      val d = verdad.rayo(e[k * 2], e[k * 2 + 1])
      val t = (mesaY - verdad.centro[1]) / d[1]
      xs.add(verdad.centro[0] + d[0] * t + rnd.nextDouble(-0.002, 0.002)); xs.add(mesaY + rnd.nextDouble(-0.002, 0.002))
      xs.add(verdad.centro[2] + d[2] * t + rnd.nextDouble(-0.002, 0.002))
      us.add(e[k * 2]); us.add(e[k * 2 + 1])
    }
    val res = AjusteProyector.ajustar(Pares(xs.toDoubleArray(), us.toDoubleArray()), 1920, 1080, umbralPx = 10.0)!!
    val camara = doubleArrayOf(-0.5, 1.6, 0.2)        // el casco mirando la mesa desde arriba
    val (m, refl) = ArucoMesa.ladoCorrecto(res.modelo, doubleArrayOf(0.0, mesaY, 1.0), doubleArrayOf(0.0, 1.0, 0.0), camara)
    val err = sqrt((0..2).sumOf { (m.centro[it] - c[it]) * (m.centro[it] - c[it]) })
    val msg = "PnP plano: ${res.resumen()}\nreflejado=$refl centro=(%.3f, %.3f, %.3f) verdad=(%.3f, %.3f, %.3f) error %.1f cm".format(
        m.centro[0], m.centro[1], m.centro[2], c[0], c[1], c[2], err * 100)
    println(msg)
    File("build/sintetico").mkdirs(); File("build/sintetico/aruco_pnp.txt").writeText(msg + "\n")
    // con K conocida: solo la pose
    val pts3 = ArrayList<DoubleArray>(); for (i in 0 until xs.size / 3) pts3.add(doubleArrayOf(xs[i * 3], xs[i * 3 + 1], xs[i * 3 + 2]))
    val pl = ArucoMesa.ajustarPlano(pts3, doubleArrayOf(1.0, 0.0, 0.0), camara)!!
    val k = doubleArrayOf(f, 0.0, cx, 0.0, f, cy, 0.0, 0.0, 1.0)
    val (mk0, resK) = ArucoMesa.poseConK(k, xs.toDoubleArray(), us.toDoubleArray(), pl, 1920, 1080)!!
    val (mk, reflK) = ArucoMesa.ladoCorrecto(mk0, pl.c, pl.n, camara)
    val errK = sqrt((0..2).sumOf { (mk.centro[it] - c[it]) * (mk.centro[it] - c[it]) })
    // un punto fuera del plano (a 30 cm sobre la mesa) debe proyectar igual que con el modelo verdadero
    val pv = DoubleArray(3); verdad.proyectar(0.2, mesaY + 0.3, 1.2, pv); val pk = DoubleArray(3); mk.proyectar(0.2, mesaY + 0.3, 1.2, pk)
    val msgK = "con K: residuo mediana %.2f px, reflejado=%s, centro=(%.3f, %.3f, %.3f) error %.1f cm; punto a 30 cm de la mesa: %.1f px de diferencia".format(
        resK, reflK, mk.centro[0], mk.centro[1], mk.centro[2], errK * 100, sqrt((pv[0] - pk[0]) * (pv[0] - pk[0]) + (pv[1] - pk[1]) * (pv[1] - pk[1])))
    println(msgK)
    File("build/sintetico/aruco_pnp.txt").appendText(msgK + "\n")
    assertTrue("centro sobre la mesa", m.centro[1] > mesaY)
    assertTrue("residuo", res.mediana < 2.0)
    assertTrue("pose con K: centro a < 3 cm", errK < 0.03)
  }
}
