package com.smartup.proyector

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import java.io.File
import java.io.FileOutputStream
import java.util.Date
import kotlin.math.sqrt
import org.json.JSONArray
import org.json.JSONObject

/** Resultado de una calibración (lo que usa el render y lo que se guarda). */
data class Calibracion(
    val modelo: ModeloProyector,
    val ajuste: ResultadoAjuste?,
    val rejilla: RejillaCorreccion,
    val poseCabeza: PoseT,
    val intr: ScanCamera.Intrinsecos?,
    val latenciaLazoMs: Double?,
    val latenciaCapturaMs: Double?,
    val vistas: Int,
    val dir: String,
    val salida: String,
    val lazoMedianaPx: Double? = null,
    val lazoP90Px: Double? = null,
    val lazoMedianaCamPx: Double? = null,
    val lazoIter: Int = 0,
    val grayMedianaPx: Double? = ajuste?.mediana,
    val grayP90Px: Double? = ajuste?.p90,
    val tipo: String = ajuste?.tipo ?: "cargado",
    val nPares: Int = ajuste?.residuos?.size ?: 0,
    val nInliers: Int = ajuste?.nInliers ?: 0,
) {
  fun copia(rejilla: RejillaCorreccion = this.rejilla, lazoMedianaPx: Double? = this.lazoMedianaPx, lazoP90Px: Double? = this.lazoP90Px,
            lazoMedianaCamPx: Double? = this.lazoMedianaCamPx, lazoIter: Int = this.lazoIter) =
      copy(rejilla = rejilla, lazoMedianaPx = lazoMedianaPx, lazoP90Px = lazoP90Px, lazoMedianaCamPx = lazoMedianaCamPx, lazoIter = lazoIter)
}

object CalibIO {
  private fun arr(a: DoubleArray) = JSONArray().also { j -> a.forEach { j.put(it) } }
  private fun arrF(a: FloatArray) = JSONArray().also { j -> a.forEach { j.put(it.toDouble()) } }
  private fun dbl(j: JSONArray) = DoubleArray(j.length()) { j.getDouble(it) }

  fun poseJson(p: PoseT) = JSONObject().put("t", arr(p.t)).put("q_wxyz", arr(p.q)).put("t_ns", p.tNs)

  fun poseDe(j: JSONObject) = PoseT(j.optLong("t_ns"), dbl(j.getJSONArray("t")), dbl(j.getJSONArray("q_wxyz")))

  /** Pose del proyector en el marco cabeza, convención Spatial (x der, y arriba, z adelante). */
  fun poseProyector(m: ModeloProyector): Pair<DoubleArray, DoubleArray> {
    val r = m.r
    // columnas: derecha = fila 0 de R, arriba = −fila 1, adelante = fila 2
    val b = doubleArrayOf(r[0], -r[3], r[6], r[1], -r[4], r[7], r[2], -r[5], r[8])
    val q = Mat.mat3ToQuat(if (Mat.det33(b) > 0) b else doubleArrayOf(b[0], b[1], -b[2], b[3], b[4], -b[5], b[6], b[7], -b[8]))
    return Pair(m.centro, q)
  }

  fun guardar(f: File, c: Calibracion, app: JSONObject) {
    val m = c.modelo
    val (pc, qc) = poseProyector(m)
    val j = JSONObject()
    j.put("version", 1).put("fecha", Date().toString()).put("textura", JSONArray(listOf(m.ancho, m.alto)))
    j.put("salida", c.salida).put("dir_imagenes", c.dir)
    j.put("modelo", JSONObject()
        .put("tipo", c.tipo).put("P", arr(m.p)).put("K", arr(m.k)).put("R", arr(m.r))
        .put("fx", m.fx).put("fy", m.fy).put("cx", m.cx).put("cy", m.cy).put("skew", m.skew)
        .put("centro_cabeza_m", arr(m.centro)).put("marco_zurdo", m.reflejado)
        .put("nota", "P lleva puntos del marco CABEZA de Spatial SDK (m) a píxeles de la textura 1920x1080 (v=0 arriba)"))
    j.put("T_cabeza_proyector", JSONObject().put("t", arr(pc)).put("q_wxyz", arr(qc))
        .put("nota", "marco del proyector: x derecha, y arriba, z adelante (eje óptico)"))
    j.put("residuo", JSONObject()
        .put("gray_mediana_px", c.grayMedianaPx ?: JSONObject.NULL).put("gray_p90_px", c.grayP90Px ?: JSONObject.NULL)
        .put("gray_rms_px", c.ajuste?.rms ?: JSONObject.NULL)
        .put("pares", c.nPares).put("inliers", c.nInliers).put("planitud", c.ajuste?.planitud ?: JSONObject.NULL)
        .put("lazo_mediana_px", c.lazoMedianaPx ?: JSONObject.NULL).put("lazo_p90_px", c.lazoP90Px ?: JSONObject.NULL)
        .put("lazo_mediana_px_camara", c.lazoMedianaCamPx ?: JSONObject.NULL).put("lazo_iteraciones", c.lazoIter))
    j.put("vistas", c.vistas)
    j.put("latencia_lazo_ms", c.latenciaLazoMs ?: JSONObject.NULL).put("latencia_captura_ms", c.latenciaCapturaMs ?: JSONObject.NULL)
    j.put("pose_cabeza_calibracion", poseJson(c.poseCabeza))
    c.intr?.let { i ->
      j.put("camara", JSONObject().put("fx", i.fx).put("fy", i.fy).put("cx", i.cx).put("cy", i.cy).put("w", i.w).put("h", i.h)
          .put("cabeza_desde_camara", arr(i.cabezaDesdeCam)).put("lens_t", arrF(i.lensT)).put("lens_q_xyzw", arrF(i.lensQ))
          .put("distorsion", i.distorsion?.let { arrF(it) } ?: JSONObject.NULL).put("referencia", i.referencia ?: JSONObject.NULL))
    }
    val r = c.rejilla
    j.put("rejilla", JSONObject().put("celda", r.celda).put("nx", r.nx).put("ny", r.ny).put("dx", arrF(r.dx)).put("dy", arrF(r.dy))
        .put("nota", "final(u) = modelo(u + C(u)), C en px de textura en los nodos (i*celda, j*celda)"))
    j.put("app", app)
    f.parentFile?.mkdirs()
    val tmp = File(f.parentFile, f.name + ".tmp")
    tmp.writeText(j.toString(1))
    tmp.renameTo(f)
  }

  class Cargada(val cal: Calibracion, val app: JSONObject)

  fun cargar(f: File): Cargada {
    val j = JSONObject(f.readText())
    val tex = j.getJSONArray("textura")
    val mj = j.getJSONObject("modelo")
    val modelo = ModeloProyector(dbl(mj.getJSONArray("P")), tex.getInt(0), tex.getInt(1))
    val rj = j.getJSONObject("rejilla")
    val rej = RejillaCorreccion(modelo.ancho, modelo.alto, rj.getInt("celda"))
    val dx = rj.getJSONArray("dx"); val dy = rj.getJSONArray("dy")
    for (k in 0 until rej.nx * rej.ny) { rej.dx[k] = dx.getDouble(k).toFloat(); rej.dy[k] = dy.getDouble(k).toFloat() }
    val res = j.getJSONObject("residuo")
    fun optD(o: JSONObject, k: String): Double? = if (o.isNull(k)) null else o.optDouble(k)
    val cal = Calibracion(
        modelo = modelo, ajuste = null, rejilla = rej, poseCabeza = poseDe(j.getJSONObject("pose_cabeza_calibracion")),
        intr = null, latenciaLazoMs = optD(j, "latencia_lazo_ms"), latenciaCapturaMs = optD(j, "latencia_captura_ms"),
        vistas = j.optInt("vistas"), dir = j.optString("dir_imagenes"), salida = j.optString("salida"),
        lazoMedianaPx = optD(res, "lazo_mediana_px"), lazoP90Px = optD(res, "lazo_p90_px"),
        lazoMedianaCamPx = optD(res, "lazo_mediana_px_camara"), lazoIter = res.optInt("lazo_iteraciones"),
        grayMedianaPx = optD(res, "gray_mediana_px"), grayP90Px = optD(res, "gray_p90_px"),
        tipo = mj.optString("tipo", "cargado"), nPares = res.optInt("pares"), nInliers = res.optInt("inliers"))
    return Cargada(cal, j.optJSONObject("app") ?: JSONObject())
  }

  // ----------------------------------------------------------------- distorsión (camera2)
  /**
   * LENS_DISTORTION de Android lleva coordenadas normalizadas OBSERVADAS a corregidas en forma
   * directa: x_c = x_i(1 + k1 r² + k2 r⁴ + k3 r⁶) + k4·2x_i y_i + k5(r² + 2x_i²) (ídem y).
   */
  fun sinDistorsion(i: ScanCamera.Intrinsecos, u: Double, v: Double): Pair<Double, Double> {
    val d = i.distorsion ?: return Pair(u, v)
    if (d.size < 5 || d.all { kotlin.math.abs(it) < 1e-9f }) return Pair(u, v)
    val x = (u - i.cx) / i.fx; val y = (v - i.cy) / i.fy
    val r2 = x * x + y * y
    val rad = 1 + d[0] * r2 + d[1] * r2 * r2 + d[2] * r2 * r2 * r2
    val xc = x * rad + d[3] * 2 * x * y + d[4] * (r2 + 2 * x * x)
    val yc = y * rad + d[3] * (r2 + 2 * y * y) + d[4] * 2 * x * y
    return Pair(i.cx + i.fx * xc, i.cy + i.fy * yc)
  }

  // ----------------------------------------------------------------- imágenes de verificación
  fun lumaABitmap(y: ByteArray, w: Int, h: Int): Bitmap {
    val px = IntArray(w * h) { val g = y[it].toInt() and 0xFF; Color.rgb(g, g, g) }
    return Bitmap.createBitmap(px, w, h, Bitmap.Config.ARGB_8888)
  }

  fun guardarBitmap(f: File, b: Bitmap, png: Boolean = false) {
    f.parentFile?.mkdirs()
    FileOutputStream(f).use { b.compress(if (png) Bitmap.CompressFormat.PNG else Bitmap.CompressFormat.JPEG, 90, it) }
  }

  fun guardarLuma(f: File, y: ByteArray, w: Int, h: Int) = runCatching { guardarBitmap(f, lumaABitmap(y, w, h)) }

  fun guardarMascara(f: File, m: BooleanArray, w: Int, h: Int) = runCatching {
    val px = IntArray(w * h) { if (m[it]) Color.WHITE else Color.BLACK }
    guardarBitmap(f, Bitmap.createBitmap(px, w, h, Bitmap.Config.ARGB_8888), png = true)
  }

  fun guardarMapa(f: File, mapa: MapaDecodificado, ejeU: Boolean) = runCatching {
    val n = if (ejeU) 1920f else 1080f
    val px = IntArray(mapa.w * mapa.h) { i ->
      val v = if (ejeU) mapa.u[i] else mapa.v[i]
      if (v.isNaN()) Color.rgb(24, 24, 24) else Color.HSVToColor(floatArrayOf((v / n).coerceIn(0f, 1f) * 270f, 1f, 1f))
    }
    guardarBitmap(f, Bitmap.createBitmap(px, mapa.w, mapa.h, Bitmap.Config.ARGB_8888), png = true)
  }

  fun guardarPares(f: File, v: Calibrador.Vista, r: ResultadoAjuste, offset: Int) = runCatching {
    val out = DoubleArray(3)
    f.printWriter().use { o ->
      o.println("cam_u,cam_v,tex_u,tex_v,x_cabeza,y_cabeza,z_cabeza,x_mundo,y_mundo,z_mundo,pred_u,pred_v,residuo_px,inlier")
      for (i in 0 until v.pares.n) {
        val x = v.pares.x
        r.modelo.proyectar(x[i * 3], x[i * 3 + 1], x[i * 3 + 2], out)
        o.println("%.2f,%.2f,%.3f,%.3f,%.5f,%.5f,%.5f,%.5f,%.5f,%.5f,%.3f,%.3f,%.3f,%d".format(
            v.camPx[i * 2], v.camPx[i * 2 + 1], v.pares.uv[i * 2], v.pares.uv[i * 2 + 1], x[i * 3], x[i * 3 + 1], x[i * 3 + 2],
            v.xMundo[i * 3], v.xMundo[i * 3 + 1], v.xMundo[i * 3 + 2], out[0], out[1], r.residuos[offset + i],
            if (r.inliers[offset + i]) 1 else 0))
      }
    }
  }

  /** Cuadro blanco de la cámara con cada muestra coloreada por su residuo (verde < 1 px … rojo > 5 px). */
  fun guardarMuestras(f: File, blanco: ByteArray, w: Int, h: Int, v: Calibrador.Vista, r: ResultadoAjuste, offset: Int) = runCatching {
    val b = lumaABitmap(blanco, w, h).copy(Bitmap.Config.ARGB_8888, true)
    val cv = Canvas(b); val p = Paint().apply { isAntiAlias = true }
    for (i in 0 until v.pares.n) {
      val e = r.residuos[offset + i]
      p.color = if (!r.inliers[offset + i]) Color.MAGENTA else Color.HSVToColor(floatArrayOf((120 - 24 * e).coerceIn(0.0, 120.0).toFloat(), 1f, 1f))
      cv.drawCircle(v.camPx[i * 2].toFloat(), v.camPx[i * 2 + 1].toFloat(), 2.2f, p)
    }
    guardarBitmap(f, b)
  }

  /** Puntos esperados (verde) y medidos (rojo) del lazo, con la línea entre ambos ×5. */
  fun guardarVerificacion(f: File, img: ByteArray, w: Int, h: Int, pares: List<DoubleArray>) = runCatching {
    val b = lumaABitmap(img, w, h).copy(Bitmap.Config.ARGB_8888, true)
    val cv = Canvas(b)
    val g = Paint().apply { color = Color.GREEN; style = Paint.Style.STROKE; strokeWidth = 1.5f; isAntiAlias = true }
    val rr = Paint().apply { color = Color.RED; strokeWidth = 2f; isAntiAlias = true }
    for (q in pares) {
      cv.drawCircle(q[0].toFloat(), q[1].toFloat(), 7f, g)
      cv.drawLine(q[0].toFloat(), q[1].toFloat(), (q[0] + 5 * (q[2] - q[0])).toFloat(), (q[1] + 5 * (q[3] - q[1])).toFloat(), rr)
    }
    guardarBitmap(f, b)
  }

  fun dist3(a: DoubleArray, b: DoubleArray) =
      sqrt((a[0] - b[0]) * (a[0] - b[0]) + (a[1] - b[1]) * (a[1] - b[1]) + (a[2] - b[2]) * (a[2] - b[2]))
}
