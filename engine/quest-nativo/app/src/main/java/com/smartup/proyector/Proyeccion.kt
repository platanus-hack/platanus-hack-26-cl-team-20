package com.smartup.proyector

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt
import kotlin.random.Random

/**
 * Modelo del proyector: matriz de proyección 3x4 P que lleva un punto 3D del marco de la CABEZA
 * (coordenadas de Spatial SDK, metros) a un píxel de la textura de salida (u, v), v = 0 arriba.
 *
 * Una P general (11 grados de libertad) absorbe cualquier homografía fija entre nuestra textura y
 * el chip del proyector (panel fijo a la cabeza + espejo del ojo izquierdo + reescalado a 1080p),
 * así que la calibración en lazo cerrado sirve igual para la salida A (display) y la B (espejo).
 *
 * Normalización: ||P[2, 0:3]|| = 1 y w > 0 delante del proyector ⇒ w = profundidad en metros.
 */
class ModeloProyector(p0: DoubleArray, val ancho: Int, val alto: Int) {
  val p: DoubleArray = normalizar(p0)

  /** K (triangular sup.), R (filas ortonormales, det ±1), centro C en el marco cabeza. */
  val k: DoubleArray
  val r: DoubleArray
  val centro: DoubleArray

  init {
    val m = doubleArrayOf(p[0], p[1], p[2], p[4], p[5], p[6], p[8], p[9], p[10])
    val (kk, rr) = Mat.rq33(m)
    k = kk; r = rr
    val mi = Mat.inv33(m)
    centro = if (mi != null) Mat.mulVec33(mi, -p[3], -p[7], -p[11]) else doubleArrayOf(0.0, 0.0, 0.0)
  }

  val fx get() = k[0]
  val fy get() = k[4]
  val cx get() = k[2]
  val cy get() = k[5]
  val skew get() = k[1]
  val reflejado get() = Mat.det33(r) < 0

  /** Proyecta (x,y,z) cabeza → (u, v, w). w ≤ 0: detrás del proyector. */
  fun proyectar(x: Double, y: Double, z: Double, out: DoubleArray) {
    val w = p[8] * x + p[9] * y + p[10] * z + p[11]
    out[0] = (p[0] * x + p[1] * y + p[2] * z + p[3]) / w
    out[1] = (p[4] * x + p[5] * y + p[6] * z + p[7]) / w
    out[2] = w
  }

  /** Rayo (dirección, marco cabeza) que pasa por el píxel (u, v). */
  fun rayo(u: Double, v: Double): DoubleArray {
    val m = doubleArrayOf(p[0], p[1], p[2], p[4], p[5], p[6], p[8], p[9], p[10])
    val mi = Mat.inv33(m)!!
    val d = Mat.mulVec33(mi, u, v, 1.0)
    val n = sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
    return doubleArrayOf(d[0] / n, d[1] / n, d[2] / n)
  }

  /**
   * Matriz GL 4x4 (row-major) cabeza → clip para un framebuffer ancho×alto donde la fila v = 0
   * de la textura es la de ARRIBA (NDC y = +1).
   */
  fun matrizGl(cerca: Double = 0.05, lejos: Double = 20.0): DoubleArray {
    val a = (lejos + cerca) / (lejos - cerca)
    val b = -2 * lejos * cerca / (lejos - cerca)
    val out = DoubleArray(16)
    for (j in 0..3) {
      val p1 = p[j]; val p2 = p[4 + j]; val p3 = p[8 + j]
      out[j] = 2.0 / ancho * p1 - p3
      out[4 + j] = p3 - 2.0 / alto * p2
      out[8 + j] = a * p3 + (if (j == 3) b else 0.0)
      out[12 + j] = p3
    }
    return out
  }

  fun resumen(): String =
      "fx=%.1f fy=%.1f cx=%.1f cy=%.1f skew=%.2f C=(%.3f, %.3f, %.3f) m%s".format(
          fx, fy, cx, cy, skew, centro[0], centro[1], centro[2], if (reflejado) " (marco zurdo)" else "")

  companion object {
    fun normalizar(p0: DoubleArray): DoubleArray {
      val n = sqrt(p0[8] * p0[8] + p0[9] * p0[9] + p0[10] * p0[10])
      return DoubleArray(12) { p0[it] / n }
    }
  }
}

/** Pares 3D (marco cabeza) ↔ 2D (textura del proyector). */
class Pares(val x: DoubleArray, val uv: DoubleArray) {
  val n get() = uv.size / 2

  fun sub(idx: IntArray): Pares {
    val xx = DoubleArray(idx.size * 3); val uu = DoubleArray(idx.size * 2)
    for ((k, i) in idx.withIndex()) {
      xx[k * 3] = x[i * 3]; xx[k * 3 + 1] = x[i * 3 + 1]; xx[k * 3 + 2] = x[i * 3 + 2]
      uu[k * 2] = uv[i * 2]; uu[k * 2 + 1] = uv[i * 2 + 1]
    }
    return Pares(xx, uu)
  }

  /**
   * Planitud: raíz(λmin / λmed) de la covarianza de los puntos 3D. ~0 = coplanares (DLT
   * degenerado), > 0.15 holgado. También devuelve el espesor (m) = 2·raíz(λmin).
   */
  fun planitud(): Pair<Double, Double> {
    if (n < 4) return Pair(0.0, 0.0)
    var mx = 0.0; var my = 0.0; var mz = 0.0
    for (i in 0 until n) { mx += x[i * 3]; my += x[i * 3 + 1]; mz += x[i * 3 + 2] }
    mx /= n; my /= n; mz /= n
    val c = DoubleArray(9)
    for (i in 0 until n) {
      val d = doubleArrayOf(x[i * 3] - mx, x[i * 3 + 1] - my, x[i * 3 + 2] - mz)
      for (r in 0..2) for (k in 0..2) c[r * 3 + k] += d[r] * d[k] / n
    }
    val vals = Mat.eigSym(c, 3).first.sorted()
    return Pair(sqrt(max(vals[0], 0.0) / max(vals[1], 1e-12)), 2 * sqrt(max(vals[0], 0.0)))
  }
}

class ResultadoAjuste(
    val modelo: ModeloProyector,
    val inliers: BooleanArray,
    val residuos: DoubleArray,     // px, todos los pares
    val tipo: String,              // "dlt" | "restringido_plano"
    val planitud: Double,
) {
  private fun inl() = residuos.filterIndexed { i, _ -> inliers[i] }.toDoubleArray()
  val nInliers get() = inliers.count { it }
  val mediana get() = Mat.median(inl())
  val p90 get() = Mat.percentile(inl(), 90.0)
  val rms get() = inl().let { r -> sqrt(r.sumOf { it * it } / max(1, r.size)) }

  fun resumen() =
      "%s: %d/%d inliers, residuo mediana %.2f px p90 %.2f rms %.2f, planitud %.3f; %s".format(
          tipo, nInliers, residuos.size, mediana, p90, rms, planitud, modelo.resumen())
}

/** DLT normalizado + RANSAC + Levenberg-Marquardt; respaldo restringido si los puntos son coplanares. */
object AjusteProyector {
  /** DLT de Hartley sobre los pares indicados (idx = null: todos). */
  fun dlt(par: Pares, idx: IntArray? = null): DoubleArray? {
    val ids = idx ?: IntArray(par.n) { it }
    if (ids.size < 6) return null
    // normalización 3D y 2D
    var mx = 0.0; var my = 0.0; var mz = 0.0; var mu = 0.0; var mv = 0.0
    for (i in ids) {
      mx += par.x[i * 3]; my += par.x[i * 3 + 1]; mz += par.x[i * 3 + 2]
      mu += par.uv[i * 2]; mv += par.uv[i * 2 + 1]
    }
    val n = ids.size.toDouble()
    mx /= n; my /= n; mz /= n; mu /= n; mv /= n
    var d3 = 0.0; var d2 = 0.0
    for (i in ids) {
      val ax = par.x[i * 3] - mx; val ay = par.x[i * 3 + 1] - my; val az = par.x[i * 3 + 2] - mz
      d3 += sqrt(ax * ax + ay * ay + az * az)
      val bu = par.uv[i * 2] - mu; val bv = par.uv[i * 2 + 1] - mv
      d2 += sqrt(bu * bu + bv * bv)
    }
    val s3 = sqrt(3.0) / max(d3 / n, 1e-12)
    val s2 = sqrt(2.0) / max(d2 / n, 1e-12)
    val ata = DoubleArray(144)
    val row = DoubleArray(12)
    fun acc() { for (r in 0 until 12) { val a = row[r]; if (a == 0.0) continue; for (c in 0 until 12) ata[r * 12 + c] += a * row[c] } }
    for (i in ids) {
      val X = (par.x[i * 3] - mx) * s3; val Y = (par.x[i * 3 + 1] - my) * s3; val Z = (par.x[i * 3 + 2] - mz) * s3
      val u = (par.uv[i * 2] - mu) * s2; val v = (par.uv[i * 2 + 1] - mv) * s2
      // fila u: [X Y Z 1 0 0 0 0 -uX -uY -uZ -u]
      row.fill(0.0)
      row[0] = X; row[1] = Y; row[2] = Z; row[3] = 1.0
      row[8] = -u * X; row[9] = -u * Y; row[10] = -u * Z; row[11] = -u
      acc()
      row.fill(0.0)
      row[4] = X; row[5] = Y; row[6] = Z; row[7] = 1.0
      row[8] = -v * X; row[9] = -v * Y; row[10] = -v * Z; row[11] = -v
      acc()
    }
    val pn = Mat.minEigVec(ata, 12)
    // desnormalizar: P = T2^-1 · Pn · T3
    val t3 = doubleArrayOf(s3, 0.0, 0.0, -s3 * mx, 0.0, s3, 0.0, -s3 * my, 0.0, 0.0, s3, -s3 * mz, 0.0, 0.0, 0.0, 1.0)
    val t2i = doubleArrayOf(1 / s2, 0.0, mu, 0.0, 1 / s2, mv, 0.0, 0.0, 1.0)
    val p = Mat.mul(Mat.mul(t2i, 3, 3, pn, 4), 3, 4, t3, 4)
    // signo: la mayoría de los puntos delante (w > 0)
    var pos = 0
    for (i in ids) if (p[8] * par.x[i * 3] + p[9] * par.x[i * 3 + 1] + p[10] * par.x[i * 3 + 2] + p[11] > 0) pos++
    if (pos * 2 < ids.size) for (k in 0 until 12) p[k] = -p[k]
    return p
  }

  fun residuos(p: DoubleArray, par: Pares): DoubleArray {
    val out = DoubleArray(par.n)
    for (i in 0 until par.n) {
      val X = par.x[i * 3]; val Y = par.x[i * 3 + 1]; val Z = par.x[i * 3 + 2]
      val w = p[8] * X + p[9] * Y + p[10] * Z + p[11]
      if (w <= 1e-9) { out[i] = 1e9; continue }
      val u = (p[0] * X + p[1] * Y + p[2] * Z + p[3]) / w
      val v = (p[4] * X + p[5] * Y + p[6] * Z + p[7]) / w
      val du = u - par.uv[i * 2]; val dv = v - par.uv[i * 2 + 1]
      out[i] = sqrt(du * du + dv * dv)
    }
    return out
  }

  // ---------------------------------------------------------------- parametrización física
  // θ = [fx, fy, cx, cy, skew, wx, wy, wz, Cx, Cy, Cz]; P = K · R(w)·F · [I | -C]
  // F = diag(1,1,-1) si el marco es zurdo (Spatial SDK) para que R(w) sea una rotación propia.

  private fun pDeTheta(th: DoubleArray, reflejo: Boolean): DoubleArray {
    val k = doubleArrayOf(th[0], th[4], th[2], 0.0, th[1], th[3], 0.0, 0.0, 1.0)
    var r = Mat.rodrigues(th[5], th[6], th[7])
    if (reflejo) r = Mat.mul33(r, doubleArrayOf(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, -1.0))
    val m = Mat.mul33(k, r)
    val t = Mat.mulVec33(m, -th[8], -th[9], -th[10])
    return doubleArrayOf(m[0], m[1], m[2], t[0], m[3], m[4], m[5], t[1], m[6], m[7], m[8], t[2])
  }

  private fun thetaDeP(p: DoubleArray, ancho: Int, alto: Int): Pair<DoubleArray, Boolean> {
    val mod = ModeloProyector(p, ancho, alto)
    val reflejo = mod.reflejado
    var r = mod.r
    if (reflejo) r = Mat.mul33(r, doubleArrayOf(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, -1.0))
    val w = Mat.rodriguesInv(r)
    val k = mod.k
    return Pair(doubleArrayOf(k[0] / k[8], k[4] / k[8], k[2] / k[8], k[5] / k[8], k[1] / k[8], w[0], w[1], w[2],
        mod.centro[0], mod.centro[1], mod.centro[2]), reflejo)
  }

  /**
   * Levenberg-Marquardt sobre los inliers. `libres` marca qué parámetros se optimizan (para el
   * modelo restringido: skew = 0, fy = fx, cx fijo).
   */
  fun lm(
      th0: DoubleArray, reflejo: Boolean, par: Pares, usar: BooleanArray,
      libres: BooleanArray = BooleanArray(11) { true }, fyIgualFx: Boolean = false, iters: Int = 40,
  ): DoubleArray {
    var th = th0.copyOf()
    if (fyIgualFx) th[1] = th[0]
    val ids = (0 until par.n).filter { usar[it] }
    val np = 11
    fun costo(t: DoubleArray): Double {
      val p = pDeTheta(t, reflejo)
      var s = 0.0
      for (i in ids) {
        val X = par.x[i * 3]; val Y = par.x[i * 3 + 1]; val Z = par.x[i * 3 + 2]
        val w = p[8] * X + p[9] * Y + p[10] * Z + p[11]
        if (w <= 1e-9) { s += 1e6; continue }
        val du = (p[0] * X + p[1] * Y + p[2] * Z + p[3]) / w - par.uv[i * 2]
        val dv = (p[4] * X + p[5] * Y + p[6] * Z + p[7]) / w - par.uv[i * 2 + 1]
        s += du * du + dv * dv
      }
      return s
    }
    fun resid(t: DoubleArray, out: DoubleArray) {
      val p = pDeTheta(t, reflejo)
      for ((k, i) in ids.withIndex()) {
        val X = par.x[i * 3]; val Y = par.x[i * 3 + 1]; val Z = par.x[i * 3 + 2]
        val w = p[8] * X + p[9] * Y + p[10] * Z + p[11]
        out[k * 2] = (p[0] * X + p[1] * Y + p[2] * Z + p[3]) / w - par.uv[i * 2]
        out[k * 2 + 1] = (p[4] * X + p[5] * Y + p[6] * Z + p[7]) / w - par.uv[i * 2 + 1]
      }
    }
    val m = ids.size * 2
    val r0 = DoubleArray(m); val rp = DoubleArray(m)
    var lambda = 1e-3
    var c0 = costo(th)
    val idxLibres = (0 until np).filter { libres[it] && !(fyIgualFx && it == 1) }
    val nl = idxLibres.size
    for (it in 0 until iters) {
      resid(th, r0)
      // jacobiano numérico (diferencias hacia adelante, paso relativo)
      val jac = Array(nl) { DoubleArray(m) }
      for ((c, pi) in idxLibres.withIndex()) {
        val h = 1e-6 * max(1.0, abs(th[pi]))
        val t2 = th.copyOf(); t2[pi] += h
        if (fyIgualFx && pi == 0) t2[1] = t2[0]
        resid(t2, rp)
        for (q in 0 until m) jac[c][q] = (rp[q] - r0[q]) / h
      }
      val jtj = DoubleArray(nl * nl); val jtr = DoubleArray(nl)
      for (a in 0 until nl) {
        for (b in a until nl) {
          var s = 0.0
          for (q in 0 until m) s += jac[a][q] * jac[b][q]
          jtj[a * nl + b] = s; jtj[b * nl + a] = s
        }
        var s = 0.0
        for (q in 0 until m) s += jac[a][q] * r0[q]
        jtr[a] = s
      }
      var mejoro = false
      for (intento in 0 until 8) {
        val aug = jtj.copyOf()
        for (a in 0 until nl) aug[a * nl + a] += lambda * max(jtj[a * nl + a], 1e-9)
        val delta = Mat.solve(aug, DoubleArray(nl) { -jtr[it] }, nl) ?: break
        val t2 = th.copyOf()
        for ((c, pi) in idxLibres.withIndex()) t2[pi] += delta[c]
        if (fyIgualFx) t2[1] = t2[0]
        val c2 = costo(t2)
        if (c2 < c0) {
          th = t2; lambda = max(lambda / 10, 1e-12)
          val rel = (c0 - c2) / max(c0, 1e-12)
          c0 = c2; mejoro = true
          if (rel < 1e-10) return th
          break
        } else lambda *= 10
      }
      if (!mejoro) break
    }
    return th
  }

  /**
   * Ajuste completo: RANSAC-DLT → LM sobre inliers → re-selección de inliers → LM.
   * Si los puntos son casi coplanares, cae al modelo restringido (skew 0, fx = fy, cx fijo).
   */
  fun ajustar(
      par: Pares, ancho: Int, alto: Int, umbralPx: Double = 6.0, iteraciones: Int = 400,
      semilla: Int = 7, planitudMin: Double = 0.04, cxFijo: Double = ancho / 2.0,
  ): ResultadoAjuste? {
    if (par.n < 12) return null
    val (plan, _) = par.planitud()
    if (plan < planitudMin) return ajustarPlano(par, ancho, alto, umbralPx, semilla, plan, cxFijo)
    val rnd = Random(semilla)
    var mejorP: DoubleArray? = null
    var mejorN = -1
    val muestra = IntArray(8)
    for (it in 0 until iteraciones) {
      for (k in 0 until 8) muestra[k] = rnd.nextInt(par.n)
      if (muestra.distinct().size < 8) continue
      val p = dlt(par, muestra) ?: continue
      val res = residuos(p, par)
      val nIn = res.count { it < umbralPx }
      if (nIn > mejorN) { mejorN = nIn; mejorP = p }
    }
    var p = mejorP ?: return null
    var inl = residuos(p, par).map { it < umbralPx }.toBooleanArray()
    p = dlt(par, (0 until par.n).filter { inl[it] }.toIntArray()) ?: p
    val (th0, refl) = thetaDeP(p, ancho, alto)
    var th = lm(th0, refl, par, residuos(p, par).map { it < umbralPx }.toBooleanArray())
    // umbral final adaptativo: 3·MAD de los residuos de inliers, acotado
    var res = residuos(pDeTheta(th, refl), par)
    val med = Mat.median(res.filter { it < umbralPx }.toDoubleArray())
    val umbral2 = min(umbralPx, max(1.5, 3.0 * 1.4826 * med))
    inl = res.map { it < umbral2 }.toBooleanArray()
    th = lm(th, refl, par, inl)
    res = residuos(pDeTheta(th, refl), par)
    inl = res.map { it < umbral2 }.toBooleanArray()
    return ResultadoAjuste(ModeloProyector(pDeTheta(th, refl), ancho, alto), inl, res, "dlt", plan)
  }

  /**
   * Puntos coplanares (una sola pared): DLT degenerado. Se ajusta K = [f 0 cx; 0 f cy; 0 0 1] con
   * cx fijo (proyector centrado en horizontal) y se prueban varias f y cy iniciales con la pose
   * sacada de la homografía (Zhang). Queda marcado como "restringido_plano".
   */
  fun ajustarPlano(par: Pares, ancho: Int, alto: Int, umbralPx: Double, semilla: Int, plan: Double, cxFijo: Double): ResultadoAjuste? {
    // marco del plano: centroide + base de la covarianza
    val n = par.n
    var mx = 0.0; var my = 0.0; var mz = 0.0
    for (i in 0 until n) { mx += par.x[i * 3]; my += par.x[i * 3 + 1]; mz += par.x[i * 3 + 2] }
    mx /= n; my /= n; mz /= n
    val c = DoubleArray(9)
    for (i in 0 until n) {
      val d = doubleArrayOf(par.x[i * 3] - mx, par.x[i * 3 + 1] - my, par.x[i * 3 + 2] - mz)
      for (r in 0..2) for (k in 0..2) c[r * 3 + k] += d[r] * d[k]
    }
    val (vals, vecs) = Mat.eigSym(c, 3)
    val orden = (0..2).sortedByDescending { vals[it] }
    val e1 = DoubleArray(3) { vecs[it * 3 + orden[0]] }
    val e2 = DoubleArray(3) { vecs[it * 3 + orden[1]] }
    // homografía plano(a,b) → uv por DLT 2D
    val ab = DoubleArray(n * 2)
    for (i in 0 until n) {
      val d = doubleArrayOf(par.x[i * 3] - mx, par.x[i * 3 + 1] - my, par.x[i * 3 + 2] - mz)
      ab[i * 2] = d[0] * e1[0] + d[1] * e1[1] + d[2] * e1[2]
      ab[i * 2 + 1] = d[0] * e2[0] + d[1] * e2[1] + d[2] * e2[2]
    }
    val h = homografia(ab, par.uv, n) ?: return null
    var mejor: Pair<DoubleArray, Boolean>? = null
    var mejorCosto = Double.MAX_VALUE
    val libres = BooleanArray(11) { true }.also { it[1] = false; it[2] = false; it[4] = false }
    for (f in doubleArrayOf(1200.0, 1800.0, 2600.0, 3600.0, 5000.0)) for (cy in doubleArrayOf(alto * 0.5, alto * 0.9, alto * 1.2)) {
      val k = doubleArrayOf(f, 0.0, cxFijo, 0.0, f, cy, 0.0, 0.0, 1.0)
      val ki = Mat.inv33(k)!!
      val m = Mat.mul33(ki, h)
      // columnas: r1, r2, t (escala λ)
      val c1 = doubleArrayOf(m[0], m[3], m[6]); val c2 = doubleArrayOf(m[1], m[4], m[7]); val c3 = doubleArrayOf(m[2], m[5], m[8])
      val l = 2.0 / (sqrt(c1[0] * c1[0] + c1[1] * c1[1] + c1[2] * c1[2]) + sqrt(c2[0] * c2[0] + c2[1] * c2[1] + c2[2] * c2[2]))
      for (sg in doubleArrayOf(1.0, -1.0)) {
        val r1 = DoubleArray(3) { sg * l * c1[it] }; val r2 = DoubleArray(3) { sg * l * c2[it] }; val t = DoubleArray(3) { sg * l * c3[it] }
        if (t[2] <= 0) continue
        // ortonormalizar
        val r1n = norm(r1); val r2o = DoubleArray(3) { r2[it] - (r1n[0] * r2[0] + r1n[1] * r2[1] + r1n[2] * r2[2]) * r1n[it] }
        val r2n = norm(r2o); val r3 = Mat.cross(r1n, r2n)
        // R_plano: columnas r1 r2 r3 lleva (a,b,c) plano → cámara; R_cabeza = R_plano · Bᵀ, B = [e1 e2 e3]
        val e3 = Mat.cross(e1, e2)
        val rp = doubleArrayOf(r1n[0], r2n[0], r3[0], r1n[1], r2n[1], r3[1], r1n[2], r2n[2], r3[2])
        val bt = doubleArrayOf(e1[0], e1[1], e1[2], e2[0], e2[1], e2[2], e3[0], e3[1], e3[2])
        var rc = Mat.mul33(rp, bt)
        // cámara: Xc = Rc (X - M) + t ⇒ C = M - Rcᵀ t
        val rct = Mat.transpose(rc, 3, 3)
        val ct = Mat.mulVec33(rct, t[0], t[1], t[2])
        var cc = doubleArrayOf(mx - ct[0], my - ct[1], mz - ct[2])
        // el proyector va en el rig: mismo lado del plano que la cabeza (origen). Si no, se refleja
        // por el plano (mismas proyecciones de los puntos del plano, marco de mano contraria).
        val ladoC = (cc[0] - mx) * e3[0] + (cc[1] - my) * e3[1] + (cc[2] - mz) * e3[2]
        val ladoCabeza = -mx * e3[0] - my * e3[1] - mz * e3[2]
        if (ladoC * ladoCabeza < 0) {
          val hh = DoubleArray(9) { i -> (if (i % 4 == 0) 1.0 else 0.0) - 2 * e3[i / 3] * e3[i % 3] }
          rc = Mat.mul33(rc, hh)
          cc = doubleArrayOf(cc[0] - 2 * ladoC * e3[0], cc[1] - 2 * ladoC * e3[1], cc[2] - 2 * ladoC * e3[2])
        }
        val reflejo = Mat.det33(rc) < 0
        if (reflejo) rc = Mat.mul33(rc, doubleArrayOf(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, -1.0))
        val w = Mat.rodriguesInv(rc)
        val th0 = doubleArrayOf(f, f, cxFijo, cy, 0.0, w[0], w[1], w[2], cc[0], cc[1], cc[2])
        val usar = BooleanArray(n) { true }
        var th = lm(th0, reflejo, par, usar, libres, fyIgualFx = true, iters = 25)
        val res = residuos(pDeTheta(th, reflejo), par)
        val inl = res.map { it < umbralPx }.toBooleanArray()
        th = lm(th, reflejo, par, inl, libres, fyIgualFx = true, iters = 25)
        val res2 = residuos(pDeTheta(th, reflejo), par)
        val costo = res2.sumOf { min(it, umbralPx).let { e -> e * e } }
        if (costo < mejorCosto) { mejorCosto = costo; mejor = Pair(th, reflejo) }
      }
    }
    val (th, refl) = mejor ?: return null
    val p = pDeTheta(th, refl)
    val res = residuos(p, par)
    return ResultadoAjuste(ModeloProyector(p, ancho, alto), res.map { it < umbralPx }.toBooleanArray(), res, "restringido_plano", plan)
  }

  private fun norm(a: DoubleArray): DoubleArray {
    val n = sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
    return DoubleArray(3) { a[it] / n }
  }

  /** Homografía (a,b) → (u,v) por DLT 2D normalizado. */
  fun homografia(ab: DoubleArray, uv: DoubleArray, n: Int): DoubleArray? {
    if (n < 4) return null
    fun norma(p: DoubleArray): DoubleArray {
      var mx = 0.0; var my = 0.0
      for (i in 0 until n) { mx += p[i * 2]; my += p[i * 2 + 1] }
      mx /= n; my /= n
      var d = 0.0
      for (i in 0 until n) d += sqrt((p[i * 2] - mx) * (p[i * 2] - mx) + (p[i * 2 + 1] - my) * (p[i * 2 + 1] - my))
      val s = sqrt(2.0) / max(d / n, 1e-12)
      return doubleArrayOf(s, 0.0, -s * mx, 0.0, s, -s * my, 0.0, 0.0, 1.0)
    }
    val ta = norma(ab); val tu = norma(uv)
    val ata = DoubleArray(81); val row = DoubleArray(9)
    fun acc() { for (r in 0 until 9) for (c in 0 until 9) ata[r * 9 + c] += row[r] * row[c] }
    for (i in 0 until n) {
      val a = ta[0] * ab[i * 2] + ta[2]; val b = ta[4] * ab[i * 2 + 1] + ta[5]
      val u = tu[0] * uv[i * 2] + tu[2]; val v = tu[4] * uv[i * 2 + 1] + tu[5]
      row.fill(0.0); row[0] = a; row[1] = b; row[2] = 1.0; row[6] = -u * a; row[7] = -u * b; row[8] = -u; acc()
      row.fill(0.0); row[3] = a; row[4] = b; row[5] = 1.0; row[6] = -v * a; row[7] = -v * b; row[8] = -v; acc()
    }
    val hn = Mat.minEigVec(ata, 9)
    val tui = Mat.inv33(tu) ?: return null
    return Mat.mul33(Mat.mul33(tui, hn), ta)
  }
}
