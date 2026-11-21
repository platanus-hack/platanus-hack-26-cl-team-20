@file:OptIn(com.meta.spatial.core.SpatialSDKExperimentalAPI::class)

package com.smartup.proyector

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.widget.Button
import android.widget.TextView
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Pose
import com.meta.spatial.core.Quaternion
import com.meta.spatial.core.Query
import com.meta.spatial.core.SpatialFeature
import com.meta.spatial.core.SystemBase
import com.meta.spatial.core.SpatialSDKExperimentalAPI
import com.meta.spatial.runtime.BodyTrackingFidelity
import com.meta.spatial.runtime.ControllerPose
import com.meta.spatial.runtime.JointPose
import com.meta.spatial.runtime.JointSet
import com.meta.spatial.runtime.SkeletonJoint
import com.meta.spatial.core.Vector3
import com.meta.spatial.mruk.MRUKEnvironmentRaycastHitResult
import com.meta.spatial.mruk.MRUKFeature
import com.meta.spatial.mruk.MRUKLoadDeviceResult
import com.meta.spatial.mruk.MRUKStartEnvironmentRaycasterResult
import com.meta.spatial.runtime.ButtonBits
import com.meta.spatial.runtime.LayerConfig
import com.meta.spatial.toolkit.AppSystemActivity
import com.meta.spatial.toolkit.AvatarBody
import com.meta.spatial.toolkit.Controller
import com.meta.spatial.toolkit.Grabbable
import com.meta.spatial.toolkit.MediaPanelRenderOptions
import com.meta.spatial.toolkit.MediaPanelSettings
import com.meta.spatial.toolkit.Panel
import com.meta.spatial.toolkit.PanelRegistration
import com.meta.spatial.toolkit.PixelDisplayOptions
import com.meta.spatial.toolkit.QuadShapeOptions
import com.meta.spatial.toolkit.Scale
import com.meta.spatial.toolkit.Transform
import com.meta.spatial.toolkit.VideoSurfacePanelRegistration
import com.meta.spatial.toolkit.Visible
import com.meta.spatial.toolkit.createPanelEntity
import com.meta.spatial.vr.LocomotionSystem
import com.meta.spatial.vr.VRFeature
import com.meta.spatial.mruk.MRUKAnchor
import com.meta.spatial.mruk.MRUKStartTrackerResult
import com.meta.spatial.mruk.TrackedQrCode
import com.meta.spatial.mruk.Tracker
import com.meta.spatial.mruk.TrackingStatus
import com.meta.spatial.mruk.MRUKPlane
import com.meta.spatial.mruk.MRUKVolume
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.sqrt
import org.json.JSONArray
import org.json.JSONObject

/**
 * Projection mapping sin computador: Quest 3 (sensor + cerebro) + proyector por USB-C → HDMI.
 *
 * - escena de la sala: MRUK (Space Setup) y/o malla de profundidad (Environment Raycaster)
 * - cámara passthrough izquierda (camera2) con intrínsecos y extrínseca cabeza→cámara
 * - salida: (A) Presentation en el display HDMI o (B) panel fijo a la cabeza (espejo ojo izq.)
 * - calibración en lazo cerrado (Gray code + raycast + DLT/LM + rejilla + lazo con puntos)
 * - render del mapeo: pose de la cabeza predicha + T_cabeza→proyector, o pose fija (trípode)
 *
 * Control sin ponerse el casco:
 *   adb shell am broadcast -a com.smartup.proyector.CMD -p com.smartup.proyector --es c "estado"
 *   adb logcat -s Proyector:*
 */
class MainActivity : AppSystemActivity() {
  companion object {
    private const val TAG = "Proyector"
    private const val PERM_SCENE = "com.oculus.permission.USE_SCENE"
    private const val PERM_HEADSET_CAM = "horizonos.permission.HEADSET_CAMERA"
    private const val PERM_CAMERA = "android.permission.CAMERA"
    private const val REQ_PERMS = 100
    private const val PANEL_UI = 7
    const val ACCION = "com.smartup.proyector.CMD"
    private const val ARCHIVO = "calib_proyector.json"

    /** XR_FULL_BODY_JOINT_* (XR_META_body_tracking_full_body), índice → nombre. */
    val NOMBRES_ARTICULACION: List<String> = run {
      val l = mutableListOf("ROOT", "HIPS", "SPINE_LOWER", "SPINE_MIDDLE", "SPINE_UPPER", "CHEST", "NECK", "HEAD")
      for (lado in listOf("LEFT", "RIGHT")) l.addAll(listOf("${lado}_SHOULDER", "${lado}_SCAPULA", "${lado}_ARM_UPPER", "${lado}_ARM_LOWER", "${lado}_HAND_WRIST_TWIST"))
      for (lado in listOf("LEFT", "RIGHT")) {
        l.addAll(listOf("${lado}_HAND_PALM", "${lado}_HAND_WRIST", "${lado}_HAND_THUMB_METACARPAL", "${lado}_HAND_THUMB_PROXIMAL", "${lado}_HAND_THUMB_DISTAL", "${lado}_HAND_THUMB_TIP"))
        for (dedo in listOf("INDEX", "MIDDLE", "RING", "LITTLE")) for (seg in listOf("METACARPAL", "PROXIMAL", "INTERMEDIATE", "DISTAL", "TIP")) l.add("${lado}_HAND_${dedo}_$seg")
      }
      for (lado in listOf("LEFT", "RIGHT")) l.addAll(listOf("${lado}_UPPER_LEG", "${lado}_LOWER_LEG", "${lado}_FOOT_ANKLE_TWIST", "${lado}_FOOT_ANKLE", "${lado}_FOOT_SUBTALAR", "${lado}_FOOT_TRANSVERSE", "${lado}_FOOT_BALL"))
      l
    }
  }

  private lateinit var mruk: MRUKFeature
  private val renderer = RendererProyector(1920, 1080)
  private lateinit var salida: SalidaProyector
  private lateinit var calibrador: Calibrador
  private val historial = HistorialPose()
  private var camera: ScanCamera? = null
  private val main = Handler(Looper.getMainLooper())
  private val dirFiles by lazy { getExternalFilesDir(null)!! }

  // estado de la app (se guarda en calib_proyector.json → "app")
  @Volatile private var modoFijo = false
  @Volatile private var poseFija: PoseT? = null
  @Volatile private var latenciaPrediccionMs = 60.0
  @Volatile private var prediccion = true
  private var panelDist = 1.0f
  private var panelDx = -0.032f      // ojo izquierdo ≈ −IPD/2
  private var panelDy = 0.0f
  private var panelSx = 1.0f
  private var panelSy = 1.0f
  private val panelAncho = 1.6f
  private val panelAlto = 0.9f
  @Volatile private var panelProyVisible = true
  @Volatile private var surfacePanelRecibida = false
  private var reintentoPanel = false
  private var vista = "mapeo"           // mapeo | negro | blanco | prueba
  private var geometria = "ambas"       // mruk | profundidad | ambas
  private var calib: Calibracion? = null
  private var mallaMruk: FloatArray? = null
  private var mallaProf: FloatArray? = null
  private var mallaVersion = 0
  private var resumenSala = "sin cargar"
  private var anclaSala: DoubleArray? = null
  private var cabezaEnSalaGuardada: DoubleArray? = null
  @Volatile private var raycasterOk = false
  private var raycasterEstado = "—"
  private var permisosListos = false
  @Volatile private var camFalloMsg: String? = null
  @Volatile private var sceneLista = false

  private var uiEntity: Entity? = null
  private var proyEntity: Entity? = null
  private var statusView: TextView? = null
  private var btnModo: Button? = null
  private var btnEfecto: Button? = null
  private var btnSalida: Button? = null
  private var btnProyectar: Button? = null
  private var ultimoLog = 0L

  // monitor HTTP (http://<casco>:8787/): estado, cámara, render, profundidad, malla, log
  private val servidor = ServidorMonitor(8787)
  @Volatile private var tieneFoco = false
  @Volatile private var resumida = false
  @Volatile private var anclasJson = JSONArray()
  class PoseControl(val tipo: String, val activo: Boolean, val t: DoubleArray, val q: DoubleArray, val botones: Int)
  @Volatile private var controles: Map<String, PoseControl> = emptyMap()
  @Volatile private var controlesTs = 0L

  // grilla de marcadores QR proyectada + tracking de QR de MRUK
  @Volatile private var layoutMarc: Marcadores.Layout? = null
  @Volatile private var imagenMarc: RendererProyector.Contenido.Imagen? = null
  @Volatile private var trackerQr = "apagado"
  @Volatile private var detecciones: List<Marcadores.Deteccion> = emptyList()
  @Volatile private var marcTasa = 0.0
  @Volatile private var marcResiduo: Double? = null
  @Volatile private var marcMensaje = "sin iniciar (comando 'marcadores')"
  @Volatile private var payloadsRaros: Set<String> = emptySet()

  // ArUco del proyector de la mesa (OpenCV sobre la cámara passthrough) + panel AR en su plano
  class AcumMarca(val id: Int, var esq: Array<DoubleArray>, var esqCam: DoubleArray, var tNs: Long, var vistas: Int,
                  var residuoMm: Double = 0.0, var descartada: Boolean = false)
  class ResAruco(val tsNs: Long, val wCam: Int, val hCam: Int, val marcas: List<ArucoMesa.Marca>, val esq3d: Map<Int, Array<DoubleArray?>>,
                 val acum: List<AcumMarca>, val plano: ArucoMesa.Plano?, val hUvAb: DoubleArray?, val residuoHmm: Double?,
                 val msDet: Long, val msTotal: Long, val camPos: DoubleArray, val panel: DoubleArray?)
  @Volatile private var arucoActivo = false
  @Volatile private var arucoMsg = "apagado (comando 'aruco')"
  @Volatile private var arucoRes: ResAruco? = null
  @Volatile private var arucoCuadros = 0L
  @Volatile private var arucoConDeteccion = 0L
  private val arucoHist = java.util.ArrayDeque<Pair<Long, Int>>()   // (ms, marcadores en el cuadro), últimos 10 s
  @Volatile private var arucoCfg = ArucoMesa.Config()
  @Volatile private var arucoZoom = 1.0                 // >1: recorta la zona de la grilla y la amplía (bicúbica)
  @Volatile private var arucoRoi: IntArray? = null      // zona de la grilla en px de cámara (de las detecciones)
  @Volatile private var arucoPar = false                // par patrón/invertido: detecta sobre el signo de la diferencia
  @Volatile private var arucoParUmbral = 8
  @Volatile private var arucoParDif = 0.0
  @Volatile private var arucoParMin = 3.0
  @Volatile private var arucoModoUltimo = "cuadro"
  @Volatile private var arucoDetectorVersion = 0
  @Volatile private var arucoVentanaMs = 2000L
  @Volatile private var arucoEv: Double? = -1.0
  @Volatile private var arucoExpMs = 0.0
  @Volatile private var arucoIso = 0
  @Volatile private var arucoExpMsg = ""
  @Volatile private var arucoReset = false
  @Volatile private var grillaAruco: ArucoMesa.Grilla? = null
  @Volatile private var ajusteMesa: ResultadoAjuste? = null
  @Volatile private var modeloMesa: ModeloProyector? = null
  @Volatile private var ajusteMesaTs = 0L
  @Volatile private var ajusteMesaPares = 0
  @Volatile private var ajusteMesaMarcadores = 0
  @Volatile private var mesaReflejada = false
  @Volatile private var mesaInfo = "sin calibrar"
  /** K conocida del proyector de la mesa (files/proyector_mesa_K.json o 'proyector K fx [cx cy]'): con un solo plano la focal no es observable. */
  @Volatile private var kMesa: DoubleArray? = null
  // pose FIJA del proyector de la mesa (files/calib_mesa.json con "pose_fija": true, la escribe la calibración del Mac):
  // no se re-estima; el ArUco en vivo solo verifica (residuo contra la pose fija) y avisa "rig movido"
  @Volatile private var mesaFija = false
  @Volatile private var mesaIncertidumbre: Any? = null
  @Volatile private var mesaFuente = "—"
  @Volatile private var calibMesaMtime = 0L
  @Volatile private var verifTs = 0L
  @Volatile private var verifResiduo: Double? = null
  @Volatile private var verifP90: Double? = null
  @Volatile private var verifN = 0
  @Volatile private var verifMalos = 0
  @Volatile private var rigMovido = false
  @Volatile private var umbralRigPx = 8.0
  @Volatile private var candidatosMesa: List<ArucoMesa.Candidato> = emptyList()
  @Volatile private var kMesaFuente = "—"
  @Volatile private var proyectorSel = "auto"          // auto | mesa | rig
  @Volatile private var proyectorAplicado = "rig"
  private var rigModoFijo = false
  private var rigPoseFija: PoseT? = null
  @Volatile private var panelVoltear = false
  private var arucoEntity: Entity? = null
  private var vistaAruco: VistaAruco? = null

  // profundidad en vivo (rejilla de raycasts del Environment Raycaster alineada con la cámara izquierda)
  class FrameDepth(val nx: Int, val ny: Int, val z: FloatArray, val pts: FloatArray, val idx: IntArray, val tNs: Long,
                   val ms: Long, val origen: DoubleArray, val marco: String)
  @Volatile private var depthFrame: FrameDepth? = null
  @Volatile private var depthHastaMs = 0L
  @Volatile private var depthNx = 40
  @Volatile private var depthNy = 40
  @Volatile private var depthHz = 0.0
  @Volatile private var depthMaxM = 4.0
  private var hiloDepth: Thread? = null

  // ----------------------------------------------------------------- ciclo de vida
  // body tracking (XR_META_body_tracking_full_body): cuerpo de QUIEN LLEVA EL CASCO, inferido por el sistema
  override fun registerRequiredOpenXRExtensions(): List<String> =
      listOf("XR_META_body_tracking_full_body", "XR_META_body_tracking_fidelity")

  class Cuerpo(val tNs: Long, val t: FloatArray, val q: FloatArray, val valido: BooleanArray, val padre: IntArray)
  @Volatile private var cuerpo: Cuerpo? = null
  @Volatile private var cuerpoMsg = "sin iniciar"
  private val jointPoses: MutableList<ControllerPose> = mutableListOf()
  private val skeletonPoses: MutableList<SkeletonJoint> = mutableListOf()
  private var cuerpoUltimoNs = 0L

  @OptIn(SpatialSDKExperimentalAPI::class)
  private fun iniciarCuerpo() {
    runCatching {
      scene.setBodyTrackingJointSet(JointSet.FULL_BODY)
      scene.setBodyTrackingFidelity(BodyTrackingFidelity.HIGH)
      cuerpoMsg = "body tracking FULL_BODY / HIGH pedido"
    }.onFailure { cuerpoMsg = "body tracking no disponible: ${it.message}"; Log.w(TAG, "[cuerpo] $cuerpoMsg") }
    Log.i(TAG, "[cuerpo] $cuerpoMsg")
  }

  /** ~15 Hz: articulaciones del cuerpo (mundo) y jerarquía del esqueleto. Hilo de los sistemas (ECS). */
  @OptIn(SpatialSDKExperimentalAPI::class)
  private fun leerCuerpo(ahora: Long) {
    if (ahora - cuerpoUltimoNs < 66_000_000L) return
    cuerpoUltimoNs = ahora
    val ok = runCatching { scene.updateBodyTrackingBuffersAtTime(jointPoses, skeletonPoses) }.getOrElse { cuerpoMsg = "error: ${it.message}"; false }
    if (!ok) { if (!cuerpoMsg.startsWith("error")) cuerpoMsg = "sin datos de body tracking (¿casco sin nadie puesto?)"; return }
    val n = jointPoses.size
    val t = FloatArray(n * 3); val q = FloatArray(n * 4); val v = BooleanArray(n); val padre = IntArray(n) { -1 }
    for (i in 0 until n) {
      val jp = jointPoses[i]; val p = jp.pose
      t[i * 3] = p.t.x; t[i * 3 + 1] = p.t.y; t[i * 3 + 2] = p.t.z
      q[i * 4] = p.q.w; q[i * 4 + 1] = p.q.x; q[i * 4 + 2] = p.q.y; q[i * 4 + 3] = p.q.z
      v[i] = (jp.flags and JointPose.ValidBits) != 0
    }
    for (sj in skeletonPoses) if (sj.jointIndex in 0 until n) padre[sj.jointIndex] = sj.parentJointIndex
    cuerpo = Cuerpo(ahora, t, q, v, padre)
    cuerpoMsg = "${v.count { it }}/$n articulaciones válidas"
  }

  private fun cuerpoJson(conNombres: Boolean): JSONObject {
    val c = cuerpo ?: return JSONObject().put("estado", cuerpoMsg).put("n", 0)
    val n = c.valido.size
    val j = JSONObject().put("estado", cuerpoMsg).put("n", n).put("validas", c.valido.count { it })
        .put("edad_ms", (SystemClock.elapsedRealtimeNanos() - c.tNs) / 1e6)
        .put("t", JSONArray(c.t.map { Math.round(it * 1000) / 1000.0 })).put("valido", JSONArray(c.valido.toList())).put("padre", JSONArray(c.padre.toList()))
        .put("nota", "XR_META_body_tracking: es el cuerpo de quien lleva puesto el casco (inferido); no sigue a otras personas que vea la cámara")
    if (conNombres) j.put("q", JSONArray(c.q.map { Math.round(it * 10000) / 10000.0 })).put("nombres", JSONArray((0 until n).map { NOMBRES_ARTICULACION.getOrElse(it) { "J$it" } }))
    return j
  }

  override fun registerFeatures(): List<SpatialFeature> {
    mruk = MRUKFeature(this, systemManager)
    return listOf(VRFeature(this), mruk)
  }

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    systemManager.findSystem<LocomotionSystem>().enableLocomotion(false)
    Log.i(TAG, "onCreate: Proyector v0.1 (Spatial SDK 0.13.2)")
    renderer.vista = { vistaRenderer() }
    renderer.iniciar()
    salida = SalidaProyector(this, renderer)
    salida.onCambio = { a -> main.post { aplicarPoliticaSalida(a) } }
    salida.iniciar()
    calibrador = Calibrador(renderer, { camera }, mruk, historial, dirFiles, { raycasterOk }) { r -> main.post { calibracionTerminada(r) } }
    systemManager.registerSystem(SistemaCabeza())
    systemManager.registerSystem(SistemaMarcadores())

    registerReceiver(receptor, IntentFilter(ACCION), Context.RECEIVER_EXPORTED)
    configurarMonitor()
    servidor.iniciar()

    val faltan = listOf(PERM_SCENE, PERM_HEADSET_CAM, PERM_CAMERA).filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
    if (faltan.isNotEmpty()) {
      Log.i(TAG, "pidiendo permisos: $faltan")
      requestPermissions(faltan.toTypedArray(), REQ_PERMS)
    } else permisosOk()
    main.postDelayed(tick, 500)
  }

  override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
    super.onRequestPermissionsResult(requestCode, permissions, grantResults)
    if (requestCode == REQ_PERMS) {
      Log.i(TAG, "permisos: " + permissions.zip(grantResults.toList()).joinToString { "${it.first}=${it.second == 0}" })
      permisosOk()
    }
  }

  private fun permisosOk() {
    if (permisosListos) return
    permisosListos = true
    if (checkSelfPermission(PERM_SCENE) == PackageManager.PERMISSION_GRANTED) {
      val r = runCatching { mruk.startEnvironmentRaycaster() }.getOrNull()
      raycasterOk = r == MRUKStartEnvironmentRaycasterResult.SUCCESS
      raycasterEstado = r?.toString() ?: "error"
      Log.i(TAG, "environmentRaycaster: $r")
      cargarSala()
    } else {
      raycasterEstado = "sin permiso USE_SCENE"
      resumenSala = "sin permiso USE_SCENE (aceptar el diálogo en el casco o adb pm grant)"
      Log.w(TAG, resumenSala)
    }
    // calibración guardada
    val f = File(dirFiles, ARCHIVO)
    if (f.exists()) runCatching { cargar() }.onFailure { Log.e(TAG, "no se pudo cargar $ARCHIVO", it) }
  }

  override fun onResume() {
    super.onResume()
    resumida = true
    servidor.iniciar()
    // la cámara se abre en foreground; el proc state tarda en actualizarse (como Metraje)
    main.postDelayed({ iniciarCamara() }, 1500L)
  }

  override fun onPause() {
    resumida = false
    super.onPause()
  }

  /** Sin foco (p. ej. otra app o el menú del sistema al frente) la cámara da CAMERA_DISABLED: al volver el foco se reintenta. */
  override fun onWindowFocusChanged(hasFocus: Boolean) {
    super.onWindowFocusChanged(hasFocus)
    tieneFoco = hasFocus
    Log.i(TAG, "[app] foco ${if (hasFocus) "ganado" else "perdido"}")
    val c = camera
    if (hasFocus && (c == null || c.failed)) main.postDelayed({ iniciarCamara() }, 1000L)
  }

  private fun iniciarCamara() {
    if (camera != null && !camera!!.failed) return
    camera?.stop(); camera = null
    if (checkSelfPermission(PERM_HEADSET_CAM) != PackageManager.PERMISSION_GRANTED) {
      Log.w(TAG, "[cam] sin permiso HEADSET_CAMERA")
      return
    }
    camera = ScanCamera(this).also { c -> runCatching { c.start() }.onFailure { Log.e(TAG, "[cam] start", it); camera = null; camFalloMsg = it.message ?: it.toString() } }
  }

  override fun onSpatialShutdown() {
    runCatching { unregisterReceiver(receptor) }
    servidor.detener()
    calibrador.cancelar()
    camera?.stop()
    salida.detener()
    renderer.detener()
    super.onSpatialShutdown()
  }

  // ----------------------------------------------------------------- escena
  override fun onSceneReady() {
    super.onSceneReady()
    scene.enablePassthrough(true)
    iniciarCuerpo()
    scene.setLightingEnvironment(ambientColor = Vector3(0.3f), sunColor = Vector3(1f), sunDirection = -Vector3(1f, 3f, -2f), environmentIntensity = 0.3f)
    uiEntity = Entity.createPanelEntity(R.layout.ui_proyector,
        Transform(Pose(Vector3(0.45f, 1.2f, 0.9f), Quaternion(0f, 180f, 0f))), Visible(true), Grabbable())
    proyEntity = Entity.create(listOf(Panel(R.id.panel_proyector), Transform(Pose(Vector3(0f, 1.5f, 1f))), Visible(true),
        Scale(Vector3(1f, 1f, 1f))))
    sceneLista = true
    aplicarPoliticaSalida(salida.activa)
    Log.i(TAG, "escena lista; displays de presentación: ${salida.displaysInfo()}")
  }

  override fun registerPanels(): List<PanelRegistration> = listOf(
      // panel AR sobre el plano de los ArUco de la mesa (1,6 × 0,9 de base; se escala al tamaño real)
      PanelRegistration(R.layout.ui_aruco) {
        config {
          height = 0.9f
          width = 1.6f
          fractionOfScreen = 1f
          layoutDpi = 160
          layerConfig = LayerConfig()
          enableTransparent = true
        }
        panel { vistaAruco = rootView?.findViewById(R.id.vista_aruco) }
      },
      PanelRegistration(R.layout.ui_proyector) {
        config {
          height = 0.70f
          width = 0.55f
          fractionOfScreen = 1f
          layoutDpi = 480
          layerConfig = LayerConfig()
          enableTransparent = true
        }
        panel {
          val root = requireNotNull(rootView)
          statusView = root.findViewById(R.id.status)
          btnModo = root.findViewById(R.id.btn_modo)
          btnEfecto = root.findViewById(R.id.btn_efecto)
          btnSalida = root.findViewById(R.id.btn_salida)
          btnProyectar = root.findViewById(R.id.btn_proyectar)
          root.findViewById<Button>(R.id.btn_calibrar).setOnClickListener { comando("calibrar") }
          root.findViewById<Button>(R.id.btn_cancelar).setOnClickListener { comando("cancelar") }
          root.findViewById<Button>(R.id.btn_verificar).setOnClickListener { comando("verificar") }
          root.findViewById<Button>(R.id.btn_reiniciar).setOnClickListener { comando("reiniciar") }
          btnModo?.setOnClickListener { comando(if (modoFijo) "modo cabeza" else "modo fijo") }
          btnEfecto?.setOnClickListener { comando("efecto siguiente") }
          btnSalida?.setOnClickListener { comando("salida siguiente") }
          btnProyectar?.setOnClickListener { comando("ver siguiente") }
          root.findViewById<Button>(R.id.btn_guardar).setOnClickListener { comando("guardar") }
          root.findViewById<Button>(R.id.btn_cargar).setOnClickListener { comando("cargar") }
          root.findViewById<Button>(R.id.btn_profundidad).setOnClickListener { comando("profundidad") }
          root.findViewById<Button>(R.id.btn_sala).setOnClickListener { comando("sala") }
          root.findViewById<Button>(R.id.btn_escanear).setOnClickListener { comando("escanear") }
          root.findViewById<Button>(R.id.btn_marcadores).setOnClickListener { comando(if (vista == "marcadores") "ver mapeo" else "marcadores") }
          refrescarUi()
        }
      },
      // salida B: panel de superficie (layer del compositor) que se mueve con la cabeza
      VideoSurfacePanelRegistration(
          R.id.panel_proyector,
          surfaceConsumer = { _, surface -> surfacePanelRecibida = true; salida.setPanelSurface(surface, 1920, 1080) },
          settingsCreator = {
            MediaPanelSettings(
                shape = QuadShapeOptions(width = panelAncho, height = panelAlto),
                display = PixelDisplayOptions(width = 1920, height = 1080),
                rendering = MediaPanelRenderOptions(zIndex = 10),
            )
          },
      ),
  )

  /**
   * Salida B: lo que ve el ojo izquierdo va a la pared, así que se apaga el passthrough (si no,
   * la imagen de la cámara se re-proyecta) y se oculta la UI (Menú del control la muestra).
   */
  private fun aplicarPoliticaSalida(a: String) {
    Log.i(TAG, "[salida] → $a")
    if (!sceneLista) return
    val b = a.startsWith("panel")
    runCatching { scene.enablePassthrough(!b) }
    uiEntity?.setComponent(Visible(!b))
    if (b) panelProyVisible = true
    refrescarUi()
  }

  private fun cargarSala() {
    resumenSala = "cargando salas..."
    mruk.loadSceneFromDevice().whenComplete { result: MRUKLoadDeviceResult, err ->
      main.post {
        when (result) {
          MRUKLoadDeviceResult.SUCCESS -> {
            val g = runCatching { GeometriaSala.desdeMruk(mruk) }.onFailure { Log.e(TAG, "[sala] geometría", it) }.getOrNull()
            if (g != null) {
              mallaMruk = g.verts; anclaSala = g.anclaSala
              anclasJson = runCatching { anclasDeMruk() }.getOrElse { JSONArray() }
              resumenSala = g.resumen
              reconstruirMalla()
              reanclarPoseFija()
            }
            Log.i(TAG, "[sala] cargada: $resumenSala")
            cargarCalibMesa(forzar = true)
          }
          MRUKLoadDeviceResult.ERROR_NO_ROOMS_FOUND -> {
            resumenSala = "SIN SPACE SETUP: pulsa SPACE SETUP (o 'sala' por adb) o usa la malla de profundidad"
            Log.w(TAG, "[sala] $resumenSala")
            cargarCalibMesa(forzar = true)
          }
          else -> { resumenSala = "error cargando salas: $result ${err?.message ?: ""}"; Log.w(TAG, "[sala] $resumenSala") }
        }
      }
    }
  }

  /** Con la sala ya cargada, la pose fija guardada (relativa al ancla) vuelve al mundo de esta sesión. */
  private fun reanclarPoseFija() {
    val cen = cabezaEnSalaGuardada ?: return
    val ancla = anclaSala ?: return
    val m = Mat.mul44(ancla, cen)
    val q = Mat.mat3ToQuat(doubleArrayOf(m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]))
    poseFija = PoseT(0, doubleArrayOf(m[3], m[7], m[11]), q)
    Log.i(TAG, "[sala] pose fija re-anclada a la sala: t=(%.3f, %.3f, %.3f)".format(m[3], m[7], m[11]))
  }

  private fun reconstruirMalla() {
    val partes = ArrayList<FloatArray>()
    if (geometria != "profundidad") mallaMruk?.let { partes.add(it) }
    if (geometria != "mruk") mallaProf?.let { partes.add(it) }
    val total = partes.sumOf { it.size }
    val v = FloatArray(total)
    var o = 0
    for (p in partes) { p.copyInto(v, o); o += p.size }
    renderer.malla = RendererProyector.Malla(v, ++mallaVersion)
    Log.i(TAG, "[sala] malla del render: ${v.size / RendererProyector.FLOATS_VERT} vértices (geometría=$geometria)")
  }

  // ----------------------------------------------------------------- pose y modos
  private fun poseActual(): PoseT? = historial.ultima()

  /** 4x4 cabeza ← mundo para el render de este cuadro. */
  private fun vistaRenderer(): DoubleArray? {
    if (!::calibrador.isInitialized) return null
    val p = when {
      calibrador.calibrando -> historial.ultima()
      modoFijo && poseFija != null -> poseFija
      prediccion -> historial.predecir(SystemClock.elapsedRealtimeNanos() + (latenciaPrediccionMs * 1e6).toLong())
      else -> historial.ultima()
    } ?: return null
    val m = renderer.modelo
    if (m != null) {
      val w = Mat.xformPt(p.mat4(), m.centro[0], m.centro[1], m.centro[2])
      renderer.origenProyector = floatArrayOf(w[0].toFloat(), w[1].toFloat(), w[2].toFloat())
    }
    return Mat.invRigid(p.mat4())
  }

  /** Sistema ECS: historial de pose, panel fijo a la cabeza y botones del control. */
  inner class SistemaCabeza : SystemBase() {
    private var ultimoBotones = 0L
    private var visibleAntes: Boolean? = null

    override fun execute() {
      val p = runCatching { getScene().getViewerPose() }.getOrNull() ?: return
      val ahora = SystemClock.elapsedRealtimeNanos()
      if (sceneLista) leerCuerpo(ahora)
      historial.agregar(PoseT(ahora, doubleArrayOf(p.t.x.toDouble(), p.t.y.toDouble(), p.t.z.toDouble()),
          doubleArrayOf(p.q.w.toDouble(), p.q.x.toDouble(), p.q.y.toDouble(), p.q.z.toDouble())))
      proyEntity?.let { e ->
        // visible mientras se usa como salida B (y hasta que el SDK entregue su Surface)
        val ver = (salida.usandoPanel || !surfacePanelRecibida) && panelProyVisible
        if (ver) {
          val pose = p.times(Pose(Vector3(panelDx, panelDy, panelDist), Quaternion(1f, 0f, 0f, 0f)))
          e.setComponent(Transform(pose))
          e.setComponent(Scale(Vector3(panelSx, panelSy, 1f)))
        }
        if (visibleAntes != ver) { e.setComponent(Visible(ver)); visibleAntes = ver }
      }
      if (ahora - ultimoBotones > 20_000_000L) { ultimoBotones = ahora; botones() }
    }

    private fun botones() {
      val avatar = runCatching {
        Query.where { has(AvatarBody.id) }.eval().firstOrNull { it.isLocal() && it.getComponent<AvatarBody>().isPlayerControlled }
      }.getOrNull() ?: return
      val body = avatar.getComponent<AvatarBody>()
      val der = body.rightHand.tryGetComponent<Controller>()
      val izq = body.leftHand.tryGetComponent<Controller>()
      runCatching {
        val m = HashMap<String, PoseControl>()
        for ((k, e, c) in listOf(Triple("izq", body.leftHand, izq), Triple("der", body.rightHand, der))) {
          val p = e.tryGetComponent<Transform>()?.transform ?: continue
          m[k] = PoseControl(c?.type?.name ?: "?", c?.isActive ?: false,
              doubleArrayOf(p.t.x.toDouble(), p.t.y.toDouble(), p.t.z.toDouble()),
              doubleArrayOf(p.q.w.toDouble(), p.q.x.toDouble(), p.q.y.toDouble(), p.q.z.toDouble()), c?.buttonState ?: 0)
        }
        controles = m; controlesTs = SystemClock.elapsedRealtimeNanos()
      }
      fun Controller.pres(b: Int) = (buttonState and changedButtons and b) != 0
      fun Controller.abajo(b: Int) = (buttonState and b) != 0
      der?.let { c ->
        if (c.pres(ButtonBits.ButtonA)) comando("calibrar")
        if (c.pres(ButtonBits.ButtonB)) comando(if (modoFijo) "modo cabeza" else "modo fijo")
        val gat = c.abajo(ButtonBits.ButtonTriggerR)
        if (c.pres(ButtonBits.ButtonThumbRL)) comando(if (gat) "ajuste 0 0 -0.2 1" else "ajuste -2 0 0 1")
        if (c.pres(ButtonBits.ButtonThumbRR)) comando(if (gat) "ajuste 0 0 0.2 1" else "ajuste 2 0 0 1")
        if (c.pres(ButtonBits.ButtonThumbRU)) comando(if (gat) "ajuste 0 0 0 1.002" else "ajuste 0 -2 0 1")
        if (c.pres(ButtonBits.ButtonThumbRD)) comando(if (gat) "ajuste 0 0 0 0.998" else "ajuste 0 2 0 1")
      }
      izq?.let { c ->
        if (c.pres(ButtonBits.ButtonX)) comando("efecto siguiente")
        if (c.pres(ButtonBits.ButtonY)) comando("guardar")
        if (c.pres(ButtonBits.ButtonMenu)) comando("ui alternar")
      }
    }
  }

  // ----------------------------------------------------------------- comandos (UI, control, adb)
  private val receptor = object : BroadcastReceiver() {
    override fun onReceive(context: Context?, intent: Intent?) {
      val c = intent?.getStringExtra("c") ?: intent?.getStringExtra("cmd") ?: return
      comando(c)
    }
  }

  fun comando(linea: String): String {
    val r = runCatching { ejecutar(linea.trim()) }.getOrElse { "error: ${it.message}" }
    Log.i(TAG, "[cmd] '$linea' → $r")
    refrescarUi()
    return r
  }

  private fun ejecutar(linea: String): String {
    val t = linea.split(Regex("\\s+"))
    val a = t.getOrNull(1)
    fun num(i: Int, def: Double) = t.getOrNull(i)?.toDoubleOrNull() ?: def
    return when (t[0].lowercase()) {
      "estado" -> estadoTexto().replace('\n', ' ')
      "calibrar" -> {
        if (calibrador.ocupado()) "ocupado: ${calibrador.mensaje}" else {
          mostrarVista("negro"); calibrador.iniciar(); "calibrando (vista ${calibrador.vistas.size + 1})"
        }
      }
      "verificar" -> { calibrador.iniciar(soloVerificar = true); "verificando lazo" }
      "cancelar" -> { calibrador.cancelar(); "cancelando" }
      "reiniciar" -> { calibrador.reiniciarVistas(); "vistas borradas" }
      "modo" -> {
        when (a) {
          "fijo" -> {
            modoFijo = true
            poseFija = calib?.poseCabeza ?: poseActual()
            "modo fijo (pose de ${if (calib != null) "la calibración" else "ahora"})"
          }
          "fijar" -> { modoFijo = true; poseFija = poseActual(); "modo fijo con la pose actual" }
          else -> { modoFijo = false; "modo cabeza" }
        }
      }
      "efecto" -> {
        val n = RendererProyector.NOMBRES_EFECTO
        renderer.efecto = when (a) {
          null, "siguiente" -> (renderer.efecto + 1) % n.size
          else -> n.indexOf(a).takeIf { it >= 0 } ?: a.toIntOrNull()?.coerceIn(0, n.size - 1) ?: renderer.efecto
        }
        mostrarVista("mapeo")
        "efecto ${n[renderer.efecto]}"
      }
      "ver", "proyectar" -> {
        val ops = listOf("mapeo", "negro", "blanco", "prueba", "marcadores")
        val v = if (a == null || a == "siguiente") ops[(ops.indexOf(vista) + 1) % ops.size] else a
        if (v == "gray") {
          renderer.mostrar(RendererProyector.Contenido.Gray(num(2, 0.0).toInt(), num(3, 9.0).toInt(), num(4, 0.0) > 0))
          vista = "gray"; "gray ${t.drop(2)}"
        } else { mostrarVista(v); "viendo $v" }
      }
      "salida" -> {
        val ps = SalidaProyector.Preferencia.values()
        salida.preferencia = when (a) {
          "auto" -> SalidaProyector.Preferencia.AUTO
          "presentacion", "hdmi" -> SalidaProyector.Preferencia.PRESENTACION
          "panel" -> SalidaProyector.Preferencia.PANEL
          "ninguna" -> SalidaProyector.Preferencia.NINGUNA
          else -> ps[(salida.preferencia.ordinal + 1) % ps.size]
        }
        "salida ${salida.preferencia} (activa: ${salida.activa}; displays: ${salida.displaysInfo()})"
      }
      "guardar" -> guardar()
      "cargar" -> cargar()
      "ajuste" -> {
        if (a == "reset") { renderer.ajuste = RendererProyector.Ajuste2D(); "ajuste manual a cero" } else {
          val j = renderer.ajuste
          renderer.ajuste = RendererProyector.Ajuste2D(j.dx + num(1, 0.0), j.dy + num(2, 0.0), j.rotDeg + num(3, 0.0), j.escala * num(4, 1.0))
          mostrarVista("mapeo")
          val n = renderer.ajuste
          "ajuste dx=%.1f dy=%.1f rot=%.2f° esc=%.4f".format(n.dx, n.dy, n.rotDeg, n.escala)
        }
      }
      "latencia" -> { latenciaPrediccionMs = num(1, latenciaPrediccionMs); "latencia de predicción ${latenciaPrediccionMs} ms" }
      "prediccion" -> { prediccion = a != "off"; "predicción ${if (prediccion) "on" else "off"}" }
      "panel" -> {
        panelDist = num(1, panelDist.toDouble()).toFloat(); panelDx = num(2, panelDx.toDouble()).toFloat()
        panelDy = num(3, panelDy.toDouble()).toFloat(); panelSx = num(4, panelSx.toDouble()).toFloat(); panelSy = num(5, panelSy.toDouble()).toFloat()
        "panel dist=$panelDist dx=$panelDx dy=$panelDy sx=$panelSx sy=$panelSy (cubre %.0f°×%.0f°)".format(
            Math.toDegrees(2 * Math.atan(panelAncho * panelSx / 2.0 / panelDist)), Math.toDegrees(2 * Math.atan(panelAlto * panelSy / 2.0 / panelDist)))
      }
      "ui" -> {
        val ver = when (a) { "on" -> true; "off" -> false; else -> !(uiEntity?.tryGetComponent<Visible>()?.isVisible ?: true) }
        uiEntity?.setComponent(Visible(ver))
        if (a == "alternar" || a == null) panelProyVisible = !ver   // vista de operador: con la UI visible se oculta la salida B
        "ui ${if (ver) "visible" else "oculta"}, panel proyector ${if (panelProyVisible) "visible" else "oculto"}"
      }
      "panelproy" -> { panelProyVisible = a != "off"; "panel proyector ${if (panelProyVisible) "visible" else "oculto"}" }
      "passthrough" -> { scene.enablePassthrough(a != "off"); "passthrough ${a ?: "on"}" }
      "geometria" -> { geometria = a ?: "ambas"; reconstruirMalla(); "geometría $geometria" }
      "profundidad" -> escanearProfundidad(num(1, 64.0).toInt(), num(2, 36.0).toInt())
      "sala", "escanear" -> {
        // Space Setup del sistema (MRUK requestSceneCapture, como el MrukSample); al volver se recargan malla y anclas
        Log.i(TAG, "[sala] pidiendo Space Setup (requestSceneCapture)")
        mruk.requestSceneCapture().whenComplete { r, err ->
          Log.i(TAG, "[sala] Space Setup terminó: $r ${err?.message ?: ""}")
          main.post { cargarSala() }
        }
        "abriendo Space Setup del sistema (escaneo de la pieza; se hace con el casco puesto). Al volver se recargan malla y anclas"
      }
      "recargar_sala" -> { cargarSala(); "recargando salas" }
      "umbral" -> { calibrador.umbral = num(1, 6.0).toInt(); "umbral gray ${calibrador.umbral}" }
      "bits_omitidos" -> { calibrador.bitsOmitidos = num(1, 1.0).toInt().coerceIn(0, 5); "bits omitidos ${calibrador.bitsOmitidos}" }
      "promedio" -> { calibrador.cuadrosPromedio = num(1, 2.0).toInt().coerceIn(1, 8); "promedio ${calibrador.cuadrosPromedio} cuadros" }
      "latencia_min" -> { calibrador.latenciaMinMs = num(1, 120.0); "latencia mínima de captura ${calibrador.latenciaMinMs} ms" }
      "lazo_iter" -> { calibrador.iteracionesLazo = num(1, 3.0).toInt(); "iteraciones del lazo ${calibrador.iteracionesLazo}" }
      "raycast" -> { calibrador.preferirSala = a == "sala"; "raycast de calibración contra ${if (calibrador.preferirSala) "la sala MRUK" else "la profundidad del entorno"}" }
      "rejilla" -> { renderer.usarRejilla = a != "off"; "rejilla ${if (renderer.usarRejilla) "on" else "off"}" }
      "captura_salida" -> { capturarSalida(a ?: "manual"); "capturando la salida" }
      "autoprueba" -> { autoprueba(); "autoprueba de render en curso (ver files/prueba_*.png)" }
      "camara_debug" -> camaraDebug()
      "camara" -> { camera?.stop(); camera = null; iniciarCamara(); "reabriendo la cámara (foco=${tieneFoco})" }
      "marcadores" -> comandoMarcadores(t.drop(1))
      "aruco" -> comandoAruco(t.drop(1))
      "ae_lock" -> {
        val cam = camera ?: return "sin cámara"
        val lock = a != "off"
        val comp = t.getOrNull(2)?.replace(',', '.')?.toDoubleOrNull()
        cam.setExposicion(if (lock) comp ?: 0.0 else 0.0, lock) +
            (if (!cam.aeLockDisponible) " · OJO: esta cámara (passthrough Quest 3) no ofrece AE lock (CONTROL_AE_LOCK_AVAILABLE=false)" else "") +
            (if (cam.evRango.let { it == null || (it.lower == 0 && it.upper == 0) }) " ni compensación (rango [0,0]); exposición siempre automática" else "")
      }
      "proyector" -> comandoProyector(a, t)
      "depth" -> { depthNx = num(1, 40.0).toInt().coerceIn(4, 128); depthNy = num(2, depthNx.toDouble()).toInt().coerceIn(4, 128); if (t.size > 3) depthMaxM = num(3, 4.0); asegurarDepth(); "profundidad en vivo ${depthNx}x$depthNy, escala 0,2–$depthMaxM m" }
      "modelo_prueba" -> {
        // modelo virtual alineado con la cabeza (sin calibrar), para probar el render del mapeo
        val m = modeloPorDefecto(num(1, 1400.0))
        calib = Calibracion(m, null, RejillaCorreccion(), poseActual() ?: PoseT(0, doubleArrayOf(0.0, 0.0, 0.0), doubleArrayOf(1.0, 0.0, 0.0, 0.0)),
            null, null, null, 0, "", salida.activa, tipo = "prueba_sin_calibrar")
        renderer.modelo = m; renderer.rejilla = RejillaCorreccion(); renderer.rejillaVersion++
        "modelo de prueba f=${m.fx}"
      }
      else -> "comando desconocido. Ver README (estado, calibrar, verificar, modo, efecto, ver, salida, guardar, cargar, ajuste, latencia, panel, ui, profundidad, sala, camara_debug, autoprueba)"
    }
  }

  private fun mostrarVista(v: String) {
    vista = v
    renderer.mostrar(when (v) {
      "negro" -> RendererProyector.Contenido.Negro
      "blanco" -> RendererProyector.Contenido.Blanco
      "prueba" -> RendererProyector.Contenido.Prueba
      "marcadores" -> imagenMarc ?: run { armarMarcadores(4, 3, 0); imagenMarc!! }
      else -> { vista = "mapeo"; RendererProyector.Contenido.Mapeo }
    })
  }

  private fun modeloPorDefecto(f: Double): ModeloProyector {
    // K [diag(1,-1,1) | 0]: cámara virtual en el centro de la cabeza mirando hacia +z
    val p = doubleArrayOf(f, 0.0, 960.0, 0.0, 0.0, -f, 540.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    return ModeloProyector(p, 1920, 1080)
  }

  private fun calibracionTerminada(r: Calibracion?) {
    val noVe = calibrador.mensaje.contains("no ve la proyección")
    if (r == null && noVe && salida.activa.startsWith("presentacion") && salida.preferencia == SalidaProyector.Preferencia.AUTO && !reintentoPanel) {
      reintentoPanel = true
      Log.w(TAG, "[cal] con la Presentation la cámara no ve nada: el HDMI parece seguir mostrando el espejo. Reintento con la salida B (panel)")
      salida.preferencia = SalidaProyector.Preferencia.PANEL
      main.postDelayed({ comando("calibrar") }, 1500)
      return
    }
    reintentoPanel = false
    if (r != null) {
      calib = r
      renderer.modelo = r.modelo
      renderer.rejilla = r.rejilla; renderer.rejillaVersion++
      if (modoFijo) poseFija = r.poseCabeza
      runCatching { guardar() }.onFailure { Log.e(TAG, "guardar", it) }
      mostrarVista("mapeo")
    } else mostrarVista(vista)
    refrescarUi()
  }

  // ----------------------------------------------------------------- guardar / cargar
  private fun appJson(): JSONObject {
    val j = JSONObject()
    j.put("modo", if (modoFijo) "fijo" else "cabeza").put("latencia_prediccion_ms", latenciaPrediccionMs).put("prediccion", prediccion)
    j.put("panel", JSONObject().put("dist", panelDist.toDouble()).put("dx", panelDx.toDouble()).put("dy", panelDy.toDouble())
        .put("sx", panelSx.toDouble()).put("sy", panelSy.toDouble()).put("ancho", panelAncho.toDouble()).put("alto", panelAlto.toDouble()))
    val a = renderer.ajuste
    j.put("ajuste_manual", JSONObject().put("dx", a.dx).put("dy", a.dy).put("rot_deg", a.rotDeg).put("escala", a.escala))
    j.put("efecto", RendererProyector.NOMBRES_EFECTO[renderer.efecto]).put("geometria", geometria).put("salida_preferida", salida.preferencia.name)
    poseFija?.let { j.put("pose_fija_mundo", CalibIO.poseJson(it)) }
    val ancla = anclaSala
    val pf = poseFija ?: calib?.poseCabeza
    if (ancla != null && pf != null) {
      // cabeza (de referencia) en el marco del ancla de la sala: sobrevive entre sesiones
      j.put("ancla_sala_mundo", JSONArray(ancla.toList()))
      j.put("cabeza_en_sala", JSONArray(Mat.mul44(Mat.invRigid(ancla), pf.mat4()).toList()))
    }
    return j
  }

  private fun guardar(): String {
    val c = calib ?: return "no hay calibración que guardar"
    val f = File(dirFiles, ARCHIVO)
    CalibIO.guardar(f, c, appJson())
    if (c.dir.isNotEmpty()) runCatching { f.copyTo(File(File(dirFiles, c.dir), ARCHIVO), overwrite = true) }
    return "guardado ${f.absolutePath}"
  }

  private fun cargar(): String {
    val f = File(dirFiles, ARCHIVO)
    if (!f.exists()) return "no existe ${f.absolutePath}"
    val cg = CalibIO.cargar(f)
    calib = cg.cal
    renderer.modelo = cg.cal.modelo
    renderer.rejilla = cg.cal.rejilla; renderer.rejillaVersion++
    val app = cg.app
    latenciaPrediccionMs = app.optDouble("latencia_prediccion_ms", latenciaPrediccionMs)
    prediccion = app.optBoolean("prediccion", true)
    app.optJSONObject("panel")?.let { p ->
      panelDist = p.optDouble("dist", 1.0).toFloat(); panelDx = p.optDouble("dx", -0.032).toFloat(); panelDy = p.optDouble("dy", 0.0).toFloat()
      panelSx = p.optDouble("sx", 1.0).toFloat(); panelSy = p.optDouble("sy", 1.0).toFloat()
    }
    app.optJSONObject("ajuste_manual")?.let { m ->
      renderer.ajuste = RendererProyector.Ajuste2D(m.optDouble("dx"), m.optDouble("dy"), m.optDouble("rot_deg"), m.optDouble("escala", 1.0))
    }
    RendererProyector.NOMBRES_EFECTO.indexOf(app.optString("efecto")).takeIf { it >= 0 }?.let { renderer.efecto = it }
    geometria = app.optString("geometria", geometria)
    modoFijo = app.optString("modo") == "fijo"
    // pose fija: si hay ancla de sala ahora y guardada, se re-ancla; si no, la del mundo guardada
    var nota = ""
    val cen = app.optJSONArray("cabeza_en_sala")
    cabezaEnSalaGuardada = cen?.let { a -> DoubleArray(16) { a.getDouble(it) } }
    val ancla = anclaSala
    poseFija = if (cen != null && ancla != null) {
      val m = Mat.mul44(ancla, DoubleArray(16) { cen.getDouble(it) })
      val q = Mat.mat3ToQuat(doubleArrayOf(m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]))
      nota = " (pose fija re-anclada a la sala)"
      PoseT(0, doubleArrayOf(m[3], m[7], m[11]), q)
    } else app.optJSONObject("pose_fija_mundo")?.let { CalibIO.poseDe(it) } ?: cg.cal.poseCabeza
    if (modoFijo && cen != null && ancla == null) nota = " (OJO: sala aún no cargada; pose fija en coordenadas de mundo guardadas)"
    reconstruirMalla()
    return "cargado: ${cg.cal.tipo}, gray ${cg.cal.grayMedianaPx?.let { "%.2f".format(it) }} px, lazo ${cg.cal.lazoMedianaPx?.let { "%.2f".format(it) }} px$nota"
  }

  // ----------------------------------------------------------------- malla de profundidad
  private fun escanearProfundidad(nx: Int, ny: Int): String {
    if (!raycasterOk) return "Environment Raycaster no disponible ($raycasterEstado)"
    val pose = (if (modoFijo) poseFija else null) ?: poseActual() ?: return "sin pose"
    val m = calib?.modelo ?: modeloPorDefecto(1400.0)
    val mw = pose.mat4()
    val c = Mat.xformPt(mw, m.centro[0], m.centro[1], m.centro[2])
    val rayos = Array(nx * ny) { k ->
      val i = k % nx; val j = k / nx
      val d = m.rayo((i + 0.5) / nx * m.ancho, (j + 0.5) / ny * m.alto)
      Mat.xformDir(mw, d[0], d[1], d[2])
    }
    val esc = GeometriaSala.EscaneoProfundidad(mruk, c, rayos, nx, ny)
    val t0 = SystemClock.elapsedRealtime()
    val paso = object : Runnable {
      override fun run() {
        if (esc.paso(400)) {
          mallaProf = esc.malla()
          Log.i(TAG, "[prof] ${esc.aciertos}/${nx * ny} aciertos en ${SystemClock.elapsedRealtime() - t0} ms → ${mallaProf!!.size / RendererProyector.FLOATS_VERT} vértices")
          reconstruirMalla()
          refrescarUi()
        } else main.post(this)
      }
    }
    main.post(paso)
    return "escaneando profundidad ${nx}x$ny desde el ${if (calib != null) "proyector calibrado" else "centro de la cabeza (sin calibrar)"}"
  }

  // ----------------------------------------------------------------- pruebas y depuración
  private fun capturarSalida(nombre: String) {
    renderer.capturar { bmp ->
      if (bmp == null) Log.w(TAG, "[prueba] captura vacía") else {
        val f = File(dirFiles, "prueba_$nombre.png")
        CalibIO.guardarBitmap(f, bmp, png = true)
        Log.i(TAG, "[prueba] salida guardada ${f.absolutePath}")
      }
    }
  }

  /** Renderiza cada contenido y guarda lo que va al proyector (textura del FBO) como PNG. */
  private fun autoprueba() {
    val pasos = listOf<Pair<String, () -> Unit>>(
        "prueba" to { renderer.mostrar(RendererProyector.Contenido.Prueba) },
        "gray_x9" to { renderer.mostrar(RendererProyector.Contenido.Gray(0, 9, false)) },
        "gray_x1" to { renderer.mostrar(RendererProyector.Contenido.Gray(0, 1, false)) },
        "gray_y3i" to { renderer.mostrar(RendererProyector.Contenido.Gray(1, 3, true)) },
        "mapeo" to {
          if (calib == null) ejecutar("modelo_prueba")
          renderer.mostrar(RendererProyector.Contenido.Mapeo)
        },
    )
    var k = 0
    val vistaAntes = vista
    val r = object : Runnable {
      override fun run() {
        if (k >= pasos.size) { mostrarVista(vistaAntes); Log.i(TAG, "[prueba] autoprueba terminada"); return }
        val (n, f) = pasos[k++]
        f()
        main.postDelayed({ capturarSalida(n); main.postDelayed(this, 400) }, 300)
      }
    }
    main.post(r)
  }

  /**
   * Chequeo independiente de la extrínseca cámara↔cabeza: puntos de profundidad (raycast desde
   * el CENTRO de la cabeza, no desde la cámara) proyectados en el cuadro de la cámara con la
   * extrínseca. Si la extrínseca está bien, los saltos de color calzan con los bordes reales.
   */
  private fun camaraDebug(): String {
    val cam = camera ?: return "sin cámara"
    val intr = cam.intrinsecos ?: return "sin intrínsecos"
    if (!raycasterOk) return "sin Environment Raycaster"
    cam.onCuadro = { c ->
      cam.onCuadro = null; cam.copiarLuma = false
      main.post {
        val pose = historial.en(c.tsNs) ?: return@post
        val mw = pose.mat4()
        val mwc = Mat.mul44(mw, intr.cabezaDesdeCam)
        val mcw = Mat.invRigid(mwc)
        val o = Vector3(pose.t[0].toFloat(), pose.t[1].toFloat(), pose.t[2].toFloat())
        val pts = ArrayList<DoubleArray>()
        for (j in 0 until 36) for (i in 0 until 48) {
          val d = Mat.xformDir(mw, Math.tan(Math.toRadians(-50.0 + 100.0 * i / 47)), Math.tan(Math.toRadians(40.0 - 80.0 * j / 35)), 1.0)
          val h = runCatching { mruk.raycastEnvironment(o, Vector3(d[0].toFloat(), d[1].toFloat(), d[2].toFloat()), 8f) }.getOrNull() ?: continue
          if (h.result != MRUKEnvironmentRaycastHitResult.SUCCESS) continue
          val pc = Mat.xformPt(mcw, h.point.x.toDouble(), h.point.y.toDouble(), h.point.z.toDouble())
          val px = intr.proyectarCam(pc[0], pc[1], pc[2]) ?: continue
          if (px[0] < 0 || px[1] < 0 || px[0] >= intr.w || px[1] >= intr.h) continue
          pts.add(doubleArrayOf(px[0], px[1], sqrt(pc[0] * pc[0] + pc[1] * pc[1] + pc[2] * pc[2])))
        }
        val b = CalibIO.lumaABitmap(c.y, c.w, c.h).copy(android.graphics.Bitmap.Config.ARGB_8888, true)
        val cv = android.graphics.Canvas(b)
        val p = android.graphics.Paint().apply { isAntiAlias = true }
        for (q in pts) {
          p.color = android.graphics.Color.HSVToColor(floatArrayOf(((q[2] % 1.0) * 300).toFloat(), 1f, 1f))
          cv.drawCircle(q[0].toFloat(), q[1].toFloat(), 4f, p)
        }
        val fwd = Mat.xformDir(mwc, 0.0, 0.0, 1.0)
        val hf = Mat.xformDir(mw, 0.0, 0.0, 1.0)
        val f = File(dirFiles, "camara_debug.jpg")
        CalibIO.guardarBitmap(f, b)
        Log.i(TAG, "[debug] ${pts.size} puntos de profundidad sobre la cámara → ${f.absolutePath}; eje cámara·eje cabeza = %.3f".format(
            fwd[0] * hf[0] + fwd[1] * hf[1] + fwd[2] * hf[2]))
      }
    }
    cam.copiarLuma = true
    return "capturando cuadro para camara_debug.jpg"
  }

  // ----------------------------------------------------------------- ArUco de la mesa
  private fun cargarGrillaAruco(): ArucoMesa.Grilla? {
    grillaAruco?.let { return it }
    val txt = runCatching { File(dirFiles, "aruco_grid.json").readText() }.getOrNull()
        ?: runCatching { assets.open("aruco_grid.json").use { it.readBytes().toString(Charsets.UTF_8) } }.getOrNull() ?: return null
    return runCatching { ArucoMesa.Grilla.desdeJson(JSONObject(txt)) }.onFailure { Log.e(TAG, "[aruco] aruco_grid.json", it) }.getOrNull()
        ?.also { grillaAruco = it; Log.i(TAG, "[aruco] grilla ${it.cols}x${it.filas}, ${it.esquinas.size} marcadores, ${it.ancho}x${it.alto}") }
  }

  private fun comandoAruco(a: List<String>): String {
    fun d(i: Int) = a.getOrNull(i)?.replace(',', '.')?.toDoubleOrNull()
    return when (a.getOrNull(0)) {
      null, "on" -> {
        // relee files/aruco_grid.json en cada 'on' (la grilla puede haber cambiado en el Mac)
        grillaAruco = null; arucoReset = true
        cargarGrillaAruco()
        if (!arucoActivo) {
          arucoActivo = true
          Thread({ runCatching { bucleAruco() }.onFailure { arucoMsg = "error: ${it.message}"; Log.e(TAG, "[aruco] bucle", it) }; arucoActivo = false }, "Aruco").start()
        }
        "detección ArUco activa (grilla ${grillaAruco?.cols}x${grillaAruco?.filas} ${grillaAruco?.ancho}x${grillaAruco?.alto}, detector ${arucoCfg.nombre}${if (arucoPar) ", par patrón/invertido" else ""}${if (arucoZoom > 1) ", zoom ${arucoZoom}x" else ""})"
      }
      "off" -> {
        arucoActivo = false
        camera?.setExposicion(0.0, false)
        main.post { arucoEntity?.setComponent(Visible(false)) }
        "detección ArUco apagada (exposición automática)"
      }
      "voltear" -> { panelVoltear = !panelVoltear; "panel AR volteado=$panelVoltear" }
      "pnp" -> { ajusteMesaTs = 0; "PnP del proyector de la mesa en el próximo cuadro" }
      "reset" -> { arucoReset = true; "acumulación de marcadores borrada" }
      "grilla" -> { grillaAruco = null; arucoReset = true; cargarGrillaAruco()?.let { "grilla recargada: ${it.cols}x${it.filas}, ${it.esquinas.size} marcadores" } ?: "no hay aruco_grid.json" }
      "params" -> { arucoCfg = if (a.getOrNull(1) == "fabrica") arucoCfg.copy(ajustado = false) else if (a.getOrNull(1) == "pedido") ArucoMesa.Config(winMax = 53, ecr = 0.8) else arucoCfg.copy(ajustado = true); arucoDetectorVersion++; "detector: ${arucoCfg.nombre}" }
      "aruco3" -> { arucoCfg = arucoCfg.copy(aruco3 = a.getOrNull(1) != "off"); arucoDetectorVersion++; "detector: ${arucoCfg.nombre}" }
      "perimetro" -> { arucoCfg = arucoCfg.copy(perimetro = (d(1) ?: 0.01).coerceIn(0.001, 0.5)); arucoDetectorVersion++; "detector: ${arucoCfg.nombre}" }
      "paso" -> { arucoCfg = arucoCfg.copy(paso = (d(1) ?: 4.0).toInt().coerceIn(1, 50)); arucoDetectorVersion++; "detector: ${arucoCfg.nombre}" }
      "wmax" -> { arucoCfg = arucoCfg.copy(winMax = (d(1) ?: 23.0).toInt().coerceIn(3, 151)); arucoDetectorVersion++; "detector: ${arucoCfg.nombre}" }
      "ecr" -> { arucoCfg = arucoCfg.copy(ecr = (d(1) ?: 0.6).coerceIn(0.0, 1.0)); arucoDetectorVersion++; "detector: ${arucoCfg.nombre}" }
      "ppc" -> { arucoCfg = arucoCfg.copy(ppc = (d(1) ?: 4.0).toInt().coerceIn(1, 20)); arucoDetectorVersion++; "detector: ${arucoCfg.nombre}" }
      "invertidos" -> { arucoCfg = arucoCfg.copy(invertidos = a.getOrNull(1) != "off"); arucoDetectorVersion++; "detector: ${arucoCfg.nombre}" }
      "zoom" -> { arucoZoom = (d(1) ?: 2.0).coerceIn(1.0, 4.0); "zoom ${arucoZoom}x sobre la zona de la grilla (${arucoRoi?.joinToString() ?: "aún sin zona: primero hay que detectar algo a 1x"})" }
      "par" -> {
        arucoPar = a.getOrNull(1) != "off"; d(2)?.let { arucoParUmbral = it.toInt().coerceIn(1, 80) }
        "par patrón/invertido ${if (arucoPar) "ON (el Mac debe alternar la grilla y su inversa; umbral $arucoParUmbral)" else "off"}"
      }
      "ventana" -> { arucoVentanaMs = ((d(1) ?: 2.0) * 1000).toLong().coerceIn(0, 30000); "ventana de acumulación ${arucoVentanaMs} ms" }
      "ev" -> {
        arucoEv = if (a.getOrNull(1) == "auto") null else d(1) ?: -1.0
        arucoExpMs = 0.0
        val r = camera?.setExposicion(arucoEv ?: 0.0, false) ?: "sin cámara"
        if (arucoEv != null) main.postDelayed({ arucoExpMsg = camera?.setExposicion(arucoEv, true) ?: arucoExpMsg }, 1200)
        arucoExpMsg = r; r + (if (arucoEv != null) " · se bloquea en 1,2 s" else "")
      }
      "exp" -> {
        arucoExpMs = d(1) ?: 0.0; arucoIso = d(2)?.toInt() ?: 0
        val r = camera?.setExposicion(arucoEv, false, arucoExpMs, arucoIso) ?: "sin cámara"
        arucoExpMsg = r; r
      }
      "grid" -> "la grilla se genera y se proyecta desde el Mac: escribe 'aruco grid ${a.drop(1).joinToString(" ")}' en el monitor http://<mac>:8810 o corre tools/aruco_grid.py (genera PNG+JSON, la manda al runtime :8790 y empuja el JSON a files/)"
      else -> "aruco [on|off|voltear|pnp|reset|grilla|params ajustado|fabrica|aruco3 on|off|ventana s|ev EV|ev auto|exp ms [iso]]"
    }
  }

  private fun bucleAruco() {
    if (!ArucoMesa.cargarOpenCv()) { arucoMsg = "OpenCV no cargó"; return }
    var det = ArucoMesa.Detector(arucoCfg)
    var detInv = ArucoMesa.Detector(arucoCfg.copy(invertidos = true))
    val anillo = java.util.ArrayDeque<ScanCamera.Cuadro>()   // cuadros recientes para el par patrón/invertido
    var versionDet = arucoDetectorVersion
    var ultimo = -1L
    var expAplicada = false
    val acum = HashMap<Int, AcumMarca>()
    arucoMsg = "esperando cuadros"
    while (arucoActivo) {
      val cam = camera
      if (cam == null || !cam.abierta) {
        arucoMsg = "cámara ${estadoCamara()}${if (!tieneFoco) " · la app no tiene foco (CAMERA_DISABLED en segundo plano): ponla al frente" else ""}"
        expAplicada = false
        Thread.sleep(500); continue
      }
      if (!expAplicada) {
        // exposición baja y bloqueada: el monitor / la proyección no se queman
        expAplicada = true
        if (arucoExpMs > 0) arucoExpMsg = cam.setExposicion(arucoEv, false, arucoExpMs, arucoIso)
        else if (arucoEv != null) { cam.setExposicion(arucoEv, false); Thread.sleep(1200); arucoExpMsg = cam.setExposicion(arucoEv, true) }
        Log.i(TAG, "[aruco] $arucoExpMsg")
      }
      if (versionDet != arucoDetectorVersion) { versionDet = arucoDetectorVersion; det = ArucoMesa.Detector(arucoCfg); detInv = ArucoMesa.Detector(arucoCfg.copy(invertidos = true)); synchronized(arucoHist) { arucoHist.clear() } }
      if (arucoReset) { arucoReset = false; acum.clear() }
      cam.lumaHastaMs = SystemClock.elapsedRealtime() + 2000
      val c = cam.lumaCuadro
      val intr = cam.intrinsecos
      if (c == null || c.indice == ultimo || intr == null) { Thread.sleep(10); continue }
      ultimo = c.indice
      val t0 = SystemClock.elapsedRealtime()
      val roi = if (arucoZoom > 1.01) arucoRoi else null
      var marcas: List<ArucoMesa.Marca>
      if (arucoPar) {
        // el cuadro reciente más distinto del actual = el del patrón opuesto (la cámara está fija)
        anillo.addLast(c); while (anillo.size > 10 || (anillo.isNotEmpty() && c.tsNs - anillo.first.tsNs > 1_600_000_000L)) anillo.removeFirst()
        var mejor: ScanCamera.Cuadro? = null; var dmax = 0.0
        for (b in anillo) if (b !== c && b.w == c.w) { val dd = ArucoMesa.difMedia(c.y, b.y); if (dd > dmax) { dmax = dd; mejor = b } }
        arucoParDif = dmax
        if (mejor != null && dmax >= arucoParMin) {
          marcas = detInv.detectar(ArucoMesa.imagenSigno(c.y, mejor.y, arucoParUmbral), c.w, c.h, roi, arucoZoom)
          arucoModoUltimo = "par (dif media %.1f)".format(dmax)
        } else {
          marcas = det.detectar(c.y, c.w, c.h, roi, arucoZoom)
          arucoModoUltimo = "cuadro (sin alternancia: dif media %.1f < %.1f)".format(dmax, arucoParMin)
        }
      } else {
        anillo.clear()
        marcas = det.detectar(c.y, c.w, c.h, roi, arucoZoom)
        arucoModoUltimo = if (roi != null) "cuadro + zoom ${arucoZoom}x en ${roi.joinToString()}" else "cuadro"
      }
      val tDet = SystemClock.elapsedRealtime() - t0
      arucoCuadros++
      if (marcas.isNotEmpty()) arucoConDeteccion++
      synchronized(arucoHist) {
        arucoHist.addLast(t0 to marcas.size)
        while (arucoHist.isNotEmpty() && t0 - arucoHist.first.first > 10_000) arucoHist.removeFirst()
      }
      runCatching { procesarAruco(c, intr, marcas, tDet, t0, acum) }.onFailure { arucoMsg = "error: ${it.message}"; Log.w(TAG, "[aruco] proceso", it) }
      val dt = SystemClock.elapsedRealtime() - t0
      if (dt < 120) Thread.sleep(120 - dt)
    }
  }

  /** Raycast (hilo principal, en tandas) contra la sala MRUK (planos, volúmenes y global mesh) y, si no, la profundidad. */
  private fun raycastMalla(o: DoubleArray, dirs: List<DoubleArray>, soloEntorno: Boolean = false): Array<DoubleArray?> {
    val out = arrayOfNulls<DoubleArray>(dirs.size)
    val ov = Vector3(o[0].toFloat(), o[1].toFloat(), o[2].toFloat())
    var i = 0
    while (i < dirs.size) {
      val desde = i; val hasta = minOf(dirs.size, i + 300)
      val l = CountDownLatch(1)
      main.post {
        try {
          val room = if (soloEntorno) null else runCatching { mruk.getCurrentRoom() ?: mruk.rooms.firstOrNull() }.getOrNull()
          for (k in desde until hasta) {
            val d = dirs[k]
            val dv = Vector3(d[0].toFloat(), d[1].toFloat(), d[2].toFloat())
            if (room != null) {
              val h = runCatching { mruk.raycastRoom(room.anchor.uuid, ov, dv, 8f, com.meta.spatial.mruk.SurfaceType.ALL) }.getOrNull()
              if (h != null) out[k] = doubleArrayOf(h.hitPosition.x.toDouble(), h.hitPosition.y.toDouble(), h.hitPosition.z.toDouble())
            }
            if (out[k] == null && raycasterOk) {
              val h = runCatching { mruk.raycastEnvironment(ov, dv, 8f) }.getOrNull()
              if (h != null && h.result == MRUKEnvironmentRaycastHitResult.SUCCESS) out[k] = doubleArrayOf(h.point.x.toDouble(), h.point.y.toDouble(), h.point.z.toDouble())
            }
          }
        } finally { l.countDown() }
      }
      l.await(2, TimeUnit.SECONDS)
      i = hasta
    }
    return out
  }

  private fun dist3(a: DoubleArray, b: DoubleArray) = sqrt((a[0] - b[0]) * (a[0] - b[0]) + (a[1] - b[1]) * (a[1] - b[1]) + (a[2] - b[2]) * (a[2] - b[2]))

  private fun procesarAruco(c: ScanCamera.Cuadro, intr: ScanCamera.Intrinsecos, marcas: List<ArucoMesa.Marca>, tDet: Long, t0: Long,
                            acum: HashMap<Int, AcumMarca>) {
    val pose = historial.en(c.tsNs) ?: return
    val mWC = Mat.mul44(pose.mat4(), intr.cabezaDesdeCam)
    val o = doubleArrayOf(mWC[3], mWC[7], mWC[11])
    val dirs = ArrayList<DoubleArray>()
    for (m in marcas) for (k in 0 until 4) {
      val (u, v) = CalibIO.sinDistorsion(intr, m.esq[k * 2], m.esq[k * 2 + 1])
      val d = intr.rayoCam(u, v)
      dirs.add(ArucoMesa.norm(Mat.xformDir(mWC, d[0], d[1], d[2])))
    }
    val hits = if (dirs.isEmpty()) emptyArray() else raycastMalla(o, dirs)
    val esq3d = HashMap<Int, Array<DoubleArray?>>()
    marcas.forEachIndexed { i, m -> esq3d[m.id] = Array(4) { k -> hits[i * 4 + k] } }
    // ---- acumulación por id: los marcadores vistos en los últimos `ventana` ms (EMA si se re-detectan en el mismo sitio)
    val g = grillaAruco
    val ventanaNs = arucoVentanaMs * 1_000_000L
    for (m in marcas) {
      if (g != null && m.id !in g.esquinas) continue
      val e = esq3d[m.id] ?: continue
      if (e.any { it == null }) continue
      val nuevo = Array(4) { e[it]!! }
      val prev = acum[m.id]
      if (prev != null && c.tsNs - prev.tNs < ventanaNs && (0 until 4).all { dist3(prev.esq[it], nuevo[it]) < 0.03 }) {
        for (k in 0 until 4) for (q in 0..2) prev.esq[k][q] = prev.esq[k][q] * 0.6 + nuevo[k][q] * 0.4
        prev.vistas++; prev.tNs = c.tsNs; prev.esqCam = m.esq
      } else acum[m.id] = AcumMarca(m.id, nuevo, m.esq, c.tsNs, 1)
    }
    acum.values.removeAll { c.tsNs - it.tNs > maxOf(ventanaNs, 1L) && it.tNs != c.tsNs }
    acum.values.forEach { it.descartada = false; it.residuoMm = 0.0 }
    // zona de la grilla en la cámara (para el zoom): caja de las esquinas vistas, ampliada a la grilla completa (+60 %)
    if (acum.isNotEmpty()) {
      var x0 = 1e9; var y0 = 1e9; var x1 = -1e9; var y1 = -1e9
      for (a in acum.values) for (k in 0 until 4) { x0 = minOf(x0, a.esqCam[k * 2]); x1 = maxOf(x1, a.esqCam[k * 2]); y0 = minOf(y0, a.esqCam[k * 2 + 1]); y1 = maxOf(y1, a.esqCam[k * 2 + 1]) }
      val mx = (x1 - x0) * 0.6 + 40; val my = (y1 - y0) * 0.6 + 40
      arucoRoi = intArrayOf((x0 - mx).toInt().coerceAtLeast(0), (y0 - my).toInt().coerceAtLeast(0), (x1 - x0 + 2 * mx).toInt(), (y1 - y0 + 2 * my).toInt())
    }
    var usados = acum.values.toMutableList()
    // ---- plano + homografía px del proyector → plano, con rechazo de marcadores que no calzan (residuo > max(8 mm, 3×mediana))
    fun planoDe(us: List<AcumMarca>): ArucoMesa.Plano? {
      val pts = us.flatMap { it.esq.toList() }
      val du = DoubleArray(3); val dv = DoubleArray(3)
      for (a in us) {
        val v1 = ArucoMesa.norm(ArucoMesa.sub(a.esq[1], a.esq[0])); val v3 = ArucoMesa.norm(ArucoMesa.sub(a.esq[3], a.esq[0]))
        for (q in 0..2) { du[q] += v1[q]; dv[q] += v3[q] }
      }
      val p = if (pts.size >= 4) ArucoMesa.ajustarPlano(pts, du, o) else null
      return if (p != null && ArucoMesa.dot(dv, dv) > 0) ArucoMesa.orientarV(p, dv) else p
    }
    var plano = planoDe(usados)
    var h: DoubleArray? = null; var resH: Double? = null
    if (plano != null && g != null) {
      for (iter in 0 until 8) {
        val pl = plano ?: break
        val uv = ArrayList<Double>(); val ab = ArrayList<Double>()
        for (a in usados) { val ge = g.esquinas[a.id]!!; for (k in 0 until 4) { uv.add(ge[k * 2]); uv.add(ge[k * 2 + 1]); val q = pl.ab(a.esq[k]); ab.add(q[0]); ab.add(q[1]) } }
        val n = uv.size / 2
        if (n < 8) { h = null; break }
        h = AjusteProyector.homografia(uv.toDoubleArray(), ab.toDoubleArray(), n) ?: break
        var s2 = 0.0
        for (a in usados) {
          val ge = g.esquinas[a.id]!!
          var r = 0.0
          for (k in 0 until 4) { val q = aplicarH(h, ge[k * 2], ge[k * 2 + 1]); val p = pl.ab(a.esq[k]); val e = sqrt((q[0] - p[0]) * (q[0] - p[0]) + (q[1] - p[1]) * (q[1] - p[1])); r += e / 4; s2 += e * e }
          a.residuoMm = r * 1000
        }
        resH = sqrt(s2 / n) * 1000
        if (usados.size <= 3) break
        val med = Mat.median(usados.map { it.residuoMm }.toDoubleArray())
        val peor = usados.maxByOrNull { it.residuoMm }!!
        if (peor.residuoMm <= maxOf(8.0, 3 * med)) break
        peor.descartada = true
        usados = usados.filter { it !== peor }.toMutableList()
        plano = planoDe(usados)
      }
      // ---- PnP del proyector de la mesa (pose fija en la sala) con todos los marcadores aceptados, cada ~2 s
      val ahora = SystemClock.elapsedRealtime()
      val mf = modeloMesa
      if (mesaFija && mf != null) {
        // pose fija: solo verificación (residuo de las esquinas detectadas contra P fija)
        if (usados.isNotEmpty() && ahora - verifTs > 1000) {
          verifTs = ahora
          val xs = ArrayList<Double>(); val us = ArrayList<Double>()
          for (a in usados) { val ge = g.esquinas[a.id]!!; for (k in 0 until 4) { xs.addAll(a.esq[k].toList()); us.add(ge[k * 2]); us.add(ge[k * 2 + 1]) } }
          val res = AjusteProyector.residuos(mf.p, Pares(xs.toDoubleArray(), us.toDoubleArray()))
          verifResiduo = Mat.median(res); verifP90 = Mat.percentile(res, 90.0); verifN = usados.size
          if (verifResiduo!! > umbralRigPx) verifMalos++ else verifMalos = 0
          val antes = rigMovido
          rigMovido = verifMalos >= 3
          if (rigMovido && !antes) Log.w(TAG, "[proy] RIG MOVIDO: residuo %.1f px > %.1f px contra la pose fija (recalibrar)".format(verifResiduo, umbralRigPx))
        }
      } else if (plano != null && usados.size >= 3 && ahora - ajusteMesaTs > 2000) {
        ajusteMesaTs = ahora
        val xs = ArrayList<Double>(); val us = ArrayList<Double>()
        for (a in usados) { val ge = g.esquinas[a.id]!!; for (k in 0 until 4) { xs.addAll(a.esq[k].toList()); us.add(ge[k * 2]); us.add(ge[k * 2 + 1]) } }
        val km = kMesa ?: runCatching { File(dirFiles, "proyector_mesa_K.json").takeIf { it.exists() }?.let { ArucoMesa.kDesdeJson(JSONObject(it.readText())) } }.getOrNull()
            ?.also { kMesa = it; kMesaFuente = "files/proyector_mesa_K.json" }
        if (km != null) {
          // K conocida: solo la pose (6 gdl), bien condicionada con la mesa
          val pk = ArucoMesa.poseConK(km, xs.toDoubleArray(), us.toDoubleArray(), plano, g.ancho, g.alto)
          if (pk != null) {
            val (m, refl) = ArucoMesa.ladoCorrecto(pk.first, plano.c, plano.n, o)
            val resid = AjusteProyector.residuos(m.p, Pares(xs.toDoubleArray(), us.toDoubleArray()))
            mesaReflejada = refl; modeloMesa = m; ajusteMesaPares = us.size / 2; ajusteMesaMarcadores = usados.size
            ajusteMesa = ResultadoAjuste(m, BooleanArray(resid.size) { true }, resid, "pose_con_K", 0.0)
            mesaInfo = "pose con K conocida ($kMesaFuente): mediana %.2f px p90 %.2f, %d marcadores (%d pares)%s; centro a %.2f m del plano".format(
                Mat.median(resid), Mat.percentile(resid, 90.0), usados.size, us.size / 2, if (refl) ", reflejado al lado de la cámara" else "",
                Math.abs(ArucoMesa.dot(ArucoMesa.sub(m.centro, plano.c), plano.n)))
            Log.i(TAG, "[aruco] PnP mesa $mesaInfo · ${m.resumen()}")
            runCatching { guardarCalibMesa(ajusteMesa!!, m, us.size / 2, usados.size) }
            main.post { aplicarProyector() }
          }
        }
        val r = if (km != null || usados.size < 4) null else AjusteProyector.ajustar(Pares(xs.toDoubleArray(), us.toDoubleArray()), g.ancho, g.alto, umbralPx = 10.0)
            ?.takeIf { it.nInliers >= 0.75 * (us.size / 2) }   // con 3 marcadores el ajuste es exacto y no dice nada
        if (r != null) {
          // ambigüedad del caso plano: el proyector tiene que quedar ARRIBA de la mesa (del lado de la cámara); se publican las dos
          val casco = historial.ultima()?.t
          val (arriba, refl0) = ArucoMesa.ladoCorrecto(r.modelo, plano.c, plano.n, o)
          val abajo = ModeloProyector(Mat.mul(arriba.p, 3, 4, ArucoMesa.reflexion(plano.c, plano.n), 4), arriba.ancho, arriba.alto)
          fun dPrior(m: ModeloProyector) = casco?.let { c -> ArucoMesa.dist3(m.centro, doubleArrayOf(c[0], c[1] + 0.10, c[2])) }
          val cands = mutableListOf(
              ArucoMesa.Candidato(arriba, "restringido_plano, arriba de la mesa", r.mediana, dPrior(arriba), Double.NaN),
              ArucoMesa.Candidato(abajo, "restringido_plano, espejo (debajo de la mesa)", r.mediana, dPrior(abajo), Double.NaN))
          // prior de Max: casco ~20 cm al lado y ~10 cm más abajo que el proyector (la focal no es observable con un solo plano)
          val conPrior = casco?.let { ArucoMesa.pnpConPrior(xs.toDoubleArray(), us.toDoubleArray(), plano, g.ancho, g.alto, it, o) }
          if (conPrior != null) cands.add(0, conPrior)
          candidatosMesa = cands
          val elegido = cands[0]
          val m = elegido.modelo; val refl = if (conPrior != null) false else refl0
          mesaReflejada = refl
          ajusteMesa = r; modeloMesa = m; ajusteMesaPares = us.size / 2; ajusteMesaMarcadores = usados.size
          mesaInfo = "%s: mediana %.2f px, %d marcadores (%d pares); centro a %.2f m del plano%s (OJO: con un solo plano la focal no es observable: usa la calibración fija del Mac o 'proyector K')".format(
              elegido.tipo, elegido.medianaPx, usados.size, us.size / 2, Math.abs(ArucoMesa.dot(ArucoMesa.sub(m.centro, plano.c), plano.n)),
              elegido.distPrior?.let { ", a %.2f m del prior".format(it) } ?: "")
          Log.i(TAG, "[aruco] PnP mesa $mesaInfo · ${m.resumen()}")
          runCatching { guardarCalibMesa(r, m, us.size / 2, usados.size) }.onFailure { Log.w(TAG, "[aruco] guardar calib_mesa_aruco", it) }
          main.post { aplicarProyector() }
        }
      }
    }
    // ---- panel AR: rectángulo de la grilla (esperada con H, o lo detectado) en el plano
    var panel: DoubleArray? = null
    val pl = plano
    if (pl != null) {
      val quadsAb = HashMap<Int, DoubleArray>()
      if (h != null && g != null) for ((id, ge) in g.esquinas) quadsAb[id] = DoubleArray(8) { k -> aplicarH(h, ge[(k / 2) * 2], ge[(k / 2) * 2 + 1])[k % 2] }
      val detAb = HashMap<Int, DoubleArray>()
      for (a in usados) detAb[a.id] = DoubleArray(8) { k -> pl.ab(a.esq[k / 2])[k % 2] }
      val todos = (if (quadsAb.isNotEmpty()) quadsAb.values else detAb.values).toList()
      if (todos.isNotEmpty()) {
        var a0 = 1e9; var b0 = 1e9; var a1 = -1e9; var b1 = -1e9
        for (q in todos) for (k in 0 until 4) { a0 = minOf(a0, q[k * 2]); a1 = maxOf(a1, q[k * 2]); b0 = minOf(b0, q[k * 2 + 1]); b1 = maxOf(b1, q[k * 2 + 1]) }
        val mg = 0.02; a0 -= mg; b0 -= mg; a1 += mg; b1 += mg
        panel = doubleArrayOf(a0, b0, a1, b1)
        val W = a1 - a0; val H = b1 - b0
        fun norma(q: DoubleArray) = DoubleArray(8) { k -> if (k % 2 == 0) (q[k] - a0) / W else (q[k] - b0) / H }
        val esp = quadsAb.mapValues { norma(it.value) }; val dts = detAb.mapValues { norma(it.value) }
        val txt = "ArUco ${marcas.size} en cuadro · ${usados.size} acumulados · plano %.1f mm · H %s mm%s".format(pl.rms * 1000, resH?.let { "%.1f".format(it) } ?: "—",
            if (mesaFija) " · pose FIJA, verificación %s px%s".format(verifResiduo?.let { "%.1f".format(it) } ?: "—", if (rigMovido) " · RIG MOVIDO: recalibrar" else "")
            else ajusteMesa?.let { " · PnP mesa %.2f px".format(it.mediana) } ?: "")
        main.post { actualizarPanelAruco(pl, a0, b0, W, H, esp, dts, txt) }
      }
    }
    // puntos detectados (3D) y grilla esperada (2D) para la vista del proyector de la mesa
    if (proyectorAplicado == "mesa") renderer.puntos = FloatArray(usados.size * 12).also { f -> var i = 0; for (a in usados) for (k in 0 until 4) for (q in 0..2) f[i++] = a.esq[k][q].toFloat() }
    arucoRes = ResAruco(c.tsNs, c.w, c.h, marcas, esq3d, acum.values.map { it }, plano, h, resH, tDet, SystemClock.elapsedRealtime() - t0, o, panel)
    arucoMsg = "${marcas.size} en el cuadro (${tDet} ms), ${usados.size} acumulados${acum.values.count { it.descartada }.let { if (it > 0) ", $it descartados" else "" }}"
  }

  private fun aplicarH(h: DoubleArray, u: Double, v: Double): DoubleArray {
    val w = h[6] * u + h[7] * v + h[8]
    return doubleArrayOf((h[0] * u + h[1] * v + h[2]) / w, (h[3] * u + h[4] * v + h[5]) / w)
  }

  /** Qué proyector se renderiza: el de la mesa (PnP ArUco, pose fija en la sala) o el rig del casco. */
  private fun aplicarProyector() {
    val mm = modeloMesa
    val okMesa = mm != null && (ajusteMesa?.let { it.mediana < 6.0 && ajusteMesaMarcadores >= 3 } ?: true)
    val usarMesa = when (proyectorSel) { "mesa" -> mm != null; "rig" -> false; else -> okMesa }
    if (usarMesa && mm != null) {
      if (proyectorAplicado != "mesa") { rigModoFijo = modoFijo; rigPoseFija = poseFija }
      renderer.modelo = mm
      renderer.rejilla = RejillaCorreccion(renderer.ancho, renderer.alto); renderer.rejillaVersion++
      modoFijo = true
      poseFija = PoseT(0, doubleArrayOf(0.0, 0.0, 0.0), doubleArrayOf(1.0, 0.0, 0.0, 0.0))   // P ya está en el marco de la sala
      val g = grillaAruco ?: cargarGrillaAruco()
      // la grilla está en px del cuadro del proyector de la mesa (p. ej. 1900x1040 de la ventana); el FBO es 1920x1080
      renderer.lineas2d = g?.let { gr ->
        val sx = renderer.ancho.toFloat() / gr.ancho; val sy = renderer.alto.toFloat() / gr.alto
        FloatArray(gr.esquinas.size * 16).also { f -> var i = 0; for (e in gr.esquinas.values) for (k in 0 until 4) { val k2 = (k + 1) % 4; f[i++] = e[k * 2].toFloat() * sx; f[i++] = e[k * 2 + 1].toFloat() * sy; f[i++] = e[k2 * 2].toFloat() * sx; f[i++] = e[k2 * 2 + 1].toFloat() * sy } }
      }
      if (proyectorAplicado != "mesa") { if (vista != "marcadores") mostrarVista("mapeo"); Log.i(TAG, "[proy] render desde el proyector de la mesa (fijo, PnP ArUco)") }
      proyectorAplicado = "mesa"
    } else if (proyectorAplicado == "mesa") {
      renderer.modelo = calib?.modelo
      renderer.rejilla = calib?.rejilla ?: RejillaCorreccion(renderer.ancho, renderer.alto); renderer.rejillaVersion++
      modoFijo = rigModoFijo; poseFija = rigPoseFija
      renderer.lineas2d = null; renderer.puntos = null
      proyectorAplicado = "rig"
      Log.i(TAG, "[proy] render desde el rig del casco")
    }
    refrescarUi()
  }

  private fun actualizarPanelAruco(pl: ArucoMesa.Plano, a0: Double, b0: Double, w: Double, h: Double,
                                   esp: Map<Int, DoubleArray>, det: Map<Int, DoubleArray>, txt: String) {
    if (!sceneLista) return
    // ejes del panel: X = +u, Y = −v (arriba en la imagen del proyector), Z = X × Y; la cara visible mira a −Z
    var x = pl.e1; val y = doubleArrayOf(-pl.e2[0], -pl.e2[1], -pl.e2[2])
    var z = ArucoMesa.cross(x, y)
    var espejo = ArucoMesa.dot(z, pl.n) > 0          // −Z debe apuntar hacia la cámara (normal del plano)
    if (panelVoltear) espejo = !espejo
    if (espejo) { x = doubleArrayOf(-x[0], -x[1], -x[2]); z = ArucoMesa.cross(x, y) }
    val q = Mat.mat3ToQuat(doubleArrayOf(x[0], y[0], z[0], x[1], y[1], z[1], x[2], y[2], z[2]))
    val c = pl.punto(a0 + w / 2, b0 + h / 2).let { p -> DoubleArray(3) { p[it] + pl.n[it] * 0.004 } }
    val pose = Pose(Vector3(c[0].toFloat(), c[1].toFloat(), c[2].toFloat()), Quaternion(q[0].toFloat(), q[1].toFloat(), q[2].toFloat(), q[3].toFloat()))
    val e = arucoEntity ?: Entity.createPanelEntity(R.layout.ui_aruco, Transform(pose), Visible(true)).also { arucoEntity = it }
    e.setComponent(Transform(pose))
    e.setComponent(Scale(Vector3((w / 1.6).toFloat(), (h / 0.9).toFloat(), 1f)))
    e.setComponent(Visible(arucoActivo))
    vistaAruco?.let { v ->
      fun mx(m: Map<Int, DoubleArray>) = if (!espejo) m else m.mapValues { (_, a) -> DoubleArray(8) { k -> if (k % 2 == 0) 1 - a[k] else a[k] } }
      v.esperados = mx(esp); v.detectados = mx(det); v.texto = txt; v.postInvalidate()
    }
  }

  private fun guardarCalibMesa(r: ResultadoAjuste, m: ModeloProyector, n: Int, marcadores: Int) {
    val ancla = anclaSala
    val j = JSONObject().put("tipo", r.tipo).put("marco", "mundo de la sala (Spatial SDK: x der, y arriba, z adelante, zurdo)")
        .put("pares", n).put("marcadores", marcadores).put("inliers", r.nInliers).put("residuo_mediana_px", r.mediana).put("residuo_p90_px", r.p90)
        .put("rms_px", r.rms).put("planitud", r.planitud).put("reflejado_al_lado_de_la_camara", mesaReflejada)
        .put("P", JSONArray(m.p.toList())).put("K", JSONArray(m.k.toList())).put("R", JSONArray(m.r.toList()))
        .put("centro", JSONArray(m.centro.toList())).put("ancho", m.ancho).put("alto", m.alto).put("resumen", m.resumen())
        .put("ancla_sala", ancla?.let { JSONArray(it.toList()) } ?: JSONObject.NULL).put("fecha_ms", System.currentTimeMillis())
    // P en el marco del ancla de la sala (sobrevive entre sesiones): P_sala = P_mundo · T_mundo←sala
    if (ancla != null) j.put("P_sala", JSONArray(Mat.mul(m.p, 3, 4, ancla, 4).toList()))
    // el PnP en vivo no pisa calib_mesa.json (esa es la calibración fija que escribe el Mac)
    File(dirFiles, "calib_mesa_aruco.json").writeText(j.toString(1))
  }

  /** calib_mesa.json guardado: si hay ancla de sala, se re-ancla al mundo de esta sesión. */
  /** files/calib_mesa.json (calibración del Mac; con "pose_fija": true es LA pose del proyector de la mesa). Se re-ancla a la sala. */
  private fun cargarCalibMesa(forzar: Boolean = false) {
    val f = File(dirFiles, "calib_mesa.json")
    if (!f.exists()) {
      if (forzar && modeloMesa != null) {
        // sin archivo: se olvida el proyector de la mesa y se vuelve a renderizar desde el rig
        modeloMesa = null; ajusteMesa = null; mesaFija = false; calibMesaMtime = 0; mesaInfo = "sin calib_mesa.json"
        main.post { aplicarProyector() }
      }
      return
    }
    if (!forzar && f.lastModified() == calibMesaMtime) return
    calibMesaMtime = f.lastModified()
    runCatching {
      val j = JSONObject(f.readText())
      val ps = j.optJSONArray("P_sala"); val ancla = anclaSala
      val p = if (ps != null && ancla != null) Mat.mul(DoubleArray(12) { ps.getDouble(it) }, 3, 4, Mat.invRigid(ancla), 4)
              else j.getJSONArray("P").let { a -> DoubleArray(12) { a.getDouble(it) } }
      modeloMesa = ModeloProyector(p, j.optInt("ancho", 1920), j.optInt("alto", 1080))
      ajusteMesa = null
      mesaFija = j.optBoolean("pose_fija", false)
      mesaIncertidumbre = j.opt("incertidumbre")
      mesaFuente = j.optString("fuente", j.optString("tipo", "?"))
      verifMalos = 0; rigMovido = false; verifResiduo = null
      mesaInfo = "%s calib_mesa.json (%s, mediana %.2f px, %s)".format(if (mesaFija) "POSE FIJA de" else "cargado de", j.optString("tipo"),
          j.optDouble("residuo_mediana_px"), if (ps != null && ancla != null) "re-anclado a la sala" else "coordenadas de mundo guardadas")
      Log.i(TAG, "[proy] mesa $mesaInfo · ${modeloMesa!!.resumen()}")
      main.post { aplicarProyector() }
    }.onFailure { Log.w(TAG, "[proy] calib_mesa.json", it) }
  }

  private fun comandoProyector(a: String?, t: List<String> = emptyList()): String {
    when (a) {
      "recargar" -> { cargarCalibMesa(forzar = true); return "calib_mesa.json recargado: $mesaInfo" }
      "recalibrar" -> { mesaFija = false; ajusteMesaTs = 0; rigMovido = false; return "pose fija suspendida: el ArUco vuelve a estimar (PnP en vivo → files/calib_mesa_aruco.json); 'proyector recargar' vuelve a la fija" }
      "umbral" -> { umbralRigPx = t.getOrNull(2)?.toDoubleOrNull() ?: 8.0; return "umbral de 'rig movido' $umbralRigPx px" }
    }
    if (a == "K" || a == "k") {
      val fx = t.getOrNull(2)?.toDoubleOrNull()
      if (fx == null) { kMesa = null; kMesaFuente = "—"; ajusteMesaTs = 0; return "K del proyector de la mesa borrada (vuelve el ajuste restringido)" }
      kMesa = doubleArrayOf(fx, 0.0, t.getOrNull(3)?.toDoubleOrNull() ?: 960.0, 0.0, fx, t.getOrNull(4)?.toDoubleOrNull() ?: 540.0, 0.0, 0.0, 1.0)
      kMesaFuente = "comando"; ajusteMesaTs = 0
      runCatching { File(dirFiles, "proyector_mesa_K.json").writeText(JSONObject().put("K", JSONArray(kMesa!!.toList())).toString()) }
      return "K del proyector de la mesa: fx=fy=$fx cx=${kMesa!![2]} cy=${kMesa!![5]} (PnP solo de pose en el próximo cuadro)"
    }
    proyectorSel = when (a) { "mesa" -> "mesa"; "rig", "casco" -> "rig"; else -> "auto" }
    aplicarProyector()
    return "proyector: $proyectorSel → renderizando ${proyectorAplicado}${if (proyectorSel == "mesa" && modeloMesa == null) " (aún no hay PnP de la mesa: 'aruco on' y mirar la grilla)" else ""}"
  }

  private fun arucoJson(): JSONObject {
    val j = JSONObject().put("activo", arucoActivo).put("mensaje", arucoMsg).put("cuadros", arucoCuadros).put("con_deteccion", arucoConDeteccion)
        .put("detector", arucoCfg.nombre).put("modo", arucoModoUltimo).put("par", arucoPar).put("par_dif_media", arucoParDif)
        .put("zoom", arucoZoom).put("roi_cam", arucoRoi?.let { JSONArray(it.toList()) } ?: JSONObject.NULL)
        .put("ventana_ms", arucoVentanaMs).put("exposicion", arucoExpMsg)
    camera?.let { cm -> j.put("exp_ms", cm.expActualNs / 1e6).put("iso", cm.isoActual) }
    synchronized(arucoHist) {
      if (arucoHist.isNotEmpty()) j.put("por_cuadro_10s", JSONObject().put("cuadros", arucoHist.size)
          .put("media", arucoHist.sumOf { it.second }.toDouble() / arucoHist.size).put("max", arucoHist.maxOf { it.second })
          .put("min", arucoHist.minOf { it.second }))
    }
    val g = grillaAruco
    if (g != null) j.put("grilla", JSONObject().put("cols", g.cols).put("filas", g.filas).put("n", g.esquinas.size).put("ancho", g.ancho).put("alto", g.alto))
    val r = arucoRes
    if (r != null) {
      j.put("ts_ns", r.tsNs).put("edad_ms", (SystemClock.elapsedRealtimeNanos() - r.tsNs) / 1e6).put("cam_w", r.wCam).put("cam_h", r.hCam)
          .put("ms_det", r.msDet).put("ms_total", r.msTotal).put("n", r.marcas.size).put("cam_pos", arr(r.camPos))
      val a = JSONArray()
      for (m in r.marcas) {
        val e3 = JSONArray(); r.esq3d[m.id]?.forEach { p -> e3.put(if (p == null) JSONObject.NULL else arr(p)) }
        a.put(JSONObject().put("id", m.id).put("esq_cam", arr(m.esq)).put("esq_mundo", e3))
      }
      j.put("marcas", a)
      val ac = JSONArray()
      for (m in r.acum.sortedBy { it.id }) {
        val e3 = JSONArray(); m.esq.forEach { e3.put(arr(it)) }
        ac.put(JSONObject().put("id", m.id).put("esq_mundo", e3).put("vistas", m.vistas).put("residuo_mm", m.residuoMm).put("descartado", m.descartada)
            .put("edad_ms", (r.tsNs - m.tNs) / 1e6))
      }
      j.put("acumulados", ac).put("n_acumulados", r.acum.count { !it.descartada }).put("n_descartados", r.acum.count { it.descartada })
      r.plano?.let { p -> j.put("plano", JSONObject().put("centro", arr(p.c)).put("normal", arr(p.n)).put("e1", arr(p.e1)).put("e2", arr(p.e2)).put("rms_mm", p.rms * 1000)) }
      r.residuoHmm?.let { j.put("homografia_rms_mm", it) }
      if (r.plano != null && r.panel != null) {
        val p = r.plano; val pn = r.panel
        j.put("panel", JSONObject().put("esquinas", JSONArray().put(arr(p.punto(pn[0], pn[1]))).put(arr(p.punto(pn[2], pn[1])))
            .put(arr(p.punto(pn[2], pn[3]))).put(arr(p.punto(pn[0], pn[3])))).put("ancho_m", pn[2] - pn[0]).put("alto_m", pn[3] - pn[1]))
      }
    }
    val m = modeloMesa
    if (m != null) {
      val am = ajusteMesa
      val pj = JSONObject().put("info", mesaInfo).put("reflejado", mesaReflejada).put("modelo", m.resumen()).put("k_fuente", kMesaFuente)
          .put("pose_fija", mesaFija).put("fuente", mesaFuente).put("incertidumbre", mesaIncertidumbre ?: JSONObject.NULL)
          .put("verificacion", JSONObject().put("residuo_mediana_px", verifResiduo ?: JSONObject.NULL).put("residuo_p90_px", verifP90 ?: JSONObject.NULL)
              .put("marcadores", verifN).put("umbral_px", umbralRigPx).put("rig_movido", rigMovido))
      if (candidatosMesa.isNotEmpty() && !mesaFija) pj.put("candidatos", JSONArray(candidatosMesa.mapIndexed { i, c ->
        JSONObject().put("elegido", i == 0).put("tipo", c.tipo).put("mediana_px", c.medianaPx).put("dist_prior_m", c.distPrior ?: JSONObject.NULL)
            .put("centro", arr(c.modelo.centro)).put("modelo", c.modelo.resumen())
      }))
          .put("proyector", proyectorJson(m, null, "mesa (PnP ArUco)"))
      if (am != null) pj.put("tipo", am.tipo).put("pares", ajusteMesaPares).put("marcadores", ajusteMesaMarcadores).put("inliers", am.nInliers)
          .put("mediana_px", am.mediana).put("p90_px", am.p90).put("rms_px", am.rms).put("edad_s", (SystemClock.elapsedRealtime() - ajusteMesaTs) / 1000.0)
      j.put("pnp", pj)
    }
    return j
  }

  /** Origen y esquinas del frustum (plano a 1 m por el eje óptico) en el mundo; `pose` = pose del marco del modelo (null = mundo). */
  private fun proyectorJson(m: ModeloProyector, pose: PoseT?, tipo: String): JSONObject {
    val mw = pose?.mat4() ?: Mat.pose4(0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
    val eje = rayoNorm(m.rayo(m.ancho / 2.0, m.alto / 2.0))
    val es = JSONArray(); val ep = JSONArray()
    for ((u, v) in listOf(0.0 to 0.0, m.ancho.toDouble() to 0.0, m.ancho.toDouble() to m.alto.toDouble(), 0.0 to m.alto.toDouble())) {
      val d = m.rayo(u, v)
      es.put(arr(Mat.xformPt(mw, m.centro[0] + 2 * d[0], m.centro[1] + 2 * d[1], m.centro[2] + 2 * d[2])))
      val s0 = 1.0 / Math.abs(d[0] * eje[0] + d[1] * eje[1] + d[2] * eje[2]).coerceAtLeast(1e-6)
      ep.put(arr(Mat.xformPt(mw, m.centro[0] + d[0] * s0, m.centro[1] + d[1] * s0, m.centro[2] + d[2] * s0)))
    }
    return JSONObject().put("origen", arr(Mat.xformPt(mw, m.centro[0], m.centro[1], m.centro[2]))).put("esquinas_2m", es)
        .put("esquinas_plano_1m", ep).put("ancho", m.ancho).put("alto", m.alto).put("tipo", tipo)
  }

  // ----------------------------------------------------------------- cuadro completo y pose de la cámara
  @Volatile private var ultimoFull: ScanCamera.Cuadro? = null

  private fun cuadroFull(): ScanCamera.Cuadro? {
    val cam = camera ?: return null
    cam.lumaHastaMs = SystemClock.elapsedRealtime() + 2000
    val t0 = SystemClock.elapsedRealtimeNanos()
    var c = cam.lumaCuadro; val fin = SystemClock.elapsedRealtime() + 800
    while ((c == null || t0 - c.tsNs > 250_000_000L) && SystemClock.elapsedRealtime() < fin) { Thread.sleep(15); c = cam.lumaCuadro }
    return c
  }

  private fun camFull(png: Boolean): ServidorMonitor.Resp {
    val c = cuadroFull() ?: return ServidorMonitor.Resp(JSONObject().put("error", "sin cuadro: cámara ${estadoCamara()}${if (!tieneFoco) " (app sin foco)" else ""}").toString(), codigo = 503)
    ultimoFull = c
    val o = ByteArrayOutputStream()
    val b = CalibIO.lumaABitmap(c.y, c.w, c.h)
    b.compress(if (png) android.graphics.Bitmap.CompressFormat.PNG else android.graphics.Bitmap.CompressFormat.JPEG, if (png) 100 else 97, o)
    b.recycle()
    val cam = camera
    val cab = HashMap<String, String>()
    cab["X-Ts-Ns"] = c.tsNs.toString()
    cab["X-Frame-Index"] = c.indice.toString()
    cab["X-Size"] = "${c.w}x${c.h}"
    cab["X-Exposure"] = if (cam != null && cam.expActualNs > 0) "%.3f ms iso %d".format(java.util.Locale.US, cam.expActualNs / 1e6, cam.isoActual) else "desconocida (la cámara no la reporta)"
    cab["X-AE"] = if (cam?.aeLockDisponible == true) "lock disponible" else "automática, sin lock ni compensación"
    return ServidorMonitor.Resp(o.toByteArray(), if (png) "image/png" else "image/jpeg", cabeceras = cab)
  }

  /** Pose de la cámara izquierda (mundo de la sala) en `ts` (default: el último /cam_full), intrínsecos y extrínseca. */
  private fun camPoseJson(tsPedido: Long?): JSONObject {
    val cam = camera; val intr = cam?.intrinsecos
    val ts = tsPedido ?: ultimoFull?.tsNs ?: cam?.lumaCuadro?.tsNs ?: cam?.ultimoTsNs ?: return JSONObject().put("error", "sin cámara")
    if (intr == null) return JSONObject().put("error", "sin intrínsecos (cámara ${estadoCamara()})")
    val ph = historial.en(ts) ?: return JSONObject().put("error", "sin pose de la cabeza en ese instante")
    val mWC = Mat.mul44(ph.mat4(), intr.cabezaDesdeCam)
    val j = JSONObject().put("ts_ns", ts).put("ts_pose_cabeza_ns", ph.tNs)
        .put("historial_desfase_ms", (ts - (historial.ultima()?.tNs ?: ts)) / 1e6)
    j.put("camara", poseJson(mWC).put("m4_mundo_desde_cam", JSONArray(mWC.map { Math.round(it * 1e6) / 1e6 })))
    j.put("cabeza", poseJson(ph.mat4()).put("m4_mundo_desde_cabeza", JSONArray(ph.mat4().map { Math.round(it * 1e6) / 1e6 })))
    j.put("cabeza_desde_cam", JSONArray(intr.cabezaDesdeCam.map { Math.round(it * 1e6) / 1e6 }))
    j.put("intrinsecos", JSONObject().put("fx", intr.fx).put("fy", intr.fy).put("cx", intr.cx).put("cy", intr.cy).put("w", intr.w).put("h", intr.h)
        .put("distorsion", intr.distorsion?.let { d -> JSONArray(d.map { it.toDouble() }) } ?: JSONObject.NULL).put("active_array", intr.activeArray))
    j.put("ancla_sala", anclaSala?.let { poseJson(it).put("m4", JSONArray(it.toList())) } ?: JSONObject.NULL)
    j.put("convencion", "mundo Spatial SDK (x der, y arriba, z adelante, zurdo, m). Marco cámara: x der, y arriba, z adelante. " +
        "Rayo del píxel (u, v desde arriba): d_cam = ((u-cx)/fx, (cy-v)/fy, 1); d_mundo = R(m4_mundo_desde_cam)·d_cam. q = [w, x, y, z]. " +
        "m4 en orden de filas. La distorsión (camera2 LENS_DISTORTION) se aplica como en CalibIO.sinDistorsion.")
    return j
  }

  // ----------------------------------------------------------------- profundidad en vivo
  /** Pide frames de profundidad durante los próximos segundos (los endpoints /depth.* lo llaman). */
  private fun asegurarDepth() {
    depthHastaMs = SystemClock.elapsedRealtime() + 4000
    if (hiloDepth?.isAlive == true) return
    hiloDepth = Thread({
      var ultimoMs = 0L
      while (true) {
        val ahora = SystemClock.elapsedRealtime()
        if (ahora > depthHastaMs) { Thread.sleep(150); continue }
        val t0 = ahora
        runCatching { depthFrame = calcularDepth() }.onFailure { Log.w(TAG, "[depth]", it); Thread.sleep(500) }
        val fin = SystemClock.elapsedRealtime()
        if (ultimoMs > 0) { val hz = 1000.0 / (fin - ultimoMs).coerceAtLeast(1); depthHz = if (depthHz == 0.0) hz else depthHz * 0.8 + hz * 0.2 }
        ultimoMs = fin
        val dt = fin - t0
        if (dt < 100) Thread.sleep(100 - dt)
      }
    }, "Depth").apply { isDaemon = true; start() }
  }

  /** Rejilla nx×ny de raycasts del Environment Raycaster por los píxeles de la cámara izquierda (o desde la cabeza, 100°×80°). */
  private fun calcularDepth(): FrameDepth? {
    if (!raycasterOk) return null
    val nx = depthNx; val ny = depthNy
    val cam = camera; val intr = cam?.intrinsecos
    val ts: Long; val mW: DoubleArray; val marco: String
    val dirsLoc = ArrayList<DoubleArray>(nx * ny)
    if (cam != null && cam.abierta && intr != null && cam.ultimoTsNs > 0) {
      ts = cam.ultimoTsNs
      val p = historial.en(ts) ?: return null
      mW = Mat.mul44(p.mat4(), intr.cabezaDesdeCam); marco = "cámara izq"
      for (j in 0 until ny) for (i in 0 until nx) dirsLoc.add(intr.rayoCam((i + 0.5) * intr.w / nx, (j + 0.5) * intr.h / ny))
    } else {
      val p = historial.ultima() ?: return null
      ts = p.tNs; mW = p.mat4(); marco = "cabeza (100°×80°)"
      for (j in 0 until ny) for (i in 0 until nx) dirsLoc.add(doubleArrayOf(Math.tan(Math.toRadians(-50.0 + 100.0 * (i + 0.5) / nx)), Math.tan(Math.toRadians(40.0 - 80.0 * (j + 0.5) / ny)), 1.0))
    }
    val o = doubleArrayOf(mW[3], mW[7], mW[11])
    val t0 = SystemClock.elapsedRealtime()
    val dirs = dirsLoc.map { ArucoMesa.norm(Mat.xformDir(mW, it[0], it[1], it[2])) }
    val hits = raycastMalla(o, dirs, soloEntorno = true)
    val z = FloatArray(nx * ny) { Float.NaN }
    val pts = ArrayList<Float>(); val idx = ArrayList<Int>()
    for (k in 0 until nx * ny) {
      val h = hits[k] ?: continue
      val dl = dirsLoc[k]; val n = sqrt(dl[0] * dl[0] + dl[1] * dl[1] + dl[2] * dl[2])
      z[k] = (dist3(h, o) * dl[2] / n).toFloat()      // profundidad z en el marco del sensor
      pts.add(h[0].toFloat()); pts.add(h[1].toFloat()); pts.add(h[2].toFloat()); idx.add(k)
    }
    return FrameDepth(nx, ny, z, pts.toFloatArray(), idx.toIntArray(), ts, SystemClock.elapsedRealtime() - t0, o, marco)
  }

  /** Paleta tipo turbo (0 = cerca, 1 = lejos). */
  private fun colorTurbo(t0: Double): Int {
    val t = t0.coerceIn(0.0, 1.0)
    val r = (34.61 + t * (1172.33 - t * (10793.56 - t * (33300.12 - t * (38394.49 - t * 14825.05))))).coerceIn(0.0, 255.0)
    val g = (23.31 + t * (557.33 + t * (1225.33 - t * (3574.96 - t * (1073.77 + t * 707.56))))).coerceIn(0.0, 255.0)
    val b = (27.2 + t * (3211.1 - t * (15327.97 - t * (27814.0 - t * (22569.18 - t * 6838.66))))).coerceIn(0.0, 255.0)
    return android.graphics.Color.rgb(r.toInt(), g.toInt(), b.toInt())
  }

  private fun jpegDepth(): ByteArray {
    val f = depthFrame ?: return jpegCartel(listOf("PROFUNDIDAD: calculando…", if (!raycasterOk) "Environment Raycaster no disponible ($raycasterEstado)" else "primer frame en camino"))
    val maxM = depthMaxM; val minM = 0.2
    val px = IntArray(f.nx * f.ny) { k -> val v = f.z[k]; if (v.isNaN()) android.graphics.Color.rgb(20, 20, 24) else colorTurbo((v - minM) / (maxM - minM)) }
    val chico = android.graphics.Bitmap.createBitmap(px, f.nx, f.ny, android.graphics.Bitmap.Config.ARGB_8888)
    val W = 560; val Hm = (W.toLong() * f.ny / f.nx).toInt(); val H = Hm + 70
    val b = android.graphics.Bitmap.createBitmap(W, H, android.graphics.Bitmap.Config.ARGB_8888)
    val cv = android.graphics.Canvas(b)
    cv.drawColor(android.graphics.Color.rgb(12, 14, 18))
    cv.drawBitmap(android.graphics.Bitmap.createScaledBitmap(chico, W, Hm, true), 0f, 0f, null)
    // leyenda en metros
    val p = android.graphics.Paint().apply { isAntiAlias = true; textSize = 16f; color = android.graphics.Color.WHITE }
    for (x in 20 until W - 20) { p.color = colorTurbo((x - 20).toDouble() / (W - 40)); cv.drawLine(x.toFloat(), (Hm + 10).toFloat(), x.toFloat(), (Hm + 26).toFloat(), p) }
    p.color = android.graphics.Color.WHITE
    var m = 0.5
    while (m <= maxM + 1e-6) { val x = 20 + (m - minM) / (maxM - minM) * (W - 40); cv.drawLine(x.toFloat(), (Hm + 26).toFloat(), x.toFloat(), (Hm + 32).toFloat(), p); cv.drawText("%.1f m".format(m), (x - 16).toFloat(), (Hm + 48).toFloat(), p); m += 0.5 }
    val validos = f.z.count { !it.isNaN() }
    cv.drawText("Environment Raycaster ${f.nx}x${f.ny} · %.1f Hz · %d ms · %d%% válidos · %s".format(depthHz, f.ms, validos * 100 / f.z.size, f.marco), 10f, (H - 6).toFloat(), p)
    val o = ByteArrayOutputStream(); b.compress(android.graphics.Bitmap.CompressFormat.JPEG, 85, o); b.recycle(); chico.recycle()
    return o.toByteArray()
  }

  private fun depthJson(): String {
    val f = depthFrame ?: return JSONObject().put("error", if (!raycasterOk) "Environment Raycaster no disponible ($raycasterEstado)" else "calculando").toString()
    val sb = StringBuilder(f.pts.size * 8)
    sb.append("{\"fuente\":\"raycast Environment Raycaster (sin Depth API en Spatial SDK 0.13.2)\",\"marco\":\"").append(f.marco)
        .append("\",\"origen\":").append(arr(f.origen)).append(",\"nx\":").append(f.nx).append(",\"ny\":").append(f.ny)
        .append(",\"hz\":").append("%.2f".format(java.util.Locale.US, depthHz)).append(",\"ms\":").append(f.ms).append(",\"aciertos\":").append(f.idx.size).append(",\"puntos\":[")
    for (k in f.pts.indices) { if (k > 0) sb.append(','); sb.append(Math.round(f.pts[k] * 1000) / 1000.0) }
    sb.append("],\"idx\":[")
    for (k in f.idx.indices) { if (k > 0) sb.append(','); sb.append(f.idx[k]) }
    sb.append("],\"t_ns\":").append(f.tNs).append('}')
    return sb.toString()
  }

  // ----------------------------------------------------------------- marcadores QR
  private fun armarMarcadores(cols: Int, filas: Int, ladoMax: Int) {
    val l = Marcadores.layout(cols, filas, renderer.ancho, renderer.alto, ladoMax)
    layoutMarc = l
    imagenMarc = RendererProyector.Contenido.Imagen(Marcadores.rasterizar(l), l.ancho, l.alto, "marcadores ${cols}x$filas")
    runCatching { File(dirFiles, "marcadores.json").writeText(l.json().toString(1)) }
    val it0 = l.items[0]
    Log.i(TAG, "[marc] grilla ${cols}x$filas: QR versión ${(it0.modulos - 17) / 4} (${it0.modulos} módulos) de ${it0.modulos * it0.modPx} px (${it0.modPx} px/módulo) → files/marcadores.json")
  }

  private fun iniciarTrackerQr() {
    if (trackerQr == "activo" || trackerQr == "iniciando") return
    trackerQr = "iniciando"
    runCatching {
      mruk.configureTrackers(setOf(Tracker.QrCode)).whenComplete { r, err ->
        trackerQr = if (r == MRUKStartTrackerResult.SUCCESS) "activo" else "error: ${r ?: err?.message}"
        Log.i(TAG, "[marc] tracker QR de MRUK: $trackerQr")
      }
    }.onFailure { trackerQr = "error: ${it.message}"; Log.e(TAG, "[marc] configureTrackers", it) }
  }

  private fun comandoMarcadores(a: List<String>): String {
    fun n(i: Int, d: Int) = a.getOrNull(i)?.toIntOrNull() ?: d
    return when (a.getOrNull(0)) {
      null, "ver", "on" -> {
        if (imagenMarc == null) armarMarcadores(4, 3, 0)
        iniciarTrackerQr()
        mostrarVista("marcadores")
        main.postDelayed({ capturarSalida("marcadores") }, 500)
        "proyectando grilla ${layoutMarc?.cols}x${layoutMarc?.filas} (${layoutMarc?.total} QR) · tracker QR $trackerQr"
      }
      "grid", "grilla" -> {
        armarMarcadores(n(1, 4).coerceIn(1, 12), n(2, 3).coerceIn(1, 8), n(3, 0))
        if (vista == "marcadores") mostrarVista("marcadores")
        "grilla ${layoutMarc?.cols}x${layoutMarc?.filas}, QR de ${layoutMarc?.items?.get(0)?.let { it.modulos * it.modPx }} px"
      }
      "detectar" -> if (a.getOrNull(1) == "off") { runCatching { mruk.stopTrackers() }; trackerQr = "apagado"; "tracker QR apagado" } else { iniciarTrackerQr(); "tracker QR $trackerQr" }
      "calibrar" -> marcadoresCalibrar()
      "off" -> { mostrarVista("mapeo"); "grilla oculta (tracker sigue: 'marcadores detectar off')" }
      else -> "marcadores [ver|grid C F [ladoMax]|detectar on|off|calibrar|off]"
    }
  }

  /** Pares de la grilla seguida ahora → una vista más del Calibrador (re-ajuste con todas). */
  private fun marcadoresCalibrar(): String {
    val l = layoutMarc ?: return "primero 'marcadores' (proyecta la grilla)"
    val pose = poseActual() ?: return "sin pose de la cabeza"
    // centros (robustos); las esquinas del cuadrado de MRUK no calzan con un QR proyectado con
    // keystone (prueba sintética: hasta 10 cm), solo se suman si faltan centros para el mínimo de 12
    val c0 = Marcadores.correspondencias(l, detecciones, conEsquinas = false)
    val c = if (c0.nCentros >= 12) c0 else Marcadores.correspondencias(l, detecciones, conEsquinas = true)
    if (c.nCentros < 4) return "muy pocos QR seguidos (${c.nCentros}/${l.total}); hacen falta ≥4 (mejor todos)"
    val r = calibrador.agregarVistaExterna(c.xMundo, c.uv, pose, "marcadores QR (${c.nCentros}/${l.total})")
    marcResiduo = r?.grayMedianaPx
    marcMensaje = calibrador.mensaje
    if (r != null) calibracionTerminada(r)
    return marcMensaje
  }

  /** Sistema ECS: lee los QR que sigue MRUK (TrackedQrCode + Transform + MRUKPlane + TrackingStatus). */
  inner class SistemaMarcadores : SystemBase() {
    private var ultimo = 0L
    override fun execute() {
      if (trackerQr != "activo") return
      val ahora = SystemClock.elapsedRealtimeNanos()
      if (ahora - ultimo < 50_000_000L) return
      ultimo = ahora
      val lista = ArrayList<Marcadores.Deteccion>()
      val raros = HashSet<String>()
      runCatching {
        Query.where { has(TrackedQrCode.id) }.eval().forEach { e ->
          val qr = e.getComponent<TrackedQrCode>()
          val pose = e.tryGetComponent<Transform>()?.transform ?: return@forEach
          val pl = e.tryGetComponent<MRUKPlane>() ?: return@forEach
          val seguido = e.tryGetComponent<TrackingStatus>()?.isTracked ?: true
          val p = qr.payload
          val id = Regex("^P(\\d{1,3})$").find(p.trim())?.groupValues?.get(1)?.toIntOrNull() ?: -1
          if (id < 0) raros.add(p.take(40))
          lista.add(Marcadores.Deteccion(id, p, GeometriaSala.poseMat(pose), doubleArrayOf(pl.min.x.toDouble(), pl.min.y.toDouble()),
              doubleArrayOf(pl.max.x.toDouble(), pl.max.y.toDouble()), seguido, ahora))
        }
      }.onFailure { marcMensaje = "error leyendo QR: ${it.message}" }
      detecciones = lista
      payloadsRaros = raros
      val tot = layoutMarc?.total ?: 0
      if (tot > 0) {
        val det = lista.count { it.seguido && it.id in 0 until tot }.toDouble() / tot
        marcTasa = if (vista == "marcadores") marcTasa * 0.9 + det * 0.1 else det
      }
    }
  }

  private fun marcadoresJson(): JSONObject {
    val l = layoutMarc
    val j = JSONObject().put("modo", "qr_mruk").put("tracker", trackerQr).put("proyectando", vista == "marcadores")
        .put("total", l?.total ?: 0).put("tasa", marcTasa).put("residuo_px", marcResiduo ?: JSONObject.NULL).put("mensaje", marcMensaje)
    if (l != null) j.put("layout", JSONObject().put("cols", l.cols).put("filas", l.filas).put("lado_px", l.items[0].modulos * l.items[0].modPx)
        .put("mod_px", l.items[0].modPx).put("modulos", l.items[0].modulos))
    val ahora = SystemClock.elapsedRealtimeNanos()
    val a = JSONArray()
    for (d in detecciones) {
      val es = JSONArray(); for (p in d.esquinasCiclo()) es.put(arr(p))
      a.put(JSONObject().put("id", d.id).put("payload", d.payload).put("seguido", d.seguido).put("centro_mundo", arr(d.centro))
          .put("esquinas_mundo", es).put("lado_m", d.lado).put("edad_ms", (ahora - d.tNs) / 1e6))
    }
    j.put("detectados", a)
    if (payloadsRaros.isNotEmpty()) j.put("payloads_ajenos", JSONArray(payloadsRaros.toList()))
    return j
  }

  // ----------------------------------------------------------------- monitor HTTP
  private fun estadoCamara(): String {
    val cam = camera
    return when {
      cam == null -> if (camFalloMsg != null) "no (${camFalloMsg!!.take(90)})" else "no"
      cam.abierta -> "%.0f fps, %d cuadros".format(cam.fps, cam.cuadros)
      cam.failed -> "falló"
      else -> "abriendo"
    }
  }

  private fun arr(v: DoubleArray) = JSONArray().also { a -> v.forEach { a.put(Math.round(it * 10000) / 10000.0) } }
  private fun arrN(vararg v: Double) = arr(v)

  private fun poseJson(m: DoubleArray): JSONObject {
    val q = Mat.mat3ToQuat(doubleArrayOf(m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]))
    return JSONObject().put("t", arrN(m[3], m[7], m[11])).put("q", arr(q))
        .put("adelante", arr(Mat.xformDir(m, 0.0, 0.0, 1.0))).put("arriba", arr(Mat.xformDir(m, 0.0, 1.0, 0.0)))
  }

  /** Anclas MRUK (Space Setup): etiqueta, pose y tamaño. Hilo principal. */
  private fun anclasDeMruk(): JSONArray {
    val a = JSONArray()
    for (room in mruk.rooms) for (e in room.anchors) {
      val anchor = e.tryGetComponent<MRUKAnchor>() ?: continue
      val label = runCatching { if (anchor.labelsCount > 0) anchor.labels[0] ?: "OTHER" else "OTHER" }.getOrDefault("OTHER")
      val pose = e.tryGetComponent<Transform>()?.transform ?: continue
      val m = GeometriaSala.poseMat(pose)
      val j = JSONObject().put("etiqueta", label).put("pose", poseJson(m))
      e.tryGetComponent<MRUKPlane>()?.let { p -> j.put("plano", arrN((p.max.x - p.min.x).toDouble(), (p.max.y - p.min.y).toDouble())).put("bordes", p.boundariesCount) }
      e.tryGetComponent<MRUKVolume>()?.let { v ->
        j.put("volumen", arrN((v.max.x - v.min.x).toDouble(), (v.max.y - v.min.y).toDouble(), (v.max.z - v.min.z).toDouble()))
        j.put("centro_local", arrN((v.max.x + v.min.x) / 2.0, (v.max.y + v.min.y) / 2.0, (v.max.z + v.min.z) / 2.0))
      }
      a.put(j)
    }
    return a
  }

  private fun displaysJson(): JSONArray {
    val dm = getSystemService(android.hardware.display.DisplayManager::class.java)
    val a = JSONArray()
    for (d in dm.displays) {
      val fl = ArrayList<String>()
      if (d.flags and android.view.Display.FLAG_PRESENTATION != 0) fl.add("PRESENTATION")
      if (d.flags and android.view.Display.FLAG_PRIVATE != 0) fl.add("PRIVATE")
      if (d.flags and android.view.Display.FLAG_SECURE != 0) fl.add("SECURE")
      a.put(JSONObject().put("id", d.displayId).put("nombre", d.name).put("w", d.mode.physicalWidth).put("h", d.mode.physicalHeight)
          .put("hz", d.mode.refreshRate.toDouble()).put("flags", JSONArray(fl)).put("estado", d.state)
          .put("hdmi", d.name.contains("HDMI", ignoreCase = true)))
    }
    return a
  }

  private fun estadoJson(): JSONObject {
    val ahora = SystemClock.elapsedRealtimeNanos()
    val j = JSONObject().put("t_ns", ahora).put("wall_ms", System.currentTimeMillis())
    j.put("app", JSONObject().put("foco", tieneFoco).put("resumida", resumida).put("pid", android.os.Process.myPid())
        .put("http_peticiones", servidor.peticiones).put("http_clientes", servidor.clientesActivos))
    val pc = historial.ultima()
    if (pc != null) j.put("cabeza", poseJson(pc.mat4()).put("ts_ns", pc.tNs).put("edad_ms", (ahora - pc.tNs) / 1e6))
    val cam = camera
    val cj = JSONObject().put("estado", estadoCamara())
    if (cam != null) {
      cj.put("abierta", cam.abierta).put("fallo", cam.failed).put("fps", cam.fps).put("cuadros", cam.cuadros)
      if (cam.ultimoTsNs > 0) cj.put("ts_ns", cam.ultimoTsNs).put("edad_ms", (ahora - cam.ultimoTsNs) / 1e6)
      val intr = cam.intrinsecos
      if (intr != null) {
        cj.put("intrinsecos", JSONObject().put("fx", intr.fx).put("fy", intr.fy).put("cx", intr.cx).put("cy", intr.cy).put("w", intr.w).put("h", intr.h))
        val pf = (if (cam.ultimoTsNs > 0) historial.en(cam.ultimoTsNs) else null) ?: pc
        if (pf != null) {
          val mwc = Mat.mul44(pf.mat4(), intr.cabezaDesdeCam)
          cj.put("pose", poseJson(mwc))
          // frustum de la cámara a 1 m (mundo)
          val es = JSONArray()
          for ((u, v) in listOf(0.0 to 0.0, intr.w.toDouble() to 0.0, intr.w.toDouble() to intr.h.toDouble(), 0.0 to intr.h.toDouble())) {
            val d = intr.rayoCam(u, v)
            es.put(arr(Mat.xformPt(mwc, d[0], d[1], d[2])))
          }
          cj.put("frustum_1m", es)
        }
      }
    } else camFalloMsg?.let { cj.put("error", it) }
    j.put("camara", cj)
    val ctl = JSONObject()
    for ((k, c) in controles) ctl.put(k, JSONObject().put("tipo", c.tipo).put("activo", c.activo).put("t", arr(c.t)).put("q", arr(c.q)).put("botones", c.botones))
    j.put("controles", ctl.put("edad_ms", if (controlesTs > 0) (ahora - controlesTs) / 1e6 else JSONObject.NULL))
    val tm = (mallaMruk?.size ?: 0) / RendererProyector.FLOATS_VERT / 3
    val tp = (mallaProf?.size ?: 0) / RendererProyector.FLOATS_VERT / 3
    j.put("mruk", JSONObject().put("resumen", resumenSala).put("raycaster", raycasterEstado).put("raycaster_ok", raycasterOk)
        .put("salas", runCatching { mruk.rooms.size }.getOrDefault(0)).put("anclas", anclasJson)
        .put("triangulos_mruk", tm).put("triangulos_prof", tp).put("malla_version", mallaVersion).put("geometria", geometria)
        .put("ancla_sala", anclaSala?.let { poseJson(it) } ?: JSONObject.NULL))
    j.put("salida", JSONObject().put("activa", salida.activa).put("preferencia", salida.preferencia.name)
        .put("destino", renderer.destinoInfo).put("presentacion", salida.displaysInfo()).put("displays", displaysJson()))
    j.put("render", JSONObject().put("fps", renderer.fps).put("cuadros", renderer.cuadros).put("vista", vista)
        .put("efecto", RendererProyector.NOMBRES_EFECTO[renderer.efecto]).put("error", renderer.ultimoError ?: JSONObject.NULL))
    val c = calib
    val cal = JSONObject().put("fase", calibrador.fase.name).put("progreso", calibrador.progreso).put("mensaje", calibrador.mensaje)
        .put("vistas", calibrador.vistas.size).put("latencia_lazo_ms", calibrador.latenciaLazoMs ?: JSONObject.NULL)
    if (c != null) {
      cal.put("tipo", c.tipo).put("gray_mediana_px", c.grayMedianaPx ?: JSONObject.NULL).put("gray_p90_px", c.grayP90Px ?: JSONObject.NULL)
          .put("lazo_mediana_px", c.lazoMedianaPx ?: JSONObject.NULL).put("lazo_p90_px", c.lazoP90Px ?: JSONObject.NULL)
          .put("lazo_iter", c.lazoIter).put("pares", c.nPares).put("inliers", c.nInliers).put("salida", c.salida)
          .put("modelo", c.modelo.resumen())
      // frustum del proyector en el mundo con la pose que usa el render (modo fijo o cabeza actual)
      val pr = (if (modoFijo) poseFija else null) ?: pc
      if (pr != null) {
        val mw = pr.mat4(); val m = c.modelo
        val es = JSONArray()
        for ((u, v) in listOf(0.0 to 0.0, m.ancho.toDouble() to 0.0, m.ancho.toDouble() to m.alto.toDouble(), 0.0 to m.alto.toDouble())) {
          val d = m.rayo(u, v); val n = sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
          es.put(arr(Mat.xformPt(mw, m.centro[0] + 2 * d[0] / n, m.centro[1] + 2 * d[1] / n, m.centro[2] + 2 * d[2] / n)))
        }
        // esquinas sobre el plano a 1 m por el eje óptico (coplanares: la interpolación bilineal es exacta)
        val eje = rayoNorm(m.rayo(m.ancho / 2.0, m.alto / 2.0))
        val ep = JSONArray()
        for ((u, v) in listOf(0.0 to 0.0, m.ancho.toDouble() to 0.0, m.ancho.toDouble() to m.alto.toDouble(), 0.0 to m.alto.toDouble())) {
          val d = m.rayo(u, v); val s0 = 1.0 / Math.abs(d[0] * eje[0] + d[1] * eje[1] + d[2] * eje[2]).coerceAtLeast(1e-6)
          ep.put(arr(Mat.xformPt(mw, m.centro[0] + d[0] * s0, m.centro[1] + d[1] * s0, m.centro[2] + d[2] * s0)))
        }
        cal.put("proyector", JSONObject().put("origen", arr(Mat.xformPt(mw, m.centro[0], m.centro[1], m.centro[2]))).put("esquinas_2m", es)
            .put("esquinas_plano_1m", ep).put("ancho", m.ancho).put("alto", m.alto).put("tipo", c.tipo))
      }
    }
    cal.put("proyector_activo", proyectorAplicado).put("proyector_sel", proyectorSel)
    if (proyectorAplicado == "mesa") modeloMesa?.let { cal.put("proyector", proyectorJson(it, null, "mesa (PnP ArUco)")) }
    j.put("calibracion", cal)
    depthFrame?.let { f -> j.put("depth", JSONObject().put("nx", f.nx).put("ny", f.ny).put("hz", depthHz).put("ms", f.ms).put("aciertos", f.idx.size).put("marco", f.marco)
        .put("fuente", "raycast Environment Raycaster (Spatial SDK 0.13.2 no expone el mapa de la Depth API)")) }
    j.put("marcadores", marcadoresJson())
    j.put("aruco", arucoJson())
    j.put("cuerpo", cuerpoJson(false))
    val a = renderer.ajuste
    j.put("modo", JSONObject().put("modo", if (modoFijo) "fijo" else "cabeza").put("prediccion", prediccion).put("latencia_prediccion_ms", latenciaPrediccionMs)
        .put("ajuste", JSONObject().put("dx", a.dx).put("dy", a.dy).put("rot_deg", a.rotDeg).put("escala", a.escala)))
    j.put("texto", estadoTexto())
    return j
  }

  private fun rayoNorm(d: DoubleArray): DoubleArray { val n = sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]); return doubleArrayOf(d[0] / n, d[1] / n, d[2] / n) }

  private fun jpegCartel(lineas: List<String>): ByteArray {
    val b = android.graphics.Bitmap.createBitmap(640, 360, android.graphics.Bitmap.Config.ARGB_8888)
    val cv = android.graphics.Canvas(b)
    cv.drawColor(android.graphics.Color.rgb(16, 18, 22))
    val p = android.graphics.Paint().apply { isAntiAlias = true; textSize = 24f; color = android.graphics.Color.rgb(250, 190, 60) }
    lineas.forEachIndexed { i, l -> cv.drawText(l.take(48), 24f, 60f + i * 34f, p) }
    val o = ByteArrayOutputStream(); b.compress(android.graphics.Bitmap.CompressFormat.JPEG, 80, o); b.recycle()
    return o.toByteArray()
  }

  /** Último cuadro passthrough como JPEG (640 px, color). Si la cámara no está, un cartel con el motivo. */
  /** `w` = ancho de salida (≤ 864, el cuadro reducido del monitor), `q` = calidad JPEG. */
  private fun jpegCamara(esperarNuevo: Boolean, ultimoIndice: Long = -1, w: Int = 864, q: Int = 70): ByteArray? {
    val cam = camera
    if (cam == null || !cam.abierta) {
      return jpegCartel(listOf("CAMARA: ${estadoCamara()}", if (!tieneFoco) "la app no tiene foco: la camara se bloquea" else "",
          "ponte el casco y abre la app (o tools/cmd.sh camara)"))
    }
    cam.monitorHastaMs = SystemClock.elapsedRealtime() + 3000
    val t0 = SystemClock.elapsedRealtimeNanos()
    var c = cam.monitorCuadro
    val fin = SystemClock.elapsedRealtime() + 800
    while ((c == null || (esperarNuevo && t0 - c.tsNs > 300_000_000L) || c.indice == ultimoIndice) && SystemClock.elapsedRealtime() < fin) {
      Thread.sleep(15); c = cam.monitorCuadro
    }
    if (c == null) return jpegCartel(listOf("CAMARA abierta pero sin cuadros", estadoCamara()))
    if (c.indice == ultimoIndice) return null
    val o = ByteArrayOutputStream()
    if (w >= c.w) android.graphics.YuvImage(c.nv21, android.graphics.ImageFormat.NV21, c.w, c.h, null).compressToJpeg(android.graphics.Rect(0, 0, c.w, c.h), q, o)
    else {
      val t = ByteArrayOutputStream()
      android.graphics.YuvImage(c.nv21, android.graphics.ImageFormat.NV21, c.w, c.h, null).compressToJpeg(android.graphics.Rect(0, 0, c.w, c.h), 95, t)
      val b0 = android.graphics.BitmapFactory.decodeByteArray(t.toByteArray(), 0, t.size())
      val b1 = android.graphics.Bitmap.createScaledBitmap(b0, w.coerceAtLeast(64), (w.coerceAtLeast(64).toLong() * c.h / c.w).toInt(), true)
      b1.compress(android.graphics.Bitmap.CompressFormat.JPEG, q, o); b0.recycle(); b1.recycle()
    }
    ultimoJpegCamIndice = c.indice
    return o.toByteArray()
  }
  @Volatile private var ultimoJpegCamIndice = -1L

  private fun jpegRender(ancho: Int = 640): ByteArray? {
    val l = CountDownLatch(1)
    var out: ByteArray? = null
    renderer.capturarChica(ancho) { bmp ->
      if (bmp != null) { val o = ByteArrayOutputStream(); bmp.compress(android.graphics.Bitmap.CompressFormat.JPEG, 80, o); bmp.recycle(); out = o.toByteArray() }
      l.countDown()
    }
    l.await(800, TimeUnit.MILLISECONDS)
    return out ?: jpegCartel(listOf("RENDER: sin captura", "destino: ${renderer.destinoInfo}"))
  }

  @Volatile private var profCache: Pair<Long, String>? = null

  /** Nube de profundidad en vivo: rejilla de raycasts del Environment Raycaster desde la cabeza. */
  private fun profundidadJson(nx: Int, ny: Int): String {
    profCache?.let { if (SystemClock.elapsedRealtime() - it.first < 700) return it.second }
    if (!raycasterOk) return JSONObject().put("error", "Environment Raycaster no disponible ($raycasterEstado)").toString()
    val pose = poseActual() ?: return JSONObject().put("error", "sin pose").toString()
    val mw = pose.mat4()
    val o = Vector3(pose.t[0].toFloat(), pose.t[1].toFloat(), pose.t[2].toFloat())
    val n = nx * ny
    val pts = DoubleArray(n * 3); val ok = BooleanArray(n)
    val l = CountDownLatch(1)
    val t0 = SystemClock.elapsedRealtime()
    var i = 0
    val paso = object : Runnable {
      override fun run() {
        var k = 0
        while (i < n && k < 300) {
          val u = i % nx; val v = i / nx
          val d = Mat.xformDir(mw, Math.tan(Math.toRadians(-55.0 + 110.0 * u / (nx - 1))), Math.tan(Math.toRadians(42.0 - 84.0 * v / (ny - 1))), 1.0)
          val h = runCatching { mruk.raycastEnvironment(o, Vector3(d[0].toFloat(), d[1].toFloat(), d[2].toFloat()), 8f) }.getOrNull()
          if (h != null && h.result == MRUKEnvironmentRaycastHitResult.SUCCESS) {
            pts[i * 3] = h.point.x.toDouble(); pts[i * 3 + 1] = h.point.y.toDouble(); pts[i * 3 + 2] = h.point.z.toDouble(); ok[i] = true
          }
          i++; k++
        }
        if (i >= n) l.countDown() else main.post(this)
      }
    }
    main.post(paso)
    l.await(4, TimeUnit.SECONDS)
    val sb = StringBuilder(n * 30)
    var aciertos = 0
    sb.append("{\"origen\":").append(arr(pose.t)).append(",\"nx\":").append(nx).append(",\"ny\":").append(ny).append(",\"puntos\":[")
    for (k in 0 until n) if (ok[k]) {
      if (aciertos++ > 0) sb.append(',')
      sb.append(Math.round(pts[k * 3] * 1000) / 1000.0).append(',').append(Math.round(pts[k * 3 + 1] * 1000) / 1000.0).append(',').append(Math.round(pts[k * 3 + 2] * 1000) / 1000.0)
    }
    sb.append("],\"idx\":[")
    var pr = true
    for (k in 0 until n) if (ok[k]) { if (!pr) sb.append(','); pr = false; sb.append(k) }
    sb.append("],\"fov_h\":110,\"fov_v\":84")
    sb.append(",\"aciertos\":").append(aciertos).append(",\"ms\":").append(SystemClock.elapsedRealtime() - t0).append(",\"t_ns\":").append(pose.tNs).append('}')
    val s = sb.toString()
    profCache = SystemClock.elapsedRealtime() to s
    return s
  }

  /** Malla del render (MRUK y profundidad) como posiciones + color por triángulo. */
  private fun mallaJson(): String {
    val sb = StringBuilder(1 shl 16)
    sb.append("{\"version\":").append(mallaVersion).append(",\"geometria\":\"").append(geometria).append('"')
    // anclas MRUK (planos y volúmenes) con etiqueta, pose (mundo) y tamaño; la sopa "mruk" incluye además la global mesh (tipo 0) y sus contornos (tipo 1)
    sb.append(",\"anclas\":").append(anclasJson.toString())
    sb.append(",\"convencion\":\"mundo Spatial SDK: x der, y arriba, z adelante (zurdo), metros; anclas: plano en XY local (normal +Z), volumen min/max local\"")
    for ((nombre, v) in listOf("mruk" to mallaMruk, "prof" to mallaProf)) {
      sb.append(",\"").append(nombre).append("\":")
      if (v == null) { sb.append("null"); continue }
      val nv = v.size / RendererProyector.FLOATS_VERT
      sb.append("{\"pos\":[")
      for (k in 0 until nv) {
        val b = k * RendererProyector.FLOATS_VERT
        if (k > 0) sb.append(',')
        sb.append(Math.round(v[b] * 1000) / 1000.0).append(',').append(Math.round(v[b + 1] * 1000) / 1000.0).append(',').append(Math.round(v[b + 2] * 1000) / 1000.0)
      }
      sb.append("],\"col\":[")
      for (t in 0 until nv / 3) {
        val b = t * 3 * RendererProyector.FLOATS_VERT + 9
        if (t > 0) sb.append(',')
        sb.append(Math.round(v[b] * 100) / 100.0).append(',').append(Math.round(v[b + 1] * 100) / 100.0).append(',').append(Math.round(v[b + 2] * 100) / 100.0)
      }
      sb.append("],\"tipo\":[")
      for (t in 0 until nv / 3) { if (t > 0) sb.append(','); sb.append(v[t * 3 * RendererProyector.FLOATS_VERT + 12].toInt()) }
      sb.append("]}")
    }
    sb.append('}')
    return sb.toString()
  }

  private fun logTexto(n: Int): String {
    val p = ProcessBuilder("logcat", "-d", "-v", "time", "--pid=${android.os.Process.myPid()}", "Proyector:V", "AndroidRuntime:V", "*:W")
        .redirectErrorStream(true).start()
    val lineas = p.inputStream.bufferedReader().readLines()
    p.waitFor(2, TimeUnit.SECONDS)
    return lineas.takeLast(n).joinToString("\n")
  }

  private fun enMain(timeoutMs: Long = 2000, f: () -> String): String {
    if (Looper.myLooper() == Looper.getMainLooper()) return f()
    val l = CountDownLatch(1)
    var r = "timeout"
    main.post { r = runCatching(f).getOrElse { "error: ${it.message}" }; l.countDown() }
    l.await(timeoutMs, TimeUnit.MILLISECONDS)
    return r
  }

  private fun configurarMonitor() {
    val s = servidor
    s.get("/estado.json") { ServidorMonitor.Resp(estadoJson().toString()) }
    s.get("/profundidad.json") { q -> ServidorMonitor.Resp(profundidadJson((q["nx"]?.toIntOrNull() ?: 48).coerceIn(4, 128), (q["ny"]?.toIntOrNull() ?: 32).coerceIn(4, 96))) }
    s.get("/malla.json") { ServidorMonitor.Resp(mallaJson()) }
    s.get("/log") { q -> ServidorMonitor.Resp(logTexto((q["n"]?.toIntOrNull() ?: 80).coerceIn(1, 2000)), "text/plain; charset=utf-8") }
    s.get("/cmd") { q ->
      val c = q["c"].orEmpty().trim()
      if (c.isEmpty()) ServidorMonitor.Resp("{\"error\":\"falta c\"}", codigo = 404)
      else ServidorMonitor.Resp(JSONObject().put("c", c).put("r", enMain { comando(c) }).toString())
    }
    s.get("/cam.jpg") { q ->
      if (q["full"] != null) {
        // cuadro de luma a resolución completa (el mismo que ve el detector ArUco), para afinar en el Mac
        val cam = camera
        cam?.lumaHastaMs = SystemClock.elapsedRealtime() + 2000
        var c = cam?.lumaCuadro; val fin = SystemClock.elapsedRealtime() + 800
        while ((c == null || SystemClock.elapsedRealtimeNanos() - c.tsNs > 400_000_000L) && SystemClock.elapsedRealtime() < fin) { Thread.sleep(20); c = cam?.lumaCuadro }
        if (c == null) ServidorMonitor.Resp(jpegCartel(listOf("sin cuadro de luma", estadoCamara())), "image/jpeg")
        else { val o = ByteArrayOutputStream(); CalibIO.lumaABitmap(c.y, c.w, c.h).compress(android.graphics.Bitmap.CompressFormat.JPEG, 95, o); ServidorMonitor.Resp(o.toByteArray(), "image/jpeg") }
      } else ServidorMonitor.Resp(jpegCamara(true, w = q["w"]?.toIntOrNull() ?: 864, q = (q["q"]?.toIntOrNull() ?: 75).coerceIn(30, 95)) ?: ByteArray(0), "image/jpeg")
    }
    // cuadro completo de la cámara izquierda (luma, sin reescalar) + su pose, para calibraciones desde el Mac
    s.get("/cam_full.png") { camFull(png = true) }
    s.get("/cam_full.jpg") { camFull(png = false) }
    s.get("/cuerpo.json") { ServidorMonitor.Resp(cuerpoJson(true).toString()) }
    s.get("/cam_pose.json") { q -> ServidorMonitor.Resp(camPoseJson(q["ts"]?.toLongOrNull()).toString()) }
    s.get("/depth.jpg") { asegurarDepth(); ServidorMonitor.Resp(jpegDepth(), "image/jpeg") }
    s.get("/depth.json") { asegurarDepth(); ServidorMonitor.Resp(depthJson()) }
    s.stream("/depth.mjpg") { q, out ->
      var ultimo = 0L
      s.mjpeg(out, (q["fps"]?.toDoubleOrNull() ?: 5.0).coerceIn(0.5, 20.0)) {
        asegurarDepth()
        val f = depthFrame
        if (f != null && f.tNs == ultimo) null else { ultimo = f?.tNs ?: 0L; jpegDepth() }
      }
    }
    s.get("/render.jpg") { q -> ServidorMonitor.Resp(jpegRender(q["w"]?.toIntOrNull() ?: 640) ?: ByteArray(0), "image/jpeg") }
    s.stream("/cam.mjpg") { q, out ->
      var ultimo = -1L
      // por defecto 864 px a 8 fps y calidad 65 (el Wi-Fi lo comparte el AirPlay del proyector de la mesa); ?fps=&w=&q= para subirlo
      val w = q["w"]?.toIntOrNull() ?: 864; val cq = (q["q"]?.toIntOrNull() ?: 65).coerceIn(30, 95)
      s.mjpeg(out, (q["fps"]?.toDoubleOrNull() ?: 8.0).coerceIn(0.5, 30.0)) {
        val cam = camera
        if (cam == null || !cam.abierta) { Thread.sleep(800); jpegCamara(false) } else jpegCamara(false, ultimo, w, cq)?.also { ultimo = ultimoJpegCamIndice }
      }
    }
    s.stream("/render.mjpg") { q, out ->
      val w = q["w"]?.toIntOrNull() ?: 640
      s.mjpeg(out, (q["fps"]?.toDoubleOrNull() ?: 5.0).coerceIn(0.5, 30.0)) { jpegRender(w) }
    }
    // página y three.js desde assets/monitor/
    s.get("*") { q ->
      val ruta = q["_ruta"].orEmpty().trimStart('/').ifEmpty { "index.html" }
      if (ruta.contains("..")) ServidorMonitor.Resp("{}", codigo = 404) else {
        val datos = runCatching { assets.open("monitor/$ruta").use { it.readBytes() } }.getOrNull()
        if (datos == null) ServidorMonitor.Resp("{\"error\":\"no existe /$ruta\"}", codigo = 404)
        else ServidorMonitor.Resp(datos, when {
          ruta.endsWith(".html") -> "text/html; charset=utf-8"
          ruta.endsWith(".js") -> "text/javascript; charset=utf-8"
          ruta.endsWith(".css") -> "text/css"
          else -> "application/octet-stream"
        })
      }
    }
  }

  // ----------------------------------------------------------------- estado
  private fun estadoTexto(): String {
    val cam = camera
    val c = calib
    val sb = StringBuilder()
    sb.append("CALIB: ${calibrador.fase} ${"%.0f".format(calibrador.progreso * 100)}% · ${calibrador.mensaje}\n")
    sb.append("salida: ${salida.activa} (pref ${salida.preferencia}) · render ${"%.0f".format(renderer.fps)} fps · ${renderer.destinoInfo}\n")
    sb.append("cámara: ${estadoCamara()}${if (!tieneFoco) " · app SIN foco" else ""}\n")
    sb.append("sala: $resumenSala · raycaster $raycasterEstado\n")
    if (c != null) {
      sb.append("modelo: ${c.tipo} · gray ${c.grayMedianaPx?.let { "%.2f".format(it) } ?: "—"} px · lazo ${c.lazoMedianaPx?.let { "%.2f".format(it) } ?: "—"} px · vistas ${calibrador.vistas.size}\n")
      sb.append("  ${c.modelo.resumen()}\n")
    } else sb.append("modelo: sin calibrar\n")
    val a = renderer.ajuste
    sb.append("modo: ${if (modoFijo) "FIJO" else "CABEZA"} · ver: $vista · efecto: ${RendererProyector.NOMBRES_EFECTO[renderer.efecto]} · lat ${latenciaPrediccionMs.toInt()} ms\n")
    sb.append("ajuste: dx=%.1f dy=%.1f rot=%.2f esc=%.4f · lazo lat %s ms".format(a.dx, a.dy, a.rotDeg, a.escala, calibrador.latenciaLazoMs?.let { "%.0f".format(it) } ?: "—"))
    renderer.ultimoError?.let { sb.append("\nGL: $it") }
    sb.append("\nhttp :8787 ${if (servidor.escuchando) "escuchando" else "CAÍDO ${servidor.error ?: ""}"}")
    return sb.toString()
  }

  private fun refrescarUi() {
    main.post {
      statusView?.text = estadoTexto()
      btnModo?.text = if (modoFijo) "MODO: FIJO" else "MODO: CABEZA"
      btnEfecto?.text = "EFECTO: ${RendererProyector.NOMBRES_EFECTO[renderer.efecto].uppercase()}"
      btnSalida?.text = "SALIDA: ${salida.preferencia.name}"
      btnProyectar?.text = "VER: ${vista.uppercase()}"
    }
  }

  private val tick = object : Runnable {
    override fun run() {
      refrescarUi()
      val ahora = SystemClock.elapsedRealtime()
      if (ahora % 5000 < 500) runCatching { cargarCalibMesa() }
      if (ahora - ultimoLog > 10_000) {
        ultimoLog = ahora
        Log.i(TAG, "[estado] " + estadoTexto().replace("\n", " | "))
      }
      main.postDelayed(this, 500)
    }
  }
}
