package com.smartup.proyector

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import com.meta.spatial.core.Vector3
import com.meta.spatial.mruk.MRUKEnvironmentRaycastHitResult
import com.meta.spatial.mruk.MRUKFeature
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/**
 * Calibración en lazo cerrado del proyector respecto de la CABEZA, a través de la cadena real
 * (textura → salida A/B → HDMI → proyector → pared → cámara passthrough):
 *
 *  1. negro / blanco ×3: latencia del lazo (render → cámara) y máscara de lo iluminado
 *  2. Gray code 10+10 bits (normal + inverso) → mapa denso cámara → píxel del proyector
 *  3. muestras subpíxel (ajuste afín local) → rayos de la cámara desde su pose en ese instante
 *     → raycast contra la profundidad del entorno (MRUK Environment Raycaster; si no, la sala
 *     MRUK) → pares 3D (marco cabeza) ↔ 2D (textura)
 *  4. RANSAC-DLT + Levenberg-Marquardt con TODAS las vistas acumuladas → P (K, R, C) del proyector
 *     en el marco cabeza; rejilla residual densa
 *  5. lazo: se proyectan puntos 3D por la cadena completa (modelo + rejilla), se miden con la
 *     cámara y se corrige la rejilla hasta bajar el residuo (como calibrar_lazo de la Osmo)
 *
 * Corre en su propio hilo; los raycasts de MRUK se hacen en el hilo principal por tandas.
 */
class Calibrador(
    private val renderer: RendererProyector,
    private val camara: () -> ScanCamera?,
    private val mruk: MRUKFeature,
    private val historial: HistorialPose,
    private val dirBase: File,
    private val raycasterListo: () -> Boolean,
    private val alTerminar: (Calibracion?) -> Unit,
) {
  companion object { private const val TAG = "Proyector" }

  enum class Fase { INACTIVO, PREPARANDO, LATENCIA, PATRONES, DECODIFICANDO, RAYCAST, AJUSTE, VERIFICANDO, LISTO, ERROR, CANCELADO }

  /** Una captura Gray desde una pose fija del rig. */
  class Vista(
      val pares: Pares,                 // X cabeza ↔ uv textura
      val xMundo: DoubleArray,          // mismos puntos en el mundo
      val camPx: DoubleArray,           // píxel de cámara de cada par
      val jac: Array<DoubleArray>,      // d(uv)/d(cam) de cada par
      val poseCabeza: PoseT,
  )

  @Volatile var fase = Fase.INACTIVO
    private set
  @Volatile var mensaje = "listo para calibrar"
    private set
  @Volatile var progreso = 0.0
    private set

  // configuración (comandos)
  @Volatile var bitsOmitidos = 1
  @Volatile var umbral = 6
  @Volatile var cuadrosPromedio = 2
  @Volatile var latenciaMinMs = 120.0
  @Volatile var iteracionesLazo = 3
  @Volatile var pasoMuestras = 6
  /** true: raycast contra la sala MRUK (Space Setup) antes que contra la profundidad del entorno. */
  @Volatile var preferirSala = false

  val vistas = ArrayList<Vista>()
  @Volatile var ultima: Calibracion? = null
  @Volatile var latenciaLazoMs: Double? = null
    private set

  private val main = Handler(Looper.getMainLooper())
  private val ultimoCuadro = AtomicReference<ScanCamera.Cuadro?>(null)
  @Volatile private var cancelar = false
  private var hilo: Thread? = null

  /** Para que el renderer use la pose actual sin predicción mientras se calibra (rig quieto). */
  @Volatile var calibrando = false
    private set

  fun ocupado() = hilo?.isAlive == true

  fun reiniciarVistas() { synchronized(vistas) { vistas.clear() }; mensaje = "vistas borradas" }

  fun cancelar() { cancelar = true }

  fun iniciar(soloVerificar: Boolean = false) {
    if (ocupado()) { mensaje = "ya hay una calibración corriendo"; return }
    cancelar = false
    hilo = Thread({
      calibrando = true
      val cam = camara()
      try {
        val r = if (soloVerificar) verificarSolo() else correr()
        alTerminar(r)
      } catch (e: Cancelado) {
        fase = Fase.CANCELADO; mensaje = "cancelado"; Log.w(TAG, "[cal] cancelado")
        alTerminar(null)
      } catch (e: Exception) {
        fase = Fase.ERROR; mensaje = "error: ${e.message}"; Log.e(TAG, "[cal] error", e)
        alTerminar(null)
      } finally {
        calibrando = false
        cam?.copiarLuma = false
        cam?.onCuadro = null
        runCatching { cam?.setAeLock(false) }
        renderer.puntos = null
        renderer.mostrarMalla = true
      }
    }, "ProyectorCal").apply { start() }
  }

  private class Cancelado : RuntimeException()

  private fun chequear() { if (cancelar) throw Cancelado() }

  private fun estado(f: Fase, m: String, p: Double = progreso) {
    fase = f; mensaje = m; progreso = p
    Log.i(TAG, "[cal] ${f.name}: $m")
  }

  // ----------------------------------------------------------------- utilidades de captura
  private fun esperar(ms: Long) {
    val fin = SystemClock.elapsedRealtime() + ms
    while (SystemClock.elapsedRealtime() < fin) { chequear(); Thread.sleep(5) }
  }

  private fun mostrarYEsperarSwap(c: RendererProyector.Contenido, timeoutMs: Long = 3000): Long {
    val id = renderer.mostrar(c)
    val fin = SystemClock.elapsedRealtime() + timeoutMs
    while (SystemClock.elapsedRealtime() < fin) {
      chequear()
      renderer.tiempoPresentado(id)?.let { return it }
      Thread.sleep(2)
    }
    throw IllegalStateException("el renderer no presenta cuadros (¿salida sin destino? ${renderer.destinoInfo})")
  }

  /** Primer cuadro de cámara con timestamp ≥ desdeNs. */
  private fun cuadroDesde(desdeNs: Long, timeoutMs: Long = 3000): ScanCamera.Cuadro {
    val fin = SystemClock.elapsedRealtime() + timeoutMs
    while (SystemClock.elapsedRealtime() < fin) {
      chequear()
      val c = ultimoCuadro.get()
      if (c != null && c.tsNs >= desdeNs) return c
      Thread.sleep(2)
    }
    throw IllegalStateException("la cámara no entrega cuadros")
  }

  /** Promedio de n cuadros consecutivos desde desdeNs. */
  private fun promedio(desdeNs: Long, n: Int): ScanCamera.Cuadro {
    var c = cuadroDesde(desdeNs)
    if (n <= 1) return c
    val acc = IntArray(c.w * c.h)
    var k = 0
    var ultimoIdx = -1L
    while (k < n) {
      if (c.indice != ultimoIdx) {
        for (i in acc.indices) acc[i] += c.y[i].toInt() and 0xFF
        ultimoIdx = c.indice; k++
      }
      if (k < n) c = cuadroDesde(c.tsNs + 1)
    }
    val out = ByteArray(acc.size) { ((acc[it] + n / 2) / n).toByte() }
    return ScanCamera.Cuadro(out, c.w, c.h, c.tsNs, c.llegadaNs, c.indice)
  }

  private fun mediaDiff(a: ByteArray, b: ByteArray, mascara: BooleanArray? = null): Double {
    var s = 0L; var n = 0L
    var i = 0
    while (i < a.size) {
      if (mascara == null || mascara[i]) { s += (a[i].toInt() and 0xFF) - (b[i].toInt() and 0xFF); n++ }
      i += 7
    }
    return if (n == 0L) 0.0 else s.toDouble() / n
  }

  // ----------------------------------------------------------------- flujo completo
  private fun prepararCamara(): ScanCamera {
    val cam = camara() ?: throw IllegalStateException("cámara no iniciada (permiso HEADSET_CAMERA)")
    if (!cam.abierta) throw IllegalStateException("cámara cerrada")
    if (cam.intrinsecos == null) throw IllegalStateException("la cámara no entrega intrínsecos")
    if (renderer.destinoInfo.startsWith("sin destino")) throw IllegalStateException("no hay salida al proyector (ni HDMI ni panel)")
    ultimoCuadro.set(null)
    cam.onCuadro = { ultimoCuadro.set(it) }
    cam.copiarLuma = true
    return cam
  }

  private class Referencias(val negro: ByteArray, val blanco: ByteArray, val mascara: BooleanArray, val lcapNs: Long, val w: Int, val h: Int)

  private fun medirReferencias(cam: ScanCamera, dir: File): Referencias {
    estado(Fase.LATENCIA, "negro de referencia", 0.02)
    var t = mostrarYEsperarSwap(RendererProyector.Contenido.Negro)
    var negro = promedio(t + 700_000_000L, 3)
    val lat = ArrayList<Double>()
    var blanco: ScanCamera.Cuadro = negro
    var contraste = 0.0
    for (ciclo in 0 until 3) {
      estado(Fase.LATENCIA, "latencia ${ciclo + 1}/3", 0.03 + 0.02 * ciclo)
      val tSwap = mostrarYEsperarSwap(RendererProyector.Contenido.Blanco)
      val serie = ArrayList<Pair<Long, Double>>()
      var c = cuadroDesde(tSwap - 40_000_000L)
      val fin = tSwap + 1_200_000_000L
      while (c.tsNs < fin) {
        serie.add(Pair(c.tsNs, mediaDiff(c.y, negro.y)))
        c = cuadroDesde(c.tsNs + 1)
      }
      blanco = c
      val final = serie.takeLast(5).map { it.second }.average()
      contraste = final
      val primero = serie.firstOrNull { it.second > 0.5 * final && it.first >= tSwap }
      if (primero != null && final > 2.0) lat.add((primero.first - tSwap) / 1e6)
      Log.i(TAG, "[cal] ciclo $ciclo: final=%.1f niveles, latencia=%s ms".format(final, primero?.let { "%.0f".format((it.first - tSwap) / 1e6) }))
      t = mostrarYEsperarSwap(RendererProyector.Contenido.Negro)
      if (ciclo < 2) esperar(500) else negro = promedio(t + 700_000_000L, 3)
    }
    if (contraste < 3.0) throw IllegalStateException("la cámara no ve la proyección (blanco−negro = %.1f niveles): revisa salida, foco y que el proyector apunte donde mira el casco".format(contraste))
    val latMs = if (lat.isEmpty()) 150.0 else Mat.median(lat.toDoubleArray())
    latenciaLazoMs = latMs
    val periodo = if (cam.fps > 1) 1000.0 / cam.fps else 33.0
    val lcap = max(latenciaMinMs, latMs + 2 * periodo)
    val w = negro.w; val h = negro.h
    blanco = promedio(mostrarYEsperarSwap(RendererProyector.Contenido.Blanco) + (lcap * 1e6).toLong() + 300_000_000L, 3)
    val mascara = BooleanArray(w * h)
    var nm = 0
    for (i in 0 until w * h) {
      if ((blanco.y[i].toInt() and 0xFF) - (negro.y[i].toInt() and 0xFF) > 12) { mascara[i] = true; nm++ }
    }
    CalibIO.guardarLuma(File(dir, "negro.jpg"), negro.y, w, h)
    CalibIO.guardarLuma(File(dir, "blanco.jpg"), blanco.y, w, h)
    CalibIO.guardarMascara(File(dir, "mascara.png"), mascara, w, h)
    Log.i(TAG, "[cal] latencia del lazo %.0f ms (captura a +%.0f ms), máscara %d px (%.1f%%)".format(latMs, lcap, nm, 100.0 * nm / (w * h)))
    if (nm < 3000) throw IllegalStateException("la zona iluminada que ve la cámara es muy chica ($nm px)")
    renderer.mostrar(RendererProyector.Contenido.Negro)
    return Referencias(negro.y, blanco.y, mascara, (lcap * 1e6).toLong(), w, h)
  }

  private fun correr(): Calibracion? {
    val cam = prepararCamara()
    val intr = cam.intrinsecos!!
    val ts = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
    val dir = File(dirBase, "cal_$ts").apply { mkdirs() }
    estado(Fase.PREPARANDO, "preparando (${dir.name})", 0.0)
    val ref = medirReferencias(cam, dir)

    // ---- Gray code
    val gc = GrayCode(renderer.ancho, renderer.alto, bitsOmitidos)
    if (cam.aeLockDisponible) {
      mostrarYEsperarSwap(RendererProyector.Contenido.Gray(0, gc.bitsX - 1, false))
      esperar(800)
      cam.setAeLock(true)
      esperar(200)
    }
    val pose0 = historial.ultima() ?: throw IllegalStateException("sin pose de la cabeza (¿tracking?)")
    val dec = gc.Decodificador(ref.w, ref.h, umbral, ref.mascara)
    var pendiente: ByteArray? = null
    val tPatrones = SystemClock.elapsedRealtime()
    val tsCuadros = ArrayList<Long>()
    for ((k, pat) in gc.secuencia.withIndex()) {
      estado(Fase.PATRONES, "patrón ${k + 1}/${gc.secuencia.size} (${"xy"[pat.eje]} bit ${pat.bit}${if (pat.inverso) " inv" else ""})",
          0.1 + 0.5 * k / gc.secuencia.size)
      val tSwap = mostrarYEsperarSwap(RendererProyector.Contenido.Gray(pat.eje, pat.bit, pat.inverso))
      val c = promedio(tSwap + ref.lcapNs, cuadrosPromedio)
      tsCuadros.add(c.tsNs)
      if (k < 2) CalibIO.guardarLuma(File(dir, "gray_${k}.jpg"), c.y, c.w, c.h)
      if (!pat.inverso) pendiente = c.y else { dec.agregarPar(pat.eje, pat.bit, pendiente!!, c.y); pendiente = null }
    }
    cam.setAeLock(false)
    renderer.mostrar(RendererProyector.Contenido.Negro)
    val pose1 = historial.ultima()!!
    val (dt, dang) = pose1.distancia(pose0)
    Log.i(TAG, "[cal] patrones en %.1f s; movimiento del rig %.1f mm / %.2f°".format((SystemClock.elapsedRealtime() - tPatrones) / 1000.0, dt * 1000, dang))
    if (dt > 0.01 || dang > 0.5) throw IllegalStateException("el rig se movió durante la captura (%.0f mm, %.1f°): fíjalo y repite".format(dt * 1000, dang))

    // ---- decodificar
    estado(Fase.DECODIFICANDO, "decodificando", 0.62)
    val mapa = dec.resultado()
    val muestras = mapa.muestras(pasoCam = pasoMuestras, radio = 3, fraccionMin = 0.4)
    Log.i(TAG, "[cal] decodificados ${mapa.validos} px (bits finos descartados x=${mapa.omitidosX} y=${mapa.omitidosY}); ${muestras.size} muestras")
    Log.i(TAG, "[cal] fracción válida por bit x=${dec.fraccionValidos(0)} y=${dec.fraccionValidos(1)}; contraste ${dec.contrasteMedio}")
    CalibIO.guardarMapa(File(dir, "mapa_u.png"), mapa, true)
    CalibIO.guardarMapa(File(dir, "mapa_v.png"), mapa, false)
    if (muestras.size < 50) throw IllegalStateException("muy pocas muestras decodificadas (${muestras.size}): sube el contraste (sala oscura) o baja 'umbral'")

    // ---- raycast desde la pose de la cámara a mitad de la captura
    estado(Fase.RAYCAST, "raycast de ${muestras.size} muestras", 0.65)
    val tMedio = tsCuadros[tsCuadros.size / 2]
    val poseCab = historial.en(tMedio) ?: pose1
    val mWH = poseCab.mat4()
    val mWC = Mat.mul44(mWH, intr.cabezaDesdeCam)
    val origen = doubleArrayOf(mWC[3], mWC[7], mWC[11])
    val dirs = Array(muestras.size) { i ->
      val (u, v) = CalibIO.sinDistorsion(intr, muestras[i].cu.toDouble(), muestras[i].cv.toDouble())
      val d = intr.rayoCam(u, v)
      val w = Mat.xformDir(mWC, d[0], d[1], d[2])
      val n = sqrt(w[0] * w[0] + w[1] * w[1] + w[2] * w[2])
      doubleArrayOf(w[0] / n, w[1] / n, w[2] / n)
    }
    val hits = raycast(origen, dirs)
    val mHW = Mat.invRigid(mWH)
    val xs = ArrayList<Double>(); val uvs = ArrayList<Double>(); val xw = ArrayList<Double>(); val cps = ArrayList<Double>()
    val jacs = ArrayList<DoubleArray>()
    for ((i, h) in hits.withIndex()) {
      if (h == null) continue
      val ph = Mat.xformPt(mHW, h[0], h[1], h[2])
      xs.add(ph[0]); xs.add(ph[1]); xs.add(ph[2])
      xw.add(h[0]); xw.add(h[1]); xw.add(h[2])
      uvs.add(muestras[i].u); uvs.add(muestras[i].v)
      cps.add(muestras[i].cu.toDouble()); cps.add(muestras[i].cv.toDouble())
      jacs.add(muestras[i].j)
    }
    Log.i(TAG, "[cal] raycast: ${xs.size / 3}/${muestras.size} con punto 3D")
    if (xs.size / 3 < 30) throw IllegalStateException("el raycast no encontró superficies (${xs.size / 3}): ¿Environment Raycaster/escena MRUK disponibles?")
    val vista = Vista(Pares(xs.toDoubleArray(), uvs.toDoubleArray()), xw.toDoubleArray(), cps.toDoubleArray(), jacs.toTypedArray(), poseCab)
    synchronized(vistas) { vistas.add(vista) }

    // ---- ajuste con todas las vistas
    estado(Fase.AJUSTE, "ajustando con ${vistas.size} vista(s)", 0.72)
    val todos = unir(vistas)
    val res = AjusteProyector.ajustar(todos, renderer.ancho, renderer.alto)
        ?: throw IllegalStateException("el ajuste del proyector falló")
    Log.i(TAG, "[cal] ${res.resumen()}")
    val rej = rejillaDesde(res, todos)
    Log.i(TAG, "[cal] rejilla residual |C|max %.1f px".format(rej.magnitudMax()))
    CalibIO.guardarPares(File(dir, "pares.csv"), vista, res, todos.n - vista.pares.n)
    CalibIO.guardarMuestras(File(dir, "muestras.jpg"), ref.blanco, ref.w, ref.h, vista, res, todos.n - vista.pares.n)

    var cal = Calibracion(
        modelo = res.modelo, ajuste = res, rejilla = rej, poseCabeza = poseCab, intr = intr,
        latenciaLazoMs = latenciaLazoMs, latenciaCapturaMs = ref.lcapNs / 1e6, vistas = vistas.size,
        dir = dir.name, salida = renderer.destinoInfo)
    ultima = cal

    // ---- lazo de verificación
    cal = lazo(cal, vista, ref, dir)
    ultima = cal
    estado(Fase.LISTO, "listo: gray %.2f px (p90 %.2f), lazo %s px, %d vista(s)%s".format(
        res.mediana, res.p90, cal.lazoMedianaPx?.let { "%.2f".format(it) } ?: "—", vistas.size,
        if (res.tipo != "dlt") " [plano: captura otra vista moviendo el rig]" else ""), 1.0)
    return cal
  }

  /**
   * Vista "externa" (p. ej. grilla de marcadores QR seguida por MRUK): pares mundo ↔ textura con la
   * pose de la cabeza de ese momento. Se suma a las vistas y se re-ajusta con todas. Sin lazo ni rejilla.
   */
  fun agregarVistaExterna(xMundo: DoubleArray, uv: DoubleArray, pose: PoseT, nombre: String): Calibracion? {
    val n = uv.size / 2
    val mHW = Mat.invRigid(pose.mat4())
    val xh = DoubleArray(n * 3)
    for (i in 0 until n) { val p = Mat.xformPt(mHW, xMundo[i * 3], xMundo[i * 3 + 1], xMundo[i * 3 + 2]); p.copyInto(xh, i * 3) }
    val vista = Vista(Pares(xh, uv), xMundo, DoubleArray(n * 2), Array(n) { doubleArrayOf(1.0, 0.0, 0.0, 1.0) }, pose)
    synchronized(vistas) { vistas.add(vista) }
    val todos = synchronized(vistas) { unir(vistas) }
    val res = AjusteProyector.ajustar(todos, renderer.ancho, renderer.alto, umbralPx = 12.0)
    if (res == null) {
      synchronized(vistas) { vistas.remove(vista) }
      mensaje = "$nombre: el ajuste falló con ${todos.n} pares"
      return null
    }
    Log.i(TAG, "[cal] $nombre: ${res.resumen()}")
    val cal = Calibracion(modelo = res.modelo, ajuste = res, rejilla = RejillaCorreccion(renderer.ancho, renderer.alto), poseCabeza = pose,
        intr = camara()?.intrinsecos, latenciaLazoMs = latenciaLazoMs, latenciaCapturaMs = null, vistas = vistas.size, dir = "",
        salida = renderer.destinoInfo)
    ultima = cal
    fase = Fase.LISTO
    mensaje = "%s: %s mediana %.2f px p90 %.2f (%d pares, %d vistas)".format(nombre, res.tipo, res.mediana, res.p90, todos.n, vistas.size)
    return cal
  }

  private fun unir(vs: List<Vista>): Pares {
    val n = vs.sumOf { it.pares.n }
    val x = DoubleArray(n * 3); val uv = DoubleArray(n * 2)
    var o = 0
    for (v in vs) {
      v.pares.x.copyInto(x, o * 3); v.pares.uv.copyInto(uv, o * 2); o += v.pares.n
    }
    return Pares(x, uv)
  }

  private fun rejillaDesde(res: ResultadoAjuste, par: Pares): RejillaCorreccion {
    val pos = ArrayList<Double>(); val vals = ArrayList<Double>()
    val out = DoubleArray(3)
    for (i in 0 until par.n) {
      if (!res.inliers[i]) continue
      res.modelo.proyectar(par.x[i * 3], par.x[i * 3 + 1], par.x[i * 3 + 2], out)
      pos.add(par.uv[i * 2]); pos.add(par.uv[i * 2 + 1])
      vals.add(out[0] - par.uv[i * 2]); vals.add(out[1] - par.uv[i * 2 + 1])
    }
    val r = RejillaCorreccion(renderer.ancho, renderer.alto)
    r.desdeMuestras(pos.toDoubleArray(), vals.toDoubleArray(), pos.size / 2)
    return r
  }

  /** Raycast en el hilo principal, por tandas. Devuelve punto mundo o null por rayo. */
  private fun raycast(origen: DoubleArray, dirs: Array<DoubleArray>): Array<DoubleArray?> {
    val out = arrayOfNulls<DoubleArray>(dirs.size)
    val usarEnv = raycasterListo() && !(preferirSala && mruk.rooms.isNotEmpty())
    val o = Vector3(origen[0].toFloat(), origen[1].toFloat(), origen[2].toFloat())
    var i = 0
    val t0 = SystemClock.elapsedRealtime()
    while (i < dirs.size) {
      chequear()
      val desde = i
      val hasta = min(dirs.size, i + 300)
      val latch = CountDownLatch(1)
      main.post {
        try {
          for (k in desde until hasta) {
            val d = Vector3(dirs[k][0].toFloat(), dirs[k][1].toFloat(), dirs[k][2].toFloat())
            if (usarEnv) {
              val h = runCatching { mruk.raycastEnvironment(o, d, 8f) }.getOrNull()
              if (h != null && h.result == MRUKEnvironmentRaycastHitResult.SUCCESS)
                out[k] = doubleArrayOf(h.point.x.toDouble(), h.point.y.toDouble(), h.point.z.toDouble())
            }
            if (out[k] == null) {
              val room = mruk.getCurrentRoom() ?: mruk.rooms.firstOrNull()
              if (room != null) {
                val h = runCatching { mruk.raycastRoom(room.anchor.uuid, o, d, 8f, com.meta.spatial.mruk.SurfaceType.PLANE_VOLUME) }.getOrNull()
                if (h != null) out[k] = doubleArrayOf(h.hitPosition.x.toDouble(), h.hitPosition.y.toDouble(), h.hitPosition.z.toDouble())
              }
            }
          }
        } finally { latch.countDown() }
      }
      latch.await(10, TimeUnit.SECONDS)
      i = hasta
      progreso = 0.65 + 0.07 * i / dirs.size
    }
    Log.i(TAG, "[cal] ${dirs.size} raycasts en ${SystemClock.elapsedRealtime() - t0} ms (env=$usarEnv)")
    return out
  }

  // ----------------------------------------------------------------- lazo cerrado con puntos
  private fun lazo(cal0: Calibracion, vista: Vista, ref: Referencias, dir: File): Calibracion {
    var cal = cal0
    renderer.modelo = cal.modelo
    renderer.rejilla = cal.rejilla.copiar(); renderer.rejillaVersion++
    renderer.mostrarMalla = false
    // puntos bien repartidos: el inlier más cercano a cada celda de una rejilla 12×7 en textura
    val nPar = vista.pares.n
    val inl = cal.ajuste?.inliers
    val base = (inl?.size ?: nPar) - nPar   // índice del primer par de esta vista en el ajuste conjunto
    val elegidos = ArrayList<Int>()
    val cols = 12; val filas = 7
    for (j in 0 until filas) for (i in 0 until cols) {
      val cu = (i + 0.5) / cols * renderer.ancho; val cv = (j + 0.5) / filas * renderer.alto
      var mejor = -1; var md = Double.MAX_VALUE
      for (k in 0 until nPar) {
        if (inl != null && !inl[base + k]) continue
        val du = vista.pares.uv[k * 2] - cu; val dv = vista.pares.uv[k * 2 + 1] - cv
        val d = du * du + dv * dv
        if (d < md) { md = d; mejor = k }
      }
      if (mejor >= 0 && md < (renderer.ancho / cols.toDouble()).let { it * it } && mejor !in elegidos) elegidos.add(mejor)
    }
    if (elegidos.size < 8) { Log.w(TAG, "[cal] lazo: pocos puntos (${elegidos.size}), se omite"); return cal }
    val pts = FloatArray(elegidos.size * 3)
    for ((q, k) in elegidos.withIndex()) for (c in 0..2) pts[q * 3 + c] = vista.xMundo[k * 3 + c].toFloat()
    // tamaño del punto: ~6 px de cámara
    val escala = Mat.median(elegidos.map { k -> val j = vista.jac[k]; sqrt(abs(j[0] * j[3] - j[1] * j[2])) }.toDoubleArray())
    renderer.tamPunto = (6 * escala).coerceIn(10.0, 48.0).toFloat()
    renderer.puntos = pts
    var medianaPx = Double.NaN; var p90Px = Double.NaN; var medianaCam = Double.NaN
    var iter = 0
    for (it in 0 until max(1, iteracionesLazo + 1)) {
      iter = it
      estado(Fase.VERIFICANDO, "lazo ${it + 1}: midiendo ${elegidos.size} puntos", 0.8 + 0.05 * it)
      val tSwap = mostrarYEsperarSwap(RendererProyector.Contenido.Mapeo)
      val c = promedio(tSwap + ref.lcapNs + 60_000_000L, cuadrosPromedio)
      val blobs = Blobs.detectar(c.y, ref.negro, ref.w, ref.h, ref.mascara)
      val errTex = ArrayList<Double>(); val errCam = ArrayList<Double>()
      val posFix = ArrayList<Double>(); val deltaFix = ArrayList<Double>()
      val pares = ArrayList<DoubleArray>()
      val out = DoubleArray(3); val cc = DoubleArray(2)
      for (k in elegidos) {
        val ex = vista.camPx[k * 2]; val ey = vista.camPx[k * 2 + 1]
        val j = vista.jac[k]
        val radioCam = max(12.0, 2.5 * renderer.tamPunto / max(escala, 0.3))
        val b = blobs.minByOrNull { (it[0] - ex) * (it[0] - ex) + (it[1] - ey) * (it[1] - ey) } ?: continue
        val dx = b[0] - ex; val dy = b[1] - ey
        if (dx * dx + dy * dy > radioCam * radioCam) continue
        val ru = j[0] * dx + j[1] * dy; val rv = j[2] * dx + j[3] * dy
        errTex.add(sqrt(ru * ru + rv * rv)); errCam.add(sqrt(dx * dx + dy * dy))
        // posición final del punto: p + C(p) = û  (dos iteraciones de punto fijo)
        val x = vista.pares.x
        cal.modelo.proyectar(x[k * 3], x[k * 3 + 1], x[k * 3 + 2], out)
        var pu = out[0]; var pv = out[1]
        for (q in 0..1) { renderer.rejilla!!.evaluar(pu, pv, cc); pu = out[0] - cc[0]; pv = out[1] - cc[1] }
        posFix.add(pu - ru); posFix.add(pv - rv); deltaFix.add(ru); deltaFix.add(rv)
        pares.add(doubleArrayOf(ex, ey, b[0], b[1]))
      }
      CalibIO.guardarVerificacion(File(dir, "verif_$it.jpg"), c.y, ref.w, ref.h, pares)
      if (errTex.isEmpty()) { Log.w(TAG, "[cal] lazo $it: no se encontraron los puntos (${blobs.size} manchas)"); break }
      medianaPx = Mat.median(errTex.toDoubleArray()); p90Px = Mat.percentile(errTex.toDoubleArray(), 90.0)
      medianaCam = Mat.median(errCam.toDoubleArray())
      Log.i(TAG, "[cal] lazo $it: ${errTex.size}/${elegidos.size} puntos, residuo %.2f px proyector (p90 %.2f), %.2f px cámara".format(medianaPx, p90Px, medianaCam))
      if (medianaPx < 0.8 || it >= iteracionesLazo) break
      val r = renderer.rejilla!!.copiar()
      r.sumarDispersas(posFix.toDoubleArray(), deltaFix.toDoubleArray(), posFix.size / 2)
      renderer.rejilla = r; renderer.rejillaVersion++
    }
    renderer.puntos = null
    renderer.mostrarMalla = true
    renderer.mostrar(RendererProyector.Contenido.Negro)
    return cal.copia(rejilla = renderer.rejilla!!.copiar(), lazoMedianaPx = medianaPx, lazoP90Px = p90Px, lazoMedianaCamPx = medianaCam, lazoIter = iter + 1)
  }

  /** Solo el lazo de verificación sobre la última vista (sin recapturar Gray). */
  private fun verificarSolo(): Calibracion? {
    val cal = ultima ?: throw IllegalStateException("no hay calibración")
    val v = synchronized(vistas) { vistas.lastOrNull() } ?: throw IllegalStateException("no hay vistas en esta sesión")
    val cam = prepararCamara()
    val dir = File(dirBase, cal.dir).apply { mkdirs() }
    val ref = medirReferencias(cam, dir)
    val r = lazo(cal, v, ref, dir)
    ultima = r
    estado(Fase.LISTO, "verificado: lazo %.2f px".format(r.lazoMedianaPx ?: Double.NaN), 1.0)
    return r
  }
}

/** Manchas brillantes (puntos proyectados) en (cuadro − negro), dentro de la máscara. */
object Blobs {
  fun detectar(img: ByteArray, negro: ByteArray, w: Int, h: Int, mascara: BooleanArray?): List<DoubleArray> {
    val d = IntArray(w * h)
    val hist = IntArray(256)
    for (i in 0 until w * h) {
      if (mascara != null && !mascara[i]) continue
      val v = ((img[i].toInt() and 0xFF) - (negro[i].toInt() and 0xFF)).coerceIn(0, 255)
      d[i] = v; hist[v]++
    }
    // umbral: máx(15, 40% del percentil 99.7)
    val total = hist.sum()
    var acc = 0; var p997 = 255
    for (v in 255 downTo 0) { acc += hist[v]; if (acc >= total * 0.003) { p997 = v; break } }
    val umbral = max(15, (0.4 * p997).toInt())
    val visto = BooleanArray(w * h)
    val out = ArrayList<DoubleArray>()
    val pila = IntArray(w * h)
    for (i in 0 until w * h) {
      if (visto[i] || d[i] <= umbral) continue
      var sp = 0; pila[sp++] = i; visto[i] = true
      var sx = 0.0; var sy = 0.0; var sw = 0.0; var n = 0
      while (sp > 0) {
        val p = pila[--sp]
        val x = p % w; val y = p / w
        val wt = (d[p] - umbral).toDouble()
        sx += wt * x; sy += wt * y; sw += wt; n++
        if (n > 4000) break
        for ((ox, oy) in arrayOf(1 to 0, -1 to 0, 0 to 1, 0 to -1)) {
          val xx = x + ox; val yy = y + oy
          if (xx < 0 || yy < 0 || xx >= w || yy >= h) continue
          val q = yy * w + xx
          if (!visto[q] && d[q] > umbral) { visto[q] = true; pila[sp++] = q }
        }
      }
      if (n in 2..2500 && sw > 0) out.add(doubleArrayOf(sx / sw, sy / sw, n.toDouble()))
    }
    return out
  }
}
