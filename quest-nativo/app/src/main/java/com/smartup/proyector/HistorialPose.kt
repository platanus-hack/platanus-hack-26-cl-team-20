package com.smartup.proyector

import kotlin.math.acos
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

/** Pose con tiempo (base SystemClock.elapsedRealtimeNanos). q = (w, x, y, z). */
class PoseT(val tNs: Long, val t: DoubleArray, val q: DoubleArray) {
  /** 4x4 mundo ← cabeza. */
  fun mat4(): DoubleArray = Mat.pose4(t[0], t[1], t[2], q[0], q[1], q[2], q[3])

  fun distancia(o: PoseT): Pair<Double, Double> {
    val dx = t[0] - o.t[0]; val dy = t[1] - o.t[1]; val dz = t[2] - o.t[2]
    val d = q[0] * o.q[0] + q[1] * o.q[1] + q[2] * o.q[2] + q[3] * o.q[3]
    val ang = 2 * acos(min(1.0, kotlin.math.abs(d)))
    return Pair(sqrt(dx * dx + dy * dy + dz * dz), Math.toDegrees(ang))
  }
}

/**
 * Historial de poses de la cabeza (lo llena el sistema ECS cada cuadro) con interpolación por
 * tiempo (para la pose de la cámara en el timestamp del cuadro) y predicción lineal + angular
 * (para la latencia cabeza → luz en la pared).
 */
class HistorialPose(private val capacidad: Int = 512) {
  private val buf = arrayOfNulls<PoseT>(capacidad)
  private var n = 0
  private var cab = 0

  @Synchronized
  fun agregar(p: PoseT) {
    buf[cab] = p
    cab = (cab + 1) % capacidad
    if (n < capacidad) n++
  }

  private fun get(i: Int): PoseT = buf[(cab - n + i + capacidad) % capacidad]!!

  @Synchronized
  fun ultima(): PoseT? = if (n == 0) null else get(n - 1)

  /** Pose interpolada en tNs (slerp); fuera del rango devuelve el extremo. */
  @Synchronized
  fun en(tNs: Long): PoseT? {
    if (n == 0) return null
    if (tNs <= get(0).tNs) return get(0)
    if (tNs >= get(n - 1).tNs) return get(n - 1)
    var lo = 0; var hi = n - 1
    while (hi - lo > 1) { val m = (lo + hi) / 2; if (get(m).tNs <= tNs) lo = m else hi = m }
    val a = get(lo); val b = get(hi)
    val f = (tNs - a.tNs).toDouble() / (b.tNs - a.tNs).coerceAtLeast(1)
    return PoseT(tNs, DoubleArray(3) { a.t[it] + (b.t[it] - a.t[it]) * f }, slerp(a.q, b.q, f))
  }

  /**
   * Extrapola hasta tObjetivoNs con la velocidad medida en los últimos `ventanaNs`. Δ se acota a
   * `maxNs` para que un salto de tracking no dispare la imagen.
   */
  @Synchronized
  fun predecir(tObjetivoNs: Long, ventanaNs: Long = 30_000_000L, maxNs: Long = 200_000_000L): PoseT? {
    if (n == 0) return null
    val l = get(n - 1)
    if (n < 2) return l
    var k = n - 2
    while (k > 0 && l.tNs - get(k).tNs < ventanaNs) k--
    val e = get(k)
    val dt = (l.tNs - e.tNs) / 1e9
    if (dt <= 1e-4) return l
    val delta = ((tObjetivoNs - l.tNs).coerceIn(0L, maxNs)) / 1e9
    val t = DoubleArray(3) { l.t[it] + (l.t[it] - e.t[it]) / dt * delta }
    // rotación relativa (mundo) de e a l: qr = ql * conj(qe)
    val qr = mul(l.q, conj(e.q))
    val ang = 2 * acos(qr[0].coerceIn(-1.0, 1.0))
    val s = sqrt(1 - qr[0] * qr[0])
    val q = if (s < 1e-9 || ang < 1e-9) l.q else {
      var a = ang; var ax = qr[1] / s; var ay = qr[2] / s; var az = qr[3] / s
      if (a > Math.PI) { a -= 2 * Math.PI }
      val ae = a / dt * delta
      val dq = doubleArrayOf(cos(ae / 2), ax * sin(ae / 2), ay * sin(ae / 2), az * sin(ae / 2))
      mul(dq, l.q)
    }
    return PoseT(tObjetivoNs, t, q)
  }

  companion object {
    fun conj(q: DoubleArray) = doubleArrayOf(q[0], -q[1], -q[2], -q[3])

    fun mul(a: DoubleArray, b: DoubleArray) = doubleArrayOf(
        a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3],
        a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
        a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1],
        a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0],
    )

    fun slerp(a: DoubleArray, b0: DoubleArray, f: Double): DoubleArray {
      var b = b0
      var d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]
      if (d < 0) { b = DoubleArray(4) { -b0[it] }; d = -d }
      if (d > 0.9995) {
        val r = DoubleArray(4) { a[it] + (b[it] - a[it]) * f }
        val nn = sqrt(r.sumOf { it * it })
        return DoubleArray(4) { r[it] / nn }
      }
      val th = acos(d); val s = sin(th)
      val wa = sin((1 - f) * th) / s; val wb = sin(f * th) / s
      return DoubleArray(4) { a[it] * wa + b[it] * wb }
    }
  }
}
