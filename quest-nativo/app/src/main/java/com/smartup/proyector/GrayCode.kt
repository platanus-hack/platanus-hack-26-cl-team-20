package com.smartup.proyector

import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.sqrt

/**
 * Gray code igual al de `slscan/graycode.py`: por eje (0 = columnas u, 1 = filas v), bits del más
 * significativo al menos, cada patrón seguido de su inverso. Un píxel de cámara da bit 1 si el
 * patrón es más brillante que su inverso por más de `umbral`, 0 si al revés, inválido si no.
 *
 * Convención de coordenadas del proyector ("textura"): píxel entero `i` cubre [i, i+1); el centro
 * de un bloque de 2^s píxeles que empieza en b es b + 2^s/2. Fila v = 0 arriba.
 *
 * Sin Android: se prueba en la JVM (ver SinteticoTest).
 */
class GrayCode(val ancho: Int = 1920, val alto: Int = 1080, val bitsOmitidos: Int = 1) {
  data class Patron(val eje: Int, val bit: Int, val inverso: Boolean, val indice: Int)

  val bitsX = bitsPara(ancho)
  val bitsY = bitsPara(alto)

  /** Orden de proyección: eje x (MSB→LSB, normal + inverso), después eje y. */
  val secuencia: List<Patron> = buildList {
    var i = 0
    for (eje in 0..1) {
      val nb = if (eje == 0) bitsX else bitsY
      for (bit in nb - 1 downTo bitsOmitidos) {
        add(Patron(eje, bit, false, i++))
        add(Patron(eje, bit, true, i++))
      }
    }
  }

  /** Valor (0/255) del patrón en el píxel (u, v) del proyector: el mismo cálculo que el shader. */
  fun valor(p: Patron, u: Int, v: Int): Int {
    val coord = if (p.eje == 0) u else v
    val g = coord xor (coord shr 1)
    var b = (g shr p.bit) and 1
    if (p.inverso) b = 1 - b
    return b * 255
  }

  companion object {
    fun bitsPara(n: Int): Int = max(1, ceil(ln(n.toDouble()) / ln(2.0)).toInt())

    fun gray2bin(g0: Int): Int {
      var g = g0
      var s = 1
      while (s < 32) { g = g xor (g ushr s); s = s shl 1 }
      return g
    }
  }

  /**
   * Decodificador incremental: se le entregan pares (patrón, inverso) de imágenes de luma
   * (ByteArray w*h, 0..255 sin signo). Guarda por píxel qué bits fueron inválidos, así al final
   * se elige cuántos bits finos descartar por eje (cuando cámara y proyector van juntos en el rig,
   * un píxel de cámara cubre ~4–8 px del proyector y las franjas finas no se resuelven).
   */
  inner class Decodificador(val w: Int, val h: Int, val umbral: Int = 6, val mascara: BooleanArray? = null) {
    private val codigo = arrayOf(IntArray(w * h), IntArray(w * h))
    private val invalidos = arrayOf(IntArray(w * h), IntArray(w * h))   // máscara de bits inválidos
    private val contrasteMin = IntArray(w * h) { 255 }
    private val validosPorBit = arrayOf(IntArray(32), IntArray(32))
    val bitsRecibidos = intArrayOf(0, 0)
    val contrasteMedio = LinkedHashMap<String, Double>()

    fun agregarPar(eje: Int, bit: Int, img: ByteArray, inv: ByteArray) {
      require(img.size >= w * h && inv.size >= w * h)
      val cod = codigo[eje]; val bad = invalidos[eje]
      var suma = 0L; var n = 0L
      val m = 1 shl bit
      for (i in 0 until w * h) {
        if (mascara != null && !mascara[i]) { bad[i] = bad[i] or m; continue }
        val d = (img[i].toInt() and 0xFF) - (inv[i].toInt() and 0xFF)
        val ad = abs(d)
        if (ad < contrasteMin[i]) contrasteMin[i] = ad
        if (d > umbral) { cod[i] = cod[i] or m; suma += ad; n++ }
        else if (d < -umbral) { suma += ad; n++ }
        else bad[i] = bad[i] or m
      }
      validosPorBit[eje][bit] = n.toInt()
      bitsRecibidos[eje]++
      contrasteMedio["${"xy"[eje]}$bit"] = if (n > 0) suma.toDouble() / n else -1.0
    }

    /** Fracción de píxeles válidos de cada bit relativa al bit más significativo (eje). */
    fun fraccionValidos(eje: Int): Map<Int, Double> {
      val nb = if (eje == 0) bitsX else bitsY
      val base = validosPorBit[eje][nb - 1].coerceAtLeast(1).toDouble()
      return (bitsOmitidos until nb).associateWith { validosPorBit[eje][it] / base }
    }

    /** Menor cantidad de bits finos a descartar tal que todos los bits que quedan tengan ≥ fraccionMin. */
    fun omitidosAuto(eje: Int, fraccionMin: Double = 0.6): Int {
      val nb = if (eje == 0) bitsX else bitsY
      val f = fraccionValidos(eje)
      var s = nb - 1
      while (s > bitsOmitidos && (f[s - 1] ?: 0.0) >= fraccionMin) s--
      return s
    }

    /** Mapa denso cámara → proyector (centro de bloque, NaN = inválido). skip < 0: automático. */
    fun resultado(skipX: Int = -1, skipY: Int = -1): MapaDecodificado {
      val sx = if (skipX >= 0) maxOf(skipX, bitsOmitidos) else omitidosAuto(0)
      val sy = if (skipY >= 0) maxOf(skipY, bitsOmitidos) else omitidosAuto(1)
      val u = FloatArray(w * h) { Float.NaN }
      val v = FloatArray(w * h) { Float.NaN }
      val mx = (1 shl sx) - 1; val my = (1 shl sy) - 1
      var validos = 0
      for (i in 0 until w * h) {
        if ((invalidos[0][i] and mx.inv()) != 0 || (invalidos[1][i] and my.inv()) != 0) continue
        // sin los bits finos, gray2bin deja bits bajos sucios: se limpian antes de ir al centro del bloque
        val bx = (gray2bin(codigo[0][i]) shr sx) shl sx
        val by = (gray2bin(codigo[1][i]) shr sy) shl sy
        if (bx >= ancho || by >= alto) continue
        u[i] = bx + (1 shl sx) / 2.0f
        v[i] = by + (1 shl sy) / 2.0f
        validos++
      }
      return MapaDecodificado(w, h, u, v, contrasteMin.copyOf(), 1 shl maxOf(sx, sy), validos, sx, sy)
    }
  }
}

/** Mapa denso cámara → proyector. u, v en píxeles de textura del proyector (continuos). */
class MapaDecodificado(
    val w: Int,
    val h: Int,
    val u: FloatArray,
    val v: FloatArray,
    val contrasteMin: IntArray,
    val paso: Int,
    val validos: Int,
    val omitidosX: Int = 0,
    val omitidosY: Int = 0,
) {
  /** Muestra con ajuste afín local: posición subbloque y jacobiano d(u,v)/d(cámara). */
  class Muestra(
      val cu: Float, val cv: Float,       // píxel de cámara (entero = centro del píxel)
      val u: Double, val v: Double,       // textura del proyector
      val j: DoubleArray,                 // [du/dcx, du/dcy, dv/dcx, dv/dcy]
      val rms: Double,                    // residuo del ajuste local (px proyector)
      val n: Int,
  )

  /**
   * Muestrea una rejilla de `pasoCam` px de cámara; en cada punto ajusta u = a + b·dx + c·dy (y v
   * igual) sobre la ventana (2·radio+1)², con una pasada de rechazo de outliers. Así el centro sale
   * con precisión mejor que el bloque Gray.
   */
  fun muestras(pasoCam: Int = 6, radio: Int = 3, fraccionMin: Double = 0.5, rmsMax: Double = 3.0): List<Muestra> {
    val out = ArrayList<Muestra>()
    val nVent = (2 * radio + 1) * (2 * radio + 1)
    val dxs = IntArray(nVent); val dys = IntArray(nVent)
    val us = DoubleArray(nVent); val vs = DoubleArray(nVent)
    val tol = max(2.0 * paso, 4.0)
    var y = radio
    while (y < h - radio) {
      var x = radio
      while (x < w - radio) {
        val c = y * w + x
        if (!u[c].isNaN()) {
          var n = 0
          for (dy in -radio..radio) for (dx in -radio..radio) {
            val i = (y + dy) * w + (x + dx)
            if (!u[i].isNaN()) { dxs[n] = dx; dys[n] = dy; us[n] = u[i].toDouble(); vs[n] = v[i].toDouble(); n++ }
          }
          if (n >= fraccionMin * nVent) {
            val m = ajusteAfin(n, dxs, dys, us, vs, tol)
            if (m != null && m.rms <= rmsMax * max(1, paso / 2)) {
              out.add(Muestra(x.toFloat(), y.toFloat(), m.u, m.v, m.j, m.rms, m.n))
            }
          }
        }
        x += pasoCam
      }
      y += pasoCam
    }
    return out
  }

  private class Afin(val u: Double, val v: Double, val j: DoubleArray, val rms: Double, val n: Int)

  private fun ajusteAfin(n: Int, dx: IntArray, dy: IntArray, us: DoubleArray, vs: DoubleArray, tol: Double): Afin? {
    val usar = BooleanArray(n) { true }
    var res: Afin? = null
    for (pasada in 0..1) {
      val ata = DoubleArray(9); val atu = DoubleArray(3); val atv = DoubleArray(3)
      var k = 0
      for (i in 0 until n) {
        if (!usar[i]) continue
        val a0 = 1.0; val a1 = dx[i].toDouble(); val a2 = dy[i].toDouble()
        val a = doubleArrayOf(a0, a1, a2)
        for (r in 0..2) {
          for (c in 0..2) ata[r * 3 + c] += a[r] * a[c]
          atu[r] += a[r] * us[i]; atv[r] += a[r] * vs[i]
        }
        k++
      }
      if (k < 6) return null
      val cu = Mat.solve(ata, atu, 3) ?: return null
      val cv = Mat.solve(ata, atv, 3) ?: return null
      var se = 0.0; var ne = 0
      for (i in 0 until n) {
        val eu = us[i] - (cu[0] + cu[1] * dx[i] + cu[2] * dy[i])
        val ev = vs[i] - (cv[0] + cv[1] * dx[i] + cv[2] * dy[i])
        val e = sqrt(eu * eu + ev * ev)
        if (pasada == 0) usar[i] = e <= tol
        if (usar[i]) { se += e * e; ne++ }
      }
      res = Afin(cu[0], cv[0], doubleArrayOf(cu[1], cu[2], cv[1], cv[2]), if (ne > 0) sqrt(se / ne) else 0.0, ne)
    }
    return res
  }
}
