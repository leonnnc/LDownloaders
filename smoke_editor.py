"""Prueba de humo del editor de audio.

Recorre el camino completo contra un servidor en marcha: subir un MP3, medirlo,
pedir su onda, montar dos tramos con volumen y fundido, exportar, descargar el
resultado y comprobar que lo que salió es de verdad lo que se pidió.

El audio de prueba lo genera el propio FFmpeg (un tono), así que esta prueba no
necesita internet ni descargar nada de terceros.

También cubre la parte que no toca la red: que el comando de FFmpeg se arme como
lista de argumentos (nunca como cadena de shell) y que un montaje imposible se
rechace con un mensaje entendible.

Uso:  python smoke_editor.py [base-url]
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from pydantic import ValidationError  # noqa: E402

from app import editor  # noqa: E402
from app.media import find_ffmpeg  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'OK  ' if ok else 'FALLA'}] {name}" + (f"  — {detail}" if detail else ""))


def request(
    path: str,
    payload: dict | None = None,
    *,
    raw: bytes | None = None,
    method: str | None = None,
    timeout: int = 120,
) -> tuple[int, object]:
    """Devuelve (código, cuerpo). El cuerpo es dict si es JSON, si no bytes."""
    data = None
    headers: dict = {}
    if raw is not None:
        data = raw
        headers["Content-Type"] = "application/octet-stream"
    elif payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers=headers,
        method=method or ("POST" if data is not None else "GET"),
    )
    try:
        with OPENER.open(req, timeout=timeout) as resp:
            body = resp.read()
            try:
                return resp.status, json.loads(body.decode())
            except (UnicodeDecodeError, json.JSONDecodeError):
                return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return exc.code, body
    except urllib.error.URLError as exc:
        raise SystemExit(f"No se pudo hablar con {BASE}: {exc}")


def wait_render(render_id: str, timeout: float = 120) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        _, last = request(f"/api/audio/render/{render_id}")
        if isinstance(last, dict) and last.get("status") in ("done", "error"):
            return last
        time.sleep(0.5)
    return last


# ---------------------------------------------------------------------------
# Parte A · sin red: cómo se arma el trabajo
# ---------------------------------------------------------------------------
def offline() -> None:
    print("\nA · Montaje y comando de FFmpeg (sin red)")

    ffmpeg = find_ffmpeg()
    check("FFmpeg localizado para el editor", bool(ffmpeg), ffmpeg or "no se encontró")

    # El comando debe ser una lista: con una cadena, cualquier valor del montaje
    # podría convertirse en otro comando.
    clips = [
        editor.PreparedClip(
            asset_id="0" * 32,
            path="/tmp/ejemplo uno.mp3",
            start=1.5,
            duration=4.0,
            gain_db=-6.0,
            fade_in=1.0,
            fade_out=2.0,
        ),
        editor.PreparedClip(
            asset_id="1" * 32,
            path="/tmp/ejemplo dos.mp3",
            start=0.0,
            duration=3.0,
            gain_db=-60.0,
            fade_in=0.0,
            fade_out=0.0,
        ),
    ]
    request_model = editor.RenderRequest(
        clips=[
            editor.ClipSpec(asset_id="0" * 32, start=1.5, end=5.5),
            editor.ClipSpec(asset_id="1" * 32, start=0.0, end=3.0),
        ],
        format="mp3",
        bitrate=192,
    )
    command = editor.build_command(clips, request_model, Path("/tmp/salida.mp3"))

    check("El comando es una lista de argumentos", isinstance(command, list))
    # Lo que se comprueba no es que no haya ';' —el grafo de filtros los lleva
    # por dentro— sino que el grafo viaje como UN argumento: si se armara una
    # cadena de shell, los valores del montaje podrían convertirse en comandos.
    semicolons = [arg for arg in command if ";" in arg]
    check(
        "El grafo de filtros viaja como un argumento propio, sin cadena de shell",
        len(semicolons) == 1
        and command[command.index("-filter_complex") + 1] == semicolons[0],
        f"{len(semicolons)} argumento(s) con ';'",
    )
    check("Cada tramo entra con su propio -ss y -t", command.count("-ss") == 2)
    check(
        "El silencio se pide como volumen 0 (no como -60 dB)",
        "volume=0" in " ".join(command),
    )
    check(
        "Los fundidos se traducen a afade",
        " ".join(command).count("afade=") == 2,
    )
    check(
        "Los tramos se concatenan",
        "concat=n=2" in " ".join(command),
    )
    check(
        "La salida fuerza el formato elegido",
        command[-1].endswith(".mp3") and "-c:a" in command,
    )

    # Un montaje imposible tiene que explicarse, no reventar. (Un tramo sin
    # duración se prueba más abajo, contra una fuente real: aquí no hay ninguna.)
    try:
        editor.RenderRequest(clips=[])
        check("Un montaje sin tramos se rechaza", False, "no se levantó ningún error")
    except ValidationError as exc:
        check("Un montaje sin tramos se rechaza", True, "pydantic exige al menos uno")

    try:
        editor.prepare(
            editor.RenderRequest(
                clips=[editor.ClipSpec(asset_id="f" * 32, start=0.0, end=5.0)]
            )
        )
        check("Una fuente inexistente se rechaza", False, "no se levantó ningún error")
    except editor.EditorError as exc:
        check("Una fuente inexistente se rechaza", "no está disponible" in str(exc), str(exc))

    check(
        "El tipo se decide por los primeros bytes, no por el nombre",
        editor.sniff_container(b"ID3\x04\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00") == "mp3"
        and editor.sniff_container(b"NOPE" * 8) is None,
    )


# ---------------------------------------------------------------------------
# Parte B · contra el servidor
# ---------------------------------------------------------------------------
def httppart(tmp: Path) -> None:
    print(f"\nB · Servidor en {BASE}")

    status, info = request("/api/audio/info")
    if status != 200 or not isinstance(info, dict):
        check("El editor está disponible", False, f"HTTP {status}: {info}")
        return

    check(
        "El editor está disponible",
        bool(info.get("enabled")),
        f"{info.get('assets_count', 0)} audios cargados · "
        f"máx {info.get('max_upload_mb')} MB · {info.get('max_minutes')} min",
    )
    status, page = request("/editor")
    html = page.decode("utf-8", "replace") if isinstance(page, bytes) else ""
    controls = [
        "btn-play", "btn-start", "btn-end", "btn-back5", "btn-back10",
        "btn-fwd5", "btn-fwd10", "btn-loop", "scrub", "speed",
    ]
    missing = [name for name in controls if f'id="{name}"' not in html]
    check(
        "La página trae los controles de reproducción",
        status == 200 and not missing,
        "todo presente" if not missing else f"falta: {', '.join(missing)}",
    )

    formats = [item["id"] for item in info.get("formats") or []]
    check(
        "Ofrece solo formatos que este FFmpeg sabe generar",
        "mp3" in formats and len(formats) >= 1,
        ", ".join(formats),
    )

    # --- tono de prueba generado por FFmpeg ------------------------------
    tone = tmp / "tono.mp3"
    built = subprocess.run(
        [
            find_ffmpeg() or "ffmpeg",
            "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
            "-c:a", "libmp3lame", "-b:a", "128k", str(tone),
        ],
        capture_output=True,
        timeout=120,
    )
    if built.returncode != 0 or not tone.is_file():
        check("Generar el tono de prueba", False, built.stderr.decode()[-200:])
        return
    check("Generar el tono de prueba", True, f"{tone.stat().st_size} bytes")

    # --- subida -----------------------------------------------------------
    status, asset = request(
        "/api/audio/upload?name=tono%20de%20prueba.mp3", raw=tone.read_bytes()
    )
    if status != 201 or not isinstance(asset, dict):
        check("Subir un MP3", False, f"HTTP {status}: {asset}")
        return
    asset_id = asset["id"]
    check(
        "Subir un MP3",
        status == 201 and abs((asset.get("duration") or 0) - 12) < 0.5,
        f"{asset_id[:8]}… · {asset.get('duration')}s · {asset.get('codec')} "
        f"{asset.get('sample_rate')} Hz · {asset.get('channels')} canales",
    )

    # --- lo que no es audio ----------------------------------------------
    status, body = request("/api/audio/upload?name=no-soy-audio.mp3", raw=b"x" * 4096)
    check(
        "Un archivo que no es audio se rechaza por su contenido",
        status == 415,
        f"HTTP {status}: {body}",
    )

    # --- onda -------------------------------------------------------------
    status, peaks = request(f"/api/audio/asset/{asset_id}/peaks")
    ok = (
        status == 200
        and isinstance(peaks, dict)
        and len(peaks.get("peaks") or []) > 100
        and (peaks.get("per_second") or 0) > 0
    )
    check(
        "La onda llega para dibujar la línea de tiempo",
        ok,
        f"{len(peaks.get('peaks') or [])} puntos a {peaks.get('per_second')}/s",
    )

    # --- reproducción -----------------------------------------------------
    status, body = request(f"/api/audio/asset/{asset_id}")
    size = len(body) if isinstance(body, bytes) else 0
    check(
        "El audio se puede escuchar desde el navegador",
        status == 200 and size == tone.stat().st_size,
        f"HTTP {status} · {size} bytes",
    )

    # --- exportar: dos tramos, volumen y fundido --------------------------
    status, render = request(
        "/api/audio/render",
        {
            "clips": [
                {"asset_id": asset_id, "start": 0.0, "end": 4.0, "gain_db": 0.0, "fade_in": 1.0},
                {"asset_id": asset_id, "start": 6.0, "end": 10.0, "gain_db": -6.0, "fade_out": 1.5},
            ],
            "format": "mp3",
            "bitrate": 192,
            "name": "montaje de prueba",
        },
    )
    if status != 202 or not isinstance(render, dict):
        check("Exportar un montaje de dos tramos", False, f"HTTP {status}: {render}")
        return

    check(
        "Exportar un montaje de dos tramos",
        render.get("status") in ("queued", "processing"),
        f"{render.get('render_id', '')[:8]}… · {render.get('clips')} tramos · "
        f"{render.get('duration')}s de salida",
    )

    final = wait_render(render["render_id"])
    if final.get("status") != "done":
        check("La exportación termina bien", False, str(final.get("error")))
        return

    check(
        "La exportación termina bien",
        True,
        f"{final.get('filesize')} bytes · {final.get('filename')}",
    )

    out = tmp / str(final.get("filename") or "salida.mp3")
    status, body = request(final["download_url"], timeout=180)
    if isinstance(body, bytes):
        out.write_bytes(body)

    measured = editor.probe(out) if out.is_file() else {}
    duration = measured.get("duration") or 0
    check(
        "El archivo entregado dura lo que se montó (8 s)",
        abs(duration - 8.0) < 0.6,
        f"{duration}s · {measured.get('codec')} · {measured.get('bitrate_kbps')} kbps",
    )
    check(
        "El MP3 entregado tiene cabecera ID3",
        out.is_file() and out.read_bytes()[:3] in (b"ID3",),
        out.read_bytes()[:3].hex() if out.is_file() else "sin archivo",
    )

    # --- otro formato de salida -------------------------------------------
    status, wav_render = request(
        "/api/audio/render",
        {
            "clips": [{"asset_id": asset_id, "start": 0.0, "end": 2.0}],
            "format": "wav",
            "name": "prueba wav",
        },
    )
    if status == 202 and isinstance(wav_render, dict):
        wav_final = wait_render(wav_render["render_id"], timeout=90)
        status, body = (
            request(wav_final["download_url"], timeout=120)
            if wav_final.get("status") == "done"
            else (0, b"")
        )
        head = body[:4] if isinstance(body, bytes) else b""
        check(
            "Exportar en WAV genera un WAV de verdad",
            head == b"RIFF",
            f"cabecera {head!r} · {len(body) if isinstance(body, bytes) else 0} bytes",
        )
    else:
        check("Exportar en WAV", False, f"HTTP {status}: {wav_render}")

    # --- montajes imposibles ----------------------------------------------
    status, body = request(
        "/api/audio/render",
        {"clips": [{"asset_id": asset_id, "start": 3.0, "end": 3.0}], "format": "mp3"},
    )
    check("Un tramo sin duración se rechaza con 422", status == 422, f"HTTP {status}")

    status, body = request(
        "/api/audio/render",
        {"clips": [{"asset_id": "a" * 32, "start": 0.0, "end": 2.0}], "format": "mp3"},
    )
    check("Una fuente inexistente se rechaza con 422", status == 422, f"HTTP {status}")

    status, body = request(
        "/api/audio/render",
        {"clips": [{"asset_id": asset_id, "start": 0.0, "end": 2.0}], "format": "flac"},
    )
    check(
        "Un formato no soportado se rechaza (400 o 422, según dónde se corte)",
        status in (400, 422),
        f"HTTP {status}",
    )

    status, body = request(
        "/api/audio/render",
        {"clips": [{"asset_id": "../etc/passwd", "start": 0.0, "end": 1.0}], "format": "mp3"},
    )
    check(
        "Un identificador con ruta se rechaza antes de tocar el disco",
        status in (400, 422),
        f"HTTP {status}",
    )

    # --- borrar ------------------------------------------------------------
    status, _ = request(f"/api/audio/asset/{asset_id}", method="DELETE")
    check("Borrar un audio del servidor", status == 200, f"HTTP {status}")

    status, _ = request(f"/api/audio/asset/{asset_id}")
    check("Un audio borrado ya no se sirve", status == 404, f"HTTP {status}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="editor-smoke-"))
    try:
        offline()
        httppart(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [name for name, ok, _ in results if not ok]
    print(f"\nRESULTADO: {len(results) - len(failed)}/{len(results)} pruebas OK")
    if failed:
        print("Fallaron: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
