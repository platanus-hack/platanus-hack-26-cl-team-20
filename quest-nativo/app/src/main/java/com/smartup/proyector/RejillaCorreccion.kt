package com.smartup.proyector

import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/**
 * Corrección densa residual en el espacio de la textura del proyector (como `lazo_corr.npz` de la
 * Osmo): la imagen final se arma como final(u) = modelo(u + C(u)). C se guarda en nodos cada
 * `celda` px y se interpola bilineal (en el shader, textura RG16F con filtro lineal).
 *
 * Sin Android: se prueba en la JVM.
 */
class RejillaCorreccion(val ancho: Int = 1920, val alto: Int = 1080, val celda: Int = 40) {
  val nx = ancho / celda + 1
  val ny = alto / celda + 1
  val dx = FloatArray(nx * ny)
  val dy = FloatArray(nx * ny)

  fun vacia() = dx.all { it == 0f } && dy.all { it == 0f }

  fun limpiar() { dx.fill(0f); dy.fill(0f) }

  fun copiar(): RejillaCorreccion = RejillaCorreccion(ancho, alto, celda).also {
    dx.copyInto(it.dx); dy.copyInto(it.dy)
  }

  /** C(u, v) bilineal entre nodos (borde: se repite el último nodo). */
  fun evaluar(u: Double, v: Double, out: DoubleArray) {
    val gx = (u / celda).coerceIn(0.0, (nx - 1).toDouble())
    val gy = (v / celda).coerceIn(0.0, (ny - 1).toDouble())
    val i0 = min(gx.toInt(), nx - 2).coerceAtLeast(0); val j0 = min(gy.toInt(), ny - 2).coerceAtLeast(0)
    val fx = gx - i0; val fy = gy - j0
    fun at(a: FloatArray, i: Int, j: Int) = a[j * nx + i].toDouble()
    for ((k, a) in arrayOf(dx, dy).withIndex()) {
      out[k] = (1 - fx) * (1 - fy) * at(a, i0, j0) + fx * (1 - fy) * at(a, i0 + 1, j0) +
          (1 - fx) * fy * at(a, i0, j0 + 1) + fx * fy * at(a, i0 + 1, j0 + 1)
    }
  }

  /**
   * Arma C desde muestras dispersas: en `pos` (u,v observados) el valor `val` (û − u_obs).
   * Pesos gaussianos en un radio de 1.5 celdas; los nodos sin datos se rellenan por difusión
   * desde los vecinos (equivale a "nearest" suave) y al final se suaviza una pasada.
   */
  fun desdeMuestras(pos: DoubleArray, valores: DoubleArray, n: Int, sigmaCeldas: Double = 0.8) {
    val sx = DoubleArray(nx * ny); val sy = DoubleArray(nx * ny); val sw = DoubleArray(nx * ny)
    acumular(pos, valores, n, sigmaCeldas, sx, sy, sw)
    val tiene = BooleanArray(nx * ny)
    for (k in 0 until nx * ny) {
      if (sw[k] > 0.05) { dx[k] = (sx[k] / sw[k]).toFloat(); dy[k] = (sy[k] / sw[k]).toFloat(); tiene[k] = true }
      else { dx[k] = 0f; dy[k] = 0f }
    }
    rellenar(tiene)
    suavizar()
  }

  /** Suma una corrección dispersa (lazo de verificación); lejos de los datos el aporte cae a 0. */
  fun sumarDispersas(pos: DoubleArray, delta: DoubleArray, n: Int, sigmaCeldas: Double = 2.5) {
    val sx = DoubleArray(nx * ny); val sy = DoubleArray(nx * ny); val sw = DoubleArray(nx * ny)
    acumular(pos, delta, n, sigmaCeldas, sx, sy, sw)
    for (k in 0 until nx * ny) {
      if (sw[k] <= 0) continue
      val a = min(1.0, sw[k])           // atenuación donde hay poco soporte
      dx[k] += (sx[k] / sw[k] * a).toFloat()
      dy[k] += (sy[k] / sw[k] * a).toFloat()
    }
  }

  private fun acumular(pos: DoubleArray, valores: DoubleArray, n: Int, sigmaCeldas: Double,
                       sx: DoubleArray, sy: DoubleArray, sw: DoubleArray) {
    val sig = sigmaCeldas * celda
    val rad = (3 * sigmaCeldas).toInt() + 1
    for (s in 0 until n) {
      val u = pos[s * 2]; val v = pos[s * 2 + 1]
      val ci = (u / celda).toInt(); val cj = (v / celda).toInt()
      for (j in max(0, cj - rad)..min(ny - 1, cj + rad + 1)) for (i in max(0, ci - rad)..min(nx - 1, ci + rad + 1)) {
        val ddx = i * celda - u; val ddy = j * celda - v
        val w = exp(-(ddx * ddx + ddy * ddy) / (2 * sig * sig))
        if (w < 1e-4) continue
        val k = j * nx + i
        sx[k] += w * valores[s * 2]; sy[k] += w * valores[s * 2 + 1]; sw[k] += w
      }
    }
  }

  private fun rellenar(tiene: BooleanArray) {
    if (tiene.none { it }) return
    val conocido = tiene.copyOf()
    var faltan = conocido.count { !it }
    var pasadas = 0
    while (faltan > 0 && pasadas < nx + ny) {
      val nuevos = ArrayList<Int>()
      for (j in 0 until ny) for (i in 0 until nx) {
        val k = j * nx + i
        if (conocido[k]) continue
        var sxx = 0.0; var syy = 0.0; var c = 0
        for ((di, dj) in arrayOf(1 to 0, -1 to 0, 0 to 1, 0 to -1)) {
          val ii = i + di; val jj = j + dj
          if (ii < 0 || jj < 0 || ii >= nx || jj >= ny) continue
          val kk = jj * nx + ii
          if (conocido[kk]) { sxx += dx[kk]; syy += dy[kk]; c++ }
        }
        if (c > 0) { dx[k] = (sxx / c).toFloat(); dy[k] = (syy / c).toFloat(); nuevos.add(k) }
      }
      for (k in nuevos) conocido[k] = true
      faltan -= nuevos.size
      pasadas++
    }
  }

  private fun suavizar() {
    val ox = dx.copyOf(); val oy = dy.copyOf()
    for (j in 0 until ny) for (i in 0 until nx) {
      var sxx = 0.0; var syy = 0.0; var sw = 0.0
      for (dj in -1..1) for (di in -1..1) {
        val ii = i + di; val jj = j + dj
        if (ii < 0 || jj < 0 || ii >= nx || jj >= ny) continue
        val w = if (di == 0 && dj == 0) 4.0 else if (di == 0 || dj == 0) 2.0 else 1.0
        sxx += w * ox[jj * nx + ii]; syy += w * oy[jj * nx + ii]; sw += w
      }
      dx[j * nx + i] = (sxx / sw).toFloat(); dy[j * nx + i] = (syy / sw).toFloat()
    }
  }

  fun magnitudMax(): Double {
    var m = 0.0
    for (k in dx.indices) m = max(m, sqrt((dx[k] * dx[k] + dy[k] * dy[k]).toDouble()))
    return m
  }
}
