package com.smartup.proyector

import android.app.Activity
import android.app.Presentation
import android.content.Context
import android.graphics.Color
import android.hardware.display.DisplayManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Display
import android.view.Surface
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.WindowManager

/**
 * Salida al proyector, con dos variantes:
 *
 *  (A) PRESENTACION: el adaptador USB-C → DP → HDMI aparece como display Android secundario
 *      ("HDMI Screen", FLAG_PRESENTATION; verificado con dumpsys display en el Quest 3 v209).
 *      Se abre una Presentation con una SurfaceView 1920x1080 y el renderer dibuja ahí directo.
 *  (B) PANEL: si no hay display secundario (o el sistema sigue mostrando el espejo del ojo
 *      izquierdo encima), la textura va a un panel de superficie fijo a la cabeza que llena el
 *      encuadre del ojo izquierdo; el espejo de Meta lo lleva al HDMI. Como la calibración es de
 *      punta a punta, el recorte/escala fijo del espejo queda absorbido en P.
 *
 * AUTO elige A si hay display de presentación; si no, B.
 */
class SalidaProyector(private val act: Activity, private val renderer: RendererProyector) {
  companion object { private const val TAG = "Proyector" }

  enum class Preferencia { AUTO, PRESENTACION, PANEL, NINGUNA }

  @Volatile var preferencia = Preferencia.AUTO
    set(v) { field = v; evaluar() }
  @Volatile var activa = "ninguna"
    private set
  var onCambio: ((String) -> Unit)? = null

  private val dm = act.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager
  private val main = Handler(Looper.getMainLooper())
  private var presentation: Presentation? = null
  private var presDisplayId = -1
  private var panelSurface: Surface? = null
  private var panelW = 1920
  private var panelH = 1080

  private val listener = object : DisplayManager.DisplayListener {
    override fun onDisplayAdded(displayId: Int) { Log.i(TAG, "[salida] display agregado $displayId ${dm.getDisplay(displayId)?.name}"); evaluar() }
    override fun onDisplayRemoved(displayId: Int) { Log.i(TAG, "[salida] display quitado $displayId"); evaluar() }
    override fun onDisplayChanged(displayId: Int) {}
  }

  fun iniciar() {
    dm.registerDisplayListener(listener, main)
    for (d in dm.displays) Log.i(TAG, "[salida] display ${d.displayId} '${d.name}' ${d.mode.physicalWidth}x${d.mode.physicalHeight} flags=0x%x".format(d.flags))
    evaluar()
  }

  fun detener() {
    runCatching { dm.unregisterDisplayListener(listener) }
    cerrarPresentacion()
  }

  fun displaysInfo(): String = dm.getDisplays(DisplayManager.DISPLAY_CATEGORY_PRESENTATION)
      .joinToString { "${it.displayId}:'${it.name}' ${it.mode.physicalWidth}x${it.mode.physicalHeight}@${"%.0f".format(it.mode.refreshRate)}" }
      .ifEmpty { "ninguno" }

  /** La Surface del panel fijo a la cabeza (VideoSurfacePanelRegistration). */
  fun setPanelSurface(s: Surface?, w: Int, h: Int) {
    panelSurface = s; panelW = w; panelH = h
    Log.i(TAG, "[salida] surface del panel ${if (s != null) "${w}x$h" else "null"}")
    evaluar()
  }

  private fun displayPresentacion(): Display? =
      dm.getDisplays(DisplayManager.DISPLAY_CATEGORY_PRESENTATION).firstOrNull { it.displayId != Display.DEFAULT_DISPLAY && it.isValid }

  fun evaluar() {
    main.post {
      val disp = displayPresentacion()
      when {
        preferencia == Preferencia.NINGUNA -> { cerrarPresentacion(); usar(null, 0, 0, "ninguna") }
        preferencia != Preferencia.PANEL && disp != null -> abrirPresentacion(disp)
        preferencia != Preferencia.PRESENTACION && panelSurface != null -> {
          cerrarPresentacion(); usar(panelSurface, panelW, panelH, "panel")
        }
        else -> { cerrarPresentacion(); usar(null, 0, 0, "ninguna") }
      }
    }
  }

  val usandoPanel get() = activa.startsWith("panel")

  private var surfaceActual: Surface? = null

  private fun usar(s: Surface?, w: Int, h: Int, nombre: String) {
    if (s === surfaceActual && activa == (if (s != null) nombre else "ninguna")) return
    renderer.setDestino(s, w, h, nombre)
    surfaceActual = s
    activa = if (s != null) nombre else "ninguna"
    Log.i(TAG, "[salida] activa: $activa")
    onCambio?.invoke(activa)
  }

  private fun abrirPresentacion(d: Display) {
    if (presentation != null && presDisplayId == d.displayId) return
    cerrarPresentacion()
    val nombre = "presentacion:${d.name}"
    val p = object : Presentation(act, d) {
      override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window?.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val sv = SurfaceView(context)
        sv.setBackgroundColor(Color.TRANSPARENT)
        sv.holder.setFixedSize(d.mode.physicalWidth, d.mode.physicalHeight)
        sv.holder.addCallback(object : SurfaceHolder.Callback {
          override fun surfaceCreated(holder: SurfaceHolder) {}
          override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {
            Log.i(TAG, "[salida] surface HDMI ${width}x$height")
            surfaceActual = null  // fuerza el cambio
            usar(holder.surface, width, height, nombre)
          }
          override fun surfaceDestroyed(holder: SurfaceHolder) {
            Log.i(TAG, "[salida] surface HDMI destruida")
            renderer.setDestino(null, 0, 0, "ninguna")
            surfaceActual = null
            activa = "ninguna"
          }
        })
        setContentView(sv)
      }
    }
    try {
      p.show()
      presentation = p; presDisplayId = d.displayId
      Log.i(TAG, "[salida] Presentation abierta en display ${d.displayId} '${d.name}'")
    } catch (e: Exception) {
      Log.e(TAG, "[salida] no se pudo abrir la Presentation: ${e.message}")
      presentation = null; presDisplayId = -1
      if (preferencia != Preferencia.PRESENTACION && panelSurface != null) usar(panelSurface, panelW, panelH, "panel")
    }
  }

  private fun cerrarPresentacion() {
    presentation?.let { runCatching { it.dismiss() } }
    presentation = null; presDisplayId = -1
  }
}
