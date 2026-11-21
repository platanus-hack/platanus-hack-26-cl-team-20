package com.smartup.proyector

import java.io.File
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.random.Random
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Cadena completa simulada, con las MISMAS convenciones que en el casco:
 * sala analítica (pared, mesa, caja) → proyector verdadero (P en el marco cabeza) ilumina con los
 * patrones Gray → cámara con extrínseca cabeza←cámara (marco Spatial: y arriba, z adelante) y
 * rayo ((u−cx)/fx, (cy−v)/fy, 1) → decodificación → muestras subpíxel → raycast exacto → pares
 * 3D (cabeza) ↔ 2D → ajuste. Compara el proyector recuperado con el verdadero.
 */
class CadenaCompletaTest {
  // sala en el MUNDO: pared z = 2.6, mesa y = −0.45 (z 1.0..2.2), caja sobre la mesa
  private fun raycastSala(o: DoubleArray, d: DoubleArray): DoubleArray? {
    var mejor = Double.MAX_VALUE; var hit: DoubleArray? = null
    fun probar(t: Double, ok: (DoubleArray) -> Boolean) {
      if (t <= 1e-6 || t >= mejor) return
      val p = doubleArrayOf(o[0] + t * d[0], o[1] + t * d[1], o[2] + t * d[2])
      if (ok(p)) { mejor = t; hit = p }
    }
    if (d[2] != 0.0) probar((2.6 - o[2]) / d[2]) { it[0] in -2.5..2.5 && it[1] in -1.6..1.4 }
    if (d[1] != 0.0) probar((-0.45 - o[1]) / d[1]) { it[0] in -1.0..1.0 && it[2] in 1.0..2.2 }
    // caja [−0.25,0.1]×[−0.45,−0.2]×[1.4,1.7]
    if (d[2] != 0.0) probar((1.4 - o[2]) / d[2]) { it[0] in -0.25..0.1 && it[1] in -0.45..-0.2 }
    if (d[1] != 0.0) probar((-0.2 - o[1]) / d[1]) { it[0] in -0.25..0.1 && it[2] in 1.4..1.7 }
    if (d[0] != 0.0) probar((-0.25 - o[0]) / d[0]) { it[1] in -0.45..-0.2 && it[2] in 1.4..1.7 }
    if (d[0] != 0.0) probar((0.1 - o[0]) / d[0]) { it[1] in -0.45..-0.2 && it[2] in 1.4..1.7 }
    return hit
  }

  @Test
  fun cadenaCompleta() {
    val rnd = Random(5)
    // cabeza en el mundo: algo girada y desplazada
    val qh = Mat.mat3ToQuat(Mat.mul33(rotY(8.0), rotX(-6.0)))
    val mWH = Mat.pose4(0.1, 0.05, -0.05, qh[0], qh[1], qh[2], qh[3])
    val mHW = Mat.invRigid(mWH)
    // proyector verdadero en el marco cabeza (debajo y a la derecha de la cabeza, mirando un poco abajo)
    val cV = doubleArrayOf(0.07, -0.10, 0.04)
    val rP = Mat.mul33(Mat.mul33(rotXcv(14.0), doubleArrayOf(1.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, 1.0)), rotY(-3.0))
    val kP = doubleArrayOf(2250.0, 0.0, 955.0, 0.0, 2250.0, 760.0, 0.0, 0.0, 1.0)
    val mP = Mat.mul33(kP, rP); val tP = Mat.mulVec33(mP, -cV[0], -cV[1], -cV[2])
    val pV = doubleArrayOf(mP[0], mP[1], mP[2], tP[0], mP[3], mP[4], mP[5], tP[1], mP[6], mP[7], mP[8], tP[2])
    val proy = ModeloProyector(pV, 1920, 1080)
    // cámara: 640x640, ~95° FOV, 3 cm a la izquierda, 1 cm abajo, 5 cm adelante, pitch −12° (mira abajo)
    val cw = 640; val ch = 640
    val qc = Mat.mat3ToQuat(rotX(12.0))   // en Spatial (y arriba, z adelante) rotar +x baja la mirada
    val mHC = Mat.pose4(-0.032, -0.01, 0.05, qc[0], qc[1], qc[2], qc[3])
    val intr = ScanCamera.Intrinsecos(300.0, 300.0, 322.0, 315.0, cw, ch, null, mHC, FloatArray(3), FloatArray(4), null, null, "sim")
    val mWC = Mat.mul44(mWH, mHC)
    val oc = doubleArrayOf(mWC[3], mWC[7], mWC[11])
    // para cada subpíxel: coordenada (u,v) del proyector que lo ilumina (o NaN), y albedo
    val ss = 2
    val uv = FloatArray(cw * ch * ss * ss * 2) { Float.NaN }
    val out = DoubleArray(3)
    for (y in 0 until ch) for (x in 0 until cw) for (sy in 0 until ss) for (sx in 0 until ss) {
      val px = x - 0.5 + (sx + 0.5) / ss; val py = y - 0.5 + (sy + 0.5) / ss
      val dc = intr.rayoCam(px, py)
      val dw = Mat.xformDir(mWC, dc[0], dc[1], dc[2])
      val hw = raycastSala(oc, dw) ?: continue
      val hh = Mat.xformPt(mHW, hw[0], hw[1], hw[2])
      proy.proyectar(hh[0], hh[1], hh[2], out)
      if (out[2] <= 0 || out[0] < 0 || out[1] < 0 || out[0] >= 1920 || out[1] >= 1080) continue
      val k = ((y * cw + x) * ss * ss + sy * ss + sx) * 2
      uv[k] = out[0].toFloat(); uv[k + 1] = out[1].toFloat()
    }
    val gc = GrayCode(1920, 1080, 1)
    val dec = gc.Decodificador(cw, ch, 6)
    var pend: ByteArray? = null
    for (pat in gc.secuencia) {
      val img = ByteArray(cw * ch)
      for (i in 0 until cw * ch) {
        var acc = 0.0
        for (s in 0 until ss * ss) {
          val k = (i * ss * ss + s) * 2
          if (uv[k].isNaN()) continue
          acc += gc.valor(pat, floor(uv[k]).toInt(), floor(uv[k + 1]).toInt()) / 255.0
        }
        val v = 25 + 120 * acc / (ss * ss) + rnd.nextDouble(-2.0, 2.0)
        img[i] = v.roundToInt().coerceIn(0, 255).toByte()
      }
      if (!pat.inverso) pend = img else { dec.agregarPar(pat.eje, pat.bit, pend!!, img); pend = null }
    }
    val mapa = dec.resultado()
    println("cadena completa: omitidos auto x=${mapa.omitidosX} y=${mapa.omitidosY}; fracción válida x=${dec.fraccionValidos(0).mapValues { "%.2f".format(it.value) }}")
    val muestras = mapa.muestras(pasoCam = 4, radio = 3)
    // raycast desde la cámara (como Calibrador.correr) → pares en el marco cabeza
    val xs = ArrayList<Double>(); val uvs = ArrayList<Double>()
    for (m in muestras) {
      val dc = intr.rayoCam(m.cu.toDouble(), m.cv.toDouble())
      val dw = Mat.xformDir(mWC, dc[0], dc[1], dc[2])
      val hw = raycastSala(oc, dw) ?: continue
      val hh = Mat.xformPt(mHW, hw[0], hw[1], hw[2])
      xs.add(hh[0]); xs.add(hh[1]); xs.add(hh[2]); uvs.add(m.u); uvs.add(m.v)
    }
    val par = Pares(xs.toDoubleArray(), uvs.toDoubleArray())
    val iluminados = (0 until cw * ch * ss * ss).count { !uv[it * 2].isNaN() }
    println("cadena completa (diag): subpíxeles iluminados $iluminados, decodificados ${mapa.validos}, muestras ${muestras.size}, pares ${par.n}")
    val r = AjusteProyector.ajustar(par, 1920, 1080)!!
    val eC = CalibIO.dist3(r.modelo.centro, cV)
    // generalización: puntos de la sala NO usados (rejilla densa en la pared y la mesa)
    val errs = ArrayList<Double>(); val o2 = DoubleArray(3)
    for (i in 0 until 40) for (j in 0 until 30) {
      val pw = doubleArrayOf(-1.5 + 3.0 * i / 39, -1.2 + 2.4 * j / 29, 2.6)
      val ph = Mat.xformPt(mHW, pw[0], pw[1], pw[2])
      proy.proyectar(ph[0], ph[1], ph[2], out)
      if (out[0] !in 0.0..1920.0 || out[1] !in 0.0..1080.0) continue
      r.modelo.proyectar(ph[0], ph[1], ph[2], o2)
      errs.add(sqrt((out[0] - o2[0]) * (out[0] - o2[0]) + (out[1] - o2[1]) * (out[1] - o2[1])))
    }
    val txt = "cadena completa: decodificados ${mapa.validos}/${cw * ch}, ${muestras.size} muestras, ${par.n} pares; ${r.resumen()} | " +
        "error centro %.1f mm, reproyección en pared %.3f px (p90 %.3f)".format(eC * 1000, Mat.median(errs.toDoubleArray()), Mat.percentile(errs.toDoubleArray(), 90.0))
    println(txt)
    File("build/sintetico/pnp").mkdirs()
    File("build/sintetico/pnp/resultado_kotlin.txt").appendText(txt + "\n")
    assertTrue(r.tipo == "dlt")
    // con bloques Gray de 8 px y ~7 px de proyector por px de cámara, el residuo de medición es ~1–2 px;
    // lo que importa es que el modelo recuperado generalice (reproyección en la pared)
    assertTrue("residuo ${r.mediana}", r.mediana < 2.0)
    assertTrue("centro $eC", eC < 0.01)
    assertTrue("reproyección", Mat.median(errs.toDoubleArray()) < 0.5)
  }

  private fun rotY(deg: Double): DoubleArray {
    val a = Math.toRadians(deg)
    return doubleArrayOf(cos(a), 0.0, sin(a), 0.0, 1.0, 0.0, -sin(a), 0.0, cos(a))
  }

  private fun rotX(deg: Double): DoubleArray {
    val a = Math.toRadians(deg)
    return doubleArrayOf(1.0, 0.0, 0.0, 0.0, cos(a), -sin(a), 0.0, sin(a), cos(a))
  }

  // pitch en el marco OpenCV (y abajo): positivo = mira hacia abajo
  private fun rotXcv(deg: Double) = rotX(deg)
}
