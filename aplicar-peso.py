#!/usr/bin/env python3
"""Añade el peso del archivo a cada opción de descarga.

El motor ya devolvía `filesize` cuando el sitio lo declaraba, pero en la
mayoría de sitios no lo declara y el usuario elegía a ciegas. Aquí:

  1. Se estima el peso cuando el sitio no lo publica (bitrate x duración).
  2. Se suma la pista de audio al vídeo que la necesita, porque al descargar
     el motor une `formato+bestaudio`: sin esto, en YouTube el peso enseñado
     era el del vídeo solo y el archivo real salía más grande.

Se conserva el fin de línea CRLF del archivo.
"""

from pathlib import Path

TARGET = Path(__file__).with_name("backend") / "app" / "downloader.py"

HELPER_OLD = """# ---------------------------------------------------------------------------
# Paso 2 del diseño: resolver el link
# ---------------------------------------------------------------------------
def parse(url: str, skip_retry: bool = False) -> dict:
"""

HELPER_NEW = '''def _size_of(fmt: dict, duration: float) -> tuple:
    """Peso de una pista: el que declara el motor, o uno estimado.

    Muchos sitios no publican el tamaño por adelantado (Facebook, Instagram,
    TikTok), y el usuario elige a ciegas. Cuando no lo declaran se estima con
    el bitrate y la duración. El segundo valor avisa de si es un dato exacto o
    aproximado, para que la interfaz pueda enseñarlo como aproximado en lugar
    de hacer pasar una cuenta por una cifra oficial.
    """
    exact = fmt.get("filesize")
    if exact:
        return int(exact), False

    approx = fmt.get("filesize_approx")
    if approx:
        return int(approx), True

    # tbr es el bitrate total de la pista; sirve igual para vídeo y para audio.
    rate = fmt.get("tbr") or fmt.get("vbr") or fmt.get("abr")
    if rate and duration:
        return int(float(rate) * 1000 / 8 * duration), True

    return None, False


# ---------------------------------------------------------------------------
# Paso 2 del diseño: resolver el link
# ---------------------------------------------------------------------------
def parse(url: str, skip_retry: bool = False) -> dict:
'''

LOOP_OLD = """    video_formats: List[dict] = []
    audio_formats: List[dict] = []

    for f in info.get("formats") or []:
        ext = f.get("ext")
        if not ext:
            continue
        vcodec = f.get("vcodec") or "none"
        acodec = f.get("acodec") or "none"
        has_video = vcodec != "none"
        has_audio = acodec != "none"

        entry = {
            "format_id": f.get("format_id"),
            "ext": ext,
            "height": f.get("height"),
            "width": f.get("width"),
            "fps": f.get("fps"),
            "filesize": f.get("filesize") or f.get("filesize_approx"),
            "abr": f.get("abr"),
            "vcodec": vcodec,
            "acodec": acodec,
            "has_audio": has_audio,
            "protocol": f.get("protocol"),
        }
"""

LOOP_NEW = """    video_formats: List[dict] = []
    audio_formats: List[dict] = []

    # La duración hace falta para estimar los pesos que el sitio no declara.
    duration = info.get("duration") or 0

    for f in info.get("formats") or []:
        ext = f.get("ext")
        if not ext:
            continue
        vcodec = f.get("vcodec") or "none"
        acodec = f.get("acodec") or "none"
        has_video = vcodec != "none"
        has_audio = acodec != "none"

        size, size_estimated = _size_of(f, duration)

        entry = {
            "format_id": f.get("format_id"),
            "ext": ext,
            "height": f.get("height"),
            "width": f.get("width"),
            "fps": f.get("fps"),
            "filesize": size,
            # Si el peso es una cuenta y no un dato del sitio, la interfaz lo
            # enseña como aproximado en vez de como cifra exacta.
            "estimated": size_estimated,
            "abr": f.get("abr"),
            "vcodec": vcodec,
            "acodec": acodec,
            "has_audio": has_audio,
            "protocol": f.get("protocol"),
        }
"""

MERGE_OLD = """    # Orden: mejor calidad primero, y prefiere lo que ya trae audio (no requiere merge).
    video_formats.sort(
"""

MERGE_NEW = """    # El archivo que recibe el usuario no es la pista suelta: al elegir una
    # calidad, el motor une ese vídeo con la mejor pista de audio
    # (`formato+bestaudio`). Si la ficha enseñara solo el peso del vídeo, el
    # archivo real saldría bastante más grande de lo prometido, y eso pasa
    # justo en las calidades altas de YouTube, que vienen sin audio.
    audio_size = max((a.get("filesize") or 0 for a in audio_formats), default=0)
    audio_estimated = any(
        a.get("estimated") for a in audio_formats if (a.get("filesize") or 0) == audio_size
    )
    for f in video_formats:
        # La suma solo se hace cuando el peso del vídeo se conoce: sumarle audio
        # a un vídeo de tamaño desconocido daría una cifra que no es ni el
        # vídeo ni el archivo final.
        if f.get("muted") and f.get("filesize") and audio_size:
            f["filesize"] = f["filesize"] + audio_size
            f["estimated"] = bool(f.get("estimated") or audio_estimated)

    # Orden: mejor calidad primero, y prefiere lo que ya trae audio (no requiere merge).
    video_formats.sort(
"""

PATCHES = [
    ("helper _size_of", HELPER_OLD, HELPER_NEW),
    ("bucle de formatos", LOOP_OLD, LOOP_NEW),
    ("suma del audio", MERGE_OLD, MERGE_NEW),
]


def main() -> int:
    if not TARGET.exists():
        print(f"ERROR: no existe {TARGET}")
        return 1

    # newline="" conserva los CRLF: el archivo queda tal cual estaba, salvo el
    # parche.
    with open(TARGET, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()

    for name, old, new in PATCHES:
        old_crlf = old.replace("\n", "\r\n")
        new_crlf = new.replace("\n", "\r\n")
        found = text.count(old_crlf)
        if found == 0:
            print(f"ERROR: no se encontro el bloque {name}")
            return 1
        if found > 1:
            print(f"ERROR: el bloque {name} aparece {found} veces")
            return 1
        text = text.replace(old_crlf, new_crlf)
        print(f"ok: {name}")

    with open(TARGET, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    print(f"escrito: {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
