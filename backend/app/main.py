"""API FastAPI del servicio de descargas.

Endpoints
---------
GET  /                        -> interfaz web (SPA estática)
GET  /api/health              -> estado + disponibilidad de FFmpeg
POST /api/parse               -> resuelve un link y devuelve formatos
POST /api/download            -> encola una descarga (mp4 | mp3)
GET  /api/jobs/{job_id}       -> progreso del trabajo
GET  /api/file/{job_id}       -> entrega el archivo terminado
POST /api/facebook/private    -> extrae URLs desde el HTML pegado
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import logging
import mimetypes
import os
import re
import shutil
import socket
import threading
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Deque, Dict
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, downloader, editor, editor_api, history, pairing, updater
from . import status as system_status
from .alerts import clear_history, send_alert
from .canary import canaries
from .downloader import EngineError
from .jobs import store
from .media import ffmpeg_status
from .metrics import metrics
from .resilience import circuits

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("api")

executor = ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_JOBS, thread_name_prefix="dl")

# Interfaz web y panel de control (se monta al final del archivo).
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------
async def _cleanup_loop() -> None:
    """Borra archivos vencidos periódicamente."""
    while True:
        try:
            await asyncio.sleep(config.CLEANUP_INTERVAL_SECONDS)
            removed = await asyncio.to_thread(store.purge_expired)
            if removed:
                log.info("Limpiados %s archivos vencidos", removed)

            # El editor tiene su propio ciclo: los audios cargados duran horas y
            # las exportaciones minutos, no el TTL de las descargas.
            cleaned = await asyncio.to_thread(editor.sweep)
            if cleaned["assets_removed"] or cleaned["renders_removed"]:
                log.info(
                    "Editor: %s audios y %s exportaciones vencidas",
                    cleaned["assets_removed"],
                    cleaned["renders_removed"],
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Error en el recolector de archivos")


async def _canary_loop() -> None:
    """Prueba los canarios cada X minutos para detectar roturas temprano."""
    # Espera inicial: dejar que el servicio arranque antes de gastar red.
    await asyncio.sleep(25)
    while True:
        try:
            results = await asyncio.to_thread(canaries.run_all)
            if results:
                passing = sum(1 for r in results if r.ok)
                log.info("Canarios: %s/%s OK", passing, len(results))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Error ejecutando canarios")

        await asyncio.sleep(config.CANARY_INTERVAL_MINUTES * 60)


async def _update_loop() -> None:
    """Mantiene yt-dlp al día: actualizar, validar, revertir si falla.

    Este bucle es lo que evita que el servicio muera solo. Los sitios cambian,
    yt-dlp publica arreglos en días, y este bucle los instala sin intervención.
    """
    if not config.AUTO_UPDATE:
        log.info("Actualización automática desactivada (VDL_AUTO_UPDATE=false)")
        return

    # Margen para que el arranque y los primeros canarios terminen.
    await asyncio.sleep(90)

    while True:
        try:
            report = await asyncio.to_thread(updater.run_update_cycle)
            action = report.get("action")

            if action == "actualizado_y_validado":
                log.info(
                    "yt-dlp actualizado y validado: %s -> %s",
                    report.get("version_before"), report.get("version_after"),
                )
                if config.RESTART_AFTER_UPDATE:
                    # El proceso en curso sigue usando el módulo viejo en memoria.
                    log.warning("Solicitando reinicio para cargar la versión nueva")
                    await asyncio.sleep(3)
                    os._exit(0)
            elif action == "revertido":
                log.warning(
                    "Actualización revertida a %s (la versión nueva no pasó los canarios)",
                    report.get("rollback_to"),
                )
            elif action == "ya_actualizado":
                log.debug("yt-dlp ya está en la última versión (%s)", report.get("version_before"))
            elif action == "error_instalacion":
                log.error("La instalación de la actualización falló: %s", report.get("reason"))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Error en el ciclo de actualización")

        await asyncio.sleep(config.UPDATE_INTERVAL_HOURS * 3600)


async def _watchdog_loop() -> None:
    """Avisa cuando el estado del sistema CAMBIA, no en cada fallo.

    Un aviso por cada error de usuario sería ruido inservible. Lo que importa
    es la transición: de sano a degradado, de degradado a caído, y sobre todo
    el aviso de recuperación, que es el que confirma que el reinicio funcionó.
    """
    await asyncio.sleep(45)  # margen para que arranque y corran los canarios
    previous: str | None = None

    while True:
        try:
            state = await asyncio.to_thread(system_status.evaluate)
            current = state["status"]

            if previous is None:
                previous = current
                log.info("Vigilante de estado activo: %s", current)
            elif current != previous:
                if current == system_status.STATUS_OK:
                    send_alert(
                        "Sistema recuperado",
                        f"El servicio volvió a la normalidad (venía de «{previous}»).",
                        level="info",
                        details={"success_rate": state["success_rate"]},
                        dedupe_key="watchdog:recuperado",
                    )
                else:
                    send_alert(
                        f"Sistema {current}",
                        " · ".join(state["reasons"])[:400] or "Sin detalle disponible.",
                        level=(
                            "error"
                            if current == system_status.STATUS_DOWN
                            else "warning"
                        ),
                        details={
                            "reasons": state["reasons"],
                            "restart_advised": state["restart_advised"],
                        },
                        dedupe_key=f"watchdog:{current}",
                    )

                log.warning("Cambio de estado: %s -> %s", previous, current)
                previous = current

        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Error en el vigilante de estado")

        await asyncio.sleep(120)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_status()
    log.info("FFmpeg: %s", ffmpeg["path"] or "NO DISPONIBLE")
    if not ffmpeg["available"]:
        log.warning("MP3 y alta calidad no funcionarán hasta instalar FFmpeg. %s", ffmpeg["hint"])

    log.info("yt-dlp instalado: %s (canal %s)", updater.installed_version(), config.UPDATE_CHANNEL)

    tasks = [asyncio.create_task(_cleanup_loop())]
    if config.CANARY_ENABLED:
        tasks.append(asyncio.create_task(_canary_loop()))
    tasks.append(asyncio.create_task(_watchdog_loop()))
    tasks.append(asyncio.create_task(_update_loop()))

    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Video & MP3 Downloader API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # en producción: restringir al dominio del frontend
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """Un fallo no previsto devuelve JSON, no un error en texto plano.

    Sin esto, el cliente recibe «Internal Server Error» sin cuerpo y no puede
    ni mostrar un mensaje. El detalle va al registro, no a la respuesta: un
    volcado de pila puede revelar rutas internas y versiones.
    """
    log.exception("Error no controlado en %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno del servidor. Queda registrado."},
    )


@app.middleware("http")
async def _track_app_presence(request: Request, call_next):
    """Anota cuándo habló el APK con el servidor.

    El widget envía `X-VDL-Client` en cada petición. Guardar la última vez que
    llegó permite que el monitor distinga dos averías que se ven igual desde
    fuera pero se arreglan distinto: «la app nunca se configuró» y «la app se
    configuró pero la IP se movió».
    """
    client = request.headers.get(pairing.CLIENT_HEADER)
    if client:
        pairing.note_app_seen(client, _client_ip(request))
    return await call_next(request)


# Archivos de la interfaz que conviene revalidar siempre. Son cuatro y un 304
# cuesta menos que un malentendido: sin esto, al cambiar un estilo el navegador
# sigue pintando el viejo desde su caché y parece que el cambio no se hizo.
_REVALIDATE_SUFFIXES = (".css", ".js", ".html", ".webmanifest")


@app.middleware("http")
async def _revalidate_interface(request: Request, call_next):
    """Pide al navegador que confirme antes de usar la interfaz que guardó.

    `no-cache` no quiere decir «no guardes», sino «pregúntame antes de usarlo»:
    si el archivo no ha cambiado, el servidor responde 304 sin cuerpo y el coste
    es una petición condicional mínima. A cambio, un cambio de CSS o de JS se ve
    al recargar, sin tener que vaciar la caché a mano.
    """
    response = await call_next(request)
    if request.method != "GET":
        return response

    # Además de los archivos con extensión, cuentan las páginas de dirección
    # limpia: /, /editor, /monitor, /terminos y /privacidad no acaban en .html,
    # así que sin esta comprobación se quedaban sin revalidar y el navegador
    # seguía pintando la versión antigua de la interfaz —exactamente el sintoma
    # de «hice un cambio y no lo veo»—. Se decide por el tipo de la respuesta.
    is_page = request.url.path.endswith(_REVALIDATE_SUFFIXES) or response.headers.get(
        "content-type", ""
    ).startswith("text/html")
    if is_page:
        response.headers["Cache-Control"] = "no-cache"
    return response


# ---------------------------------------------------------------------------
# Rate limiting (en memoria; usar Redis si hay más de una instancia)
# ---------------------------------------------------------------------------
_hits: Dict[str, Deque[float]] = defaultdict(deque)
_hits_lock = threading.Lock()


def _peer_ip(request: Request) -> str:
    return request.client.host if request.client else "desconocida"


def _is_trusted_proxy(ip: str) -> bool:
    """¿La petición viene de un proxy nuestro?

    Solo entonces se cree `X-Forwarded-For`. Esa cabecera la escribe el cliente
    y cualquiera puede inventársela: fiarse de ella sin comprobar quién llama
    convierte el límite de peticiones en un adorno.
    """
    if not config.TRUSTED_PROXIES:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entry in config.TRUSTED_PROXIES:
        try:
            if addr in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def _client_ip(request: Request) -> str:
    """IP del cliente para el límite de peticiones.

    Por defecto es la del par que abre la conexión: es el único dato que el
    cliente no puede falsificar. Si hay un proxy propio delante, se lee la
    ÚLTIMA entrada de `X-Forwarded-For`, que es la que añade ese proxy; las
    anteriores puede haberlas puesto el cliente y no valen nada.
    """
    peer = _peer_ip(request)
    if not _is_trusted_proxy(peer):
        return peer

    forwarded = request.headers.get("x-forwarded-for", "")
    parts = [p.strip() for p in forwarded.split(",") if p.strip()]
    return parts[-1] if parts else peer


def rate_limit(
    request: Request,
    scope: str = "api",
    limit: int | None = None,
    window: int | None = None,
) -> None:
    """Cuenta la petición y lanza 429 si se pasó.

    `scope` separa contadores: entregar un archivo y pedir un análisis no son
    la misma clase de petición, y un contador compartido haría que ver un vídeo
    consumiera el cupo de descargas. Una descarga con rangos puede generar
    muchas peticiones seguidas, así que su cupo va aparte y más holgado.
    """
    ip = _client_ip(request)
    key = f"{scope}:{ip}"
    now = time.time()
    window = window or config.RATE_LIMIT_WINDOW_SECONDS
    limit = limit or config.RATE_LIMIT_REQUESTS

    with _hits_lock:
        # Barrido oportunista: sin esto, cada IP que pasa una vez deja su
        # entrada vacía para siempre y el diccionario crece sin fin (una
        # entrada por petición en cuanto alguien rota la cabecera).
        if len(_hits) > 10_000:
            for stale in [k for k, v in _hits.items() if not v or now - v[-1] > window]:
                _hits.pop(stale, None)

        bucket = _hits[key]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            retry = int(window - (now - bucket[0])) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Demasiadas solicitudes. Reintenta en {retry}s.",
                headers={"Retry-After": str(retry)},
            )
        bucket.append(now)


# ---------------------------------------------------------------------------
# Validación de URLs
# ---------------------------------------------------------------------------
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# Nombres que nunca pueden ser un sitio público.
_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "[::1]",
    "metadata.google.internal",
}

# Sufijos reservados para redes internas (RFC 6761/6762 y equivalentes).
_BLOCKED_SUFFIXES = (
    ".local",
    ".localhost",
    ".localdomain",
    ".internal",
    ".intranet",
    ".home.arpa",
    ".in-addr.arpa",
    ".ip6.arpa",
)


# Redes que `is_private` de Python NO marca como privadas y sí son internas.
# La más importante: 100.64.0.0/10 (espacio compartido de operador, el que usan
# muchas redes domésticas y de empresa detrás del CGNAT del ISP).
_EXTRA_BLOCKED_NETWORKS = (
    "100.64.0.0/10",   # CGNAT / espacio compartido (RFC 6598)
    "192.0.0.0/24",    # asignación IETF
    "192.88.99.0/24",  # relay 6to4
    "198.51.100.0/24",  # documentación
    "203.0.113.0/24",  # documentación
    "240.0.0.0/4",     # reservado (incluye 255.255.255.255)
    "fc00::/7",        # direcciones locales únicas (IPv6)
    "fe80::/10",       # enlace local (IPv6)
    "64:ff9b::/96",    # traducción NAT64
)


def _is_blocked_ip(ip: str) -> bool:
    """¿Es una dirección interna, reservada o no enrutable?"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        # No se pudo interpretar: ante la duda, se bloquea.
        return True

    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ):
        return True

    for network in _EXTRA_BLOCKED_NETWORKS:
        try:
            if addr in ipaddress.ip_network(network):
                return True
        except ValueError:
            continue
    return False


def validate_url(url: str) -> str:
    """Comprobaciones baratas del enlace (sin salir a la red)."""
    url = (url or "").strip()
    if not _URL_RE.match(url):
        raise HTTPException(400, "El enlace debe empezar por http:// o https://")

    host = (urlparse(url).hostname or "").lower()
    if not host:
        raise HTTPException(400, "El enlace no es válido.")

    if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_SUFFIXES):
        raise HTTPException(400, "Ese dominio no está permitido.")

    # Una IP literal se comprueba aquí mismo: no hay nada que resolver.
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        if _is_blocked_ip(host.strip("[]")):
            raise HTTPException(400, "Ese enlace apunta a una dirección interna.")

    if config.ALLOWED_DOMAINS and not any(
        host == d or host.endswith("." + d) for d in config.ALLOWED_DOMAINS
    ):
        raise HTTPException(400, f"Dominio no soportado: {host}")

    return url


def assert_public_target(url: str) -> None:
    """Resuelve el dominio y rechaza destinos de red interna (SSRF).

    Comparar el texto del dominio no basta: `localhost`, `.local` y poco más
    dejaban pasar `192.168.1.1`, `169.254.169.254` (los metadatos de las nubes)
    o cualquier nombre que resuelva a una IP privada. Aquí se resuelve de
    verdad y se comprueba la dirección resultante.

    Hace consultas de red, así que hay que llamarlo desde un hilo: en el bucle
    de eventos dejaría el servidor entero esperando.

    Queda un resquicio teórico (DNS rebinding: el dominio podría resolver a una
    IP pública ahora y a una privada cuando yt-dlp lo descargue). Cerrarlo del
    todo exigiría fijar la IP en la descarga, que yt-dlp no permite.
    """
    host = (urlparse(url).hostname or "").lower()
    if not host:
        raise HTTPException(400, "El enlace no es válido.")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise HTTPException(400, f"No se pudo resolver el dominio: {host}")

    for info in infos:
        ip = info[4][0]
        if _is_blocked_ip(ip):
            log.warning("SSRF bloqueado: %s resuelve a %s", host, ip)
            raise HTTPException(
                400,
                "Ese enlace apunta a una dirección de red interna y no está permitido.",
            )


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------
class ParseRequest(BaseModel):
    url: str = Field(..., description="Enlace del video")


class DownloadRequest(BaseModel):
    url: str
    kind: str = Field("mp4", pattern="^(mp4|mp3)$")
    # Identificador de formato que devuelve /api/parse. Se acota a lo que puede
    # ser un identificador real: llega desde fuera y acaba dentro del selector
    # de formatos de yt-dlp, donde los corchetes y las comas tienen significado.
    format_id: str | None = Field(None, max_length=64, pattern=r"^[\w.:+-]{1,64}$")
    # Opcionales: el navegador ya los conoce del análisis y sirven para que el
    # carrusel de la portada y el historial tengan algo que enseñar aunque la
    # descarga falle. Se validan como dato no confiable.
    title: str | None = Field(None, max_length=300)
    thumbnail: str | None = Field(None, max_length=600)


class PrivateSourceRequest(BaseModel):
    html: str = Field(..., min_length=200)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
def _token_matches(token: str | None) -> bool:
    """Compara el token en tiempo constante.

    Con `!=` la comparación termina en el primer carácter que difiere, así que
    el tiempo de respuesta revela cuántos caracteres iniciales se acertaron y
    el token se puede adivinar carácter a carácter. `compare_digest` tarda lo
    mismo acierte uno o ninguno.
    """
    if not token or not config.ADMIN_TOKEN:
        return False
    return hmac.compare_digest(token.encode("utf-8"), config.ADMIN_TOKEN.encode("utf-8"))


def require_admin(token: str | None) -> None:
    """Protege los endpoints de administración."""
    if not config.ADMIN_TOKEN:
        raise HTTPException(
            404,
            "Los endpoints de administración están deshabilitados. "
            "Define VDL_ADMIN_TOKEN para activarlos.",
        )
    if not _token_matches(token):
        raise HTTPException(401, "Token de administración inválido.")


def require_admin_if_configured(token: str | None) -> bool:
    """Protege una ruta sensible solo si hay token configurado.

    El historial contiene direcciones IP y enlaces, así que no debería estar al
    aire. Pero devolver 404 cuando no hay `VDL_ADMIN_TOKEN` dejaría el panel
    inservible en local, que es justo donde se prueba. Solución: se abre, y la
    respuesta lo dice («protected: false») para que nadie exponga el historial
    en internet sin enterarse.

    Devuelve True si la respuesta queda protegida por token.
    """
    if not config.ADMIN_TOKEN:
        return False
    if not _token_matches(token):
        raise HTTPException(401, "Token de administración inválido.")
    return True


@app.get("/api/health")
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
        "engine": {
            "name": "yt-dlp",
            "version": updater.installed_version(),
            "channel": config.UPDATE_CHANNEL,
            "auto_update": config.AUTO_UPDATE,
            "next_check_hours": config.UPDATE_INTERVAL_HOURS,
        },
        "ffmpeg": ffmpeg,
        "queue": store.jobs_waiting_for_upload(),
        "file_ttl_minutes": config.FILE_TTL_MINUTES,
        "allowed_domains": config.ALLOWED_DOMAINS or "todas (sin restricción)",
        "cookies_configured": bool(config.COOKIES_FILE or config.COOKIES_DIR),
        "cookies_rotation": len(list(Path(config.COOKIES_DIR).glob("*.txt")))
        if config.COOKIES_DIR and Path(config.COOKIES_DIR).is_dir()
        else 0,
        "circuits_open": circuits_open,
        "platforms_degraded": degraded,
        "editor": {
            "enabled": config.EDITOR_ENABLED,
            "assets": editor.count_assets(),
            "formats": [item["id"] for item in editor.output_formats()]
            if config.EDITOR_ENABLED
            else [],
        },
        "proxy": {
            "general": bool(config.PROXY),
            "por_plataforma": sorted(config.PROXY_MAP.keys()),
            "aviso": (
                None
                if (config.PROXY or config.PROXY_MAP)
                else "Sin proxy configurado. En un servidor, los sitios grandes "
                     "bloquearán la IP en pocos días."
            ),
        },
    }


@app.get("/api/metrics")
def api_metrics() -> dict:
    """Tasa de éxito, salud por plataforma, circuitos y canarios.

    Si esta tasa baja, algo se rompió. Es tu señal más temprana.
    """
    return {
        **metrics.snapshot(),
        "circuits": circuits.all(),
        "canaries": canaries.status(),
    }


@app.get("/api/canary")
def api_canary() -> dict:
    """Estado de los canarios y su última ejecución."""
    return canaries.status()


@app.get("/api/history")
def api_history(
    limit: int = 100,
    query: str | None = None,
    state: str | None = None,
    host: str | None = None,
    token: str | None = Header(None, alias="X-Admin-Token"),
) -> dict:
    """Historial de descargas: quién pidió qué enlace y cómo acabó.

    Contiene direcciones IP y enlaces, así que va protegido con el token en
    cuanto hay uno configurado (`protected: true`). El filtro se puede hacer
    aquí o en el navegador; se admite en el servidor para que el panel pueda
    seguir pidiendo poco cuando el historial crece.

    Ojo con `state`: se llama así y no `status` para no chocar con el módulo
    `status` importado en este archivo.
    """
    protected = require_admin_if_configured(token)

    limit = max(1, min(limit, config.HISTORY_MAX))
    return {
        "protected": protected,
        "enabled": config.HISTORY_ENABLED,
        "stats": history.stats(),
        "entries": history.entries(limit=limit, query=query, status=state, host=host),
    }


@app.get("/api/gallery")
def api_gallery(limit: int = 12) -> dict:
    """Carrusel de la portada: una muestra de las últimas descargas.

    Es público, así que solo sale lo que puede ver cualquiera: título, miniatura,
    plataforma, formato y fecha. **Nunca** la IP ni el enlace original, porque el
    enlace puede llevar identificadores de la sesión de quien lo pidió.

    Si el archivo todavía está en el almacén temporal, se añade `preview_url`
    para que la portada pueda reproducirlo en vez de quedarse en una foto.
    """
    if not config.GALLERY_ENABLED:
        return {"enabled": False, "min_items": config.GALLERY_MIN_ITEMS, "items": []}

    limit = max(1, min(limit, config.GALLERY_MAX))
    items = history.gallery(limit=limit)

    for item in items:
        job = store.get(item.get("job_id") or "")
        alive = (
            job is not None
            and job.status == "done"
            and job.filepath
            and Path(job.filepath).is_file()
        )
        item["preview_url"] = f"/api/preview/{job.id}" if alive else None
        item["preview_kind"] = ("video" if job.kind == "mp4" else "audio") if alive else None

    return {
        "enabled": True,
        "min_items": config.GALLERY_MIN_ITEMS,
        "max": config.GALLERY_MAX,
        "items": items,
    }


@app.get("/api/preview/{job_id}", include_in_schema=False)
def api_preview(job_id: str, request: Request) -> FileResponse:
    rate_limit(request, scope="files", limit=config.RATE_LIMIT_FILES_REQUESTS)

    """Sirve el archivo en línea para el carrusel, no como descarga.

    `/api/file/{id}` manda `Content-Disposition: attachment`, que fuerza la
    descarga y haría imposible reproducir nada en la página. Aquí se sirve
    `inline` y con soporte de rangos (lo aporta `FileResponse`), para que se
    pueda reproducir y saltar en la línea de tiempo.

    Solo existe mientras el archivo siga en el almacén temporal: es una vista
    previa de lo que acaba de pasar, no un archivo permanente.
    """
    job = store.get(job_id)
    if not job or job.status != "done" or not job.filepath:
        raise HTTPException(404, "La vista previa no está disponible.")

    path = Path(job.filepath)
    if not path.is_file():
        raise HTTPException(410, "El archivo expiró y fue eliminado.")

    media = mimetypes.guess_type(path.name)[0]
    if not media:
        media = "video/mp4" if job.kind == "mp4" else "audio/mpeg"

    # El nombre se limpia igual que en /api/file: sin esto, un título con comillas
    # o salto de línea rompería la cabecera.
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", path.stem)[:120].strip() or "vista-previa"

    return FileResponse(
        path,
        media_type=media,
        filename=f"{stem}{path.suffix}",
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/admin/update")
def api_admin_update(token: str | None = Header(None, alias="X-Admin-Token")) -> dict:
    """Revisa y aplica actualizaciones del motor ahora mismo."""
    require_admin(token)
    check = updater.check()
    if not check["update_available"]:
        return {"action": "ya_actualizado", **check}
    return updater.run_update_cycle()


@app.get("/api/admin/update/check")
def api_admin_update_check(token: str | None = Header(None, alias="X-Admin-Token")) -> dict:
    """Solo consulta si hay versión nueva, sin instalar nada."""
    require_admin(token)
    return updater.check()


@app.post("/api/admin/canaries/reload")
def api_admin_reload_canaries(token: str | None = Header(None, alias="X-Admin-Token")) -> dict:
    """Recarga canaries.json sin reiniciar el servicio."""
    require_admin(token)
    canaries.reload()
    return canaries.status()


# ---------------------------------------------------------------------------
# Monitorización y restablecimiento
# ---------------------------------------------------------------------------
@app.get("/api/widget")
def api_widget() -> dict:
    """Payload compacto para el widget de Android y para atajos móviles.

    Todo viene ya formateado (texto corto + color) porque un widget de Android
    no ejecuta lógica propia: solo pinta lo que recibe.
    """
    return system_status.widget_payload()


@app.get("/api/monitor")
def api_monitor() -> dict:
    """Estado completo para el panel de control."""
    return system_status.full_status()


@app.get("/api/pairing")
def api_pairing(
    request: Request,
    token: str | None = Header(None, alias="X-Admin-Token"),
) -> dict:
    """Enlace de conexión para el APK del monitor.

    Devuelve las direcciones por las que el móvil puede llegar, el estado del
    token y la última vez que la app contactó. El token completo y el enlace de
    conexión solo se incluyen si quien pregunta demuestra conocer el token
    (cabecera `X-Admin-Token`): es lo único que evita que cualquiera que abra
    el monitor se lleve la credencial que reinicia el servicio.
    """
    authorized = bool(config.ADMIN_TOKEN) and token == config.ADMIN_TOKEN
    return pairing.payload(request, authorized=authorized)


@app.post("/api/admin/circuits/reset")
def api_admin_reset_circuits(token: str | None = Header(None, alias="X-Admin-Token")) -> dict:
    """Cierra todos los circuitos abiertos.

    Útil tras actualizar el motor: no tiene sentido esperar el enfriamiento
    de un sitio si ya sabemos que la causa se corrigió.
    """
    require_admin(token)
    affected = circuits.reset_all()
    log.info("Circuitos reiniciados: %s", affected or "ninguno estaba abierto")
    return {
        "status": "circuitos_reiniciados",
        "afectados": affected,
        "nota": (
            f"Se cerraron {len(affected)} circuitos."
            if affected
            else "No había ningún circuito abierto."
        ),
    }


@app.post("/api/admin/canaries/run")
def api_admin_run_canaries(token: str | None = Header(None, alias="X-Admin-Token")) -> dict:
    """Ejecuta los canarios ahora, sin esperar al ciclo periódico."""
    require_admin(token)
    results = canaries.run_all(quiet=True)
    passing = sum(1 for r in results if r.ok)
    return {
        "status": "canarios_ejecutados",
        "pasando": passing,
        "total": len(results),
        "resultados": [r.to_public() for r in results],
    }


@app.post("/api/admin/storage/purge")
def api_admin_purge(
    keep_active: bool = True,
    token: str | None = Header(None, alias="X-Admin-Token"),
) -> dict:
    """Libera espacio borrando archivos temporales.

    Por defecto respeta las descargas en curso: borrar un archivo a medio
    escribir dejaría el trabajo en un estado roto.
    """
    require_admin(token)
    result = store.force_purge(keep_active=keep_active)
    log.info("Purga manual: %s", result)
    return {"status": "almacenamiento_liberado", **result}


@app.post("/api/admin/alerts/clear")
def api_admin_clear_alerts(token: str | None = Header(None, alias="X-Admin-Token")) -> dict:
    """Vacía el historial de alertas (no borra nada del sistema)."""
    require_admin(token)
    clear_history()
    return {"status": "historial_limpiado"}


@app.post("/api/admin/history/clear")
def api_admin_clear_history(token: str | None = Header(None, alias="X-Admin-Token")) -> dict:
    """Borra el historial de descargas.

    Es la salida de emergencia de los datos personales: si alguien pide que se
    elimine su rastro, esto lo hace de una vez.

    Se protege con la misma regla que la lectura del historial: exige el token
    en cuanto existe. Si no hay token configurado, la lectura ya está abierta,
    así que impedir el borrado solo estorbaría en local sin proteger nada.
    """
    require_admin_if_configured(token)
    removed = history.clear()
    return {
        "status": "historial_de_descargas_vaciado",
        "entradas_eliminadas": removed,
    }


@app.post("/api/admin/restart")
def api_admin_restart(token: str | None = Header(None, alias="X-Admin-Token")) -> dict:
    """Sale del proceso para que el supervisor lo levante con la versión nueva."""
    require_admin(token)

    state = system_status.evaluate()
    before = {
        "status": state["status"],
        "circuits_open": state["circuits_open"],
        "success_rate": state["success_rate"],
    }

    def _exit_soon() -> None:
        time.sleep(1.5)
        log.warning("Reinicio solicitado por el administrador")
        os._exit(0)

    threading.Thread(target=_exit_soon, daemon=True).start()
    return {
        "status": "reiniciando",
        "estado_previo": before,
        "motivos": state["restart_reasons"],
        "nota": (
            "El servicio necesita un supervisor (systemd Restart=always o "
            "Docker restart: unless-stopped) para volver a arrancar. "
            "Sin supervisor, el proceso no volverá."
        ),
    }


@app.get("/monitor", include_in_schema=False)
def monitor_page() -> FileResponse:
    """Panel de control (versión web del widget)."""
    page = STATIC_DIR / "monitor.html"
    if not page.is_file():
        raise HTTPException(404, "El panel de control no está disponible.")
    return FileResponse(page, media_type="text/html")


def _legal_page(filename: str) -> FileResponse:
    """Sirve una página legal con URL limpia.

    `StaticFiles(html=True)` solo resuelve `index.html` dentro de una carpeta, así
    que `/terminos` daría 404 aunque exista `terminos.html`. Con estas rutas las
    direcciones quedan limpias y estables, que es lo que se cita en los avisos de
    derechos de autor y en los términos.
    """
    page = STATIC_DIR / filename
    if not page.is_file():
        raise HTTPException(404, "Esa página no está disponible.")
    return FileResponse(page, media_type="text/html")


@app.get("/terminos", include_in_schema=False)
def terms_page() -> FileResponse:
    """Términos de Servicio."""
    return _legal_page("terminos.html")


@app.get("/privacidad", include_in_schema=False)
def privacy_page() -> FileResponse:
    """Política de Privacidad."""
    return _legal_page("privacidad.html")


@app.get("/legal", include_in_schema=False)
def legal_index() -> FileResponse:
    """Alias corto: los términos son el documento principal."""
    return _legal_page("terminos.html")


def _apk_version_key(path: Path) -> tuple:
    """Ordena APKs por la versión del nombre, no alfabéticamente.

    Alfabéticamente, «…-1.0.apk» va antes que «…-1.1.apk» y el endpoint servía
    la versión vieja aunque hubiera una nueva al lado. Se comparan los números
    del nombre como números, así que 1.10 también gana a 1.9.
    """
    numbers = re.findall(r"\d+", path.stem)
    return tuple(int(n) for n in numbers)


def _newest_apk(candidates: list[Path]) -> Path:
    """El APK de versión más alta y, entre iguales, el release.

    Se prefiere el release porque el de depuración lleva otro identificador de
    aplicación (`….debug`) y no es el que conviene instalar.
    """
    release = [p for p in candidates if "debug" not in p.name] or list(candidates)
    return max(sorted(release), key=_apk_version_key)


@app.get("/app.apk", include_in_schema=False)
def download_apk() -> FileResponse:
    """Sirve el APK del widget.

    Permite instalarlo abriendo esta dirección en el navegador del móvil, sin
    cables ni servicios de transferencia. La carpeta `apk/` vive en la raíz del
    proyecto, fuera de `backend/`, así que puede no existir en instalaciones
    desplegadas sin ella.
    """
    apk_dir = STATIC_DIR.parent.parent / "apk"
    candidates = sorted(apk_dir.glob("*.apk")) if apk_dir.is_dir() else []

    if not candidates:
        raise HTTPException(
            404,
            "Esta instalación no incluye el APK. Descárgalo del repositorio: "
            "https://github.com/leonnnc/Ldownloader/tree/main/apk",
        )

    chosen = _newest_apk(candidates)

    return FileResponse(
        chosen,
        media_type="application/vnd.android.package-archive",
        filename=chosen.name,
    )


@app.post("/api/parse")
async def api_parse(payload: ParseRequest, request: Request) -> dict:
    rate_limit(request)
    url = validate_url(payload.url)
    # Fuera del bucle de eventos: resuelve DNS y puede tardar.
    await asyncio.to_thread(assert_public_target, url)

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(downloader.parse, url),
            timeout=config.PARSE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "La extracción tardó demasiado. Intenta de nuevo.")
    except EngineError as exc:
        raise HTTPException(422, str(exc))

    if not result["video_formats"] and not result["audio_formats"]:
        raise HTTPException(422, "No se encontraron formatos descargables en ese enlace.")

    return result


@app.post("/api/download", status_code=202)
async def api_download(payload: DownloadRequest, request: Request) -> dict:
    rate_limit(request)
    url = validate_url(payload.url)
    await asyncio.to_thread(assert_public_target, url)

    if payload.kind == "mp3" and not ffmpeg_status()["available"]:
        raise HTTPException(
            503,
            "Falta FFmpeg en el servidor: no se puede convertir a MP3.",
        )

    # La miniatura la elige el cliente: se acepta solo si es una URL http(s), y
    # si no lo es se guarda el título sin imagen. Nunca se sirve desde aquí.
    thumbnail = payload.thumbnail or None
    if thumbnail and urlparse(thumbnail).scheme.lower() not in ("http", "https"):
        thumbnail = None

    # La IP solo se usa para el historial del panel. Ojo: sale de X-Forwarded-For
    # y es falsificable mientras el proxy no valide esa cabecera, así que en el
    # panel se etiqueta como «IP declarada», no como certeza.
    job = store.create(
        url=url,
        kind=payload.kind,
        format_id=payload.format_id,
        client_ip=_client_ip(request),
        title=payload.title,
        thumbnail=thumbnail,
    )
    executor.submit(downloader.run_job, job)
    log.info("Job %s encolado (%s) -> %s", job.id, payload.kind, url)
    return {"job_id": job.id, "status": job.status}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> dict:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Trabajo no encontrado o ya expirado.")
    return job.to_public()


@app.get("/api/file/{job_id}")
def api_file(job_id: str, request: Request) -> FileResponse:
    # Cupo propio y holgado: una reproducción con saltos genera muchas
    # peticiones de rango, y no deben gastar el cupo de los análisis.
    rate_limit(request, scope="files", limit=config.RATE_LIMIT_FILES_REQUESTS)

    job = store.get(job_id)
    if not job or not job.filepath:
        raise HTTPException(404, "Archivo no disponible.")

    path = Path(job.filepath)
    if not path.is_file():
        raise HTTPException(410, "El archivo expiró y fue eliminado.")

    # Nombre final presentable: Título [id].mp4
    stem = (job.title or "video").strip()
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", stem)[:120].strip() or "video"
    filename = f"{stem}.{path.suffix.lstrip('.')}"

    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=filename,
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/facebook/private")
async def api_private_source(payload: PrivateSourceRequest, request: Request) -> dict:
    rate_limit(request)
    try:
        return await asyncio.to_thread(downloader.extract_from_source, payload.html)
    except EngineError as exc:
        raise HTTPException(422, str(exc))


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )


# ---------------------------------------------------------------------------
# Editor de audio
#
# Va en su propio router y recibe el limitador de aquí: así el editor no
# necesita importar nada de este archivo (ni este del interior del editor).
# ---------------------------------------------------------------------------
app.include_router(editor_api.build_router(rate_limit))


# ---------------------------------------------------------------------------
# Frontend estático (montado al final para no tapar /api)
# ---------------------------------------------------------------------------
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
