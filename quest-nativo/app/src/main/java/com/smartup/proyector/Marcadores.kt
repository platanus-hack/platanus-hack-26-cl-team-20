package com.smartup.proyector

import com.google.zxing.BarcodeFormat
import com.google.zxing.EncodeHintType
import com.google.zxing.qrcode.QRCodeWriter
import com.google.zxing.qrcode.decoder.ErrorCorrectionLevel
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.sqrt

/**
 * Grilla de códigos QR proyectados (modo `marcadores`). Cada QR lleva su ID ("P07") y su posición
 * en píxeles del proyector es conocida: el tracking de QR de MRUK entrega su pose 6DoF en el mundo
 * → centro y 4 esquinas en 3D ↔ centro y esquinas en la textura → pares para AjusteProyector.
 *
 * Fondo negro; cada QR es un cuadrado blanco (con su zona de silencio de 4 módulos) con los
 * módulos en negro, como un QR impreso.
 */
object Marcadores {
  const val ZONA = 4   // módulos de zona de silencio

  class Item(val id: Int, val payload: String, val modulos: Int, val modPx: Int,
             val cx: Double, val cy: Double,
             /** esquinas del símbolo (sin zona de silencio) TL, TR, BR, BL: u0,v0,u1,v1,... */
             val esquinas: DoubleArray,
             val matriz: BooleanArray)

  class Layout(val cols: Int, val filas: Int, val ancho: Int, val alto: Int, val items: List<Item>) {
    val total get() = items.size
    fun porPayload(p: String): Item? = items.firstOrNull { it.payload == p }

    fun json(): JSONObject {
      val a = JSONArray()
      for (it in items) a.put(JSONObject().put("id", it.id).put("payload", it.payload).put("centro", JSONArray(listOf(it.cx, it.cy)))
          .put("esquinas", JSONArray(it.esquinas.toList())).put("modulos", it.modulos).put("mod_px", it.modPx))
      return JSONObject().put("cols", cols).put("filas", filas).put("ancho", ancho).put("alto", alto).put("items", a)
    }
  }

  fun payload(id: Int) = "P%02d".format(id)

  /** Matriz de módulos del QR (true = oscuro), sin zona de silencio. */
  fun matrizQr(texto: String): Pair<Int, BooleanArray> {
    val bm = QRCodeWriter().encode(texto, BarcodeFormat.QR_CODE, 0, 0,
        mapOf(EncodeHintType.MARGIN to 0, EncodeHintType.ERROR_CORRECTION to ErrorCorrectionLevel.M))
    val n = bm.width
    return n to BooleanArray(n * n) { bm.get(it % n, it / n) }
  }

  /**
   * Grilla cols×filas centrada en celdas iguales; el QR más grande que cabe con su zona de silencio
   * (y `ladoMax` px si se da). Módulos de tamaño entero para que el patrón caiga en píxeles exactos.
   */
  fun layout(cols: Int = 4, filas: Int = 3, ancho: Int = 1920, alto: Int = 1080, ladoMax: Int = 0, relleno: Double = 0.92): Layout {
    val celW = ancho.toDouble() / cols; val celH = alto.toDouble() / filas
    val items = ArrayList<Item>()
    for (f in 0 until filas) for (c in 0 until cols) {
      val id = f * cols + c
      val p = payload(id)
      val (n, m) = matrizQr(p)
      var lado = (minOf(celW, celH) * relleno).toInt()
      if (ladoMax > 0) lado = minOf(lado, ladoMax)
      val mod = maxOf(1, lado / (n + 2 * ZONA))
      val sim = n * mod
      val cx = Math.round(celW * (c + 0.5)).toDouble(); val cy = Math.round(celH * (f + 0.5)).toDouble()
      val x0 = cx - sim / 2.0; val y0 = cy - sim / 2.0
      val x0i = Math.round(x0).toDouble(); val y0i = Math.round(y0).toDouble()
      items.add(Item(id, p, n, mod, x0i + sim / 2.0, y0i + sim / 2.0,
          doubleArrayOf(x0i, y0i, x0i + sim, y0i, x0i + sim, y0i + sim, x0i, y0i + sim), m))
    }
    return Layout(cols, filas, ancho, alto, items)
  }

  /** ARGB (fila 0 = arriba) del patrón completo: fondo negro, QR blancos con módulos negros. */
  fun rasterizar(l: Layout): IntArray {
    val px = IntArray(l.ancho * l.alto) { 0xFF000000.toInt() }
    for (it in l.items) {
      val z = ZONA * it.modPx
      val x0 = it.esquinas[0].toInt(); val y0 = it.esquinas[1].toInt(); val sim = it.modulos * it.modPx
      for (y in y0 - z until y0 + sim + z) for (x in x0 - z until x0 + sim + z) {
        if (x < 0 || y < 0 || x >= l.ancho || y >= l.alto) continue
        val mx = Math.floorDiv(x - x0, it.modPx); val my = Math.floorDiv(y - y0, it.modPx)
        val oscuro = mx in 0 until it.modulos && my in 0 until it.modulos && it.matriz[my * it.modulos + mx]
        px[y * l.ancho + x] = if (oscuro) 0xFF000000.toInt() else 0xFFFFFFFF.toInt()
      }
    }
    return px
  }

  // ----------------------------------------------------------------- detecciones → pares
  /** Una detección de MRUK: pose del QR (mundo, 4x4) y su rectángulo en el plano local. */
  class Deteccion(val id: Int, val payload: String, val pose: DoubleArray, val min: DoubleArray, val max: DoubleArray,
                  val seguido: Boolean, val tNs: Long) {
    val centro get() = Mat.xformPt(pose, (min[0] + max[0]) / 2, (min[1] + max[1]) / 2, 0.0)
    /** Esquinas del rectángulo en orden cíclico (sin saber cuál es la TL del símbolo). */
    fun esquinasCiclo(): List<DoubleArray> = listOf(
        Mat.xformPt(pose, min[0], max[1], 0.0), Mat.xformPt(pose, max[0], max[1], 0.0),
        Mat.xformPt(pose, max[0], min[1], 0.0), Mat.xformPt(pose, min[0], min[1], 0.0))
    val lado get() = sqrt((max[0] - min[0]) * (max[1] - min[1]))
  }

  private fun sub(a: DoubleArray, b: DoubleArray) = doubleArrayOf(a[0] - b[0], a[1] - b[1], a[2] - b[2])
  private fun dot(a: DoubleArray, b: DoubleArray) = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
  private fun norm(a: DoubleArray): DoubleArray { val n = sqrt(dot(a, a)).coerceAtLeast(1e-12); return doubleArrayOf(a[0] / n, a[1] / n, a[2] / n) }

  /**
   * Esquinas 3D ordenadas TL, TR, BR, BL del símbolo. La orientación del plano de MRUK respecto del
   * símbolo no se conoce: se elige, entre los 8 órdenes cíclicos, el que alinea TL→TR con +u y
   * TL→BL con +v estimados con los centros vecinos de la grilla (+u = columna siguiente, +v = fila siguiente).
   */
  fun esquinasOrdenadas(d: Deteccion, dirU: DoubleArray, dirV: DoubleArray): List<DoubleArray> {
    val k = d.esquinasCiclo()
    var mejor = k; var puntaje = -1e9
    for (rot in 0 until 4) for (inv in listOf(false, true)) {
      val o = (0 until 4).map { i -> k[if (inv) ((rot - i) % 4 + 4) % 4 else (rot + i) % 4] }
      val s = dot(norm(sub(o[1], o[0])), dirU) + dot(norm(sub(o[3], o[0])), dirV)
      if (s > puntaje) { puntaje = s; mejor = o }
    }
    return mejor
  }

  class Correspondencias(val xMundo: DoubleArray, val uv: DoubleArray, val ids: List<Int>, val nCentros: Int)

  /** Pares mundo ↔ textura (centros + esquinas) de las detecciones seguidas con ID de la grilla. */
  fun correspondencias(l: Layout, dets: List<Deteccion>, conEsquinas: Boolean = true): Correspondencias {
    val porId = dets.filter { it.seguido && it.id in 0 until l.total }.associateBy { it.id }
    val xs = ArrayList<Double>(); val uvs = ArrayList<Double>(); val ids = ArrayList<Int>()
    // direcciones +u / +v globales con los centros vecinos
    val du = doubleArrayOf(0.0, 0.0, 0.0); val dv = doubleArrayOf(0.0, 0.0, 0.0)
    for ((id, d) in porId) {
      val c = id % l.cols; val f = id / l.cols
      porId[id + 1]?.takeIf { c + 1 < l.cols }?.let { n -> val v = norm(sub(n.centro, d.centro)); for (q in 0..2) du[q] += v[q] }
      porId[id + l.cols]?.takeIf { f + 1 < l.filas }?.let { n -> val v = norm(sub(n.centro, d.centro)); for (q in 0..2) dv[q] += v[q] }
    }
    val u = norm(du); val v = norm(dv)
    var nc = 0
    for ((id, d) in porId.toSortedMap()) {
      val it = l.items[id]
      val c = d.centro
      xs.addAll(c.toList()); uvs.add(it.cx); uvs.add(it.cy); ids.add(id); nc++
    }
    if (conEsquinas && dot(du, du) > 0 && dot(dv, dv) > 0) {
      for ((id, d) in porId.toSortedMap()) {
        val it = l.items[id]
        val es = esquinasOrdenadas(d, u, v)
        for (k in 0 until 4) { xs.addAll(es[k].toList()); uvs.add(it.esquinas[k * 2]); uvs.add(it.esquinas[k * 2 + 1]); ids.add(id) }
      }
    }
    return Correspondencias(xs.toDoubleArray(), uvs.toDoubleArray(), ids, nc)
  }
}
