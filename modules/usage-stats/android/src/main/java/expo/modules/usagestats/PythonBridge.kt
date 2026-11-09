package expo.modules.usagestats

import android.content.Context
import android.util.Log
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

/**
 * The app's connection to the embedded Python backend.
 *
 * WHAT THIS REPLACES
 * ------------------
 * Every analysis the app shows used to be an HTTP request to a FastAPI server
 * on a laptop. This calls the same Python functions directly, inside the
 * app's own process: no server, no socket, no port, no address to go stale
 * when the phone leaves the network.
 *
 * WHY IT SPEAKS JSON RATHER THAN RICH OBJECTS
 * -------------------------------------------
 * Converting a nested analysis result - dates, enums, numpy floats, lists of
 * dataclasses - into Kotlin types and then into JavaScript would mean writing
 * and maintaining that mapping twice over, with a silent field drop as the
 * failure mode. A JSON string crosses both boundaries unchanged, and the
 * React Native layer parses it exactly as it parsed an HTTP response body.
 *
 * The Python side returns {"status", "body"} deliberately, mirroring HTTP, so
 * the existing TypeScript client interprets a refusal the same way whichever
 * transport produced it.
 */
object PythonBridge {

  private const val TAG = "PythonBridge"

  /** Set once configure() has succeeded; calls before that are refused. */
  @Volatile
  private var configured = false

  /**
   * Whether the embedded interpreter is present in this build.
   *
   * False in any build without the Chaquopy plugin - which the app must
   * survive rather than crash on, because the capture, charts and stored
   * history do not depend on Python.
   */
  fun isAvailable(): Boolean =
    try {
      Class.forName("com.chaquo.python.Python")
      true
    } catch (_: Throwable) {
      false
    }

  /**
   * Start the interpreter and point the backend at the app's own storage.
   *
   * Returns the JSON report from mobile_api.configure(), or a JSON error.
   * Errors are RETURNED rather than thrown: an exception crossing the JNI
   * boundary loses its Python traceback and surfaces as an opaque failure,
   * which is the hardest kind to diagnose on a device.
   *
   * Safe to call repeatedly; the work happens once.
   */
  @Synchronized
  fun configure(context: Context): String {
    if (configured) return """{"ok":true,"alreadyConfigured":true}"""

    return try {
      if (!Python.isStarted()) {
        Python.start(AndroidPlatform(context.applicationContext))
      }

      // filesDir is the app's private, writable directory: deleted with the
      // app, invisible to other apps, and never on external storage where the
      // user's usage history would be world-readable.
      val filesDir = context.applicationContext.filesDir.absolutePath

      // No artifacts directory is passed. The trained models ship inside the
      // Python source tree, so config.py resolves them from its own location
      // exactly as it does on the laptop - one resolution rule, not two.
      val report = module().callAttr("configure", filesDir).toString()

      configured = report.contains("\"ok\": true") || report.contains("\"ok\":true")
      Log.i(TAG, "configure -> $report")
      report
    } catch (t: Throwable) {
      Log.e(TAG, "configure failed", t)
      """{"ok":false,"error":${quote(t.toString())}}"""
    }
  }

  /**
   * Run one backend endpoint by name.
   *
   * [paramsJson] is the query or body the HTTP call used to carry. The result
   * is {"status", "body"} as JSON - never an exception, so a failure reaches
   * the UI as a message the user can read instead of a crash.
   */
  fun call(context: Context, name: String, paramsJson: String): String {
    if (!isAvailable()) {
      return errorResponse(
        503,
        "The embedded Python backend is not present in this build. It requires " +
          "a development build with the Chaquopy plugin applied."
      )
    }

    if (!configured) {
      val report = configure(context)
      if (!configured) {
        return errorResponse(503, "Python backend could not start: $report")
      }
    }

    return try {
      module().callAttr("call", name, paramsJson).toString()
    } catch (t: Throwable) {
      Log.e(TAG, "call($name) failed", t)
      errorResponse(500, t.toString())
    }
  }

  /** Endpoint names this build serves, for diagnostics. */
  fun routes(): String =
    try {
      module().callAttr("routes").toString()
    } catch (t: Throwable) {
      "[]"
    }

  private fun module(): PyObject = Python.getInstance().getModule("app.mobile_api")

  private fun errorResponse(status: Int, detail: String): String =
    """{"status":$status,"body":{"detail":${quote(detail)}}}"""

  /**
   * Minimal JSON string escaping.
   *
   * Only used for error text, which can contain quotes, backslashes and
   * newlines from a Python traceback. Without escaping, an error message
   * would produce malformed JSON and the app would report a parse failure
   * instead of the actual problem - the error about the error.
   */
  private fun quote(value: String): String {
    val sb = StringBuilder("\"")
    for (ch in value) {
      when (ch) {
        '"' -> sb.append("\\\"")
        '\\' -> sb.append("\\\\")
        '\n' -> sb.append("\\n")
        '\r' -> sb.append("\\r")
        '\t' -> sb.append("\\t")
        else ->
          if (ch < ' ') sb.append("\\u%04x".format(ch.code)) else sb.append(ch)
      }
    }
    return sb.append("\"").toString()
  }
}
