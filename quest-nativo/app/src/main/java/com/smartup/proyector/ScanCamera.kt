package com.smartup.proyector

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.ImageFormat
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.SessionConfiguration
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.util.Log
import android.util.Size
import java.util.concurrent.Executors
import kotlin.math.abs

/**
 * Cámara RGB passthrough izquierda del Quest 3 (camera2 + Passthrough Camera API, Horizon OS v74+).
 * Base: ScanCamera de Metraje. Agrega: intrínsecos escalados al stream elegido, extrínseca
 * cabeza→cámara en la convención de Spatial SDK, copia de luma (Y) para la calibración, AE lock y
 * timestamps en la base de SystemClock.elapsedRealtimeNanos().
 *
 * Extrínseca: LENS_POSE_TRANSLATION / LENS_POSE_ROTATION de camera2, convertidos igual que el
 * showcase oficial de Meta para Spatial SDK (meta_spatial_scanner/CameraController.kt):
 *   q = Quaternion(w=r3, x=-r0, y=-r1, z=r2).inverse() * Euler(180°, 0, 0);  t = (t0, t1, -t2)
 * El marco de cámara resultante es x derecha, y arriba, z adelante (Spatial SDK). Rayo de un
 * píxel (u, v) con v desde ARRIBA (convención Android de LENS_INTRINSIC_CALIBRATION):
 *   d = ((u - cx)/fx, (cy - v)/fy, 1)
 */
class ScanCamera(private val context: Context) {
  companion object {
    private const val TAG = "Proyector"
    private const val POSITION_KEY = "com.meta.extra_metadata.position" // 0=izq 1=der
    private const val SOURCE_KEY = "com.meta.extra_metadata.camera_source"
  }

  class Intrinsecos(
      val fx: Double, val fy: Double, val cx: Double, val cy: Double,
      val w: Int, val h: Int,
      val distorsion: FloatArray?,
      /** 4x4 row-major cabeza → cámara (lleva puntos del marco cámara al marco cabeza). */
      val cabezaDesdeCam: DoubleArray,
      val lensT: FloatArray, val lensQ: FloatArray,
      val referencia: Int?, val fuenteTimestamp: Int?,
      val activeArray: String,
  ) {
    /** Dirección (marco cámara, Spatial: y arriba, z adelante) del píxel (u, v), sin normalizar. */
    fun rayoCam(u: Double, v: Double): DoubleArray = doubleArrayOf((u - cx) / fx, (cy - v) / fy, 1.0)

    /** Píxel de un punto en el marco cámara (inverso de rayoCam); null si está detrás. */
    fun proyectarCam(x: Double, y: Double, z: Double): DoubleArray? =
        if (z <= 1e-6) null else doubleArrayOf(cx + fx * x / z, cy - fy * y / z)

    fun resumen() = "fx=%.1f fy=%.1f cx=%.1f cy=%.1f %dx%d T=(%.4f,%.4f,%.4f) dist=%s ref=%s ts=%s act=%s".format(
        fx, fy, cx, cy, w, h, cabezaDesdeCam[3], cabezaDesdeCam[7], cabezaDesdeCam[11],
        distorsion?.joinToString(",") { "%.4f".format(it) }, referencia, fuenteTimestamp, activeArray)
  }

  /** Cuadro de luma copiado (se puede guardar después de cerrar el Image). */
  class Cuadro(val y: ByteArray, val w: Int, val h: Int, val tsNs: Long, val llegadaNs: Long, val indice: Long)

  /** Cuadro reducido a la mitad en NV21 (color) para el monitor HTTP; no toca onCuadro/copiarLuma. */
  class CuadroMonitor(val nv21: ByteArray, val w: Int, val h: Int, val tsNs: Long, val indice: Long)

  /** El monitor pide cuadros hasta este instante (SystemClock.elapsedRealtime, ms). */
  @Volatile var monitorHastaMs = 0L
  @Volatile var monitorCuadro: CuadroMonitor? = null
    private set
  private var monitorUltimoMs = 0L
  /** Copia de luma a resolución completa para el detector ArUco (aparte de onCuadro, que usa el Calibrador). */
  @Volatile var lumaHastaMs = 0L
  @Volatile var lumaIntervaloMs = 150L
  @Volatile var lumaCuadro: Cuadro? = null
    private set
  private var lumaUltimoMs = 0L
  private var filaTmp = ByteArray(0)

  var intrinsecos: Intrinsecos? = null
    private set

  @Volatile var copiarLuma = false
  @Volatile var onCuadro: ((Cuadro) -> Unit)? = null
  @Volatile var cuadros = 0L
    private set
  @Volatile var ultimoTsNs = 0L
    private set
  @Volatile var fps = 0.0
    private set
  @Volatile var failed = false
    private set
  @Volatile var abierta = false
    private set
  @Volatile var aeLockDisponible = false
    private set

  private var cameraDevice: CameraDevice? = null
  private var session: CameraCaptureSession? = null
  private var imageReader: ImageReader? = null
  private var thread: HandlerThread? = null
  private var handler: Handler? = null
  private var aeLock = false
  /** Compensación de exposición (pasos de CONTROL_AE_COMPENSATION_STEP) y exposición manual (ns, 0 = auto). */
  @Volatile var evPasos = 0
    private set
  @Volatile var evRango: android.util.Range<Int>? = null
    private set
  @Volatile var evPaso: Double = 0.0
    private set
  @Volatile var manualDisponible = false
    private set
  @Volatile var expManualNs = 0L
    private set
  @Volatile var isoManual = 0
    private set
  @Volatile var expRango: android.util.Range<Long>? = null
    private set
  @Volatile var isoRango: android.util.Range<Int>? = null
    private set
  /** Exposición e ISO que reporta la cámara en el último cuadro (CaptureResult). */
  @Volatile var expActualNs = 0L
    private set
  @Volatile var isoActual = 0
    private set
  // la base del timestamp del sensor se detecta con el primer cuadro: 0 = boottime, 1 = monotonic
  private var offsetTsNs: Long? = null

  @SuppressLint("MissingPermission")
  fun start() {
    val mgr = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
    var chosenId: String? = null
    var ch: CameraCharacteristics? = null
    for (id in mgr.cameraIdList) {
      val c = mgr.getCameraCharacteristics(id)
      val pos = runCatching { c.get(CameraCharacteristics.Key(POSITION_KEY, Int::class.java)) }.getOrNull()
      val src = runCatching { c.get(CameraCharacteristics.Key(SOURCE_KEY, Int::class.java)) }.getOrNull()
      Log.i(TAG, "[cam] id=$id position=$pos source=$src facing=${c.get(CameraCharacteristics.LENS_FACING)}")
      if (pos == 0 && chosenId == null) { chosenId = id; ch = c }
    }
    if (chosenId == null || ch == null) {
      Log.e(TAG, "[cam] no hay cámara passthrough izquierda (¿falta permiso HEADSET_CAMERA?)")
      failed = true
      return
    }
    val map = ch.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)!!
    val sizes = map.getOutputSizes(ImageFormat.YUV_420_888)
    Log.i(TAG, "[cam] tamaños YUV: ${sizes.joinToString()}")
    val size = sizes.maxByOrNull { it.width * it.height } ?: Size(1280, 960)
    aeLockDisponible = ch.get(CameraCharacteristics.CONTROL_AE_LOCK_AVAILABLE) == true
    evRango = ch.get(CameraCharacteristics.CONTROL_AE_COMPENSATION_RANGE)
    evPaso = ch.get(CameraCharacteristics.CONTROL_AE_COMPENSATION_STEP)?.toDouble() ?: 0.0
    manualDisponible = ch.get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES)
        ?.contains(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_SENSOR) == true
    expRango = ch.get(CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE)
    isoRango = ch.get(CameraCharacteristics.SENSOR_INFO_SENSITIVITY_RANGE)
    Log.i(TAG, "[cam] exposición: AE lock=$aeLockDisponible compensación=$evRango paso=$evPaso manual=$manualDisponible exp=$expRango iso=$isoRango")

    val calib = ch.get(CameraCharacteristics.LENS_INTRINSIC_CALIBRATION)
    val trans = ch.get(CameraCharacteristics.LENS_POSE_TRANSLATION)
    val rot = ch.get(CameraCharacteristics.LENS_POSE_ROTATION)
    val dist = ch.get(CameraCharacteristics.LENS_DISTORTION)
    val ref = ch.get(CameraCharacteristics.LENS_POSE_REFERENCE)
    val tsSrc = ch.get(CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE)
    val pre = ch.get(CameraCharacteristics.SENSOR_INFO_PRE_CORRECTION_ACTIVE_ARRAY_SIZE)
    val act = ch.get(CameraCharacteristics.SENSOR_INFO_ACTIVE_ARRAY_SIZE)
    Log.i(TAG, "[cam] calib=${calib?.joinToString()} T=${trans?.joinToString()} R=${rot?.joinToString()} " +
        "dist=${dist?.joinToString()} ref=$ref tsSrc=$tsSrc pre=$pre act=$act aeLock=$aeLockDisponible")
    if (calib != null && trans != null && rot != null) {
      // intrínsecos referidos al pre-correction active array → stream (recorte centrado + escala)
      val aw = (pre?.width() ?: size.width).toDouble(); val ah = (pre?.height() ?: size.height).toDouble()
      val sw = size.width.toDouble(); val sh = size.height.toDouble()
      val cw: Double; val chh: Double
      if (sw / sh > aw / ah) { cw = aw; chh = aw * sh / sw } else { chh = ah; cw = ah * sw / sh }
      val x0 = (aw - cw) / 2; val y0 = (ah - chh) / 2
      val s = cw / sw
      // extrínseca, como el showcase de Meta
      val qa = doubleArrayOf(rot[3].toDouble(), -rot[0].toDouble(), -rot[1].toDouble(), rot[2].toDouble())
      val qInv = doubleArrayOf(qa[0], -qa[1], -qa[2], -qa[3]) // conjugado (unitario)
      val q = quatMul(qInv, doubleArrayOf(0.0, 1.0, 0.0, 0.0))  // * Euler(180,0,0)
      val m = Mat.pose4(trans[0].toDouble(), trans[1].toDouble(), -trans[2].toDouble(), q[0], q[1], q[2], q[3])
      intrinsecos = Intrinsecos(
          fx = calib[0] / s, fy = calib[1] / s, cx = (calib[2] - x0) / s, cy = (calib[3] - y0) / s,
          w = size.width, h = size.height, distorsion = dist, cabezaDesdeCam = m,
          lensT = trans, lensQ = rot, referencia = ref, fuenteTimestamp = tsSrc,
          activeArray = "pre=$pre act=$act recorte=(%.0f,%.0f %.0fx%.0f) escala=%.3f".format(x0, y0, cw, chh, s),
      )
      val fwd = Mat.xformDir(m, 0.0, 0.0, 1.0); val up = Mat.xformDir(m, 0.0, 1.0, 0.0)
      Log.i(TAG, "[cam] intrínsecos ${intrinsecos!!.resumen()}")
      Log.i(TAG, "[cam] eje óptico en cabeza=(%.3f, %.3f, %.3f) arriba=(%.3f, %.3f, %.3f) (esperado: adelante +z y algo abajo)".format(
          fwd[0], fwd[1], fwd[2], up[0], up[1], up[2]))
    } else {
      Log.w(TAG, "[cam] la cámara no entrega intrínsecos/extrínseca")
    }

    thread = HandlerThread("ProyectorCam").apply { start() }
    handler = Handler(thread!!.looper)
    imageReader = ImageReader.newInstance(size.width, size.height, ImageFormat.YUV_420_888, 3).apply {
      setOnImageAvailableListener({ reader ->
        val img = reader.acquireLatestImage() ?: return@setOnImageAvailableListener
        try {
          val llegada = SystemClock.elapsedRealtimeNanos()
          val ts = convertirTs(img.timestamp, llegada)
          if (ultimoTsNs > 0) {
            val dt = (ts - ultimoTsNs) / 1e9
            if (dt > 0) fps = if (fps == 0.0) 1 / dt else fps * 0.9 + 0.1 / dt
          }
          ultimoTsNs = ts
          cuadros++
          if (cuadros == 1L) Log.i(TAG, "[cam] primer cuadro ${img.width}x${img.height} ts=${img.timestamp} llegada=$llegada")
          val ahoraMs = SystemClock.elapsedRealtime()
          if (ahoraMs < monitorHastaMs && ahoraMs - monitorUltimoMs >= 70) {
            monitorUltimoMs = ahoraMs
            runCatching { monitorCuadro = reducirNv21(img, ts, cuadros) }
          }
          if (ahoraMs < lumaHastaMs && ahoraMs - lumaUltimoMs >= lumaIntervaloMs) {
            lumaUltimoMs = ahoraMs
            runCatching {
              val w = img.width; val h = img.height; val pl = img.planes[0]; val buf = pl.buffer
              val out = ByteArray(w * h)
              if (pl.rowStride == w && pl.pixelStride == 1) { buf.position(0); buf.get(out, 0, w * h) }
              else for (row in 0 until h) { buf.position(row * pl.rowStride); buf.get(out, row * w, w) }
              buf.position(0)
              lumaCuadro = Cuadro(out, w, h, ts, llegada, cuadros)
            }
          }
          if (copiarLuma) {
            val w = img.width; val h = img.height
            val plane = img.planes[0]
            val buf = plane.buffer
            val out = ByteArray(w * h)
            if (plane.rowStride == w && plane.pixelStride == 1) {
              buf.position(0); buf.get(out, 0, w * h)
            } else {
              for (row in 0 until h) { buf.position(row * plane.rowStride); buf.get(out, row * w, w) }
            }
            onCuadro?.invoke(Cuadro(out, w, h, ts, llegada, cuadros))
          }
        } catch (e: Exception) {
          Log.e(TAG, "[cam] error en cuadro", e)
        } finally {
          img.close()
        }
      }, handler)
    }

    mgr.openCamera(chosenId, object : CameraDevice.StateCallback() {
      override fun onOpened(camera: CameraDevice) {
        cameraDevice = camera
        val cfg = SessionConfiguration(
            SessionConfiguration.SESSION_REGULAR,
            listOf(OutputConfiguration(imageReader!!.surface)),
            Executors.newSingleThreadExecutor(),
            object : CameraCaptureSession.StateCallback() {
              override fun onConfigured(s: CameraCaptureSession) {
                session = s
                abierta = true
                repetir()
                Log.i(TAG, "[cam] sesión configurada ${size.width}x${size.height}")
              }

              override fun onConfigureFailed(s: CameraCaptureSession) {
                Log.e(TAG, "[cam] no se pudo configurar la sesión")
                failed = true
              }
            })
        camera.createCaptureSession(cfg)
      }

      override fun onDisconnected(camera: CameraDevice) {
        Log.w(TAG, "[cam] desconectada")
        camera.close(); cameraDevice = null; abierta = false
      }

      override fun onError(camera: CameraDevice, error: Int) {
        Log.e(TAG, "[cam] error $error")
        camera.close(); cameraDevice = null; abierta = false; failed = true
      }
    }, handler)
  }

  /** YUV_420_888 → NV21 a la mitad de resolución (toma 1 de cada 2 píxeles y filas). */
  private fun reducirNv21(img: android.media.Image, ts: Long, indice: Long): CuadroMonitor {
    val w = img.width / 2; val h = img.height / 2
    val out = ByteArray(w * h + 2 * (w / 2) * (h / 2))
    val py = img.planes[0]; val bufY = py.buffer
    if (filaTmp.size < py.rowStride.coerceAtLeast(img.width)) filaTmp = ByteArray(py.rowStride.coerceAtLeast(img.width))
    var o = 0
    for (row in 0 until h) {
      val r = row * 2
      bufY.position(r * py.rowStride)
      bufY.get(filaTmp, 0, minOf(img.width, bufY.remaining()))
      for (i in 0 until w) out[o++] = filaTmp[i * 2]
    }
    val pu = img.planes[1]; val pv = img.planes[2]
    val bu = pu.buffer; val bv = pv.buffer
    val cw = w / 2; val ch = h / 2
    for (row in 0 until ch) {
      val r = row * 2
      for (i in 0 until cw) {
        val c = i * 2
        val iu = r * pu.rowStride + c * pu.pixelStride
        val iv = r * pv.rowStride + c * pv.pixelStride
        out[o++] = if (iv < bv.limit()) bv.get(iv) else 128.toByte()
        out[o++] = if (iu < bu.limit()) bu.get(iu) else 128.toByte()
      }
    }
    bufY.position(0)
    return CuadroMonitor(out, w, h, ts, indice)
  }

  private fun convertirTs(ts: Long, llegadaBoot: Long): Long {
    val off = offsetTsNs ?: run {
      // ¿el timestamp del sensor está en boottime o en monotonic? se elige el más cercano a la llegada
      val mono = System.nanoTime()
      val dBoot = abs(llegadaBoot - ts); val dMono = abs(mono - ts)
      val o = if (dBoot <= dMono) 0L else llegadaBoot - mono
      Log.i(TAG, "[cam] base de timestamp: ${if (o == 0L) "BOOTTIME" else "MONOTONIC (offset ${o} ns)"} " +
          "(retraso llegada %.1f ms)".format((if (o == 0L) dBoot else dMono) / 1e6))
      offsetTsNs = o
      o
    }
    return ts + off
  }

  private fun repetir() {
    val s = session ?: return
    val cam = cameraDevice ?: return
    val req = cam.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply {
      addTarget(imageReader!!.surface)
      if (manualDisponible && expManualNs > 0) {
        set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_OFF)
        set(CaptureRequest.SENSOR_EXPOSURE_TIME, expManualNs)
        if (isoManual > 0) set(CaptureRequest.SENSOR_SENSITIVITY, isoManual)
      } else {
        if (aeLockDisponible) set(CaptureRequest.CONTROL_AE_LOCK, aeLock)
        val r = evRango
        if (r != null && (r.lower != 0 || r.upper != 0)) set(CaptureRequest.CONTROL_AE_EXPOSURE_COMPENSATION, evPasos.coerceIn(r.lower, r.upper))
      }
    }
    val cb = object : CameraCaptureSession.CaptureCallback() {
      override fun onCaptureCompleted(session: CameraCaptureSession, request: CaptureRequest, result: android.hardware.camera2.TotalCaptureResult) {
        result.get(android.hardware.camera2.CaptureResult.SENSOR_EXPOSURE_TIME)?.let { expActualNs = it }
        result.get(android.hardware.camera2.CaptureResult.SENSOR_SENSITIVITY)?.let { isoActual = it }
      }
    }
    runCatching { s.setRepeatingRequest(req.build(), cb, handler) }
        .onFailure { Log.e(TAG, "[cam] setRepeatingRequest", it) }
  }

  /**
   * Exposición para ver proyecciones/monitores sin quemar: `ev` en EV (se convierte a pasos), `lock`
   * congela el AE; `manualMs` > 0 fija el tiempo de exposición (si la cámara tiene MANUAL_SENSOR).
   */
  fun setExposicion(ev: Double?, lock: Boolean, manualMs: Double = 0.0, iso: Int = 0): String {
    if (ev != null && evPaso > 0) evPasos = Math.round(ev / evPaso).toInt().let { p -> evRango?.let { p.coerceIn(it.lower, it.upper) } ?: p }
    expManualNs = if (manualMs > 0 && manualDisponible) (manualMs * 1e6).toLong().let { n -> expRango?.let { n.coerceIn(it.lower, it.upper) } ?: n } else 0L
    isoManual = if (iso > 0) isoRango?.let { iso.coerceIn(it.lower, it.upper) } ?: iso else 0
    aeLock = lock
    handler?.post { repetir() }
    return "exposición: ${if (expManualNs > 0) "manual %.2f ms iso %d".format(expManualNs / 1e6, isoManual) else "auto EV %+.2f (%d pasos de %.3f, rango %s)%s".format(evPasos * evPaso, evPasos, evPaso, evRango, if (lock) " BLOQUEADA" else "")}" +
        (if (manualMs > 0 && !manualDisponible) " (la cámara no tiene MANUAL_SENSOR)" else "")
  }

  /** Bloquea/desbloquea la exposición automática (si la cámara lo soporta). */
  fun setAeLock(lock: Boolean) {
    aeLock = lock
    handler?.post { repetir() }
  }

  fun stop() {
    abierta = false
    runCatching { session?.close() }; session = null
    runCatching { cameraDevice?.close() }; cameraDevice = null
    runCatching { imageReader?.close() }; imageReader = null
    thread?.quitSafely(); thread = null
  }

  private fun quatMul(a: DoubleArray, b: DoubleArray) = doubleArrayOf(
      a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3],
      a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
      a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1],
      a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0],
  )
}
