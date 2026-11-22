package com.smartup.proyector

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.util.AttributeSet
import android.util.Log
import android.view.View
import org.json.JSONObject
import org.opencv.android.OpenCVLoader
import org.opencv.core.CvType
import org.opencv.core.Mat as CvMat
import org.opencv.objdetect.ArucoDetector
import org.opencv.objdetect.DetectorParameters
import org.opencv.objdetect.Objdetect
import kotlin.math.sqrt

/**
 * ArUco (DICT_4X4_50) proyectados por el proyector de la mesa, vistos por la cámara passthrough:
 * esquinas en px de cámara → rayos → malla MRUK → esquinas 3D → plano + homografía px del
 * proyector → plano, y PnP del proyector de la mesa (pose fija en el marco de la sala).
 */
object ArucoMesa {
  private const val TAG = "Proyector"
  @Volatile private var cargado: Boolean? = null

  fun cargarOpenCv(): Boolean {
    cargado?.let { return it }
    val ok = runCatching { OpenCVLoader.initLocal() }.getOrElse { Log.e(TAG, "[aruco] OpenCV", it); false }
    Log.i(TAG, "[aruco] OpenCV ${if (ok) "cargado" else "NO cargó"}")
    cargado = ok
    return ok
  }

  class Marca(val id: Int, val esq: DoubleArray)   // 4 esquinas px de cámara (TL, TR, BR, BL del marcador)

  /** Parámetros del detector (comandos `aruco params|aruco3|perimetro|paso|wmax|ecr|zoom|par`). */
  data class Config(val ajustado: Boolean = true, val aruco3: Boolean = false, val perimetro: Double = 0.01, val paso: Int = 4,
                    val winMax: Int = 23, val ecr: Double = 0.6, val ppc: Int = 4, val invertidos: Boolean = false) {
    val nombre get() = (if (ajustado) "ajustado(per %.3f, umbral 3..%d paso %d, ecr %.2f, ppc %d)".format(perimetro, winMax, paso, ecr, ppc) else "fábrica") +
        (if (aruco3) "+aruco3" else "") + (if (invertidos) "+invertidos" else "")
  }

  /**
   * Detector ArUco DICT_4X4_50 sobre luma. `detectar` admite un recorte (roi = x0, y0, w, h) ampliado
   * `zoom` veces: las esquinas vuelven en px del cuadro completo.
   */
  class Detector(val cfg: Config = Config()) {
    private val det = ArucoDetector(Objdetect.getPredefinedDictionary(Objdetect.DICT_4X4_50),
        DetectorParameters().apply {
          set_cornerRefinementMethod(Objdetect.CORNER_REFINE_SUBPIX)
          if (cfg.ajustado) {
            set_adaptiveThreshWinSizeMin(3); set_adaptiveThreshWinSizeMax(cfg.winMax); set_adaptiveThreshWinSizeStep(cfg.paso)
            set_minMarkerPerimeterRate(cfg.perimetro); set_polygonalApproxAccuracyRate(0.05); set_errorCorrectionRate(cfg.ecr)
            set_perspectiveRemovePixelPerCell(cfg.ppc)
          }
          set_useAruco3Detection(cfg.aruco3)
          set_detectInvertedMarker(cfg.invertidos)
        })
    val nombre get() = cfg.nombre

    fun detectar(y: ByteArray, w: Int, h: Int, roi: IntArray? = null, zoom: Double = 1.0): List<Marca> {
      val full = CvMat(h, w, CvType.CV_8UC1)
      full.put(0, 0, y)
      var img = full
      var ox = 0.0; var oy = 0.0; var esc = 1.0
      if (roi != null) {
        val x0 = roi[0].coerceIn(0, w - 2); val y0 = roi[1].coerceIn(0, h - 2)
        val rw = roi[2].coerceIn(2, w - x0); val rh = roi[3].coerceIn(2, h - y0)
        val sub = full.submat(y0, y0 + rh, x0, x0 + rw)
        img = if (zoom > 1.01) CvMat().also { org.opencv.imgproc.Imgproc.resize(sub, it, org.opencv.core.Size(rw * zoom, rh * zoom), 0.0, 0.0, org.opencv.imgproc.Imgproc.INTER_CUBIC) } else sub
        ox = x0.toDouble(); oy = y0.toDouble(); esc = if (zoom > 1.01) zoom else 1.0
      }
      val esq = ArrayList<CvMat>(); val ids = CvMat()
      det.detectMarkers(img, esq, ids)
      val out = ArrayList<Marca>()
      for (k in esq.indices) {
        val c = FloatArray(8); esq[k].get(0, 0, c)
        val id = IntArray(1); ids.get(k, 0, id)
        out.add(Marca(id[0], DoubleArray(8) { if (it % 2 == 0) ox + c[it] / esc else oy + c[it] / esc }))
        esq[k].release()
      }
      if (img !== full) img.release()
      full.release(); ids.release()
      // un id por cuadro (en el par patrón/invertido el mismo marcador puede salir dos veces): el de mayor perímetro
      fun perim(m: Marca) = (0 until 4).sumOf { k -> val k2 = (k + 1) % 4; Math.hypot(m.esq[k2 * 2] - m.esq[k * 2], m.esq[k2 * 2 + 1] - m.esq[k * 2 + 1]) }
      return out.groupBy { it.id }.values.map { g -> g.maxByOrNull { perim(it) }!! }
    }
  }

  /**
   * Par patrón/invertido: imagen por el signo de la diferencia (la textura de la mesa se cancela).
   * 255 donde a > b + umbral, 0 donde a < b − umbral, 128 donde no cambió. Se detecta con invertidos
   * (no se sabe cuál de los dos cuadros era el patrón).
   */
  fun imagenSigno(a: ByteArray, b: ByteArray, umbral: Int = 8): ByteArray {
    val out = ByteArray(a.size)
    for (i in a.indices) {
      val d = (a[i].toInt() and 0xFF) - (b[i].toInt() and 0xFF)
      out[i] = if (d > umbral) 255.toByte() else if (d < -umbral) 0 else 128.toByte()
    }
    return out
  }

  /** Diferencia media |a − b| submuestreada (para encontrar el cuadro del patrón opuesto). */
  fun difMedia(a: ByteArray, b: ByteArray, paso: Int = 37): Double {
    var s = 0L; var n = 0
    var i = 0
    while (i < a.size) { s += Math.abs((a[i].toInt() and 0xFF) - (b[i].toInt() and 0xFF)); n++; i += paso }
    return s.toDouble() / n.coerceAtLeast(1)
  }

  /** Grilla conocida (aruco_grid.json del runtime de la mesa): esquinas de cada id en px del proyector. */
  class Grilla(val ancho: Int, val alto: Int, val esquinas: Map<Int, DoubleArray>, val cols: Int, val filas: Int) {
    val ids get() = esquinas.keys.sorted()
    /** Rectángulo que envuelve todos los marcadores (px del proyector): u0, v0, u1, v1. */
    fun rect(): DoubleArray {
      var u0 = 1e9; var v0 = 1e9; var u1 = -1e9; var v1 = -1e9
      for (e in esquinas.values) for (k in 0 until 4) { u0 = minOf(u0, e[k * 2]); u1 = maxOf(u1, e[k * 2]); v0 = minOf(v0, e[k * 2 + 1]); v1 = maxOf(v1, e[k * 2 + 1]) }
      return doubleArrayOf(u0, v0, u1, v1)
    }

    companion object {
      fun desdeJson(j: JSONObject): Grilla {
        val tam = j.optJSONArray("size")
        val m = HashMap<Int, DoubleArray>()
        val a = j.getJSONArray("markers")
        for (i in 0 until a.length()) {
          val o = a.getJSONObject(i); val e = o.getJSONArray("esquinas_px")
          m[o.getInt("id")] = DoubleArray(8) { k -> e.getJSONArray(k / 2).getDouble(k % 2) }
        }
        return Grilla(tam?.optInt(0, 1920) ?: 1920, tam?.optInt(1, 1080) ?: 1080, m, j.optInt("cols", 6), j.optInt("rows", 4))
      }
    }
  }

  /** Plano por mínimos cuadrados: centro, normal (hacia `haciaPunto`), base e1 (≈ +u de la grilla), e2 (≈ +v), residuo RMS (m). */
  class Plano(val c: DoubleArray, val n: DoubleArray, val e1: DoubleArray, val e2: DoubleArray, val rms: Double) {
    fun ab(p: DoubleArray) = doubleArrayOf(dot(sub(p, c), e1), dot(sub(p, c), e2))
    fun punto(a: Double, b: Double) = DoubleArray(3) { c[it] + e1[it] * a + e2[it] * b }
  }

  fun dist3(a: DoubleArray, b: DoubleArray) = sqrt((a[0] - b[0]) * (a[0] - b[0]) + (a[1] - b[1]) * (a[1] - b[1]) + (a[2] - b[2]) * (a[2] - b[2]))
  fun sub(a: DoubleArray, b: DoubleArray) = doubleArrayOf(a[0] - b[0], a[1] - b[1], a[2] - b[2])
  fun dot(a: DoubleArray, b: DoubleArray) = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
  fun cross(a: DoubleArray, b: DoubleArray) = doubleArrayOf(a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
  fun norm(a: DoubleArray): DoubleArray { val l = sqrt(dot(a, a)).coerceAtLeast(1e-12); return doubleArrayOf(a[0] / l, a[1] / l, a[2] / l) }

  /**
   * `pts` = esquinas 3D (mundo), `dirU` = suma de los vectores TL→TR de los marcadores (dirección +u
   * del proyector sobre la superficie). La normal se orienta hacia `haciaPunto` (la cámara).
   */
  fun ajustarPlano(pts: List<DoubleArray>, dirU: DoubleArray, haciaPunto: DoubleArray): Plano? {
    if (pts.size < 3) return null
    val c = DoubleArray(3); for (p in pts) for (q in 0..2) c[q] += p[q] / pts.size
    val cov = DoubleArray(9)
    for (p in pts) { val d = sub(p, c); for (r in 0..2) for (k in 0..2) cov[r * 3 + k] += d[r] * d[k] }
    var n = norm(Mat.minEigVec(cov, 3))
    if (dot(n, sub(haciaPunto, c)) < 0) n = doubleArrayOf(-n[0], -n[1], -n[2])
    // e1 = +u proyectado al plano; e2 = n × e1 o su opuesto, el que siga +v (abajo en la imagen del proyector)
    val u = sub(dirU, DoubleArray(3) { n[it] * dot(dirU, n) })
    val e1 = norm(u)
    var e2 = norm(cross(n, e1))
    var rms = 0.0; for (p in pts) { val d = dot(sub(p, c), n); rms += d * d }
    rms = sqrt(rms / pts.size)
    return Plano(c, n, e1, e2, rms).let { pl -> pl }
  }

  /** Reflexión (4x4 row-major) a través del plano que pasa por c con normal unitaria n. */
  fun reflexion(c: DoubleArray, n: DoubleArray): DoubleArray {
    val d = dot(n, c)
    return doubleArrayOf(
        1 - 2 * n[0] * n[0], -2 * n[0] * n[1], -2 * n[0] * n[2], 2 * d * n[0],
        -2 * n[1] * n[0], 1 - 2 * n[1] * n[1], -2 * n[1] * n[2], 2 * d * n[1],
        -2 * n[2] * n[0], -2 * n[2] * n[1], 1 - 2 * n[2] * n[2], 2 * d * n[2],
        0.0, 0.0, 0.0, 1.0)
  }

  /**
   * PnP del proyector fijo con puntos sobre un plano: el caso plano tiene dos soluciones espejo; se
   * queda la que deja el centro del lado de `haciaPunto` (la cámara/el espectador). Devuelve (modelo, reflejado).
   */
  fun ladoCorrecto(m: ModeloProyector, c: DoubleArray, n: DoubleArray, haciaPunto: DoubleArray): Pair<ModeloProyector, Boolean> {
    val lado = dot(sub(m.centro, c), n) * dot(sub(haciaPunto, c), n)
    if (lado >= 0) return m to false
    return ModeloProyector(Mat.mul(m.p, 3, 4, reflexion(c, n), 4), m.ancho, m.alto) to true
  }

  /**
   * Pose del proyector con K conocida (el caso plano no deja observar la focal: con una sola mesa casi
   * frontal, f y la distancia se compensan). Homografía plano→píxeles, K⁻¹H = λ[r1 r2 t] (Zhang),
   * R ortonormalizada; P = K·[R·Bᵀ | t − R·Bᵀ·c] con B = [e1 e2 n] la base del plano. El lado (espejo)
   * se resuelve después con ladoCorrecto. Devuelve (modelo, residuo mediano px) o null.
   */
  fun poseConK(k: DoubleArray, xs: DoubleArray, uv: DoubleArray, pl: Plano, ancho: Int, alto: Int): Pair<ModeloProyector, Double>? {
    val n = uv.size / 2
    if (n < 4) return null
    val ab = DoubleArray(n * 2)
    for (i in 0 until n) { val q = pl.ab(doubleArrayOf(xs[i * 3], xs[i * 3 + 1], xs[i * 3 + 2])); ab[i * 2] = q[0]; ab[i * 2 + 1] = q[1] }
    val h = AjusteProyector.homografia(ab, uv, n) ?: return null
    val ki = Mat.inv33(k) ?: return null
    val m = Mat.mul33(ki, h)
    val h1 = doubleArrayOf(m[0], m[3], m[6]); val h2 = doubleArrayOf(m[1], m[4], m[7]); val h3 = doubleArrayOf(m[2], m[5], m[8])
    var lam = 2.0 / (sqrt(dot(h1, h1)) + sqrt(dot(h2, h2)))
    if (h3[2] * lam < 0) lam = -lam                      // el plano delante del proyector
    var r1 = DoubleArray(3) { h1[it] * lam }; val r2a = DoubleArray(3) { h2[it] * lam }; val t = DoubleArray(3) { h3[it] * lam }
    r1 = norm(r1)
    val r2 = norm(sub(r2a, DoubleArray(3) { r1[it] * dot(r1, r2a) }))
    val r3 = cross(r1, r2)
    val rl = doubleArrayOf(r1[0], r2[0], r3[0], r1[1], r2[1], r3[1], r1[2], r2[2], r3[2])   // columnas r1 r2 r3
    val bt = doubleArrayOf(pl.e1[0], pl.e1[1], pl.e1[2], pl.e2[0], pl.e2[1], pl.e2[2], pl.n[0], pl.n[1], pl.n[2])  // Bᵀ (filas)
    val rw = Mat.mul33(rl, bt)
    val rc = Mat.mulVec33(rw, pl.c[0], pl.c[1], pl.c[2])
    val ext = doubleArrayOf(rw[0], rw[1], rw[2], t[0] - rc[0], rw[3], rw[4], rw[5], t[1] - rc[1], rw[6], rw[7], rw[8], t[2] - rc[2])
    val p = Mat.mul(k, 3, 3, ext, 4)
    val mod = ModeloProyector(p, ancho, alto)
    val res = AjusteProyector.residuos(mod.p, Pares(xs, uv))
    return mod to Mat.median(res)
  }

  class Candidato(val modelo: ModeloProyector, val tipo: String, val medianaPx: Double, val distPrior: Double?, val costo: Double)

  /**
   * Caso plano sin K: barrido de focal y cy (cx al centro) con la pose de cada K por homografía; costo =
   * (residuo / 2 px)² + penalización por alejarse del prior (centro ≈ casco + `arriba` m en vertical y
   * `lado` m en horizontal, ambos colgados fijos). Devuelve el mejor candidato (del lado de la cámara).
   */
  fun pnpConPrior(xs: DoubleArray, uv: DoubleArray, pl: Plano, ancho: Int, alto: Int, casco: DoubleArray, camara: DoubleArray,
                  arriba: Double = 0.10, lado: Double = 0.20): Candidato? {
    fun evaluar(f: Double, cy: Double): Candidato? {
      val k = doubleArrayOf(f, 0.0, ancho / 2.0, 0.0, f, cy, 0.0, 0.0, 1.0)
      val pk = poseConK(k, xs, uv, pl, ancho, alto) ?: return null
      val (m, _) = ladoCorrecto(pk.first, pl.c, pl.n, camara)
      val dy = m.centro[1] - casco[1]; val dh = Math.hypot(m.centro[0] - casco[0], m.centro[2] - casco[2])
      val costo = (pk.second / 2.0).let { it * it } + ((dh - lado) / 0.15).let { it * it } + ((dy - arriba) / 0.10).let { it * it }
      return Candidato(m, "prior casco (f=%.0f cy=%.0f)".format(f, cy), pk.second, sqrt(dy * dy + dh * dh), costo)
    }
    var mejor: Candidato? = null
    var mf = 0.0; var mcy = 0.0
    // barrido grueso: focal 500..8000 px (pasos de 6 %), cy 0,3..1,5 × alto (lens shift)
    var f = 500.0
    while (f <= 8000.0) {
      for (j in 0..24) {
        val cy = alto * (0.3 + 1.2 * j / 24.0)
        val c = evaluar(f, cy) ?: continue
        if (mejor == null || c.costo < mejor.costo) { mejor = c; mf = f; mcy = cy }
      }
      f *= 1.06
    }
    if (mejor == null) return null
    // fino: ±6 % de focal en pasos de 0,5 %, ±6 % del alto en cy
    val f0 = mf; val cy0 = mcy
    for (i in -12..12) for (j in -12..12) {
      val c = evaluar(f0 * (1 + 0.005 * i), cy0 + alto * 0.005 * j) ?: continue
      if (c.costo < mejor!!.costo) mejor = c
    }
    return mejor
  }

  /** K desde JSON: {"K": [[fx,0,cx],[0,fy,cy],[0,0,1]]} o {"proyector": {"K": ...}} o {"fx":..,"cx":..,"cy":..}. */
  fun kDesdeJson(j: JSONObject): DoubleArray? {
    val o = j.optJSONObject("proyector")?.takeIf { it.has("K") } ?: j
    o.optJSONArray("K")?.let { a ->
      return if (a.length() == 9) DoubleArray(9) { a.getDouble(it) } else DoubleArray(9) { a.getJSONArray(it / 3).getDouble(it % 3) }
    }
    if (o.has("fx")) { val fx = o.getDouble("fx"); return doubleArrayOf(fx, 0.0, o.optDouble("cx", 960.0), 0.0, o.optDouble("fy", fx), o.optDouble("cy", 540.0), 0.0, 0.0, 1.0) }
    return null
  }

  /** Corrige e2 para que apunte a +v usando los vectores TL→BL de los marcadores. */
  fun orientarV(pl: Plano, dirV: DoubleArray): Plano =
      if (dot(pl.e2, dirV) >= 0) pl else Plano(pl.c, pl.n, pl.e1, doubleArrayOf(-pl.e2[0], -pl.e2[1], -pl.e2[2]), pl.rms)
}

/** Vista del panel AR: rectángulo del plano con los marcadores esperados (cian) y detectados (verde). */
class VistaAruco @JvmOverloads constructor(ctx: Context, attrs: AttributeSet? = null) : View(ctx, attrs) {
  /** Cuadriláteros en coordenadas normalizadas del panel (0..1): id → 8 valores. */
  @Volatile var esperados: Map<Int, DoubleArray> = emptyMap()
  @Volatile var detectados: Map<Int, DoubleArray> = emptyMap()
  @Volatile var texto: String = ""
  private val pBorde = Paint().apply { color = Color.argb(220, 255, 0, 255); style = Paint.Style.STROKE; strokeWidth = 6f; isAntiAlias = true }
  private val pFondo = Paint().apply { color = Color.argb(40, 255, 255, 255) }
  private val pEsp = Paint().apply { color = Color.argb(230, 0, 230, 255); style = Paint.Style.STROKE; strokeWidth = 4f; isAntiAlias = true }
  private val pDet = Paint().apply { color = Color.argb(110, 40, 255, 90); style = Paint.Style.FILL; isAntiAlias = true }
  private val pId = Paint().apply { color = Color.WHITE; textSize = 34f; isAntiAlias = true; isFakeBoldText = true; setShadowLayer(4f, 0f, 0f, Color.BLACK) }
  private val pTxt = Paint().apply { color = Color.argb(255, 255, 220, 80); textSize = 26f; isAntiAlias = true; setShadowLayer(4f, 0f, 0f, Color.BLACK) }

  override fun onDraw(cv: Canvas) {
    val w = width.toFloat(); val h = height.toFloat()
    cv.drawColor(Color.TRANSPARENT)
    cv.drawRect(0f, 0f, w, h, pFondo)
    cv.drawRect(3f, 3f, w - 3f, h - 3f, pBorde)
    fun camino(q: DoubleArray) = Path().apply {
      moveTo((q[0] * w).toFloat(), (q[1] * h).toFloat())
      for (k in 1 until 4) lineTo((q[k * 2] * w).toFloat(), (q[k * 2 + 1] * h).toFloat())
      close()
    }
    for ((_, q) in detectados) cv.drawPath(camino(q), pDet)
    for ((id, q) in esperados) {
      cv.drawPath(camino(q), pEsp)
      val cx = ((q[0] + q[2] + q[4] + q[6]) / 4 * w).toFloat(); val cy = ((q[1] + q[3] + q[5] + q[7]) / 4 * h).toFloat()
      cv.drawText("$id", cx - 12f, cy + 12f, pId)
    }
    for ((id, q) in detectados) if (id !in esperados) {
      val cx = ((q[0] + q[2] + q[4] + q[6]) / 4 * w).toFloat(); val cy = ((q[1] + q[3] + q[5] + q[7]) / 4 * h).toFloat()
      cv.drawText("$id", cx - 12f, cy + 12f, pId)
    }
    cv.drawText(texto, 16f, h - 16f, pTxt)
  }
}
