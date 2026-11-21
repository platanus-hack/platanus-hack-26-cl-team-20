package com.smartup.proyector

import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Álgebra mínima en doubles, sin Android (se prueba en la JVM del Mac).
 *
 * Convención: matrices row-major. 3x3 = DoubleArray(9), 3x4 = DoubleArray(12), 4x4 = DoubleArray(16).
 * Cuaterniones (w, x, y, z), rotación estándar de Hamilton (igual que Spatial SDK `Quaternion(w,x,y,z)`).
 */
object Mat {
  fun ident4(): DoubleArray = doubleArrayOf(1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)

  fun ident3(): DoubleArray = doubleArrayOf(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)

  fun mul(a: DoubleArray, ar: Int, ac: Int, b: DoubleArray, bc: Int): DoubleArray {
    val out = DoubleArray(ar * bc)
    for (i in 0 until ar) for (j in 0 until bc) {
      var s = 0.0
      for (k in 0 until ac) s += a[i * ac + k] * b[k * bc + j]
      out[i * bc + j] = s
    }
    return out
  }

  fun mul33(a: DoubleArray, b: DoubleArray) = mul(a, 3, 3, b, 3)

  fun mul44(a: DoubleArray, b: DoubleArray) = mul(a, 4, 4, b, 4)

  fun transpose(a: DoubleArray, r: Int, c: Int): DoubleArray {
    val out = DoubleArray(r * c)
    for (i in 0 until r) for (j in 0 until c) out[j * r + i] = a[i * c + j]
    return out
  }

  fun det33(m: DoubleArray): Double =
      m[0] * (m[4] * m[8] - m[5] * m[7]) - m[1] * (m[3] * m[8] - m[5] * m[6]) + m[2] * (m[3] * m[7] - m[4] * m[6])

  fun inv33(m: DoubleArray): DoubleArray? {
    val d = det33(m)
    if (abs(d) < 1e-300) return null
    val id = 1.0 / d
    return doubleArrayOf(
        (m[4] * m[8] - m[5] * m[7]) * id, (m[2] * m[7] - m[1] * m[8]) * id, (m[1] * m[5] - m[2] * m[4]) * id,
        (m[5] * m[6] - m[3] * m[8]) * id, (m[0] * m[8] - m[2] * m[6]) * id, (m[2] * m[3] - m[0] * m[5]) * id,
        (m[3] * m[7] - m[4] * m[6]) * id, (m[1] * m[6] - m[0] * m[7]) * id, (m[0] * m[4] - m[1] * m[3]) * id,
    )
  }

  fun mulVec33(m: DoubleArray, x: Double, y: Double, z: Double): DoubleArray =
      doubleArrayOf(m[0] * x + m[1] * y + m[2] * z, m[3] * x + m[4] * y + m[5] * z, m[6] * x + m[7] * y + m[8] * z)

  /** Transforma un punto con una 4x4 afín (sin división). */
  fun xformPt(m: DoubleArray, x: Double, y: Double, z: Double): DoubleArray =
      doubleArrayOf(
          m[0] * x + m[1] * y + m[2] * z + m[3],
          m[4] * x + m[5] * y + m[6] * z + m[7],
          m[8] * x + m[9] * y + m[10] * z + m[11],
      )

  fun xformDir(m: DoubleArray, x: Double, y: Double, z: Double): DoubleArray =
      doubleArrayOf(m[0] * x + m[1] * y + m[2] * z, m[4] * x + m[5] * y + m[6] * z, m[8] * x + m[9] * y + m[10] * z)

  fun quatToMat3(w: Double, x: Double, y: Double, z: Double): DoubleArray {
    val n = sqrt(w * w + x * x + y * y + z * z).let { if (it < 1e-12) 1.0 else it }
    val qw = w / n; val qx = x / n; val qy = y / n; val qz = z / n
    return doubleArrayOf(
        1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw),
        2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw),
        2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy),
    )
  }

  /** Rotación propia (det +1) → cuaternión (w,x,y,z). */
  fun mat3ToQuat(r: DoubleArray): DoubleArray {
    val tr = r[0] + r[4] + r[8]
    val q = DoubleArray(4)
    if (tr > 0) {
      val s = sqrt(tr + 1.0) * 2
      q[0] = 0.25 * s; q[1] = (r[7] - r[5]) / s; q[2] = (r[2] - r[6]) / s; q[3] = (r[3] - r[1]) / s
    } else if (r[0] > r[4] && r[0] > r[8]) {
      val s = sqrt(1.0 + r[0] - r[4] - r[8]) * 2
      q[0] = (r[7] - r[5]) / s; q[1] = 0.25 * s; q[2] = (r[1] + r[3]) / s; q[3] = (r[2] + r[6]) / s
    } else if (r[4] > r[8]) {
      val s = sqrt(1.0 + r[4] - r[0] - r[8]) * 2
      q[0] = (r[2] - r[6]) / s; q[1] = (r[1] + r[3]) / s; q[2] = 0.25 * s; q[3] = (r[5] + r[7]) / s
    } else {
      val s = sqrt(1.0 + r[8] - r[0] - r[4]) * 2
      q[0] = (r[3] - r[1]) / s; q[1] = (r[2] + r[6]) / s; q[2] = (r[5] + r[7]) / s; q[3] = 0.25 * s
    }
    return q
  }

  /** Pose (t, q) → 4x4 que lleva puntos del marco local al marco padre. */
  fun pose4(tx: Double, ty: Double, tz: Double, qw: Double, qx: Double, qy: Double, qz: Double): DoubleArray {
    val r = quatToMat3(qw, qx, qy, qz)
    return doubleArrayOf(r[0], r[1], r[2], tx, r[3], r[4], r[5], ty, r[6], r[7], r[8], tz, 0.0, 0.0, 0.0, 1.0)
  }

  /** Inversa de una 4x4 rígida (rotación + traslación). */
  fun invRigid(m: DoubleArray): DoubleArray {
    val r = doubleArrayOf(m[0], m[4], m[8], m[1], m[5], m[9], m[2], m[6], m[10])
    val t = mulVec33(r, -m[3], -m[7], -m[11])
    return doubleArrayOf(r[0], r[1], r[2], t[0], r[3], r[4], r[5], t[1], r[6], r[7], r[8], t[2], 0.0, 0.0, 0.0, 1.0)
  }

  /** Rodrigues: vector de rotación → 3x3. */
  fun rodrigues(wx: Double, wy: Double, wz: Double): DoubleArray {
    val th = sqrt(wx * wx + wy * wy + wz * wz)
    if (th < 1e-12) return doubleArrayOf(1.0, -wz, wy, wz, 1.0, -wx, -wy, wx, 1.0)
    val kx = wx / th; val ky = wy / th; val kz = wz / th
    val c = cos(th); val s = sin(th); val v = 1 - c
    return doubleArrayOf(
        c + kx * kx * v, kx * ky * v - kz * s, kx * kz * v + ky * s,
        ky * kx * v + kz * s, c + ky * ky * v, ky * kz * v - kx * s,
        kz * kx * v - ky * s, kz * ky * v + kx * s, c + kz * kz * v,
    )
  }

  /** 3x3 rotación propia → vector de Rodrigues. */
  fun rodriguesInv(r: DoubleArray): DoubleArray {
    val q = mat3ToQuat(r)
    var w = q[0]; var x = q[1]; var y = q[2]; var z = q[3]
    if (w < 0) { w = -w; x = -x; y = -y; z = -z }
    val s = sqrt(x * x + y * y + z * z)
    if (s < 1e-12) return doubleArrayOf(0.0, 0.0, 0.0)
    val th = 2 * kotlin.math.atan2(s, w)
    return doubleArrayOf(x / s * th, y / s * th, z / s * th)
  }

  /**
   * Autovalores/autovectores de una matriz simétrica n×n (Jacobi cíclico).
   * Devuelve (valores, vectores en COLUMNAS, row-major n×n).
   */
  fun eigSym(a0: DoubleArray, n: Int): Pair<DoubleArray, DoubleArray> {
    val a = a0.copyOf()
    val v = DoubleArray(n * n).also { for (i in 0 until n) it[i * n + i] = 1.0 }
    for (sweep in 0 until 100) {
      var off = 0.0
      var diag = 0.0
      for (p in 0 until n) {
        diag += a[p * n + p] * a[p * n + p]
        for (q in p + 1 until n) off += a[p * n + q] * a[p * n + q]
      }
      if (off <= 1e-26 * diag || off < 1e-300) break
      for (p in 0 until n) for (q in p + 1 until n) {
        val apq = a[p * n + q]
        if (abs(apq) < 1e-300) continue
        val app = a[p * n + p]; val aqq = a[q * n + q]
        val theta = (aqq - app) / (2 * apq)
        val t = (if (theta >= 0) 1.0 else -1.0) / (abs(theta) + sqrt(theta * theta + 1))
        val c = 1 / sqrt(t * t + 1); val s = t * c
        for (k in 0 until n) {
          val akp = a[k * n + p]; val akq = a[k * n + q]
          a[k * n + p] = c * akp - s * akq
          a[k * n + q] = s * akp + c * akq
        }
        for (k in 0 until n) {
          val apk = a[p * n + k]; val aqk = a[q * n + k]
          a[p * n + k] = c * apk - s * aqk
          a[q * n + k] = s * apk + c * aqk
        }
        for (k in 0 until n) {
          val vkp = v[k * n + p]; val vkq = v[k * n + q]
          v[k * n + p] = c * vkp - s * vkq
          v[k * n + q] = s * vkp + c * vkq
        }
      }
    }
    return Pair(DoubleArray(n) { a[it * n + it] }, v)
  }

  /** Autovector de menor autovalor de una simétrica n×n. */
  fun minEigVec(a: DoubleArray, n: Int): DoubleArray {
    val (vals, vecs) = eigSym(a, n)
    var k = 0
    for (i in 1 until n) if (vals[i] < vals[k]) k = i
    return DoubleArray(n) { vecs[it * n + k] }
  }

  /** Resuelve A x = b (n×n) por eliminación gaussiana con pivoteo parcial. */
  fun solve(a0: DoubleArray, b0: DoubleArray, n: Int): DoubleArray? {
    val a = a0.copyOf(); val b = b0.copyOf()
    for (c in 0 until n) {
      var p = c
      for (r in c + 1 until n) if (abs(a[r * n + c]) > abs(a[p * n + c])) p = r
      if (abs(a[p * n + c]) < 1e-300) return null
      if (p != c) {
        for (k in 0 until n) { val t = a[c * n + k]; a[c * n + k] = a[p * n + k]; a[p * n + k] = t }
        val t = b[c]; b[c] = b[p]; b[p] = t
      }
      for (r in c + 1 until n) {
        val f = a[r * n + c] / a[c * n + c]
        if (f == 0.0) continue
        for (k in c until n) a[r * n + k] -= f * a[c * n + k]
        b[r] -= f * b[c]
      }
    }
    val x = DoubleArray(n)
    for (r in n - 1 downTo 0) {
      var s = b[r]
      for (k in r + 1 until n) s -= a[r * n + k] * x[k]
      x[r] = s / a[r * n + r]
    }
    return x
  }

  /**
   * RQ de una 3x3: M = K R con K triangular superior de diagonal positiva y R de filas
   * ortonormales (det(R) = signo de det(M)).
   */
  fun rq33(m: DoubleArray): Pair<DoubleArray, DoubleArray> {
    val m1 = doubleArrayOf(m[0], m[1], m[2])
    val m2 = doubleArrayOf(m[3], m[4], m[5])
    val m3 = doubleArrayOf(m[6], m[7], m[8])
    fun dot(a: DoubleArray, b: DoubleArray) = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
    fun norm(a: DoubleArray) = sqrt(dot(a, a))
    val k33 = norm(m3)
    val r3 = DoubleArray(3) { m3[it] / k33 }
    val k23 = dot(m2, r3)
    val t2 = DoubleArray(3) { m2[it] - k23 * r3[it] }
    val k22 = norm(t2)
    val r2 = DoubleArray(3) { t2[it] / k22 }
    val k13 = dot(m1, r3)
    val k12 = dot(m1, r2)
    val t1 = DoubleArray(3) { m1[it] - k12 * r2[it] - k13 * r3[it] }
    val k11 = norm(t1)
    val r1 = DoubleArray(3) { t1[it] / k11 }
    val k = doubleArrayOf(k11, k12, k13, 0.0, k22, k23, 0.0, 0.0, k33)
    val r = doubleArrayOf(r1[0], r1[1], r1[2], r2[0], r2[1], r2[2], r3[0], r3[1], r3[2])
    return Pair(k, r)
  }

  fun cross(a: DoubleArray, b: DoubleArray): DoubleArray =
      doubleArrayOf(a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

  fun median(v: DoubleArray): Double {
    if (v.isEmpty()) return Double.NaN
    val s = v.sortedArray()
    val n = s.size
    return if (n % 2 == 1) s[n / 2] else 0.5 * (s[n / 2 - 1] + s[n / 2])
  }

  fun percentile(v: DoubleArray, p: Double): Double {
    if (v.isEmpty()) return Double.NaN
    val s = v.sortedArray()
    val idx = (p / 100.0 * (s.size - 1)).coerceIn(0.0, (s.size - 1).toDouble())
    val lo = idx.toInt(); val hi = minOf(lo + 1, s.size - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)
  }

  /** Column-major float[16] para GLES a partir de row-major double 4x4. */
  fun toGl(m: DoubleArray): FloatArray = FloatArray(16) { i -> m[(i % 4) * 4 + i / 4].toFloat() }
}
