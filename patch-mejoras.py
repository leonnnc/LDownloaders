"""Aplica las mejoras del bloque 1 (A1, A2, B1, B3, B6).

Cada reemplazo exige coincidencia exacta y unica (count == 1) y el archivo se
valida con `ast.parse` antes de escribirse. Si algo no cuadra, no se toca nada.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "backend" / "app"
STATIC = ROOT / "backend" / "static"

PATCHES: list[tuple[Path, str, str, str]] = []


def patch(path: Path, old: str, new: str, label: str) -> None:
    PATCHES.append((path, old, new, label))


# ---------------------------------------------------------------------------
# A1 · metricas.py · contador aparte para fallos que no son del sitio
# ---------------------------------------------------------------------------
patch(
    APP / "metrics.py",
    """    last_success_at: float | None = None
    last_failure_at: float | None = None
    # Ventana deslizante de los últimos 50 resultados para una tasa realista.
    recent: Deque[bool] = field(default_factory=lambda: deque(maxlen=50))
""",
    """    last_success_at: float | None = None
    last_failure_at: float | None = None
    # Fallos que NO son de la plataforma: enlaces que no existen, videos
    # privados o borrados. Se cuentan aparte a propósito. Si entraran en
    # `recent`, cinco enlaces malos pegados por un visitante dejarían la
    # plataforma en «caído», y con eso el diagnóstico global anunciaría una
    # avería que no existe y aconsejaría reiniciar un servicio sano.
    user_errors: int = 0
    last_user_error: str | None = None
    # Ventana deslizante de los últimos 50 resultados para una tasa realista.
    recent: Deque[bool] = field(default_factory=lambda: deque(maxlen=50))
""",
    "A1 metrics: campos de error del usuario",
)

patch(
    APP / "metrics.py",
    """            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
""",
    """            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "user_errors": self.user_errors,
            "last_user_error": self.last_user_error,
""",
    "A1 metrics: exponer el contador",
)

patch(
    APP / "metrics.py",
    """    def platform(self, name: str) -> PlatformStats | None:
""",
    """    def record_user_error(self, platform: str, error: str | None = None) -> None:
        \"\"\"Anota un fallo del que no tiene la culpa la plataforma.

        El enlace que trajo el usuario no existe, el video es privado o lo
        borraron: eso no dice nada del sitio. Cuenta en su propio contador y
        deja intacta la ventana que decide si la plataforma está sana.
        \"\"\"
        key = (platform or "desconocido").lower()
        with self._lock:
            stats = self._platform(key)
            stats.user_errors += 1
            stats.last_user_error = (error or "")[:300]
            self._counters[f"platform.{key}.usuario"] += 1

    def platform(self, name: str) -> PlatformStats | None:
""",
    "A1 metrics: metodo record_user_error",
)

# ---------------------------------------------------------------------------
# A1 · downloader.py · clasificar antes de contabilizar
# ---------------------------------------------------------------------------
patch(
    APP / "downloader.py",
    '''def _is_permanent_error(exc: Exception) -> bool:
    """True si el error es definitivo y no tiene sentido reintentar."""
    message = f"{exc} {getattr(exc, 'raw', '')}".lower()
    return any(marker in message for marker in PERMANENT_ERROR_MARKERS)
''',
    '''def _is_permanent_error(exc: Exception) -> bool:
    """True si el error es definitivo y no tiene sentido reintentar."""
    message = f"{exc} {getattr(exc, 'raw', '')}".lower()
    return any(marker in message for marker in PERMANENT_ERROR_MARKERS)


def _record_failure(platform: str, exc: Exception | None, message: str) -> None:
    """Apunta el fallo donde corresponde: la plataforma o el enlace del usuario.

    Un enlace que no existe, un video privado o uno borrado no dicen nada del
    sitio. Antes se anotaban como fallo de la plataforma, y cinco enlaces malos
    bastaban para que el panel anunciara «Sistema caído» y aconsejara
    reiniciar un servicio que estaba perfecto (y para disparar una alerta al
    webhook que no correspondía a ninguna avería).

    Ante la duda se cuenta como fallo de la plataforma: solo se exculpa lo que
    se reconoce como permanente, con el mismo criterio que la lista de errores
    que no se reintentan. Así no se tapa una avería real.
    """
    if exc is not None and _is_permanent_error(exc):
        metrics.record_user_error(platform, message)
        return
    metrics.record(platform, False, message)
''',
    "A1 downloader: helper _record_failure",
)

patch(
    APP / "downloader.py",
    """    except EngineError as exc:
        metrics.record(platform, False, str(exc))
        metrics.inc("parse.fail")
        raise
""",
    """    except EngineError as exc:
        _record_failure(platform, exc, str(exc))
        metrics.inc("parse.fail")
        raise
""",
    "A1 downloader: fallo de parse (EngineError)",
)

patch(
    APP / "downloader.py",
    """    except Exception as exc:  # noqa: BLE001
        metrics.record(platform, False, str(exc))
        metrics.inc("parse.fail")
        raise EngineError(_human_error(exc)) from exc
""",
    """    except Exception as exc:  # noqa: BLE001
        _record_failure(platform, exc, str(exc))
        metrics.inc("parse.fail")
        raise EngineError(_human_error(exc)) from exc
""",
    "A1 downloader: fallo de parse (inesperado)",
)

patch(
    APP / "downloader.py",
    """    # Todas las estrategias fallaron.
    metrics.record(platform, False, str(last_error))
    metrics.inc("download.fail")
""",
    """    # Todas las estrategias fallaron.
    _record_failure(platform, last_error, str(last_error))
    metrics.inc("download.fail")
""",
    "A1 downloader: fallo de descarga",
)

# ---------------------------------------------------------------------------
# A2 + B3 · main.py · una sola fuente de estado, y guardia de disco
# ---------------------------------------------------------------------------
patch(
    APP / "main.py",
    '''@app.get("/api/health")
def health() -> dict:
    ffmpeg = ffmpeg_status()
    circuits_open = circuits.open_count()
    platform_states = metrics.platform_states()

    degraded = [name for name, state in platform_states.items() if state in ("degradado", "caido")]

    if not ffmpeg["available"] or circuits_open:
        overall = "degradado"
    elif degraded:
        overall = "degradado"
    else:
        overall = "ok"

    return {
        "status": overall,
        "version": "0.1.0",
''',
    '''# ---------------------------------------------------------------------------
# Espacio en disco
# ---------------------------------------------------------------------------
def _disk_usage_payload() -> dict:
    """Espacio libre donde se escriben los temporales."""
    try:
        usage = shutil.disk_usage(config.DATA_DIR)
    except OSError:
        return {"free_mb": None, "min_free_mb": config.MIN_FREE_MB}
    return {
        "free_mb": round(usage.free / (1024 * 1024), 1),
        "min_free_mb": config.MIN_FREE_MB,
        "ok": config.MIN_FREE_MB == 0
        or usage.free >= config.MIN_FREE_MB * 1024 * 1024,
    }


def _reject_if_disk_full() -> None:
    """Rechaza una descarga nueva si al disco le queda poco.

    Hasta ahora la única defensa era el TTL, y es por tiempo, no por tamaño:
    dos descargas grandes podían llenar el disco antes de que expirara nada, y
    entonces el servicio se caía de verdad. Si no se puede medir el espacio, se
    deja pasar: negarse a descargar porque no sabemos leer el disco sería peor
    que el riesgo que se evita.
    """
    if not config.MIN_FREE_MB:
        return
    try:
        free = shutil.disk_usage(config.DATA_DIR).free
    except OSError:
        return
    if free < config.MIN_FREE_MB * 1024 * 1024:
        libre = free / (1024 * 1024)
        raise HTTPException(
            507,
            f"No queda espacio suficiente en el servidor: hay {libre:.0f} MB libres "
            f"y se reservan {config.MIN_FREE_MB} MB. Intenta de nuevo más tarde.",
        )


@app.get("/api/health")
def health() -> dict:
    """Estado del servicio, con el mismo veredicto que el panel.

    El estado sale de `status.evaluate()`, la única fuente de verdad, que es la
    que alimentan el panel `/monitor` y el widget. Antes este endpoint
    recalculaba su propio veredicto (miraba FFmpeg, los circuitos y las
    plataformas por su cuenta) y no podía decir «caído» nunca: el mismo sistema
    daba dos diagnósticos distintos según a quién se le preguntara. Importa
    porque el HEALTHCHECK del contenedor pregunta justo aquí.
    """
    ffmpeg = ffmpeg_status()
    state = system_status.evaluate()
    circuits_open = state["circuits_open"]
    degraded = state["degraded"]

    return {
        "status": state["status"],
        "status_label": state["label"],
        "reasons": state["reasons"],
        "restart_advised": state["restart_advised"],
        "disk": _disk_usage_payload(),
        "version": "0.1.0",
''',
    "A2+B3 main: health unico + guardia de disco",
)

patch(
    APP / "main.py",
    """    await asyncio.to_thread(assert_public_target, url)

    if payload.kind == "mp3" and not ffmpeg_status()["available"]:
""",
    """    await asyncio.to_thread(assert_public_target, url)

    # El disco se comprueba aquí, no en el recolector: sin esto, el TTL (que es
    # por tiempo) es la única defensa y dos descargas grandes pueden llenar el
    # disco antes de que expire nada.
    _reject_if_disk_full()

    if payload.kind == "mp3" and not ffmpeg_status()["available"]:
""",
    "B3 main: comprobar disco antes de encolar",
)

# ---------------------------------------------------------------------------
# B6 · main.py · comparacion en tiempo constante + cupo en /api/pairing
# ---------------------------------------------------------------------------
patch(
    APP / "main.py",
    """    authorized = bool(config.ADMIN_TOKEN) and token == config.ADMIN_TOKEN
    return pairing.payload(request, authorized=authorized)
""",
    """    # Cupo propio: es un endpoint que se puede sondear sin coste y devuelve
    # `token_masked`, que ayuda a adivinar el token a ciegas.
    rate_limit(request, scope="pairing", limit=60)
    # Comparación en tiempo constante. Con `==` el tiempo de respuesta revela
    # cuántos caracteres iniciales se acertaron, carácter a carácter.
    authorized = _token_matches(token)
    return pairing.payload(request, authorized=authorized)
""",
    "B6 main: pairing en tiempo constante + cupo",
)

# ---------------------------------------------------------------------------
# B1 · main.py · el editor recibe la guardia del token
# ---------------------------------------------------------------------------
patch(
    APP / "main.py",
    """app.include_router(editor_api.build_router(rate_limit))""",
    """app.include_router(
    editor_api.build_router(rate_limit, guard=require_admin_if_configured)
)""",
    "B1 main: pasar la guardia al editor",
)

# ---------------------------------------------------------------------------
# B1 · editor_api.py · exigir el token en lo que cuesta CPU o disco
# ---------------------------------------------------------------------------
patch(
    APP / "editor_api.py",
    """from fastapi import APIRouter, HTTPException, Request""",
    """from fastapi import APIRouter, Depends, Header, HTTPException, Request""",
    "B1 editor_api: imports",
)

patch(
    APP / "editor_api.py",
    """def build_router(rate_limit: Callable[..., None]) -> APIRouter:
    router = APIRouter()
""",
    """def build_router(
    rate_limit: Callable[..., None],
    guard: Callable[[str | None], bool] | None = None,
) -> APIRouter:
    \"\"\"Rutas del editor de audio.

    `guard` es el `require_admin_if_configured` de `main.py`, que se recibe
    como parámetro para no importar `main` desde aquí (importación circular).
    Cuando hay `VDL_ADMIN_TOKEN` configurado, los endpoints que cuestan CPU o
    disco lo exigen: exportar es tiempo de CPU del servidor y subir archivos
    ocupa disco, así que no deben quedar abiertos en internet.

    Se protegen solo los cuatro endpoints que escriben (subir, traer de una
    descarga, borrar y exportar). Los de lectura (onda, reproducción y lista)
    se dejan abiertos a propósito: `<audio src>` y la descarga por rango no
    pueden llevar cabeceras, así que exigir el token ahí rompería la escucha.
    \"\"\"
    router = APIRouter()

    # Dependencia compartida: sin ella, `dependencies=[]` no hace nada.
    dependencies = []
    if guard is not None:
        async def _check_token(
            token: str | None = Header(None, alias="X-Admin-Token"),
        ) -> None:
            guard(token)

        dependencies.append(Depends(_check_token))
""",
    "B1 editor_api: firma + dependencia",
)

patch(
    APP / "editor_api.py",
    """    @router.post("/api/audio/upload", status_code=201)""",
    """    @router.post("/api/audio/upload", status_code=201, dependencies=dependencies)""",
    "B1 editor_api: proteger subida",
)

patch(
    APP / "editor_api.py",
    """    @router.post("/api/audio/from-job/{job_id}", status_code=201)""",
    """    @router.post(
        "/api/audio/from-job/{job_id}", status_code=201, dependencies=dependencies
    )""",
    "B1 editor_api: proteger from-job",
)

patch(
    APP / "editor_api.py",
    """    @router.delete("/api/audio/asset/{asset_id}")""",
    """    @router.delete("/api/audio/asset/{asset_id}", dependencies=dependencies)""",
    "B1 editor_api: proteger borrado",
)

patch(
    APP / "editor_api.py",
    """    @router.post("/api/audio/render", status_code=202)""",
    """    @router.post("/api/audio/render", status_code=202, dependencies=dependencies)""",
    "B1 editor_api: proteger exportacion",
)

# ---------------------------------------------------------------------------
# B1 · editor.js · mandar el token cuando lo haya
# ---------------------------------------------------------------------------
patch(
    STATIC / "editor.js",
    """  const API = {
    async request(path, options = {}) {
      const res = await fetch(path, options);
""",
    """  // Misma clave que el panel (/monitor): el token se escribe una vez en el
  // panel y el editor lo hereda, porque las dos páginas comparten origen.
  const TOKEN_KEY = "vdl_admin_token";

  function authHeaders() {
    try {
      const value = localStorage.getItem(TOKEN_KEY);
      return value ? { "X-Admin-Token": value } : {};
    } catch (err) {
      return {};
    }
  }

  const API = {
    async request(path, options = {}) {
      const res = await fetch(path, {
        ...options,
        headers: { ...authHeaders(), ...(options.headers || {}) },
      });
""",
    "B1 editor.js: cabecera en request",
)

patch(
    STATIC / "editor.js",
    """        xhr.setRequestHeader("Content-Type", "application/octet-stream");""",
    """        xhr.setRequestHeader("Content-Type", "application/octet-stream");
        const adminToken = authHeaders()["X-Admin-Token"];
        if (adminToken) xhr.setRequestHeader("X-Admin-Token", adminToken);""",
    "B1 editor.js: cabecera en la subida",
)

# ---------------------------------------------------------------------------
# B3 · config.py · topes por defecto
# ---------------------------------------------------------------------------
patch(
    APP / "config.py",
    """# Tamaño máximo aceptado por archivo, en MB. 0 = sin límite.
# En producción conviene ponerlo: sin tope, un 4K largo puede llenar el disco.
MAX_FILESIZE_MB = _env_int("VDL_MAX_FILESIZE_MB", 0)
""",
    """# Tamaño máximo aceptado por archivo, en MB. 0 = sin límite.
# Por defecto 2048: sin tope, un 4K largo puede llenar el disco, y el TTL que
# borra los temporales es por tiempo, no por tamaño. Con 0 se desactiva.
MAX_FILESIZE_MB = _env_int("VDL_MAX_FILESIZE_MB", 2048)

# Espacio libre que se reserva en el disco, en MB. Antes de aceptar una
# descarga se exige que queden al menos estos MB libres: es la única defensa
# que mira el tamaño y no el reloj. 0 = no comprobar.
MIN_FREE_MB = _env_int("VDL_MIN_FREE_MB", 2048)
""",
    "B3 config: MAX_FILESIZE por defecto y MIN_FREE_MB",
)

# ---------------------------------------------------------------------------
# Documentacion
# ---------------------------------------------------------------------------
patch(
    ROOT / "README.md",
    """| `VDL_MAX_FILESIZE_MB` | `0` | Tamaño máximo por archivo (0 = sin límite) |""",
    """| `VDL_MAX_FILESIZE_MB` | `2048` | Tamaño máximo por archivo, en MB (0 = sin límite) |
| `VDL_MIN_FREE_MB` | `2048` | Espacio libre que se exige antes de aceptar una descarga (0 = no comprobar) |""",
    "README: variables nuevas",
)

patch(
    ROOT / "README.md",
    """> Exportar consume CPU del servidor: el editor tiene su propio cupo de peticiones y
> topes de tamaño, duración y tramos. En un servidor público, ponlo detrás de
> `VDL_ADMIN_TOKEN` o desactívalo con `VDL_EDITOR_ENABLED=false` si no quieres
> regalar tiempo de CPU.""",
    """> Exportar consume CPU del servidor: el editor tiene su propio cupo de peticiones y
> topes de tamaño, duración y tramos. Y cuando hay `VDL_ADMIN_TOKEN` configurado,
> **subir, traer una descarga, borrar y exportar exigen el token** (`X-Admin-Token`):
> sin él, el editor era una forma gratuita de gastar tu CPU y tu disco. Los
> endpoints de lectura (onda, reproducción) siguen abiertos, porque `<audio src>`
> no puede llevar cabeceras. El token se escribe una vez en el panel `/monitor` y
> el editor lo reutiliza (misma clave en `localStorage`, mismo origen). Si no
> quieres el editor en absoluto: `VDL_EDITOR_ENABLED=false`.""",
    "README: el editor exige token",
)

patch(
    ROOT / "README.md",
    """| `GET /api/health` | Estado general, versión del motor, circuitos abiertos |""",
    """| `GET /api/health` | Estado general (el mismo veredicto que el panel), espacio libre en disco, versión del motor y circuitos abiertos |""",
    "README: endpoint de salud",
)


def main() -> int:
    errors: list[str] = []

    # 1) comprobacion previa: todos los anclajes existen y son unicos
    for path, old, _new, label in PATCHES:
        if not path.is_file():
            errors.append(f"[{label}] no existe el archivo {path}")
            continue
        text = path.read_text(encoding="utf-8")
        found = text.count(old)
        if found != 1:
            errors.append(f"[{label}] coincidencias={found} (se esperaba 1) en {path.name}")

    if errors:
        print("ABORTADO: los anclajes no cuadran")
        for err in errors:
            print("  -", err)
        return 1

    # 2) aplicar
    for path, old, new, label in PATCHES:
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        print(f"aplicado: {label}")

    # 3) validar sintaxis de todo lo que se ha tocado
    print("\nvalidacion de sintaxis:")
    for path in sorted({p for p, _o, _n, _l in PATCHES}):
        try:
            if path.suffix == ".py":
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                print(f"  ok (python) {path.name}")
            else:
                print(f"  (sin validador) {path.name}")
        except SyntaxError as exc:
            print(f"  ERROR de sintaxis en {path}: {exc}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
