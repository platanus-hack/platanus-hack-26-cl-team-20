package com.smartup.proyector

import android.util.Log
import com.meta.spatial.core.Pose
import com.meta.spatial.core.Vector3
import com.meta.spatial.mruk.MRUKAnchor
import com.meta.spatial.mruk.MRUKEnvironmentRaycastHitResult
import com.meta.spatial.mruk.MRUKFeature
import com.meta.spatial.mruk.MRUKPlane
import com.meta.spatial.mruk.MRUKVolume
import com.meta.spatial.toolkit.Transform
import kotlin.math.abs
import kotlin.math.sqrt

/**
 * Geometría de la sala para el mapeo, como triángulos sueltos con atributos por vértice
 * [pos(3) normal(3) baricéntricas(3) color(3) tipo(1)] (RendererProyector.FLOATS_VERT).
 * Las baricéntricas marcan qué aristas se dibujan (diagonales internas ocultas).
 *
 * Fuentes: (1) MRUK: global mesh + planos y volúmenes de las anclas (Space Setup);
 * (2) malla de profundidad: rejilla de raycasts del Environment Raycaster por el frustum del
 * proyector (no necesita Space Setup).
 */
object GeometriaSala {
  private const val TAG = "Proyector"

  fun colorEtiqueta(label: String): FloatArray = when (label.uppercase()) {
    "FLOOR" -> floatArrayOf(0.2f, 0.45f, 1f)
    "CEILING" -> floatArrayOf(0.5f, 0.5f, 0.55f)
    "WALL_FACE", "INVISIBLE_WALL_FACE" -> floatArrayOf(0f, 0.9f, 1f)
    "TABLE" -> floatArrayOf(1f, 0.75f, 0.1f)
    "COUCH" -> floatArrayOf(1f, 0.3f, 0.6f)
    "SCREEN" -> floatArrayOf(0.65f, 0.35f, 1f)
    "STORAGE" -> floatArrayOf(0.3f, 1f, 0.4f)
    "BED" -> floatArrayOf(1f, 0.5f, 0.2f)
    "DOOR_FRAME", "WINDOW_FRAME" -> floatArrayOf(1f, 1f, 1f)
    "LAMP", "PLANT" -> floatArrayOf(0.6f, 1f, 0.2f)
    "GLOBAL_MESH" -> floatArrayOf(0.85f, 0.95f, 1f)
    else -> floatArrayOf(0.8f, 0.8f, 0.8f)
  }

  class Constructor {
    private val v = ArrayList<Float>(1 shl 16)
    val nVerts get() = v.size / RendererProyector.FLOATS_VERT

    private fun vert(p: DoubleArray, n: DoubleArray, b0: Float, b1: Float, b2: Float, c: FloatArray, tipo: Float) {
      v.add(p[0].toFloat()); v.add(p[1].toFloat()); v.add(p[2].toFloat())
      v.add(n[0].toFloat()); v.add(n[1].toFloat()); v.add(n[2].toFloat())
      v.add(b0); v.add(b1); v.add(b2)
      v.add(c[0]); v.add(c[1]); v.add(c[2]); v.add(tipo)
    }

    /** Triángulo; `ocultas` = máscara de aristas ocultas (bit k = arista opuesta al vértice k). */
    fun tri(a: DoubleArray, b: DoubleArray, c: DoubleArray, color: FloatArray, tipo: Float, ocultas: Int = 0, normal: DoubleArray? = null) {
      val n = normal ?: normalDe(a, b, c)
      val h0 = if (ocultas and 1 != 0) 1f else 0f
      val h1 = if (ocultas and 2 != 0) 1f else 0f
      val h2 = if (ocultas and 4 != 0) 1f else 0f
      vert(a, n, 1f, h1, h2, color, tipo)
      vert(b, n, h0, 1f, h2, color, tipo)
      vert(c, n, h0, h1, 1f, color, tipo)
    }

    /** Cuadrilátero a-b-c-d con la diagonal a-c oculta. */
    fun quad(a: DoubleArray, b: DoubleArray, c: DoubleArray, d: DoubleArray, color: FloatArray, tipo: Float) {
      tri(a, b, c, color, tipo, ocultas = 2)
      tri(a, c, d, color, tipo, ocultas = 4)
    }

    fun toArray(): FloatArray = v.toFloatArray()
  }

  fun normalDe(a: DoubleArray, b: DoubleArray, c: DoubleArray): DoubleArray {
    val n = Mat.cross(doubleArrayOf(b[0] - a[0], b[1] - a[1], b[2] - a[2]), doubleArrayOf(c[0] - a[0], c[1] - a[1], c[2] - a[2]))
    val l = sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2])
    return if (l < 1e-12) doubleArrayOf(0.0, 1.0, 0.0) else doubleArrayOf(n[0] / l, n[1] / l, n[2] / l)
  }

  fun poseMat(p: Pose): DoubleArray =
      Mat.pose4(p.t.x.toDouble(), p.t.y.toDouble(), p.t.z.toDouble(), p.q.w.toDouble(), p.q.x.toDouble(), p.q.y.toDouble(), p.q.z.toDouble())

  private fun etiqueta(a: MRUKAnchor): String =
      runCatching { if (a.labelsCount > 0) a.labels[0] ?: "OTHER" else "OTHER" }.getOrDefault("OTHER")

  class Resultado(val verts: FloatArray, val resumen: String, val anclaSala: DoubleArray?)

  /** Toda la geometría MRUK de todas las salas cargadas. */
  fun desdeMruk(mruk: MRUKFeature, incluirGlobal: Boolean = true): Resultado {
    val k = Constructor()
    var planos = 0; var volumenes = 0; var trisGlobal = 0
    var anclaSala: DoubleArray? = null
    val centros = ArrayList<DoubleArray>()
    for (room in mruk.rooms) {
      if (anclaSala == null) {
        runCatching { room.entity?.tryGetComponent<Transform>()?.transform }.getOrNull()?.let { anclaSala = poseMat(it) }
      }
      var poseGlobal: DoubleArray? = null
      for (e in room.anchors) {
        val anchor = e.tryGetComponent<MRUKAnchor>() ?: continue
        val label = etiqueta(anchor)
        val pose = e.tryGetComponent<Transform>()?.transform ?: continue
        val m = poseMat(pose)
        if (label.uppercase() == "GLOBAL_MESH") { poseGlobal = m; continue }
        val col = colorEtiqueta(label)
        centros.add(doubleArrayOf(m[3], m[7], m[11]))
        e.tryGetComponent<MRUKPlane>()?.let { plane ->
          val pts = ArrayList<DoubleArray>()
          for (i in 0 until plane.boundariesCount) {
            runCatching { plane.boundary[i] }.getOrNull()?.let { b -> pts.add(Mat.xformPt(m, b.x.toDouble(), b.y.toDouble(), 0.0)) }
          }
          if (pts.size < 3) {
            val mn = plane.min; val mx = plane.max
            pts.clear()
            for ((x, y) in listOf(mn.x to mn.y, mx.x to mn.y, mx.x to mx.y, mn.x to mx.y)) pts.add(Mat.xformPt(m, x.toDouble(), y.toDouble(), 0.0))
          }
          val n = Mat.xformDir(m, 0.0, 0.0, 1.0)
          val c = DoubleArray(3)
          for (p in pts) for (q in 0..2) c[q] += p[q] / pts.size
          for (i in pts.indices) {
            // abanico desde el centroide: solo se dibuja el borde del polígono
            k.tri(c, pts[i], pts[(i + 1) % pts.size], col, 1f, ocultas = 2 or 4, normal = n)
          }
          planos++
        }
        e.tryGetComponent<MRUKVolume>()?.let { vol ->
          val a = vol.min; val b = vol.max
          fun p(x: Float, y: Float, z: Float) = Mat.xformPt(m, x.toDouble(), y.toDouble(), z.toDouble())
          val c000 = p(a.x, a.y, a.z); val c100 = p(b.x, a.y, a.z); val c110 = p(b.x, b.y, a.z); val c010 = p(a.x, b.y, a.z)
          val c001 = p(a.x, a.y, b.z); val c101 = p(b.x, a.y, b.z); val c111 = p(b.x, b.y, b.z); val c011 = p(a.x, b.y, b.z)
          k.quad(c000, c100, c110, c010, col, 1f); k.quad(c001, c101, c111, c011, col, 1f)
          k.quad(c000, c100, c101, c001, col, 1f); k.quad(c010, c110, c111, c011, col, 1f)
          k.quad(c000, c010, c011, c001, col, 1f); k.quad(c100, c110, c111, c101, col, 1f)
          volumenes++
        }
      }
      if (incluirGlobal) room.globalMesh?.let { gm ->
        val pos = gm.positions; val idx = gm.indices
        // ¿posiciones en el marco del ancla GLOBAL_MESH o ya en el mundo? se elige la que cae junto a las anclas
        var usar: DoubleArray? = null
        if (poseGlobal != null && centros.isNotEmpty()) {
          val cm = DoubleArray(3); for (c in centros) for (q in 0..2) cm[q] += c[q] / centros.size
          var c0 = DoubleArray(3); var c1 = DoubleArray(3)
          val nv = pos.size / 3
          val paso = maxOf(1, nv / 500)
          var cnt = 0
          for (i in 0 until nv step paso) {
            val x = pos[i * 3].toDouble(); val y = pos[i * 3 + 1].toDouble(); val z = pos[i * 3 + 2].toDouble()
            val w = Mat.xformPt(poseGlobal!!, x, y, z)
            c0[0] += x; c0[1] += y; c0[2] += z; c1[0] += w[0]; c1[1] += w[1]; c1[2] += w[2]; cnt++
          }
          c0 = DoubleArray(3) { c0[it] / cnt }; c1 = DoubleArray(3) { c1[it] / cnt }
          val d0 = dist(c0, cm); val d1 = dist(c1, cm)
          usar = if (d1 < d0) poseGlobal else null
          Log.i(TAG, "[sala] global mesh: centro crudo a %.2f m de las anclas, transformado a %.2f m → %s".format(d0, d1, if (usar != null) "marco del ancla" else "mundo"))
        }
        val col = colorEtiqueta("GLOBAL_MESH")
        for (t in 0 until idx.size / 3) {
          fun pv(i: Int): DoubleArray {
            val x = pos[i * 3].toDouble(); val y = pos[i * 3 + 1].toDouble(); val z = pos[i * 3 + 2].toDouble()
            return if (usar != null) Mat.xformPt(usar, x, y, z) else doubleArrayOf(x, y, z)
          }
          k.tri(pv(idx[t * 3]), pv(idx[t * 3 + 1]), pv(idx[t * 3 + 2]), col, 0f)
          trisGlobal++
        }
      }
    }
    val res = "salas=${mruk.rooms.size} planos=$planos volúmenes=$volumenes tris_global=$trisGlobal verts=${k.nVerts}"
    return Resultado(k.toArray(), res, anclaSala)
  }

  private fun dist(a: DoubleArray, b: DoubleArray) =
      sqrt((a[0] - b[0]) * (a[0] - b[0]) + (a[1] - b[1]) * (a[1] - b[1]) + (a[2] - b[2]) * (a[2] - b[2]))

  /**
   * Malla de profundidad: rejilla nx×ny de rayos del proyector (marco mundo) contra el Environment
   * Raycaster. Corre en el hilo principal (MRUK), en tandas; `listo` recibe los vértices.
   */
  class EscaneoProfundidad(
      private val mruk: MRUKFeature,
      private val origen: DoubleArray,             // centro del proyector, mundo
      private val rayos: Array<DoubleArray>,        // direcciones mundo, nx*ny (fila v, columna u)
      private val nx: Int, private val ny: Int,
      private val maxM: Float = 8f,
  ) {
    private val pts = arrayOfNulls<DoubleArray>(nx * ny)
    private var i = 0
    var aciertos = 0
      private set

    /** Hace hasta `tanda` raycasts; devuelve true al terminar. */
    fun paso(tanda: Int = 400): Boolean {
      val o = Vector3(origen[0].toFloat(), origen[1].toFloat(), origen[2].toFloat())
      var k = 0
      while (i < nx * ny && k < tanda) {
        val d = rayos[i]
        val hit = runCatching { mruk.raycastEnvironment(o, Vector3(d[0].toFloat(), d[1].toFloat(), d[2].toFloat()), maxM) }.getOrNull()
        if (hit != null && hit.result == MRUKEnvironmentRaycastHitResult.SUCCESS) {
          pts[i] = doubleArrayOf(hit.point.x.toDouble(), hit.point.y.toDouble(), hit.point.z.toDouble())
          aciertos++
        }
        i++; k++
      }
      return i >= nx * ny
    }

    fun malla(): FloatArray {
      val k = Constructor()
      for (j in 0 until ny - 1) for (q in 0 until nx - 1) {
        val a = pts[j * nx + q] ?: continue; val b = pts[j * nx + q + 1] ?: continue
        val c = pts[(j + 1) * nx + q + 1] ?: continue; val d = pts[(j + 1) * nx + q] ?: continue
        // no unir a través de saltos de profundidad (> 8% de la distancia)
        val da = dist(a, origen); val dz = maxOf(abs(dist(b, origen) - da), abs(dist(c, origen) - da), abs(dist(d, origen) - da))
        if (dz > 0.08 * da + 0.02) continue
        val n = normalDe(a, b, c)
        val col = if (abs(n[1]) > 0.85) colorEtiqueta("TABLE") else colorEtiqueta("WALL_FACE")
        k.quad(a, b, c, d, col, 0f)
      }
      return k.toArray()
    }
  }
}
