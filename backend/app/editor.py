"""Editor de audio: cortar, cambiar volumen, unir varios audios y fundirlos.

El editor no reimplementa el procesado de audio: describe el montaje —qué tramo
de qué archivo, en qué orden, con qué volumen y qué fundidos— y se lo entrega al
FFmpeg que el descargador ya usa. Todo se procesa en el servidor, así que el
resultado es un archivo real y no depende de la potencia del equipo que edita.

Tres piezas:

* **Fuentes (`assets`)** — el audio de entrada: un MP3 que descargó el propio
  servicio o uno subido desde el equipo. Cada fuente vive en su carpeta con su
  ficha (`meta.json`) y, si ya se pidió, sus picos de onda (`peaks.json`).
* **Montaje (`EDL`)** — la lista de tramos que el usuario compone en la línea de
  tiempo. Es lo único que viaja al servidor al exportar.
* **Exportaciones (`renders`)** — el resultado de aplicar un montaje: un archivo
  temporal con su progreso, que se entrega y se borra como cualquier descarga.

Decisiones que importan:

* El comando de FFmpeg se arma como **lista de argumentos**, nunca como cadena de
  shell: los números del montaje llegan del navegador y una cadena los
  convertiría en una inyección de comandos.
* El contenido subido se identifica por sus **primeros bytes**, no por la
  extensión ni por la cabecera que declare el cliente.
* Las rutas se construyen solo con **identificadores generados aquí** (32
  caracteres hexadecimales), así que no hay forma de salir de la carpeta del
  editor ni de leer otro archivo del servidor.
* Los picos de la onda se calculan una vez y se guardan: decodificar el audio
  entero cada vez que se abre la página sería un desperdicio de CPU y de disco.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from . import config
from .media import find_ffmpeg

log = logging.getLogger("editor")

# Sin ventana de consola en Windows: el servidor puede correr sin escritorio.
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Identificador de fuente o de exportación: 32 hex, generados aquí. Se valida
# antes de usarlo para nombrar una carpeta, porque es el único dato que llega
# del cliente y acaba formando parte de una ruta.
_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# Cabeceras de archivo aceptadas. Situar bien el límite de seguridad: el cliente
# elige el nombre del archivo, no el tipo, porque el tipo se deduce de aquí.
_MAGIC: Tuple[Tuple[bytes, str], ...] = (
    (b"ID3", "mp3"),
    (b"\xff\xfb", "mp3"),
    (b"\xff\xf3", "mp3"),
    (b"\xff\xf2", "mp3"),
    (b"\xff\xf1", "mp3"),
    (b"OggS", "ogg"),
    (b"fLaC", "flac"),
    (b"\x1a\x45\xdf\xa3", "webm"),  # Matroska / WebM
    (b"\x30\x26\xb2\x75\x8e\x66\xcf", "wma"),  # ASF / WMA
)

# Contenedores que se aceptan como fuente. `webm` y `wma` entran porque salen de
# descargas reales; el resto es audio "de toda la vida".
INPUT_CONTAINERS = {"mp3", "wav", "ogg", "flac", "m4a", "webm", "wma"}

# Formatos de salida. `bitrate` indica si admite elegir kbps.
OUTPUT_FORMATS: Dict[str, dict] = {
    "mp3": {
        "label": "MP3",
        "ext": "mp3",
        "encoder": "libmp3lame",
        "args": ["-c:a", "libmp3lame"],
        "bitrate": True,
        "mime": "audio/mpeg",
    },
    "wav": {
        "label": "WAV",
        "ext": "wav",
        "encoder": "pcm_s16le",
        "args": ["-c:a", "pcm_s16le"],
        "bitrate": False,
        "mime": "audio/wav",
    },
    "ogg": {
        "label": "OGG",
        "ext": "ogg",
        "encoder": "libvorbis",
        "args": ["-c:a", "libvorbis", "-q:a", "5"],
        "bitrate": False,
        "mime": "audio/ogg",
    },
    "m4a": {
        "label": "M4A",
        "ext": "m4a",
        "encoder": "aac",
        "args": ["-c:a", "aac"],
        "bitrate": True,
        "mime": "audio/mp4",
    },
}

MIME_BY_CONTAINER = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "m4a": "audio/mp4",
    "flac": "audio/flac",
    "webm": "audio/webm",
    "wma": "audio/x-ms-wma",
}


class EditorError(Exception):
    """Fallo entendible por el usuario, no una traza interna."""


# ---------------------------------------------------------------------------
# FFmpeg: localización y ejecución
# ---------------------------------------------------------------------------
def ffmpeg_path() -> str:
    path = find_ffmpeg()
    if not path:
        raise EditorError(
            "El editor necesita FFmpeg y no se encontró ninguno en este servidor."
        )
    return path


@dataclass
class RunResult:
    code: int
    stdout: bytes
    stderr: str


def _run(args: List[str], timeout: float = 120.0) -> RunResult:
    """Ejecuta FFmpeg sin ventana y con la salida capturada.

    Sin `shell=True` en ningún caso: la lista de argumentos es exactamente lo
    que se ejecuta, así que un valor raro del montaje no puede convertirse en
    otro comando.
    """
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        raise EditorError("La operación tardó demasiado y se canceló.")
    except OSError as exc:
        raise EditorError(f"No se pudo ejecutar FFmpeg: {exc}")

    return RunResult(
        code=proc.returncode,
        stdout=proc.stdout or b"",
        stderr=(proc.stderr or b"").decode("utf-8", "replace"),
    )


_ENCODERS: Optional[set] = None
_ENCODERS_LOCK = threading.Lock()


def available_encoders() -> set:
    """Codificadores que este FFmpeg trae de verdad.

    No todos los binarios se compilan igual (el de `imageio-ffmpeg` sí trae
    libmp3lame y libvorbis, pero uno mínimo podría no traerlos). Se consulta una
    vez y se filtra la lista de formatos ofrecidos: mejor no ofrecer un formato
    que va a fallar al exportar.
    """
    global _ENCODERS
    with _ENCODERS_LOCK:
        if _ENCODERS is not None:
            return _ENCODERS

        result = _run([ffmpeg_path(), "-hide_banner", "-encoders"], timeout=30)

        # La lista de codificadores sale por la salida ESTÁNDAR, al contrario que
        # el informe de un archivo (ese va a la de error). Se leen las dos por si
        # alguna versión de FFmpeg cambia de costumbre.
        listing = result.stdout.decode("utf-8", "replace") + "\n" + result.stderr
        found = {
            name
            for name in re.findall(r"^\s*[A-Za-z.]{6}\s+(\S+)", listing, re.MULTILINE)
            if name != "="  # la leyenda de la cabecera (« V..... = Video»)
        }
        if not found:
            log.warning("FFmpeg no informó de sus codificadores; se ofrecerá MP3 y WAV")
            found = {"libmp3lame", "pcm_s16le"}  # lo mínimo que trae cualquier build
        _ENCODERS = found
        return _ENCODERS


def output_formats() -> List[dict]:
    """Formatos de salida utilizables en este servidor."""
    encoders = available_encoders()
    usable = []
    for key, spec in OUTPUT_FORMATS.items():
        if spec["encoder"] in encoders:
            usable.append({"id": key, "label": spec["label"], "bitrate": spec["bitrate"]})
    return usable


def _timeout_for(duration: float) -> float:
    """Margen generoso: codificar va muy por encima del tiempo real, pero un
    archivo largo en un servidor cargado no debe morir por ir lento."""
    return max(120.0, min(900.0, duration * 3 + 60))


# ---------------------------------------------------------------------------
# Medición del audio
# ---------------------------------------------------------------------------
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_STREAM_RE = re.compile(r"Stream #\d+:\d+.*?: Audio:\s*([A-Za-z0-9_]+)")
_RATE_RE = re.compile(r"(\d{4,6})\s*Hz")
_CHANNELS_RE = re.compile(r"\b(mono|stereo)\b|,\s*(\d+)\s+channels")
_BITRATE_RE = re.compile(r"bitrate:\s*(\d+)\s*kb/s")


def probe(path: Path) -> dict:
    """Duración, códec, frecuencia y canales, sin depender de `ffprobe`.

    `imageio-ffmpeg` solo trae el binario de FFmpeg, no el de `ffprobe`, y este
    proyecto funciona sin instalar nada. Por eso los datos se leen del informe
    que FFmpeg escribe en su salida de error al abrir un archivo.
    """
    result = _run([ffmpeg_path(), "-hide_banner", "-i", str(path)], timeout=60)
    info = result.stderr

    duration: Optional[float] = None
    match = _DURATION_RE.search(info)
    if match:
        hours, minutes, seconds = match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if duration is None:
        duration = _duration_by_decoding(path)

    stream = _STREAM_RE.search(info)
    rate = _RATE_RE.search(info)
    channels = _CHANNELS_RE.search(info)
    bitrate = _BITRATE_RE.search(info)

    if stream is None and duration is None:
        raise EditorError("Ese archivo no parece contener audio legible.")

    return {
        "duration": round(duration, 3) if duration else None,
        "codec": stream.group(1).lower() if stream else None,
        "sample_rate": int(rate.group(1)) if rate else None,
        "channels": (
            1 if channels and channels.group(1) == "mono"
            else 2 if channels and channels.group(1) == "stereo"
            else int(channels.group(2)) if channels and channels.group(2)
            else None
        ),
        "bitrate_kbps": int(bitrate.group(1)) if bitrate else None,
    }


_DECODE_TIME_RE = re.compile(r"time=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


def _duration_by_decoding(path: Path) -> Optional[float]:
    """Último recurso cuando el contenedor no declara duración.

    Decodifica a la nada (`-f null -`) y se queda con el tiempo más alto que
    FFmpeg haya informado. Cuesta un recorrido completo del archivo, así que
    solo se usa cuando no hay otra forma de saber cuánto dura.
    """
    result = _run(
        [ffmpeg_path(), "-hide_banner", "-nostdin", "-i", str(path), "-f", "null", "-"],
        timeout=600,
    )
    best: Optional[float] = None
    for hours, minutes, seconds in _DECODE_TIME_RE.findall(result.stderr):
        value = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        best = value if best is None else max(best, value)
    return best


# ---------------------------------------------------------------------------
# Forma de onda
# ---------------------------------------------------------------------------
# Muestras por segundo con las que se miden los picos. El navegador dibuja la
# onda a partir de esto; no hace falta más resolución de la que se puede pintar.
_PEAK_SAMPLE_RATE = 8000


def peaks_for(path: Path, duration: Optional[float]) -> dict:
    """Picos de amplitud de la onda, en pares (min, max) por intervalo.

    Se decodifica a PCM mono de 8 kHz y se agrupa: barato de calcular y más que
    suficiente para dibujar. El resultado se guarda en `peaks.json` junto a la
    fuente, porque esto recorre el archivo entero.
    """
    # Para archivos largos se baja la resolución en vez de devolver un JSON enorme, pero
    # se mantiene alta de sobra para el zoom: con 60.000 puntos el archivo son unos
    # cientos de KB y un segundo de música sigue teniendo detalle al ampliar.
    per_second = config.EDITOR_PEAKS_PER_SECOND
    if duration and duration > 0:
        per_second = max(2.0, min(per_second, 60000.0 / duration))
    per_second = round(per_second, 3)

    cached = path.parent / "peaks.json"
    if cached.is_file():
        try:
            data = json.loads(cached.read_text(encoding="utf-8"))
            # La caché vale solo si trae la resolución de ahora: al cambiarla se
            # recalcula, en vez de servir una onda más basta de la cuenta.
            if data.get("peaks") and data.get("per_second") == per_second:
                return data
        except (OSError, json.JSONDecodeError):
            pass  # caché ilegible: se recalcula

    samples_per_bucket = max(1, int(round(_PEAK_SAMPLE_RATE / per_second)))
    args = [
        ffmpeg_path(), "-v", "error", "-nostdin",
        "-i", str(path),
        "-vn", "-ac", "1", "-ar", str(_PEAK_SAMPLE_RATE),
        "-f", "s16le", "-",
    ]

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )

    peaks: List[List[float]] = []
    leftovers = b""
    bucket_min = 32767
    bucket_max = -32768
    count = 0

    try:
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            data = leftovers + chunk
            usable = len(data) - (len(data) % 2)
            leftovers = data[usable:]

            # `array` no vale aquí porque el trozo puede quedar desalineado en
            # el último byte; con memoria se recorre el bloque entero.
            for index in range(0, usable, 2):
                value = int.from_bytes(data[index:index + 2], "little", signed=True)
                bucket_min = min(bucket_min, value)
                bucket_max = max(bucket_max, value)
                count += 1
                if count >= samples_per_bucket:
                    peaks.append([round(bucket_min / 32768, 3), round(bucket_max / 32768, 3)])
                    bucket_min, bucket_max, count = 32767, -32768, 0
    finally:
        if proc.stdout:
            proc.stdout.close()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()

    if count:
        peaks.append([round(bucket_min / 32768, 3), round(bucket_max / 32768, 3)])

    data = {"per_second": per_second, "peaks": peaks, "duration": duration}
    try:
        cached.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass  # sin caché se recalcula; no es motivo para fallar
    return data


# ---------------------------------------------------------------------------
# Fuentes (assets)
# ---------------------------------------------------------------------------
def _editor_root() -> Path:
    root = Path(config.EDITOR_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def assets_dir() -> Path:
    path = _editor_root() / "assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def renders_dir() -> Path:
    path = _editor_root() / "renders"
    path.mkdir(parents=True, exist_ok=True)
    return path


def uploads_dir() -> Path:
    """Zona de aterrizaje de las subidas.

    Una subida se escribe aquí y solo pasa a ser fuente cuando se ha podido
    identificar y medir. Así un archivo que no sea audio no llega nunca a la
    carpeta de fuentes.
    """
    path = _editor_root() / "incoming"
    path.mkdir(parents=True, exist_ok=True)
    return path


def valid_id(value: str) -> str:
    if not value or not _ID_RE.match(value):
        raise EditorError("Identificador de audio no válido.")
    return value


def sniff_container(head: bytes) -> Optional[str]:
    """Tipo real del archivo, según sus primeros bytes."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "m4a"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    for magic, name in _MAGIC:
        if head.startswith(magic):
            return name
    return None


def _clean_name(value: Optional[str], fallback: str = "audio") -> str:
    """Nombre presentable: sin rutas, sin caracteres de control, acotado."""
    if not value:
        return fallback
    name = Path(str(value).replace("\\", "/")).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip()
    return name[:120] or fallback


def safe_name(value: Optional[str], fallback: str = "audio") -> str:
    """Nombre presentable de un archivo exportado. Parte pública de `_clean_name`."""
    return _clean_name(value, fallback)


def _meta_path(asset_id: str) -> Path:
    return assets_dir() / valid_id(asset_id) / "meta.json"


def get_asset(asset_id: str) -> Optional[dict]:
    """Ficha de una fuente, o None si no existe."""
    try:
        path = _meta_path(asset_id)
    except EditorError:
        return None
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    data["path"] = str(path.parent / data["file"])
    return data


def list_assets() -> List[dict]:
    out: List[dict] = []
    for directory in assets_dir().iterdir():
        if not directory.is_dir():
            continue
        asset = get_asset(directory.name)
        if asset:
            asset.pop("path", None)
            out.append(asset)
    out.sort(key=lambda item: item.get("created_at") or 0, reverse=True)
    return out


def count_assets() -> int:
    return sum(1 for d in assets_dir().iterdir() if d.is_dir() and (d / "meta.json").is_file())


def delete_asset(asset_id: str) -> bool:
    if not get_asset(asset_id):
        return False
    shutil.rmtree(assets_dir() / valid_id(asset_id), ignore_errors=True)
    return True


def create_asset(source: Path, display_name: str, container: str) -> dict:
    """Convierte un archivo ya validado en una fuente del editor.

    `source` se mueve (no se copia): en el caso de las subidas ya está en la
    zona de aterrizaje, y cuando viene de una descarga, se copia antes a ella.
    """
    if container not in INPUT_CONTAINERS:
        raise EditorError("Ese tipo de archivo no se puede editar aquí.")

    if count_assets() >= config.EDITOR_MAX_ASSETS:
        raise EditorError(
            f"Ya hay {config.EDITOR_MAX_ASSETS} audios cargados. Borra alguno antes de añadir más."
        )

    asset_id = uuid.uuid4().hex
    target_dir = assets_dir() / asset_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"original.{container}"

    try:
        shutil.move(str(source), str(target))
    except OSError as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise EditorError(f"No se pudo guardar el audio: {exc}")

    try:
        measured = probe(target)
    except EditorError:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise

    duration = measured.get("duration")
    if not duration or duration <= 0:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise EditorError("No se pudo medir la duración de ese audio.")
    if duration > config.EDITOR_MAX_MINUTES * 60:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise EditorError(
            f"El audio dura más de {config.EDITOR_MAX_MINUTES} minutos. "
            "Recórtalo antes de subirlo."
        )

    asset = {
        "id": asset_id,
        "name": _clean_name(display_name, f"audio.{container}"),
        "container": container,
        "file": target.name,
        "size": target.stat().st_size,
        "created_at": time.time(),
        **measured,
    }
    (target_dir / "meta.json").write_text(
        json.dumps(asset, ensure_ascii=False), encoding="utf-8"
    )
    log.info("Fuente de audio %s (%s, %.1fs)", asset_id, container, duration)
    return asset


def asset_from_upload(tmp: Path, display_name: str, container: str) -> dict:
    return create_asset(tmp, display_name, container)


# ---------------------------------------------------------------------------
# Montaje (EDL)
# ---------------------------------------------------------------------------
class ClipSpec(BaseModel):
    """Un tramo de una fuente dentro del montaje."""

    asset_id: str = Field(..., pattern=r"^[0-9a-f]{32}$")
    start: float = Field(0.0, ge=0.0)
    end: Optional[float] = Field(None, ge=0.0)
    # Volumen en decibelios: 0 no toca nada, -6 baja a la mitad, -60 silencia.
    gain_db: float = Field(0.0, ge=-60.0, le=24.0)
    fade_in: float = Field(0.0, ge=0.0, le=30.0)
    fade_out: float = Field(0.0, ge=0.0, le=30.0)


class RenderRequest(BaseModel):
    clips: List[ClipSpec] = Field(..., min_length=1)
    format: str = Field("mp3", pattern=r"^(mp3|wav|ogg|m4a)$")
    bitrate: int = Field(192, ge=64, le=320)
    name: Optional[str] = Field(None, max_length=120)


@dataclass
class PreparedClip:
    """Tramo ya validado: números definitivos y la ruta que se le pasa a FFmpeg."""

    asset_id: str
    path: str
    start: float
    duration: float
    gain_db: float
    fade_in: float
    fade_out: float

    @property
    def fade_out_start(self) -> float:
        return max(0.0, self.duration - self.fade_out)


def prepare(request: RenderRequest) -> Tuple[List[PreparedClip], float]:
    """Valida el montaje contra los audios que existen y ajusta sus números.

    Aquí se corta todo lo que no encaje: tramos que apuntan a fuentes que no
    existen, tiempos imposibles, más tramos de los permitidos. Los valores que
    se salen un poco de rango se ajustan (recortar al final del archivo es lo
    que el usuario espera) en vez de rechazar el montaje entero.
    """
    if len(request.clips) > config.EDITOR_MAX_CLIPS:
        raise EditorError(
            f"El montaje tiene demasiados tramos (máximo {config.EDITOR_MAX_CLIPS})."
        )

    prepared: List[PreparedClip] = []
    for clip in request.clips:
        asset = get_asset(clip.asset_id)
        if not asset:
            raise EditorError("Una de las fuentes del montaje ya no está disponible.")

        total = float(asset["duration"])
        start = min(max(0.0, clip.start), total)
        end = total if clip.end is None else min(max(0.0, clip.end), total)
        duration = end - start
        if duration <= 0.05:
            raise EditorError("Uno de los tramos no tiene duración (inicio y fin coinciden).")

        fade_in = min(clip.fade_in, duration)
        fade_out = min(clip.fade_out, duration - fade_in if duration - fade_in > 0 else 0.0)

        prepared.append(
            PreparedClip(
                asset_id=clip.asset_id,
                path=asset["path"],
                start=round(start, 3),
                duration=round(duration, 3),
                gain_db=clip.gain_db,
                fade_in=round(fade_in, 3),
                fade_out=round(fade_out, 3),
            )
        )

    total_duration = sum(clip.duration for clip in prepared)
    return prepared, total_duration


def build_command(clips: List[PreparedClip], request: RenderRequest, out_path: Path) -> List[str]:
    """Comando de FFmpeg para un montaje.

    Cada tramo entra como una entrada propia con su `-ss` y su `-t`: así el
    corte se busca en el archivo (rápido, sin decodificar todo lo anterior) y se
    lee solo lo que hace falta. Después, en el filtro, se aplican el volumen y
    los fundidos, y todos los tramos se concatenan en orden.
    """
    args = [ffmpeg_path(), "-hide_banner", "-nostdin", "-y"]

    for clip in clips:
        args += ["-ss", f"{clip.start:.3f}", "-t", f"{clip.duration:.3f}", "-i", clip.path]

    # El mismo formato de muestreo en todas las ramas: `concat` exige que las
    # entradas coincidan, y las fuentes pueden venir de cualquier sitio.
    common = (
        f"aformat=sample_fmts=s16:sample_rates={config.EDITOR_SAMPLE_RATE}"
        ":channel_layouts=stereo"
    )

    chains: List[str] = []
    labels: List[str] = []
    for index, clip in enumerate(clips):
        steps = [f"[{index}:a]asetpts=PTS-STARTPTS", common]
        if clip.gain_db <= -60.0:
            steps.append("volume=0")  # silencio real, no un volumen casi nulo
        elif abs(clip.gain_db) > 0.01:
            steps.append(f"volume={clip.gain_db:.2f}dB")
        if clip.fade_in > 0.01:
            steps.append(f"afade=t=in:st=0:d={clip.fade_in:.3f}")
        if clip.fade_out > 0.01:
            steps.append(f"afade=t=out:st={clip.fade_out_start:.3f}:d={clip.fade_out:.3f}")
        labels.append(f"[a{index}]")
        chains.append(",".join(steps) + f"[a{index}]")

    if len(clips) == 1:
        out_label = "[a0]"
    else:
        out_label = "[aout]"
        chains.append(f"{''.join(labels)}concat=n={len(clips)}:v=0:a=1[aout]")

    args += ["-filter_complex", ";".join(chains), "-map", out_label]

    spec = OUTPUT_FORMATS.get(request.format) or OUTPUT_FORMATS["mp3"]
    args += list(spec["args"])
    if spec["bitrate"]:
        args += ["-b:a", f"{request.bitrate}k"]
    args += ["-ar", str(config.EDITOR_SAMPLE_RATE), "-ac", "2"]

    if request.name:
        args += ["-metadata", f"title={_clean_name(request.name, 'audio')}"]

    args += ["-f", spec["ext"], str(out_path)]
    return args


# ---------------------------------------------------------------------------
# Exportaciones (renders)
# ---------------------------------------------------------------------------
@dataclass
class Render:
    id: str
    name: str
    format: str
    status: str = "queued"  # queued | processing | done | error
    progress: float = 0.0
    duration: float = 0.0
    filename: Optional[str] = None
    filepath: Optional[str] = None
    filesize: Optional[int] = None
    clips: int = 0
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def download_url(self) -> Optional[str]:
        return f"/api/audio/render/{self.id}/file" if self.status == "done" else None

    def to_public(self) -> dict:
        return {
            "render_id": self.id,
            "status": self.status,
            "progress": round(self.progress, 1),
            "format": self.format,
            "clips": self.clips,
            "duration": round(self.duration, 2),
            "filename": self.filename,
            "filesize": self.filesize,
            "download_url": self.download_url,
            "error": self.error,
        }


class RenderStore:
    """Exportaciones vivas.

    Vive en memoria, como los trabajos de descarga: una exportación es algo que
    se pide y se recoge en la misma sesión. El archivo sí está en disco, así que
    un reinicio no deja basura: el barrido lo recoge por antigüedad.
    """

    def __init__(self) -> None:
        self._items: Dict[str, Render] = {}
        self._lock = threading.Lock()
        # Un solo hilo: codificar audio ya usa todos los núcleos por dentro, y
        # así una exportación nunca le roba el turno a una descarga en curso.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audio")

    def create(self, request: RenderRequest, duration: float, name: str) -> Render:
        render = Render(
            id=uuid.uuid4().hex,
            name=name,
            format=request.format,
            duration=duration,
            clips=len(request.clips),
        )
        with self._lock:
            self._items[render.id] = render
        return render

    def get(self, render_id: str) -> Optional[Render]:
        with self._lock:
            return self._items.get(render_id)

    def update(self, render_id: str, **fields) -> Optional[Render]:
        with self._lock:
            render = self._items.get(render_id)
            if not render:
                return None
            for key, value in fields.items():
                setattr(render, key, value)
            return render

    def counts(self) -> dict:
        with self._lock:
            summary = {"total": len(self._items)}
            for item in self._items.values():
                summary[item.status] = summary.get(item.status, 0) + 1
            return summary

    def submit(self, render: Render, clips: List[PreparedClip], request: RenderRequest) -> None:
        self._pool.submit(self._run_render, render, clips, request)

    # -- ejecución ----------------------------------------------------------
    def _run_render(self, render: Render, clips: List[PreparedClip], request: RenderRequest) -> None:
        workdir = renders_dir() / render.id
        workdir.mkdir(parents=True, exist_ok=True)
        self.update(render.id, status="processing")

        spec = OUTPUT_FORMATS.get(request.format) or OUTPUT_FORMATS["mp3"]
        stem = _clean_name(request.name or "audio-editado", "audio-editado")
        out_path = workdir / f"{stem}.{spec['ext']}"

        try:
            command = build_command(clips, request, out_path)
            stderr = _encode(command, render.duration, lambda pct: self.update(render.id, progress=pct))
            if not out_path.is_file():
                raise EditorError((stderr or "").strip()[-400:] or "FFmpeg no generó el archivo.")

            self.update(
                render.id,
                status="done",
                progress=100.0,
                filename=out_path.name,
                filepath=str(out_path),
                filesize=out_path.stat().st_size,
                finished_at=time.time(),
            )
            log.info(
                "Exportación %s lista (%s, %.1f MB, %s tramos)",
                render.id, spec["ext"], out_path.stat().st_size / (1024 * 1024), len(clips),
            )
        except EditorError as exc:
            self.update(render.id, status="error", error=str(exc), finished_at=time.time())
            log.warning("Exportación %s falló: %s", render.id, exc)
        except Exception:  # noqa: BLE001 - una exportación no debe tumbar el servicio
            log.exception("Error inesperado exportando %s", render.id)
            self.update(
                render.id,
                status="error",
                error="Falló la exportación. Revisa que los tramos sigan ahí.",
                finished_at=time.time(),
            )


def _encode(command: List[str], total: float, on_progress) -> str:
    """Lanza FFmpeg y va informando del avance.

    `-progress pipe:1` hace que FFmpeg escupa líneas `clave=valor` con el tiempo
    ya procesado; con eso el progreso es real y no una barra inventada. La
    salida de error va a un archivo, no a una tubería: si nadie la leyera, el
    proceso se quedaría bloqueado al llenarse el búfer.
    """
    error_file = Path(command[-1]).parent / "ffmpeg.log"
    with open(error_file, "wb") as errors:
        proc = subprocess.Popen(
            command + ["-progress", "pipe:1", "-nostats", "-loglevel", "error"],
            stdout=subprocess.PIPE,
            stderr=errors,
            creationflags=_NO_WINDOW,
        )

        # Un FFmpeg colgado no puede quedarse para siempre: el registro de
        # exportaciones tiene un solo hilo y bloquearía todo lo que venga
        # detrás. El vigilante lo mata al cumplirse el plazo y el bucle de
        # lectura termina solo, porque la tubería se cierra.
        timed_out = {"value": False}

        def _kill_on_timeout() -> None:
            timed_out["value"] = True
            proc.kill()

        watchdog = threading.Timer(_timeout_for(total), _kill_on_timeout)
        watchdog.daemon = True
        watchdog.start()

        last = 0.0
        try:
            for raw in proc.stdout:  # type: ignore[union-attr]
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("out_time_us="):
                    try:
                        seconds = int(line.split("=", 1)[1]) / 1_000_000
                    except ValueError:
                        continue
                    if total > 0:
                        pct = max(0.0, min(98.0, seconds / total * 100))
                        if pct - last >= 1.0:
                            last = pct
                            on_progress(pct)
                elif line.startswith("progress=end"):
                    on_progress(99.0)
            proc.wait(timeout=120)
        finally:
            watchdog.cancel()
            if proc.stdout:
                proc.stdout.close()
            if proc.poll() is None:
                proc.kill()

    text = ""
    try:
        text = error_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass

    if timed_out["value"]:
        raise EditorError("La exportación tardó demasiado y se canceló.")
    if proc.returncode != 0:
        raise EditorError(_friendly_error(text))
    return text


def _friendly_error(stderr: str) -> str:
    text = (stderr or "").lower()
    if "unknown encoder" in text or "encoder not found" in text:
        return "Este FFmpeg no puede generar ese formato. Prueba con MP3 o WAV."
    if "no space left" in text:
        return "No queda espacio en el disco del servidor."
    if "invalid argument" in text or "error opening" in text:
        return "No se pudo leer alguna de las fuentes. Vuelve a añadirla."
    summary = " ".join((stderr or "").split())[-300:]
    return f"FFmpeg falló al exportar. {summary}" if summary else "FFmpeg falló al exportar."


renders = RenderStore()


# ---------------------------------------------------------------------------
# Limpieza
# ---------------------------------------------------------------------------
def sweep() -> dict:
    """Borra lo viejo: fuentes caducadas y exportaciones ya entregadas.

    Las fuentes duran horas (una sesión de edición) y las exportaciones minutos,
    como cualquier descarga. Sin esto, editar audio sería una fuga de disco
    lenta pero segura.
    """
    now = time.time()
    removed_assets = 0
    removed_renders = 0
    freed = 0

    asset_ttl = config.EDITOR_ASSET_TTL_HOURS * 3600
    for directory in assets_dir().iterdir():
        if not directory.is_dir():
            continue
        meta = get_asset(directory.name)
        created = (meta or {}).get("created_at")
        if not created:
            created = directory.stat().st_mtime
        if now - created < asset_ttl:
            continue
        freed += _size_of(directory)
        shutil.rmtree(directory, ignore_errors=True)
        removed_assets += 1

    render_ttl = config.EDITOR_RENDER_TTL_MINUTES * 60
    known = set()
    with renders._lock:  # noqa: SLF001 - mismo módulo, es su propio registro
        items = list(renders._items.values())  # noqa: SLF001
    for render in items:
        known.add(render.id)
        reference = render.finished_at or render.created_at
        if render.status in ("done", "error") and now - reference > render_ttl:
            freed += _size_of(renders_dir() / render.id)
            shutil.rmtree(renders_dir() / render.id, ignore_errors=True)
            with renders._lock:  # noqa: SLF001
                renders._items.pop(render.id, None)  # noqa: SLF001
            removed_renders += 1

    # Carpetas sin dueño: de una exportación interrumpida por un reinicio, o de
    # una subida que nunca llegó a ser fuente.
    for folder, limit in ((renders_dir(), 3600), (uploads_dir(), 3600)):
        for directory in folder.iterdir():
            if directory.name in known:
                continue
            if now - directory.stat().st_mtime > limit:
                freed += _size_of(directory)
                shutil.rmtree(directory, ignore_errors=True)
                removed_renders += 1

    for stray in uploads_dir().glob("*"):
        if stray.is_file() and now - stray.stat().st_mtime > 3600:
            freed += stray.stat().st_size
            stray.unlink(missing_ok=True)

    if removed_assets or removed_renders:
        log.info(
            "Editor: %s fuentes y %s exportaciones borradas (%.1f MB)",
            removed_assets, removed_renders, freed / (1024 * 1024),
        )

    return {
        "assets_removed": removed_assets,
        "renders_removed": removed_renders,
        "freed_bytes": freed,
    }


def _size_of(directory: Path) -> int:
    total = 0
    for path in directory.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return total


def storage_summary() -> dict:
    """Cuánto ocupa el editor ahora mismo, para el panel y la salud."""
    assets = list_assets()
    return {
        "enabled": config.EDITOR_ENABLED,
        "assets_count": len(assets),
        "assets_max": config.EDITOR_MAX_ASSETS,
        "renders": renders.counts(),
        "bytes": _size_of(_editor_root()),
        "max_upload_mb": config.EDITOR_MAX_UPLOAD_MB,
        "max_minutes": config.EDITOR_MAX_MINUTES,
        "max_clips": config.EDITOR_MAX_CLIPS,
        "max_assets": config.EDITOR_MAX_ASSETS,
        "formats": output_formats(),
    }
