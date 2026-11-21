package com.smartup.proyector

import android.graphics.Bitmap
import android.opengl.EGL14
import android.opengl.EGLConfig
import android.opengl.EGLContext
import android.opengl.EGLDisplay
import android.opengl.EGLSurface
import android.opengl.GLES30
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.util.Log
import android.view.Choreographer
import android.view.Surface
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.cos
import kotlin.math.sin

/**
 * Renderer GLES 3 propio, en su hilo y con su EGL, que dibuja la imagen del proyector (textura
 * 1920x1080) y la presenta en una Surface de destino: la del display HDMI (Presentation, salida A)
 * o la del panel fijo a la cabeza (salida B).
 *
 * Pasada 1 → FBO 1920x1080: patrón (negro/blanco/Gray/carta de prueba) o el mapeo (malla de la
 * sala vista desde el proyector calibrado + puntos de verificación).
 * Pasada 2 → destino: identidad exacta (texelFetch) para los patrones; para el mapeo, ajuste manual
 * 2D + rejilla residual de la calibración en lazo cerrado.
 *
 * Coordenadas de textura: u = columna, v = fila desde ARRIBA, píxel i cubre [i, i+1).
 */
class RendererProyector(val ancho: Int = 1920, val alto: Int = 1080) {
  companion object {
    private const val TAG = "Proyector"
    const val EF_ARISTAS = 0
    const val EF_PLANOS = 1
    const val EF_REJILLA = 2
    const val EF_BLANCO = 3
    const val EF_PROFUNDIDAD = 4
    val NOMBRES_EFECTO = listOf("aristas", "planos", "rejilla", "blanco", "profundidad")
    const val FLOATS_VERT = 13
  }

  sealed class Contenido {
    object Negro : Contenido()
    object Blanco : Contenido()
    data class Gray(val eje: Int, val bit: Int, val inverso: Boolean) : Contenido()
    object Prueba : Contenido()
    object Mapeo : Contenido()
    /** Imagen fija w×h en ARGB (fila 0 = arriba), p. ej. la grilla de marcadores QR. */
    class Imagen(val px: IntArray, val w: Int, val h: Int, val nombre: String) : Contenido()

    override fun toString(): String = when (this) {
      is Gray -> "gray(${"xy"[eje]}${bit}${if (inverso) "i" else ""})"
      is Imagen -> "imagen($nombre)"
      else -> this::class.simpleName ?: "?"
    }
  }

  class Ajuste2D(val dx: Double = 0.0, val dy: Double = 0.0, val rotDeg: Double = 0.0, val escala: Double = 1.0) {
    /** Matriz 3x3 row-major que lleva coordenadas del modelo a la salida final. */
    fun matriz(w: Int, h: Int): DoubleArray {
      val cx = w / 2.0; val cy = h / 2.0
      val a = Math.toRadians(rotDeg); val c = cos(a) * escala; val s = sin(a) * escala
      // T(c + d) · R·S · T(-c)
      return doubleArrayOf(c, -s, cx + dx - (c * cx - s * cy), s, c, cy + dy - (s * cx + c * cy), 0.0, 0.0, 1.0)
    }
  }

  class Malla(val verts: FloatArray, val version: Int) { val nVerts get() = verts.size / FLOATS_VERT }

  // ----------------------------------------------------------------- estado (cualquier hilo)
  @Volatile private var contenido: Contenido = Contenido.Negro
  @Volatile private var contenidoId = 0
  private val presentado = ConcurrentHashMap<Int, Long>()

  @Volatile var modelo: ModeloProyector? = null
  @Volatile var rejilla: RejillaCorreccion? = null
  @Volatile var rejillaVersion = 0
  @Volatile var usarRejilla = true
  @Volatile var ajuste = Ajuste2D()
  @Volatile var efecto = EF_ARISTAS
  @Volatile var malla: Malla? = null
  @Volatile var puntos: FloatArray? = null       // xyz mundo, puntos de verificación
  @Volatile var tamPunto = 16f
  /** Segmentos 2D en px de la textura (x0, y0, x1, y1, ...), p. ej. la grilla ArUco esperada; se dibujan en cian sobre el mapeo. */
  @Volatile var lineas2d: FloatArray? = null
  @Volatile var mostrarMalla = true
  /** Devuelve la 4x4 cabeza ← mundo para este cuadro (modo cabeza con predicción o modo fijo). */
  @Volatile var vista: (() -> DoubleArray?)? = null
  /** Origen del proyector en el mundo (para el efecto profundidad). */
  @Volatile var origenProyector = floatArrayOf(0f, 0f, 0f)

  @Volatile var cuadros = 0L
    private set
  @Volatile var fps = 0.0
    private set
  @Volatile var destinoInfo = "sin destino"
    private set
  @Volatile var ultimoError: String? = null
    private set

  fun mostrar(c: Contenido): Int {
    synchronized(this) {
      contenido = c
      contenidoId++
      return contenidoId
    }
  }

  fun contenidoActual(): Contenido = contenido

  /** Tiempo (elapsedRealtimeNanos) del primer swap con el contenido `id`, o null si aún no. */
  fun tiempoPresentado(id: Int): Long? = presentado[id]

  // ----------------------------------------------------------------- hilo GL
  private var thread: HandlerThread? = null
  private var handler: Handler? = null
  private var eglDisplay: EGLDisplay = EGL14.EGL_NO_DISPLAY
  private var eglContext: EGLContext = EGL14.EGL_NO_CONTEXT
  private var eglConfig: EGLConfig? = null
  private var pbuffer: EGLSurface = EGL14.EGL_NO_SURFACE
  private var ventana: EGLSurface = EGL14.EGL_NO_SURFACE
  private var destW = 0
  private var destH = 0
  private var corriendo = false

  private var progPatron = 0
  private var progMalla = 0
  private var progPuntos = 0
  private var progWarp = 0
  private var progImagen = 0
  private var progLineas = 0
  private var texImagen = 0
  private var imagenSubida: Contenido.Imagen? = null
  private var vaoVacio = 0
  private var fbo = 0
  private var texFbo = 0
  private var rboDepth = 0
  private var texRej = 0
  private var rejSubida = -1
  private var vboMalla = 0
  private var vaoMalla = 0
  private var mallaSubida = -1
  private var nVertsMalla = 0
  private var vboPuntos = 0
  private var vaoPuntos = 0
  private val t0 = SystemClock.elapsedRealtimeNanos()
  private var tFps = 0L
  private var cuadrosFps = 0L
  private val pedidosCaptura = ArrayList<(Bitmap?) -> Unit>()
  private val pedidosChicos = ArrayList<Pair<Int, (Bitmap?) -> Unit>>()
  private var fboChico = 0
  private var texChico = 0
  private var chicoW = 0
  private var chicoH = 0

  fun iniciar() {
    if (thread != null) return
    thread = HandlerThread("ProyectorGL").apply { start() }
    handler = Handler(thread!!.looper)
    handler!!.post {
      runCatching { initEgl(); initGl() }.onFailure { Log.e(TAG, "[gl] init", it); ultimoError = it.message }
      corriendo = true
      Choreographer.getInstance().postFrameCallback(frameCb)
    }
  }

  private val frameCb = object : Choreographer.FrameCallback {
    override fun doFrame(frameTimeNanos: Long) {
      if (!corriendo) return
      try { dibujar() } catch (e: Exception) { Log.e(TAG, "[gl] cuadro", e); ultimoError = e.message }
      Choreographer.getInstance().postFrameCallback(this)
    }
  }

  /** Cambia la Surface de destino (null = ninguna). Bloquea hasta que el hilo GL la toma. */
  fun setDestino(surface: Surface?, w: Int, h: Int, nombre: String) {
    val h0 = handler ?: return
    val latch = CountDownLatch(1)
    h0.post {
      try {
        if (ventana != EGL14.EGL_NO_SURFACE) {
          EGL14.eglMakeCurrent(eglDisplay, pbuffer, pbuffer, eglContext)
          EGL14.eglDestroySurface(eglDisplay, ventana)
          ventana = EGL14.EGL_NO_SURFACE
        }
        if (surface != null && surface.isValid) {
          ventana = EGL14.eglCreateWindowSurface(eglDisplay, eglConfig, surface, intArrayOf(EGL14.EGL_NONE), 0)
          if (ventana == EGL14.EGL_NO_SURFACE) {
            ultimoError = "eglCreateWindowSurface 0x%x".format(EGL14.eglGetError())
            Log.e(TAG, "[gl] $ultimoError")
          } else {
            destW = w; destH = h
          }
        }
        destinoInfo = if (ventana != EGL14.EGL_NO_SURFACE) "$nombre ${w}x$h" else "sin destino"
        Log.i(TAG, "[gl] destino: $destinoInfo")
      } finally { latch.countDown() }
    }
    latch.await(2, TimeUnit.SECONDS)
  }

  /** Lee el FBO (lo que se manda al proyector) como Bitmap, en el hilo GL. */
  fun capturar(cb: (Bitmap?) -> Unit) {
    synchronized(pedidosCaptura) { pedidosCaptura.add(cb) }
  }

  /** Igual que capturar pero reducido a `w` de ancho (blit en la GPU, lectura chica): para el monitor. */
  fun capturarChica(w: Int, cb: (Bitmap?) -> Unit) {
    synchronized(pedidosCaptura) { pedidosChicos.add(w to cb) }
  }

  fun detener() {
    val h0 = handler ?: return
    h0.post {
      corriendo = false
      runCatching {
        if (ventana != EGL14.EGL_NO_SURFACE) EGL14.eglDestroySurface(eglDisplay, ventana)
        if (pbuffer != EGL14.EGL_NO_SURFACE) EGL14.eglDestroySurface(eglDisplay, pbuffer)
        EGL14.eglMakeCurrent(eglDisplay, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT)
        EGL14.eglDestroyContext(eglDisplay, eglContext)
        EGL14.eglTerminate(eglDisplay)
      }
      ventana = EGL14.EGL_NO_SURFACE
    }
    thread?.quitSafely(); thread = null; handler = null
  }

  private fun initEgl() {
    eglDisplay = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY)
    val ver = IntArray(2)
    check(EGL14.eglInitialize(eglDisplay, ver, 0, ver, 1)) { "eglInitialize" }
    val attrs = intArrayOf(
        EGL14.EGL_RED_SIZE, 8, EGL14.EGL_GREEN_SIZE, 8, EGL14.EGL_BLUE_SIZE, 8, EGL14.EGL_ALPHA_SIZE, 8,
        EGL14.EGL_RENDERABLE_TYPE, 0x40 /* EGL_OPENGL_ES3_BIT_KHR */,
        EGL14.EGL_SURFACE_TYPE, EGL14.EGL_WINDOW_BIT or EGL14.EGL_PBUFFER_BIT,
        EGL14.EGL_NONE)
    val cfgs = arrayOfNulls<EGLConfig>(1); val num = IntArray(1)
    check(EGL14.eglChooseConfig(eglDisplay, attrs, 0, cfgs, 0, 1, num, 0) && num[0] > 0) { "eglChooseConfig" }
    eglConfig = cfgs[0]
    eglContext = EGL14.eglCreateContext(eglDisplay, eglConfig, EGL14.EGL_NO_CONTEXT,
        intArrayOf(EGL14.EGL_CONTEXT_CLIENT_VERSION, 3, EGL14.EGL_NONE), 0)
    check(eglContext != EGL14.EGL_NO_CONTEXT) { "eglCreateContext" }
    pbuffer = EGL14.eglCreatePbufferSurface(eglDisplay, eglConfig, intArrayOf(EGL14.EGL_WIDTH, 1, EGL14.EGL_HEIGHT, 1, EGL14.EGL_NONE), 0)
    check(EGL14.eglMakeCurrent(eglDisplay, pbuffer, pbuffer, eglContext)) { "eglMakeCurrent" }
    Log.i(TAG, "[gl] EGL ${ver[0]}.${ver[1]} GL=${GLES30.glGetString(GLES30.GL_VERSION)} ${GLES30.glGetString(GLES30.GL_RENDERER)}")
  }

  // ----------------------------------------------------------------- shaders
  private val vsPantalla = """#version 300 es
    void main() {
      vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
      gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
    }"""

  private val fsPatron = """#version 300 es
    precision highp float; precision highp int;
    uniform int uModo; uniform int uEje; uniform int uBit; uniform int uInv; uniform vec2 uTam;
    out vec4 o;
    void main() {
      int ui = int(gl_FragCoord.x);
      int vi = int(uTam.y) - 1 - int(gl_FragCoord.y);
      if (uModo == 0) { o = vec4(0.0, 0.0, 0.0, 1.0); return; }
      if (uModo == 1) { o = vec4(1.0); return; }
      if (uModo == 2) {
        int c = (uEje == 0) ? ui : vi;
        int g = c ^ (c >> 1);
        int b = (g >> uBit) & 1;
        if (uInv == 1) b = 1 - b;
        o = vec4(vec3(float(b)), 1.0); return;
      }
      // carta de prueba: borde, rejilla cada 120 px, cruz central, esquinas de color (orientación)
      int W = int(uTam.x); int H = int(uTam.y);
      vec3 col = vec3(0.0);
      if (ui % 120 == 0 || vi % 120 == 0) col = vec3(0.35);
      if (ui % 480 == 0 || vi % 480 == 0) col = vec3(0.8);
      if (abs(ui - W / 2) < 2 || abs(vi - H / 2) < 2) col = vec3(1.0, 1.0, 0.0);
      float r = length(vec2(float(ui - W / 2), float(vi - H / 2)));
      if (abs(r - 300.0) < 2.0) col = vec3(0.0, 1.0, 1.0);
      if (ui < 120 && vi < 120) col = vec3(1.0, 0.0, 0.0);          // arriba-izq rojo
      if (ui >= W - 120 && vi < 120) col = vec3(0.0, 1.0, 0.0);     // arriba-der verde
      if (ui < 120 && vi >= H - 120) col = vec3(0.0, 0.3, 1.0);     // abajo-izq azul
      if (ui >= W - 120 && vi >= H - 120) col = vec3(1.0);          // abajo-der blanco
      if (ui < 6 || vi < 6 || ui >= W - 6 || vi >= H - 6) col = vec3(1.0, 0.0, 1.0); // borde magenta
      o = vec4(col, 1.0);
    }"""

  private val fsImagen = """#version 300 es
    precision highp float;
    uniform sampler2D uTex; uniform vec2 uTam;
    out vec4 o;
    void main() {
      ivec2 p = ivec2(int(gl_FragCoord.x), int(uTam.y) - 1 - int(gl_FragCoord.y));
      o = vec4(texelFetch(uTex, p, 0).rgb, 1.0);
    }"""

  private val vsLineas = """#version 300 es
    layout(location = 0) in vec3 aPos;
    void main() { gl_Position = vec4(aPos, 1.0); }"""
  private val fsLineas = """#version 300 es
    precision mediump float;
    uniform vec4 uCol; out vec4 o;
    void main() { o = uCol; }"""

  private fun dibujarLineas2d(l: FloatArray) {
    val n = l.size / 2
    val v = FloatArray(n * 3)
    for (i in 0 until n) { v[i * 3] = 2f * l[i * 2] / ancho - 1f; v[i * 3 + 1] = 1f - 2f * l[i * 2 + 1] / alto; v[i * 3 + 2] = 0f }
    GLES30.glDisable(GLES30.GL_DEPTH_TEST)
    GLES30.glBindVertexArray(vaoPuntos)
    GLES30.glBindBuffer(GLES30.GL_ARRAY_BUFFER, vboPuntos)
    GLES30.glBufferData(GLES30.GL_ARRAY_BUFFER, v.size * 4, fb(v), GLES30.GL_DYNAMIC_DRAW)
    GLES30.glUseProgram(progLineas)
    GLES30.glUniform4f(loc(progLineas, "uCol"), 0f, 0.9f, 1f, 1f)
    GLES30.glLineWidth(3f)
    GLES30.glDrawArrays(GLES30.GL_LINES, 0, n)
  }

  private fun dibujarImagen(c: Contenido.Imagen) {
    if (texImagen == 0) { val ids = IntArray(1); GLES30.glGenTextures(1, ids, 0); texImagen = ids[0] }
    GLES30.glActiveTexture(GLES30.GL_TEXTURE0)
    GLES30.glBindTexture(GLES30.GL_TEXTURE_2D, texImagen)
    if (imagenSubida !== c) {
      // ARGB → RGBA bytes
      val bb = ByteBuffer.allocateDirect(c.w * c.h * 4).order(ByteOrder.nativeOrder())
      for (v in c.px) { bb.put((v shr 16 and 0xFF).toByte()); bb.put((v shr 8 and 0xFF).toByte()); bb.put((v and 0xFF).toByte()); bb.put(0xFF.toByte()) }
      bb.position(0)
      GLES30.glPixelStorei(GLES30.GL_UNPACK_ALIGNMENT, 1)
      GLES30.glTexImage2D(GLES30.GL_TEXTURE_2D, 0, GLES30.GL_RGBA8, c.w, c.h, 0, GLES30.GL_RGBA, GLES30.GL_UNSIGNED_BYTE, bb)
      GLES30.glTexParameteri(GLES30.GL_TEXTURE_2D, GLES30.GL_TEXTURE_MIN_FILTER, GLES30.GL_NEAREST)
      GLES30.glTexParameteri(GLES30.GL_TEXTURE_2D, GLES30.GL_TEXTURE_MAG_FILTER, GLES30.GL_NEAREST)
      imagenSubida = c
    }
    GLES30.glDisable(GLES30.GL_DEPTH_TEST)
    GLES30.glUseProgram(progImagen)
    GLES30.glUniform1i(loc(progImagen, "uTex"), 0)
    GLES30.glUniform2f(loc(progImagen, "uTam"), ancho.toFloat(), alto.toFloat())
    GLES30.glBindVertexArray(vaoVacio)
    GLES30.glDrawArrays(GLES30.GL_TRIANGLES, 0, 3)
  }

  private val vsMalla = """#version 300 es
    layout(location = 0) in vec3 aPos; layout(location = 1) in vec3 aNor; layout(location = 2) in vec3 aBar;
    layout(location = 3) in vec3 aCol; layout(location = 4) in float aTipo;
    uniform mat4 uMvp;
    out vec3 vPos; out vec3 vNor; out vec3 vBar; out vec3 vCol; out float vTipo;
    void main() {
      vPos = aPos; vNor = aNor; vBar = aBar; vCol = aCol; vTipo = aTipo;
      gl_Position = uMvp * vec4(aPos, 1.0);
    }"""

  private val fsMalla = """#version 300 es
    precision highp float;
    in vec3 vPos; in vec3 vNor; in vec3 vBar; in vec3 vCol; in float vTipo;
    uniform int uEfecto; uniform float uT; uniform vec3 uOrigen;
    out vec4 o;
    float borde(vec3 b, float g) {
      vec3 d = fwidth(b);
      vec3 a = smoothstep(vec3(0.0), d * g, b);
      return 1.0 - min(min(a.x, a.y), a.z);
    }
    void main() {
      vec3 n = normalize(vNor);
      float horiz = abs(n.y);
      float e = borde(vBar, vTipo > 0.5 ? 2.5 : 1.2);
      if (uEfecto == 0) {                       // aristas por color de etiqueta
        o = vec4(vCol * e, 1.0);
      } else if (uEfecto == 1) {                // planos resaltados
        o = vec4(vCol * (0.3 + 0.7 * e), 1.0);
      } else if (uEfecto == 2) {                // rejilla sobre superficies horizontales + barrido
        vec3 col = vCol * e * 0.35;
        if (horiz > 0.85) {
          vec2 q = vPos.xz / 0.10;
          vec2 g = abs(fract(q - 0.5) - 0.5) / max(fwidth(q), vec2(1e-4));
          float l = 1.0 - min(min(g.x, g.y), 1.0);
          float fase = fract(uT * 0.25) * 6.0 - 3.0;
          float db = (vPos.x + vPos.z) * 0.7 - fase;
          float barrido = exp(-db * db * 30.0);
          col = vec3(0.1, 1.0, 0.55) * l + vec3(1.0) * barrido * 0.7;
        }
        o = vec4(col, 1.0);
      } else if (uEfecto == 3) {                // blanco total (verificar cobertura)
        o = vec4(1.0);
      } else {                                  // profundidad desde el proyector
        float d = length(vPos - uOrigen);
        o = vec4(0.5 + 0.5 * cos(6.2831 * (d * 0.8 + vec3(0.0, 0.33, 0.67))), 1.0);
      }
    }"""

  private val vsPuntos = """#version 300 es
    layout(location = 0) in vec3 aPos;
    uniform mat4 uMvp; uniform float uTam;
    void main() { gl_Position = uMvp * vec4(aPos, 1.0); gl_PointSize = uTam; }"""

  private val fsPuntos = """#version 300 es
    precision mediump float;
    out vec4 o;
    void main() { vec2 c = gl_PointCoord * 2.0 - 1.0; if (dot(c, c) > 1.0) discard; o = vec4(1.0); }"""

  private val fsWarp = """#version 300 es
    precision highp float; precision highp int;
    uniform sampler2D uTex; uniform highp sampler2D uRej;
    uniform int uModo; uniform int uRejOn; uniform vec2 uTam; uniform vec2 uDest;
    uniform mat3 uSinv; uniform float uCelda; uniform ivec2 uNRej;
    out vec4 o;
    vec2 rej(vec2 u) {
      vec2 g = clamp(u / uCelda, vec2(0.0), vec2(uNRej - 1));
      ivec2 i0 = min(ivec2(g), uNRej - 2);
      vec2 f = g - vec2(i0);
      vec2 a = texelFetch(uRej, i0, 0).rg;
      vec2 b = texelFetch(uRej, i0 + ivec2(1, 0), 0).rg;
      vec2 c = texelFetch(uRej, i0 + ivec2(0, 1), 0).rg;
      vec2 d = texelFetch(uRej, i0 + ivec2(1, 1), 0).rg;
      return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
    }
    void main() {
      vec2 p = vec2(gl_FragCoord.x, uDest.y - gl_FragCoord.y) * (uTam / uDest);
      if (uModo == 0) {
        ivec2 ip = ivec2(p);
        o = texelFetch(uTex, ivec2(ip.x, int(uTam.y) - 1 - ip.y), 0);
        return;
      }
      vec3 q = uSinv * vec3(p, 1.0);
      vec2 s = q.xy / q.z;
      if (uRejOn == 1) s += rej(s);
      if (s.x < 0.0 || s.y < 0.0 || s.x >= uTam.x || s.y >= uTam.y) { o = vec4(0.0, 0.0, 0.0, 1.0); return; }
      o = texture(uTex, vec2(s.x / uTam.x, 1.0 - s.y / uTam.y));
    }"""

  private fun compilar(tipo: Int, src: String): Int {
    val s = GLES30.glCreateShader(tipo)
    GLES30.glShaderSource(s, src.trimIndent())
    GLES30.glCompileShader(s)
    val ok = IntArray(1)
    GLES30.glGetShaderiv(s, GLES30.GL_COMPILE_STATUS, ok, 0)
    if (ok[0] == 0) throw RuntimeException("shader: " + GLES30.glGetShaderInfoLog(s))
    return s
  }

  private fun programa(vs: String, fs: String): Int {
    val p = GLES30.glCreateProgram()
    GLES30.glAttachShader(p, compilar(GLES30.GL_VERTEX_SHADER, vs))
    GLES30.glAttachShader(p, compilar(GLES30.GL_FRAGMENT_SHADER, fs))
    GLES30.glLinkProgram(p)
    val ok = IntArray(1)
    GLES30.glGetProgramiv(p, GLES30.GL_LINK_STATUS, ok, 0)
    if (ok[0] == 0) throw RuntimeException("link: " + GLES30.glGetProgramInfoLog(p))
    return p
  }

  private fun initGl() {
    progPatron = programa(vsPantalla, fsPatron)
    progMalla = programa(vsMalla, fsMalla)
    progPuntos = programa(vsPuntos, fsPuntos)
    progWarp = programa(vsPantalla, fsWarp)
    progImagen = programa(vsPantalla, fsImagen)
    progLineas = programa(vsLineas, fsLineas)
    val ids = IntArray(4)
    GLES30.glGenVertexArrays(3, ids, 0)
    vaoVacio = ids[0]; vaoMalla = ids[1]; vaoPuntos = ids[2]
    GLES30.glGenBuffers(2, ids, 0)
    vboMalla = ids[0]; vboPuntos = ids[1]
    // FBO 1920x1080 con profundidad
    GLES30.glGenTextures(2, ids, 0)
    texFbo = ids[0]; texRej = ids[1]
    GLES30.glBindTexture(GLES30.GL_TEXTURE_2D, texFbo)
    GLES30.glTexImage2D(GLES30.GL_TEXTURE_2D, 0, GLES30.GL_RGBA8, ancho, alto, 0, GLES30.GL_RGBA, GLES30.GL_UNSIGNED_BYTE, null)
    GLES30.glTexParameteri(GLES30.GL_TEXTURE_2D, GLES30.GL_TEXTURE_MIN_FILTER, GLES30.GL_LINEAR)
    GLES30.glTexParameteri(GLES30.GL_TEXTURE_2D, GLES30.GL_TEXTURE_MAG_FILTER, GLES30.GL_LINEAR)
    GLES30.glTexParameteri(GLES30.GL_TEXTURE_2D, GLES30.GL_TEXTURE_WRAP_S, GLES30.GL_CLAMP_TO_EDGE)
    GLES30.glTexParameteri(GLES30.GL_TEXTURE_2D, GLES30.GL_TEXTURE_WRAP_T, GLES30.GL_CLAMP_TO_EDGE)
    GLES30.glGenRenderbuffers(1, ids, 0)
    rboDepth = ids[0]
    GLES30.glBindRenderbuffer(GLES30.GL_RENDERBUFFER, rboDepth)
    GLES30.glRenderbufferStorage(GLES30.GL_RENDERBUFFER, GLES30.GL_DEPTH_COMPONENT24, ancho, alto)
    GLES30.glGenFramebuffers(1, ids, 0)
    fbo = ids[0]
    GLES30.glBindFramebuffer(GLES30.GL_FRAMEBUFFER, fbo)
    GLES30.glFramebufferTexture2D(GLES30.GL_FRAMEBUFFER, GLES30.GL_COLOR_ATTACHMENT0, GLES30.GL_TEXTURE_2D, texFbo, 0)
    GLES30.glFramebufferRenderbuffer(GLES30.GL_FRAMEBUFFER, GLES30.GL_DEPTH_ATTACHMENT, GLES30.GL_RENDERBUFFER, rboDepth)
    val st = GLES30.glCheckFramebufferStatus(GLES30.GL_FRAMEBUFFER)
    check(st == GLES30.GL_FRAMEBUFFER_COMPLETE) { "FBO incompleto 0x%x".format(st) }
    GLES30.glBindFramebuffer(GLES30.GL_FRAMEBUFFER, 0)
    // rejilla residual (RG32F, se lee con texelFetch + bilineal manual)
    GLES30.glBindTexture(GLES30.GL_TEXTURE_2D, texRej)
    GLES30.glTexParameteri(GLES30.GL_TEXTURE_2D, GLES30.GL_TEXTURE_MIN_FILTER, GLES30.GL_NEAREST)
    GLES30.glTexParameteri(GLES30.GL_TEXTURE_2D, GLES30.GL_TEXTURE_MAG_FILTER, GLES30.GL_NEAREST)
    subirRejilla(RejillaCorreccion(ancho, alto))
    // atributos de la malla
    GLES30.glBindVertexArray(vaoMalla)
    GLES30.glBindBuffer(GLES30.GL_ARRAY_BUFFER, vboMalla)
    val stride = FLOATS_VERT * 4
    for ((loc, off, n) in listOf(Triple(0, 0, 3), Triple(1, 3, 3), Triple(2, 6, 3), Triple(3, 9, 3), Triple(4, 12, 1))) {
      GLES30.glEnableVertexAttribArray(loc)
      GLES30.glVertexAttribPointer(loc, n, GLES30.GL_FLOAT, false, stride, off * 4)
    }
    GLES30.glBindVertexArray(vaoPuntos)
    GLES30.glBindBuffer(GLES30.GL_ARRAY_BUFFER, vboPuntos)
    GLES30.glEnableVertexAttribArray(0)
    GLES30.glVertexAttribPointer(0, 3, GLES30.GL_FLOAT, false, 12, 0)
    GLES30.glBindVertexArray(0)
    Log.i(TAG, "[gl] listo: FBO ${ancho}x$alto")
  }

  private fun subirRejilla(r: RejillaCorreccion) {
    val data = FloatArray(r.nx * r.ny * 2)
    for (k in 0 until r.nx * r.ny) { data[k * 2] = r.dx[k]; data[k * 2 + 1] = r.dy[k] }
    GLES30.glBindTexture(GLES30.GL_TEXTURE_2D, texRej)
    GLES30.glTexImage2D(GLES30.GL_TEXTURE_2D, 0, GLES30.GL_RG32F, r.nx, r.ny, 0, GLES30.GL_RG, GLES30.GL_FLOAT, fb(data))
    rejNx = r.nx; rejNy = r.ny; rejCelda = r.celda
  }

  private var rejNx = 0
  private var rejNy = 0
  private var rejCelda = 40

  private fun fb(a: FloatArray): FloatBuffer =
      ByteBuffer.allocateDirect(a.size * 4).order(ByteOrder.nativeOrder()).asFloatBuffer().apply { put(a); position(0) }

  private fun loc(p: Int, n: String) = GLES30.glGetUniformLocation(p, n)

  // ----------------------------------------------------------------- cuadro
  private fun dibujar() {
    val (c, id) = synchronized(this) { Pair(contenido, contenidoId) }
    val hayPedidos = synchronized(pedidosCaptura) { pedidosCaptura.isNotEmpty() || pedidosChicos.isNotEmpty() }
    if (ventana == EGL14.EGL_NO_SURFACE && !hayPedidos) return
    GLES30.glBindFramebuffer(GLES30.GL_FRAMEBUFFER, fbo)
    GLES30.glViewport(0, 0, ancho, alto)
    var corregir = false
    when (c) {
      is Contenido.Mapeo -> { dibujarMapeo(); corregir = true }
      is Contenido.Imagen -> dibujarImagen(c)
      else -> {
        GLES30.glDisable(GLES30.GL_DEPTH_TEST)
        GLES30.glUseProgram(progPatron)
        val modo = when (c) { is Contenido.Negro -> 0; is Contenido.Blanco -> 1; is Contenido.Gray -> 2; else -> 3 }
        GLES30.glUniform1i(loc(progPatron, "uModo"), modo)
        if (c is Contenido.Gray) {
          GLES30.glUniform1i(loc(progPatron, "uEje"), c.eje)
          GLES30.glUniform1i(loc(progPatron, "uBit"), c.bit)
          GLES30.glUniform1i(loc(progPatron, "uInv"), if (c.inverso) 1 else 0)
        }
        GLES30.glUniform2f(loc(progPatron, "uTam"), ancho.toFloat(), alto.toFloat())
        GLES30.glBindVertexArray(vaoVacio)
        GLES30.glDrawArrays(GLES30.GL_TRIANGLES, 0, 3)
      }
    }
    // capturas pedidas (lo que va al proyector, antes del warp)
    val pedidos = synchronized(pedidosCaptura) { ArrayList(pedidosCaptura).also { pedidosCaptura.clear() } }
    if (pedidos.isNotEmpty()) {
      val bmp = runCatching { leerFbo() }.getOrNull()
      for (p in pedidos) runCatching { p(bmp) }
    }
    val chicos = synchronized(pedidosCaptura) { ArrayList(pedidosChicos).also { pedidosChicos.clear() } }
    if (chicos.isNotEmpty()) {
      for ((w, grupo) in chicos.groupBy { it.first }) {
        val bmp = runCatching { leerChico(w) }.onFailure { Log.w(TAG, "[gl] captura chica", it) }.getOrNull()
        for (p in grupo) runCatching { p.second(bmp) }
      }
      GLES30.glBindFramebuffer(GLES30.GL_FRAMEBUFFER, fbo)
    }
    if (ventana == EGL14.EGL_NO_SURFACE) {
      GLES30.glBindFramebuffer(GLES30.GL_FRAMEBUFFER, 0)
      return
    }
    EGL14.eglMakeCurrent(eglDisplay, ventana, ventana, eglContext)
    GLES30.glBindFramebuffer(GLES30.GL_FRAMEBUFFER, 0)
    GLES30.glViewport(0, 0, destW, destH)
    GLES30.glDisable(GLES30.GL_DEPTH_TEST)
    GLES30.glUseProgram(progWarp)
    GLES30.glActiveTexture(GLES30.GL_TEXTURE0)
    GLES30.glBindTexture(GLES30.GL_TEXTURE_2D, texFbo)
    GLES30.glUniform1i(loc(progWarp, "uTex"), 0)
    GLES30.glActiveTexture(GLES30.GL_TEXTURE1)
    val r = rejilla
    if (r != null && rejSubida != rejillaVersion) { subirRejilla(r); rejSubida = rejillaVersion }
    GLES30.glBindTexture(GLES30.GL_TEXTURE_2D, texRej)
    GLES30.glUniform1i(loc(progWarp, "uRej"), 1)
    GLES30.glUniform1i(loc(progWarp, "uModo"), if (corregir) 1 else 0)
    GLES30.glUniform1i(loc(progWarp, "uRejOn"), if (corregir && usarRejilla && r != null) 1 else 0)
    GLES30.glUniform2f(loc(progWarp, "uTam"), ancho.toFloat(), alto.toFloat())
    GLES30.glUniform2f(loc(progWarp, "uDest"), destW.toFloat(), destH.toFloat())
    GLES30.glUniform1f(loc(progWarp, "uCelda"), rejCelda.toFloat())
    GLES30.glUniform2i(loc(progWarp, "uNRej"), rejNx, rejNy)
    val s = Mat.inv33(ajuste.matriz(ancho, alto)) ?: Mat.ident3()
    // column-major para GL
    val sm = floatArrayOf(s[0].toFloat(), s[3].toFloat(), s[6].toFloat(), s[1].toFloat(), s[4].toFloat(), s[7].toFloat(),
        s[2].toFloat(), s[5].toFloat(), s[8].toFloat())
    GLES30.glUniformMatrix3fv(loc(progWarp, "uSinv"), 1, false, sm, 0)
    GLES30.glBindVertexArray(vaoVacio)
    GLES30.glDrawArrays(GLES30.GL_TRIANGLES, 0, 3)
    GLES30.glActiveTexture(GLES30.GL_TEXTURE0)
    if (!EGL14.eglSwapBuffers(eglDisplay, ventana)) {
      val err = EGL14.eglGetError()
      ultimoError = "eglSwapBuffers 0x%x".format(err)
      if (err == EGL14.EGL_BAD_SURFACE || err == EGL14.EGL_BAD_NATIVE_WINDOW) {
        EGL14.eglMakeCurrent(eglDisplay, pbuffer, pbuffer, eglContext)
        EGL14.eglDestroySurface(eglDisplay, ventana)
        ventana = EGL14.EGL_NO_SURFACE
        destinoInfo = "sin destino (perdido)"
      }
      return
    }
    val ahora = SystemClock.elapsedRealtimeNanos()
    if (!presentado.containsKey(id)) {
      presentado[id] = ahora
      if (presentado.size > 400) presentado.keys.filter { it < id - 300 }.forEach { presentado.remove(it) }
    }
    cuadros++
    cuadrosFps++
    if (ahora - tFps > 1_000_000_000L) {
      fps = cuadrosFps * 1e9 / (ahora - tFps).coerceAtLeast(1)
      tFps = ahora; cuadrosFps = 0
    }
  }

  private fun dibujarMapeo() {
    GLES30.glClearColor(0f, 0f, 0f, 1f)
    GLES30.glClearDepthf(1f)
    GLES30.glClear(GLES30.GL_COLOR_BUFFER_BIT or GLES30.GL_DEPTH_BUFFER_BIT)
    val m = modelo ?: return
    val v = vista?.invoke() ?: return
    val mvp = Mat.mul44(m.matrizGl(), v)
    val mvpGl = Mat.toGl(mvp)
    val ml = malla
    if (ml != null && mostrarMalla) {
      GLES30.glBindVertexArray(vaoMalla)
      if (mallaSubida != ml.version) {
        GLES30.glBindBuffer(GLES30.GL_ARRAY_BUFFER, vboMalla)
        GLES30.glBufferData(GLES30.GL_ARRAY_BUFFER, ml.verts.size * 4, fb(ml.verts), GLES30.GL_STATIC_DRAW)
        mallaSubida = ml.version
        nVertsMalla = ml.nVerts
      }
      GLES30.glEnable(GLES30.GL_DEPTH_TEST)
      GLES30.glDepthFunc(GLES30.GL_LEQUAL)
      GLES30.glUseProgram(progMalla)
      GLES30.glUniformMatrix4fv(loc(progMalla, "uMvp"), 1, false, mvpGl, 0)
      GLES30.glUniform1i(loc(progMalla, "uEfecto"), efecto)
      GLES30.glUniform1f(loc(progMalla, "uT"), ((SystemClock.elapsedRealtimeNanos() - t0) / 1e9).toFloat())
      val o = origenProyector
      GLES30.glUniform3f(loc(progMalla, "uOrigen"), o[0], o[1], o[2])
      GLES30.glDrawArrays(GLES30.GL_TRIANGLES, 0, nVertsMalla)
      GLES30.glDisable(GLES30.GL_DEPTH_TEST)
    }
    val pts = puntos
    if (pts != null && pts.isNotEmpty()) {
      GLES30.glBindVertexArray(vaoPuntos)
      GLES30.glBindBuffer(GLES30.GL_ARRAY_BUFFER, vboPuntos)
      GLES30.glBufferData(GLES30.GL_ARRAY_BUFFER, pts.size * 4, fb(pts), GLES30.GL_DYNAMIC_DRAW)
      GLES30.glUseProgram(progPuntos)
      GLES30.glUniformMatrix4fv(loc(progPuntos, "uMvp"), 1, false, mvpGl, 0)
      GLES30.glUniform1f(loc(progPuntos, "uTam"), tamPunto)
      GLES30.glDrawArrays(GLES30.GL_POINTS, 0, pts.size / 3)
    }
    lineas2d?.takeIf { it.size >= 4 }?.let { dibujarLineas2d(it) }
    GLES30.glBindVertexArray(0)
  }

  private fun leerChico(w0: Int): Bitmap {
    val w = w0.coerceIn(64, ancho); val h = (w.toLong() * alto / ancho).toInt()
    if (fboChico == 0 || chicoW != w || chicoH != h) {
      val ids = IntArray(1)
      if (texChico == 0) { GLES30.glGenTextures(1, ids, 0); texChico = ids[0] }
      GLES30.glBindTexture(GLES30.GL_TEXTURE_2D, texChico)
      GLES30.glTexImage2D(GLES30.GL_TEXTURE_2D, 0, GLES30.GL_RGBA8, w, h, 0, GLES30.GL_RGBA, GLES30.GL_UNSIGNED_BYTE, null)
      if (fboChico == 0) { GLES30.glGenFramebuffers(1, ids, 0); fboChico = ids[0] }
      GLES30.glBindFramebuffer(GLES30.GL_FRAMEBUFFER, fboChico)
      GLES30.glFramebufferTexture2D(GLES30.GL_FRAMEBUFFER, GLES30.GL_COLOR_ATTACHMENT0, GLES30.GL_TEXTURE_2D, texChico, 0)
      chicoW = w; chicoH = h
    }
    GLES30.glBindFramebuffer(GLES30.GL_READ_FRAMEBUFFER, fbo)
    GLES30.glBindFramebuffer(GLES30.GL_DRAW_FRAMEBUFFER, fboChico)
    GLES30.glBlitFramebuffer(0, 0, ancho, alto, 0, 0, w, h, GLES30.GL_COLOR_BUFFER_BIT, GLES30.GL_LINEAR)
    GLES30.glBindFramebuffer(GLES30.GL_FRAMEBUFFER, fboChico)
    val buf = ByteBuffer.allocateDirect(w * h * 4).order(ByteOrder.nativeOrder())
    GLES30.glReadPixels(0, 0, w, h, GLES30.GL_RGBA, GLES30.GL_UNSIGNED_BYTE, buf)
    val fila = w * 4
    val arr = ByteArray(w * h * 4)
    for (y in 0 until h) { buf.position((h - 1 - y) * fila); buf.get(arr, y * fila, fila) }
    val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
    bmp.copyPixelsFromBuffer(ByteBuffer.wrap(arr))
    return bmp
  }

  private fun leerFbo(): Bitmap {
    GLES30.glBindFramebuffer(GLES30.GL_FRAMEBUFFER, fbo)
    val buf = ByteBuffer.allocateDirect(ancho * alto * 4).order(ByteOrder.nativeOrder())
    GLES30.glReadPixels(0, 0, ancho, alto, GLES30.GL_RGBA, GLES30.GL_UNSIGNED_BYTE, buf)
    // GL: fila 0 = abajo → se invierte para que v = 0 quede arriba
    val fila = ancho * 4
    val arr = ByteArray(ancho * alto * 4)
    buf.position(0)
    for (y in 0 until alto) { buf.position((alto - 1 - y) * fila); buf.get(arr, y * fila, fila) }
    val bmp = Bitmap.createBitmap(ancho, alto, Bitmap.Config.ARGB_8888)
    bmp.copyPixelsFromBuffer(ByteBuffer.wrap(arr))
    return bmp
  }
}
