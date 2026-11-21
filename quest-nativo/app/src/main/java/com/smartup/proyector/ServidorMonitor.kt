package com.smartup.proyector

import android.util.Log
import java.io.BufferedInputStream
import java.io.IOException
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketException
import java.net.URLDecoder
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger

/**
 * Servidor HTTP mínimo (sin librerías) para mirar la app desde el Mac: http://<casco>:8787/
 * Solo GET, una petición por conexión (Connection: close). Las rutas de "stream" (MJPEG) se quedan
 * escribiendo hasta que el cliente cierra.
 */
class ServidorMonitor(private val puerto: Int = 8787) {
  companion object {
    private const val TAG = "Proyector"
    private const val MAX_CONEXIONES = 24
  }

  class Resp(val datos: ByteArray, val tipo: String = "application/json; charset=utf-8", val codigo: Int = 200,
             val cabeceras: Map<String, String> = emptyMap()) {
    constructor(texto: String, tipo: String = "application/json; charset=utf-8", codigo: Int = 200) :
        this(texto.toByteArray(Charsets.UTF_8), tipo, codigo)
  }

  private val rutas = HashMap<String, (Map<String, String>) -> Resp>()
  private val streams = HashMap<String, (Map<String, String>, OutputStream) -> Unit>()
  private val pool = Executors.newCachedThreadPool { r -> Thread(r, "MonitorHttp").apply { isDaemon = true } }
  private val activas = AtomicInteger(0)
  private var socket: ServerSocket? = null
  @Volatile var peticiones = 0L
    private set
  @Volatile var error: String? = null
    private set
  val clientesActivos get() = activas.get()

  fun get(ruta: String, f: (Map<String, String>) -> Resp) { rutas[ruta] = f }
  fun stream(ruta: String, f: (Map<String, String>, OutputStream) -> Unit) { streams[ruta] = f }

  @Volatile private var parar = false
  @Volatile var escuchando = false
    private set

  /** Escucha en :puerto; si el bind o el accept fallan, reintenta cada 3 s hasta detener(). */
  fun iniciar() {
    if (socket != null || hiloAccept?.isAlive == true) return
    parar = false
    hiloAccept = Thread({
      while (!parar) {
        try {
          val ss = ServerSocket()
          ss.reuseAddress = true
          ss.bind(InetSocketAddress(puerto))
          socket = ss
          escuchando = true; error = null
          Log.i(TAG, "[http] monitor escuchando en :$puerto")
          while (!ss.isClosed && !parar) {
            val s = ss.accept()
            if (activas.get() >= MAX_CONEXIONES) { runCatching { responder(s.getOutputStream(), Resp("{\"error\":\"ocupado\"}", codigo = 503)); s.close() }; continue }
            activas.incrementAndGet()
            pool.execute { try { atender(s) } finally { activas.decrementAndGet(); runCatching { s.close() } } }
          }
        } catch (e: Exception) {
          error = e.toString()
          if (!parar) Log.e(TAG, "[http] servidor: $e (reintento en 3 s)")
        } finally {
          escuchando = false
          runCatching { socket?.close() }; socket = null
        }
        if (!parar) runCatching { Thread.sleep(3000) }
      }
    }, "MonitorAccept").apply { isDaemon = true; start() }
  }
  private var hiloAccept: Thread? = null

  fun detener() {
    parar = true
    runCatching { socket?.close() }
    socket = null
  }

  private fun atender(s: Socket) {
    s.soTimeout = 10_000
    s.tcpNoDelay = true
    val inp = BufferedInputStream(s.getInputStream())
    val linea = leerLinea(inp) ?: return
    while (true) { val h = leerLinea(inp) ?: break; if (h.isEmpty()) break }
    val partes = linea.split(" ")
    if (partes.size < 2) return
    val metodo = partes[0]
    val out = s.getOutputStream()
    if (metodo == "OPTIONS") {
      out.write(("HTTP/1.1 204 No Content\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Allow-Private-Network: true\r\n" +
          "Access-Control-Allow-Methods: GET\r\nConnection: close\r\n\r\n").toByteArray())
      return
    }
    val (ruta, q) = parsearUrl(partes[1])
    peticiones++
    try {
      val st = streams[ruta]
      if (st != null) {
        s.soTimeout = 0
        st(q, out)
        return
      }
      val f = rutas[ruta] ?: rutas["*"]
      val r = if (f == null) Resp("{\"error\":\"no existe $ruta\"}", codigo = 404) else f(q + mapOf("_ruta" to ruta))
      responder(out, r, metodo == "HEAD")
    } catch (_: SocketException) {
    } catch (_: IOException) {
    } catch (e: Exception) {
      Log.w(TAG, "[http] $ruta: ${e.message}")
      runCatching { responder(out, Resp("{\"error\":${org.json.JSONObject.quote(e.toString())}}", codigo = 500)) }
    }
  }

  private fun responder(out: OutputStream, r: Resp, soloCabecera: Boolean = false) {
    val estado = when (r.codigo) { 200 -> "OK"; 404 -> "Not Found"; 503 -> "Service Unavailable"; else -> "Error" }
    out.write(("HTTP/1.1 ${r.codigo} $estado\r\nContent-Type: ${r.tipo}\r\nContent-Length: ${r.datos.size}\r\n" +
        "Cache-Control: no-store\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Expose-Headers: *\r\n" +
        r.cabeceras.entries.joinToString("") { "${it.key}: ${it.value}\r\n" } + "Connection: close\r\n\r\n").toByteArray())
    if (!soloCabecera) out.write(r.datos)
    out.flush()
  }

  private fun leerLinea(inp: BufferedInputStream): String? {
    val sb = StringBuilder()
    while (true) {
      val c = inp.read()
      if (c < 0) return if (sb.isEmpty()) null else sb.toString()
      if (c == '\n'.code) return sb.toString().trimEnd('\r')
      if (sb.length > 8192) return null
      sb.append(c.toChar())
    }
  }

  private fun parsearUrl(u: String): Pair<String, Map<String, String>> {
    val i = u.indexOf('?')
    val ruta = if (i < 0) u else u.substring(0, i)
    val q = HashMap<String, String>()
    if (i >= 0) for (par in u.substring(i + 1).split("&")) {
      if (par.isEmpty()) continue
      val k = par.substringBefore("="); val v = par.substringAfter("=", "")
      q[URLDecoder.decode(k, "UTF-8")] = URLDecoder.decode(v, "UTF-8")
    }
    return URLDecoder.decode(ruta, "UTF-8") to q
  }

  /** Escribe un stream MJPEG con `cuadro()` a `fps` hasta que el cliente cierra. */
  fun mjpeg(out: OutputStream, fps: Double, cuadro: () -> ByteArray?) {
    out.write(("HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=cuadro\r\n" +
        "Cache-Control: no-store\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n").toByteArray())
    out.flush()
    val periodo = (1000.0 / fps).toLong()
    try {
      while (true) {
        val t0 = System.currentTimeMillis()
        val jpg = cuadro()
        if (jpg != null) {
          out.write("--cuadro\r\nContent-Type: image/jpeg\r\nContent-Length: ${jpg.size}\r\n\r\n".toByteArray())
          out.write(jpg)
          out.write("\r\n".toByteArray())
          out.flush()
        }
        val dt = System.currentTimeMillis() - t0
        Thread.sleep((periodo - dt).coerceAtLeast(5))
      }
    } catch (_: IOException) {
    } catch (_: InterruptedException) {
    }
  }
}
