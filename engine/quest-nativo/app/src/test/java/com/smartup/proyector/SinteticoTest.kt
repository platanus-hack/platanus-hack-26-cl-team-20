package com.smartup.proyector

import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.random.Random
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pruebas sin casco ni proyector: gray code (patrones → cámara sintética → decodificación) y
 * ajuste del proyector (DLT + RANSAC + LM, multi-vista y caso plano), más la rejilla residual.
 *
 * Deja los mismos datos en app/build/sintetico/ para que `tools/verificar_sintetico.py` los
 * decodifique/ajuste con slscan + OpenCV y compare.
 */
class SinteticoTest {
  private val dirSalida = File("build/sintetico").apply { mkdirs() }

  // ------------------------------------------------------------------ utilidades
  private fun guardarPgm(f: File, w: Int, h: Int, px: ByteArray) {
    f.outputStream().use { o ->
      o.write("P5\n$w $h\n255\n".toByteArray())
      o.write(px, 0, w * h)
    }
  }

  /** Proyector "real" sintético en el marco cabeza de Spatial SDK (x der, y arriba, z adelante). */
  private fun pVerdadera(f: Double, cx: Double, cy: Double, c: DoubleArray, pitchDeg: Double, yawDeg: Double): DoubleArray {
    // cabeza (zurdo, y arriba) → cámara OpenCV (y abajo): diag(1,-1,1), después pitch/yaw
    val p = Math.toRadians(pitchDeg); val yw = Math.toRadians(yawDeg)
    val rx = doubleArrayOf(1.0, 0.0, 0.0, 0.0, cos(p), -sin(p), 0.0, sin(p), cos(p))
    val ry = doubleArrayOf(cos(yw), 0.0, sin(yw), 0.0, 1.0, 0.0, -sin(yw), 0.0, cos(yw))
    val flip = doubleArrayOf(1.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, 1.0)
    val r = Mat.mul33(Mat.mul33(rx, flip), ry)
    val k = doubleArrayOf(f, 0.0, cx, 0.0, f, cy, 0.0, 0.0, 1.0)
    val m = Mat.mul33(k, r)
    val t = Mat.mulVec33(m, -c[0], -c[1], -c[2])
    return doubleArrayOf(m[0], m[1], m[2], t[0], m[3], m[4], m[5], t[1], m[6], m[7], m[8], t[2])
  }

  private fun proyectar(p: DoubleArray, x: Double, y: Double, z: Double): DoubleArray? {
    val w = p[8] * x + p[9] * y + p[10] * z + p[11]
    if (w <= 0) return null
    return doubleArrayOf((p[0] * x + p[1] * y + p[2] * z + p[3]) / w, (p[4] * x + p[5] * y + p[6] * z + p[7]) / w)
  }

  // ------------------------------------------------------------------ gray code
  @Test
  fun grayCodeSintetico() {
    val gc = GrayCode(1920, 1080, bitsOmitidos = 1)
    val cw = 640; val ch = 480
    // homografía cámara → proyector (la cámara ve el proyector algo inclinado, ~3 px proy por px cam)
    val h = doubleArrayOf(2.9, 0.25, 120.0, -0.12, 2.6, 60.0, 0.00006, 0.00011, 1.0)
    fun cam2proy(x: Double, y: Double): DoubleArray {
      val w = h[6] * x + h[7] * y + h[8]
      return doubleArrayOf((h[0] * x + h[1] * y + h[2]) / w, (h[3] * x + h[4] * y + h[5]) / w)
    }
    val rnd = Random(3)
    val ambiente = 30.0; val ganancia = 150.0
    // albedo con textura (no uniforme) para que se parezca a una escena
    val albedo = DoubleArray(cw * ch) { i -> 0.55 + 0.4 * sin(i % cw * 0.05) * cos(i / cw * 0.07) }
    val gdir = File(dirSalida, "gray").apply { deleteRecursively(); mkdirs() }
    val dec = gc.Decodificador(cw, ch, umbral = 6)
    var pendiente: ByteArray? = null
    val ss = 3 // sobremuestreo por píxel de cámara (integra el área del píxel ≈ desenfoque)
    for (pat in gc.secuencia) {
      val img = ByteArray(cw * ch)
      for (y in 0 until ch) for (x in 0 until cw) {
        var acc = 0.0
        for (sy in 0 until ss) for (sx in 0 until ss) {
          val q = cam2proy(x - 0.5 + (sx + 0.5) / ss, y - 0.5 + (sy + 0.5) / ss)
          val ui = kotlin.math.floor(q[0]).toInt(); val vi = kotlin.math.floor(q[1]).toInt()
          if (ui in 0 until 1920 && vi in 0 until 1080) acc += gc.valor(pat, ui, vi) / 255.0
        }
        val i = y * cw + x
        val vval = ambiente + ganancia * albedo[i] * acc / (ss * ss) + rnd.nextDouble(-1.0, 1.0) * 3.0
        img[i] = vval.roundToInt().coerceIn(0, 255).toByte()
      }
      guardarPgm(File(gdir, "img_[%02d-%02d-%02d_%02d].pgm".format(pat.eje, pat.bit, if (pat.inverso) 1 else 0, pat.indice)), cw, ch, img)
      if (!pat.inverso) pendiente = img else { dec.agregarPar(pat.eje, pat.bit, pendiente!!, img); pendiente = null }
    }
    val mapa = dec.resultado(1, 1)    // fijo, para comparar 1:1 con slscan (skip_bits=1)
    println("gray: omitidos auto x=${dec.omitidosAuto(0)} y=${dec.omitidosAuto(1)}; fracción válida por bit x=${dec.fraccionValidos(0).mapValues { "%.2f".format(it.value) }}")
    // error contra la verdad: bloque (2 px) y muestras con ajuste afín local
    val errs = ArrayList<Double>()
    val bloques = ByteBuffer.allocate(cw * ch * 8).order(ByteOrder.LITTLE_ENDIAN)
    var malos = 0
    for (y in 0 until ch) for (x in 0 until cw) {
      val i = y * cw + x
      if (mapa.u[i].isNaN()) { bloques.putInt(-1); bloques.putInt(-1); continue }
      bloques.putInt(((mapa.u[i] - 1f) / 2f).toInt()); bloques.putInt(((mapa.v[i] - 1f) / 2f).toInt())
      val gt = cam2proy(x.toDouble(), y.toDouble())
      val e = sqrt((mapa.u[i] - gt[0]) * (mapa.u[i] - gt[0]) + (mapa.v[i] - gt[1]) * (mapa.v[i] - gt[1]))
      if (e > 6) malos++ else errs.add(e)
    }
    File(gdir, "kotlin_bloques_i32le.bin").writeBytes(bloques.array())
    val muestras = mapa.muestras(pasoCam = 6, radio = 3)
    val errM = muestras.map {
      val gt = cam2proy(it.cu.toDouble(), it.cv.toDouble())
      sqrt((it.u - gt[0]) * (it.u - gt[0]) + (it.v - gt[1]) * (it.v - gt[1]))
    }.toDoubleArray()
    val medB = Mat.median(errs.toDoubleArray()); val medM = Mat.median(errM); val p90M = Mat.percentile(errM, 90.0)
    val txt = "gray: validos=${mapa.validos}/${cw * ch} malos(>6px)=$malos err_bloque_mediana=%.3f px | muestras=${muestras.size} err_mediana=%.3f p90=%.3f px".format(medB, medM, p90M)
    println(txt)
    File(gdir, "resultado_kotlin.txt").writeText(txt + "\n")
    File(gdir, "verdad_homografia.txt").writeText(h.joinToString(" ") + "\n")
    assertTrue("pocos píxeles válidos", mapa.validos > cw * ch * 0.5)
    assertTrue("error de bloque alto: $medB", medB < 1.2)
    assertTrue("error de muestras alto: $medM", medM < 0.5)
    assertTrue("demasiados píxeles mal decodificados: $malos", malos < cw * ch * 0.01)
  }

  // ------------------------------------------------------------------ PnP / DLT
  private class Escena(val pares: Pares, val pVerdad: DoubleArray, val cVerdad: DoubleArray)

  /** Pared + mesa + caja, vista por un proyector montado en la cabeza (puede haber varias vistas). */
  private fun escenaSala(
      rnd: Random, vistas: List<DoubleArray>, soloPared: Boolean, ruidoPx: Double, fraccOutliers: Double,
  ): Escena {
    val cV = doubleArrayOf(0.06, -0.09, 0.05)
    val pV = pVerdadera(2300.0, 960.0, 700.0, cV, pitchDeg = 15.0, yawDeg = -4.0)
    val xs = ArrayList<Double>(); val uvs = ArrayList<Double>()
    // puntos del mundo
    val mundo = ArrayList<DoubleArray>()
    for (i in 0 until 60) for (j in 0 until 40) {
      // pared oblicua (25°) a ~2.4 m
      val a = -1.4 + 2.8 * i / 59.0; val b = -1.0 + 1.9 * j / 39.0
      mundo.add(doubleArrayOf(a * cos(0.44), b, 2.4 + a * sin(0.44)))
    }
    if (!soloPared) {
      for (i in 0 until 50) for (j in 0 until 30) mundo.add(doubleArrayOf(-0.9 + 1.8 * i / 49.0, -0.5, 1.1 + 1.0 * j / 29.0))
      for (i in 0 until 20) for (j in 0 until 20) {
        mundo.add(doubleArrayOf(-0.2 + 0.3 * i / 19.0, -0.5 + 0.25 * j / 19.0, 1.5))       // cara frontal de la caja
        mundo.add(doubleArrayOf(-0.2 + 0.3 * i / 19.0, -0.25, 1.5 + 0.3 * j / 19.0))       // tapa
      }
    }
    for (vista in vistas) {
      // vista = [yawDeg, pitchDeg, tx, ty, tz] de la cabeza en el mundo
      val yw = Math.toRadians(vista[0]); val pt = Math.toRadians(vista[1])
      val ry = doubleArrayOf(cos(yw), 0.0, sin(yw), 0.0, 1.0, 0.0, -sin(yw), 0.0, cos(yw))
      val rx = doubleArrayOf(1.0, 0.0, 0.0, 0.0, cos(pt), -sin(pt), 0.0, sin(pt), cos(pt))
      val rWH = Mat.mul33(ry, rx)
      val tWH = doubleArrayOf(vista[2], vista[3], vista[4])
      val rHW = Mat.transpose(rWH, 3, 3)
      for (pw in mundo) {
        val ph = Mat.mulVec33(rHW, pw[0] - tWH[0], pw[1] - tWH[1], pw[2] - tWH[2])
        val uv = proyectar(pV, ph[0], ph[1], ph[2]) ?: continue
        if (uv[0] < 0 || uv[0] >= 1920 || uv[1] < 0 || uv[1] >= 1080) continue
        xs.add(ph[0]); xs.add(ph[1]); xs.add(ph[2])
        if (rnd.nextDouble() < fraccOutliers) {
          uvs.add(rnd.nextDouble() * 1920); uvs.add(rnd.nextDouble() * 1080)
        } else {
          uvs.add(uv[0] + rnd.nextGaussian() * ruidoPx); uvs.add(uv[1] + rnd.nextGaussian() * ruidoPx)
        }
      }
    }
    return Escena(Pares(xs.toDoubleArray(), uvs.toDoubleArray()), pV, cV)
  }

  private fun Random.nextGaussian(): Double {
    var u = 0.0; var v = 0.0
    while (u == 0.0) u = nextDouble()
    while (v == 0.0) v = nextDouble()
    return sqrt(-2.0 * kotlin.math.ln(u)) * cos(2 * Math.PI * v)
  }

  private fun errorCentro(m: ModeloProyector, c: DoubleArray) =
      sqrt((m.centro[0] - c[0]).let { it * it } + (m.centro[1] - c[1]).let { it * it } + (m.centro[2] - c[2]).let { it * it })

  /** Error de reproyección del modelo ajustado contra la P verdadera sobre puntos nuevos (sin ruido). */
  private fun errorGeneralizacion(m: ModeloProyector, pV: DoubleArray): Double {
    val out = DoubleArray(3); val errs = ArrayList<Double>()
    for (i in 0 until 30) for (j in 0 until 20) for (z in doubleArrayOf(0.8, 1.6, 3.5)) {
      val ray = doubleArrayOf((i - 15) * 0.05 * z, (j - 12) * 0.05 * z, z)
      val gt = proyectar(pV, ray[0], ray[1], ray[2]) ?: continue
      if (gt[0] < 0 || gt[0] > 1920 || gt[1] < 0 || gt[1] > 1080) continue
      m.proyectar(ray[0], ray[1], ray[2], out)
      errs.add(sqrt((out[0] - gt[0]) * (out[0] - gt[0]) + (out[1] - gt[1]) * (out[1] - gt[1])))
    }
    return Mat.median(errs.toDoubleArray())
  }

  private fun guardarPares(nombre: String, e: Escena, r: ResultadoAjuste) {
    val d = File(dirSalida, "pnp").apply { mkdirs() }
    File(d, "$nombre.csv").printWriter().use { o ->
      o.println("x,y,z,u,v")
      for (i in 0 until e.pares.n) o.println("${e.pares.x[i * 3]},${e.pares.x[i * 3 + 1]},${e.pares.x[i * 3 + 2]},${e.pares.uv[i * 2]},${e.pares.uv[i * 2 + 1]}")
    }
    val m = r.modelo
    File(d, "$nombre.kotlin.json").writeText(
        """{"tipo":"${r.tipo}","n":${e.pares.n},"inliers":${r.nInliers},"mediana_px":${r.mediana},"p90_px":${r.p90},"rms_px":${r.rms},""" +
            """"planitud":${r.planitud},"P":[${m.p.joinToString(",")}],"fx":${m.fx},"fy":${m.fy},"cx":${m.cx},"cy":${m.cy},""" +
            """"C":[${m.centro.joinToString(",")}],"P_verdad":[${e.pVerdad.joinToString(",")}],"C_verdad":[${e.cVerdad.joinToString(",")}]}""")
  }

  @Test
  fun pnpSalaUnaVista() {
    val rnd = Random(11)
    val e = escenaSala(rnd, listOf(doubleArrayOf(0.0, 0.0, 0.0, 0.0, 0.0)), soloPared = false, ruidoPx = 0.5, fraccOutliers = 0.03)
    val t0 = System.nanoTime()
    val r = AjusteProyector.ajustar(e.pares, 1920, 1080)!!
    val ms = (System.nanoTime() - t0) / 1e6
    val eC = errorCentro(r.modelo, e.cVerdad); val eG = errorGeneralizacion(r.modelo, e.pVerdad)
    val txt = "pnp sala 1 vista: n=${e.pares.n} ${r.resumen()} | error centro %.1f mm, generalizacion %.3f px, %.0f ms".format(eC * 1000, eG, ms)
    println(txt)
    guardarPares("sala_1vista", e, r)
    File(dirSalida, "pnp/resultado_kotlin.txt").appendText(txt + "\n")
    assertTrue(r.tipo == "dlt")
    assertTrue("residuo ${r.mediana}", r.mediana < 0.8)
    assertTrue("centro ${eC}", eC < 0.01)
    assertTrue("generalizacion $eG", eG < 1.0)
  }

  @Test
  fun pnpParedMultiVista() {
    val rnd = Random(12)
    val vistas = listOf(
        doubleArrayOf(0.0, 0.0, 0.0, 0.0, 0.0),
        doubleArrayOf(14.0, -6.0, 0.15, 0.05, -0.1),
        doubleArrayOf(-12.0, 8.0, -0.2, -0.05, 0.2),
    )
    val e = escenaSala(rnd, vistas, soloPared = true, ruidoPx = 0.5, fraccOutliers = 0.03)
    val r = AjusteProyector.ajustar(e.pares, 1920, 1080)!!
    val eC = errorCentro(r.modelo, e.cVerdad); val eG = errorGeneralizacion(r.modelo, e.pVerdad)
    val txt = "pnp pared 3 vistas: n=${e.pares.n} ${r.resumen()} | error centro %.1f mm, generalizacion %.3f px".format(eC * 1000, eG)
    println(txt)
    guardarPares("pared_3vistas", e, r)
    File(dirSalida, "pnp/resultado_kotlin.txt").appendText(txt + "\n")
    assertTrue(r.tipo == "dlt")
    assertTrue("residuo ${r.mediana}", r.mediana < 0.8)
    assertTrue("centro ${eC}", eC < 0.015)
    assertTrue("generalizacion $eG", eG < 1.5)
  }

  @Test
  fun pnpParedUnaVistaRestringido() {
    val rnd = Random(13)
    val e = escenaSala(rnd, listOf(doubleArrayOf(0.0, 0.0, 0.0, 0.0, 0.0)), soloPared = true, ruidoPx = 0.5, fraccOutliers = 0.0)
    val r = AjusteProyector.ajustar(e.pares, 1920, 1080)!!
    val eC = errorCentro(r.modelo, e.cVerdad); val eG = errorGeneralizacion(r.modelo, e.pVerdad)
    val txt = "pnp pared 1 vista (plano): n=${e.pares.n} ${r.resumen()} | error centro %.1f mm, generalizacion %.3f px".format(eC * 1000, eG)
    println(txt)
    guardarPares("pared_1vista", e, r)
    File(dirSalida, "pnp/resultado_kotlin.txt").appendText(txt + "\n")
    assertTrue(r.tipo == "restringido_plano")
    assertTrue("residuo ${r.mediana}", r.mediana < 1.0)
  }

  // ------------------------------------------------------------------ rejilla residual
  @Test
  fun rejillaAbsorbeDistorsion() {
    val rnd = Random(21)
    val e = escenaSala(rnd, listOf(doubleArrayOf(0.0, 0.0, 0.0, 0.0, 0.0)), soloPared = false, ruidoPx = 0.3, fraccOutliers = 0.0)
    // distorsión suave no proyectiva (como la del espejo/lente): radial + ondulación
    val uv = e.pares.uv.copyOf()
    for (i in 0 until e.pares.n) {
      val u = uv[i * 2]; val v = uv[i * 2 + 1]
      val du = (u - 960) / 960; val dv = (v - 540) / 960
      val r2 = du * du + dv * dv
      uv[i * 2] = u + 9.0 * r2 * du * 960 / 960 + 2.0 * sin(v / 140)
      uv[i * 2 + 1] = v + 9.0 * r2 * dv + 1.5 * cos(u / 170)
    }
    val par = Pares(e.pares.x, uv)
    val r = AjusteProyector.ajustar(par, 1920, 1080, umbralPx = 30.0)!!
    val n = par.n
    val pos = DoubleArray(n * 2); val vals = DoubleArray(n * 2); val out = DoubleArray(3)
    for (i in 0 until n) {
      r.modelo.proyectar(par.x[i * 3], par.x[i * 3 + 1], par.x[i * 3 + 2], out)
      pos[i * 2] = uv[i * 2]; pos[i * 2 + 1] = uv[i * 2 + 1]
      vals[i * 2] = out[0] - uv[i * 2]; vals[i * 2 + 1] = out[1] - uv[i * 2 + 1]
    }
    val rej = RejillaCorreccion(1920, 1080, 40)
    rej.desdeMuestras(pos, vals, n)
    // verificación: para cada punto, final(u_obs) = modelo(u_obs + C(u_obs)) debe ser el punto
    val c = DoubleArray(2); val errs = DoubleArray(n)
    for (i in 0 until n) {
      rej.evaluar(uv[i * 2], uv[i * 2 + 1], c)
      r.modelo.proyectar(par.x[i * 3], par.x[i * 3 + 1], par.x[i * 3 + 2], out)
      val ex = uv[i * 2] + c[0] - out[0]; val ey = uv[i * 2 + 1] + c[1] - out[1]
      errs[i] = sqrt(ex * ex + ey * ey)
    }
    val txt = "rejilla: residuo modelo %.2f px (p90 %.2f) → con rejilla %.3f px (p90 %.3f), |C|max %.1f px".format(
        r.mediana, r.p90, Mat.median(errs), Mat.percentile(errs, 90.0), rej.magnitudMax())
    println(txt)
    File(dirSalida, "pnp/resultado_kotlin.txt").appendText(txt + "\n")
    assertTrue(Mat.median(errs) < 0.6)
  }

  @Test
  fun matrizGlConsistente() {
    // la matriz GL debe llevar un punto al mismo píxel que P (convención v = 0 arriba)
    val p = pVerdadera(2000.0, 900.0, 1100.0, doubleArrayOf(0.02, -0.05, 0.03), 10.0, 5.0)
    val m = ModeloProyector(p, 1920, 1080)
    val g = m.matrizGl()
    val out = DoubleArray(3)
    for (pt in listOf(doubleArrayOf(0.1, -0.2, 2.0), doubleArrayOf(-0.5, 0.1, 1.2))) {
      m.proyectar(pt[0], pt[1], pt[2], out)
      val cx = g[0] * pt[0] + g[1] * pt[1] + g[2] * pt[2] + g[3]
      val cy = g[4] * pt[0] + g[5] * pt[1] + g[6] * pt[2] + g[7]
      val cw = g[12] * pt[0] + g[13] * pt[1] + g[14] * pt[2] + g[15]
      val u = (cx / cw + 1) / 2 * 1920; val v = (1 - cy / cw) / 2 * 1080
      assertTrue(abs(u - out[0]) < 1e-6 && abs(v - out[1]) < 1e-6)
    }
  }
}
