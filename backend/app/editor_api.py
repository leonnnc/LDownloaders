"""Endpoints del editor de audio.

Van en un archivo aparte porque `main.py` ya es largo, pero **no** se importa
nada de `main.py` aquí: el limitador de peticiones se recibe al construir el
router (`build_router(rate_limit)`). Así no hay importación circular y el
limitador sigue viviendo en un solo sitio.

Reglas que comparten todos estos endpoints:

* Salen **404** si el editor está desactivado, en vez de 403: no se anuncia una
  función que este servidor no ofrece.
* Los que cuestan CPU (subir, exportar) usan el cupo `audio`, que va aparte del
  cupo general: editar audio no debe consumir el derecho a analizar enlaces.
* Los que solo entregan datos (onda, reproducción) usan el cupo de archivos,
  más holgado, porque reproducir con saltos genera muchas peticiones de rango.
* Nada de lo que se edita aquí entra en el historial de descargas ni en el
  carrusel público de la portada: es material de trabajo, no una descarga.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from . import config, editor
from .jobs import store

# Bytes que se leen para reconocer el tipo de archivo. Suficiente para todas las
# cabeceras que se aceptan (la más larga es la de ASF/WMA, de 8 bytes).
_SNIFF_BYTES = 16


def _peaks_payload(asset: dict, path: Path) -> dict:
    """Picos de la onda de una fuente, para dibujar su línea de tiempo.

    Se calculan una sola vez y quedan guardados junto al audio: la primera
    consulta cuesta leer el archivo entero, las siguientes leen un JSON.
    """
    data = editor.peaks_for(path, asset.get("duration"))
    return {
        "asset_id": asset.get("id"),
        "duration": asset.get("duration"),
        "per_second": data.get("per_second"),
        "peaks": data.get("peaks") or [],
    }


def build_router(rate_limit: Callable[..., None]) -> APIRouter:
    router = APIRouter()

    # -----------------------------------------------------------------------
    # Utilidades
    # -----------------------------------------------------------------------
    def _require_enabled() -> None:
        if not config.EDITOR_ENABLED:
            raise HTTPException(404, "El editor de audio no está disponible aquí.")

    def _asset_of(asset_id: str) -> dict:
        try:
            asset = editor.get_asset(asset_id)
        except editor.EditorError as exc:
            raise HTTPException(400, str(exc))
        if not asset:
            raise HTTPException(404, "Ese audio ya no está disponible.")
        return asset

    def _containers() -> str:
        return ", ".join(sorted(c.upper() for c in editor.INPUT_CONTAINERS))

    # -----------------------------------------------------------------------
    # Interfaz
    # -----------------------------------------------------------------------
    @router.get("/editor", include_in_schema=False)
    def editor_page() -> FileResponse:
        """Editor de audio: línea de tiempo, cortes, volumen, unión y fundidos."""
        _require_enabled()
        page = Path(__file__).resolve().parent.parent / "static" / "editor.html"
        if not page.is_file():
            raise HTTPException(404, "El editor no está disponible.")
        return FileResponse(page, media_type="text/html")

    @router.get("/api/audio/info")
    def api_audio_info() -> dict:
        """Qué ofrece este servidor y qué hay cargado ahora mismo.

        Lo consulta la página al abrirse: sirve para enseñar solo los formatos
        que este FFmpeg sabe generar y para explicar los topes antes de que el
        usuario suba un archivo de 400 MB.
        """
        _require_enabled()
        return {**editor.storage_summary(), "assets": editor.list_assets()}

    @router.get("/api/audio/sources")
    def api_audio_sources() -> dict:
        """MP3 recién descargados, listos para editar sin subir nada.

        Solo los trabajos terminados cuyo archivo sigue en el almacén temporal
        (15 minutos por defecto). Es la lista que hace que «editarlo» sea un
        clic después de descargar. No se expone ni la IP ni el enlace de origen.
        """
        _require_enabled()
        items = []
        for job in store.all_jobs():
            if job.status != "done" or not job.filepath or job.kind != "mp3":
                continue
            path = Path(job.filepath)
            if not path.is_file():
                continue
            items.append(
                {
                    "job_id": job.id,
                    "title": job.title or path.stem,
                    "filename": job.filename or path.name,
                    "filesize": job.filesize,
                }
            )
        items.sort(key=lambda item: item["title"].lower())
        return {"items": items}

    # -----------------------------------------------------------------------
    # Fuentes
    # -----------------------------------------------------------------------
    @router.post("/api/audio/upload", status_code=201)
    async def api_audio_upload(request: Request, name: str | None = None) -> dict:
        """Sube un audio para editarlo.

        El cuerpo se manda crudo (sin `multipart/form-data`) para no arrastrar
        una dependencia nueva al proyecto: el navegador pone el archivo entero
        como cuerpo y el nombre en la consulta. Y el tipo se decide por los
        **primeros bytes**, nunca por el nombre ni por la cabecera que declare
        el cliente.
        """
        _require_enabled()
        rate_limit(request, scope="audio", limit=config.RATE_LIMIT_AUDIO_REQUESTS)

        landing = editor.uploads_dir() / f"{uuid.uuid4().hex}.part"
        limit_bytes = config.EDITOR_MAX_UPLOAD_MB * 1024 * 1024
        written = 0

        try:
            with landing.open("wb") as handle:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > limit_bytes:
                        raise HTTPException(
                            413,
                            f"El audio supera el máximo de {config.EDITOR_MAX_UPLOAD_MB} MB "
                            "que acepta este servidor.",
                        )
                    handle.write(chunk)

            if written < 128:
                raise HTTPException(400, "No llegó ningún archivo.")

            with landing.open("rb") as handle:
                container = editor.sniff_container(handle.read(_SNIFF_BYTES))
            if container not in editor.INPUT_CONTAINERS:
                raise HTTPException(
                    415,
                    f"Ese archivo no parece audio. Se aceptan: {_containers()}.",
                )

            try:
                return await asyncio.to_thread(
                    editor.asset_from_upload,
                    landing,
                    name or f"audio.{container}",
                    container,
                )
            except editor.EditorError as exc:
                raise HTTPException(422, str(exc))
        finally:
            # Si salió bien, el archivo ya se movió a su carpeta: esto no hace nada.
            landing.unlink(missing_ok=True)

    @router.post("/api/audio/from-job/{job_id}", status_code=201)
    async def api_audio_from_job(job_id: str, request: Request) -> dict:
        """Trae al editor una descarga en MP3 de esta misma sesión."""
        _require_enabled()
        rate_limit(request, scope="audio", limit=config.RATE_LIMIT_AUDIO_REQUESTS)

        job = store.get(job_id)
        if not job or job.status != "done" or not job.filepath:
            raise HTTPException(404, "Esa descarga ya no está disponible.")
        if job.kind != "mp3":
            raise HTTPException(400, "Solo se pueden editar las descargas en MP3.")

        source = Path(job.filepath)
        if not source.is_file():
            raise HTTPException(410, "El archivo expiró y fue eliminado.")

        # Se copia en lugar de apuntar al archivo del trabajo: el trabajo se
        # borra a los 15 minutos y la sesión de edición puede durar más.
        landing = editor.uploads_dir() / f"{uuid.uuid4().hex}.part"
        try:
            await asyncio.to_thread(shutil.copyfile, source, landing)
            with landing.open("rb") as handle:
                container = editor.sniff_container(handle.read(_SNIFF_BYTES)) or "mp3"
            try:
                return await asyncio.to_thread(
                    editor.asset_from_upload,
                    landing,
                    job.title or source.name,
                    container,
                )
            except editor.EditorError as exc:
                raise HTTPException(422, str(exc))
        finally:
            landing.unlink(missing_ok=True)

    @router.get("/api/audio/asset/{asset_id}")
    def api_audio_stream(asset_id: str, request: Request) -> FileResponse:
        """Sirve el audio para escucharlo mientras se edita.

        `inline` y con soporte de rangos: así el navegador puede saltar a
        cualquier punto de la línea de tiempo sin bajar el archivo entero.
        """
        _require_enabled()
        rate_limit(request, scope="files", limit=config.RATE_LIMIT_FILES_REQUESTS)

        asset = _asset_of(asset_id)
        path = Path(asset["path"])
        if not path.is_file():
            raise HTTPException(410, "Ese audio expiró y fue eliminado.")

        return FileResponse(
            path,
            media_type=editor.MIME_BY_CONTAINER.get(
                asset.get("container") or "", "application/octet-stream"
            ),
            filename=asset.get("name") or path.name,
            content_disposition_type="inline",
            headers={"Cache-Control": "no-store", "Accept-Ranges": "bytes"},
        )

    @router.get("/api/audio/asset/{asset_id}/peaks")
    def api_audio_peaks(asset_id: str, request: Request) -> dict:
        """Picos de la onda para dibujar la línea de tiempo."""
        _require_enabled()
        rate_limit(request, scope="files", limit=config.RATE_LIMIT_FILES_REQUESTS)

        asset = _asset_of(asset_id)
        path = Path(asset["path"])
        if not path.is_file():
            raise HTTPException(410, "Ese audio expiró y fue eliminado.")

        try:
            return _peaks_payload(asset, path)
        except editor.EditorError as exc:
            raise HTTPException(422, str(exc))

    @router.delete("/api/audio/asset/{asset_id}")
    def api_audio_delete(asset_id: str, request: Request) -> dict:
        """Borra una fuente del servidor. Es irreversible, se pide expresamente."""
        _require_enabled()
        rate_limit(request, scope="audio", limit=config.RATE_LIMIT_AUDIO_REQUESTS)

        try:
            removed = editor.delete_asset(asset_id)
        except editor.EditorError as exc:
            raise HTTPException(400, str(exc))
        if not removed:
            raise HTTPException(404, "Ese audio ya no está disponible.")
        return {"status": "borrado", "asset_id": asset_id}

    # -----------------------------------------------------------------------
    # Exportar el montaje
    # -----------------------------------------------------------------------
    @router.post("/api/audio/render", status_code=202)
    async def api_audio_render(payload: editor.RenderRequest, request: Request) -> dict:
        """Aplica el montaje y genera el archivo final.

        Devuelve enseguida un identificador y la exportación sigue en segundo
        plano: codificar un montaje largo tarda, y el navegador necesita poder
        mostrar el avance en lugar de quedarse esperando.
        """
        _require_enabled()
        rate_limit(request, scope="audio", limit=config.RATE_LIMIT_AUDIO_REQUESTS)

        allowed = {item["id"] for item in editor.output_formats()}
        if payload.format not in allowed:
            raise HTTPException(
                400,
                "Este servidor no puede generar ese formato. "
                f"Disponibles: {', '.join(sorted(allowed)).upper()}.",
            )

        try:
            clips, duration = await asyncio.to_thread(editor.prepare, payload)
        except editor.EditorError as exc:
            raise HTTPException(422, str(exc))

        render = editor.renders.create(
            payload, duration, editor.safe_name(payload.name, "audio-editado")
        )
        editor.renders.submit(render, clips, payload)
        return render.to_public()

    @router.get("/api/audio/render/{render_id}")
    def api_audio_render_status(render_id: str) -> dict:
        """Avance de una exportación."""
        _require_enabled()
        render = editor.renders.get(render_id)
        if not render:
            raise HTTPException(404, "Esa exportación ya no está disponible.")
        return render.to_public()

    @router.get("/api/audio/render/{render_id}/file")
    def api_audio_render_file(render_id: str, request: Request) -> FileResponse:
        """Entrega el archivo exportado."""
        _require_enabled()
        rate_limit(request, scope="files", limit=config.RATE_LIMIT_FILES_REQUESTS)

        render = editor.renders.get(render_id)
        if not render or not render.filepath:
            raise HTTPException(404, "La exportación no está lista o ya expiró.")
        path = Path(render.filepath)
        if not path.is_file():
            raise HTTPException(410, "La exportación expiró y fue eliminada.")

        return FileResponse(
            path,
            media_type=editor.OUTPUT_FORMATS.get(render.format, {}).get(
                "mime", "application/octet-stream"
            ),
            filename=path.name,
            headers={"Cache-Control": "no-store"},
        )

    return router
