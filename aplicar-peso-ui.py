#!/usr/bin/env python3
"""Enseña el peso del archivo en la lista de opciones (interfaz).

Dos retoques:

  1. Cada opción de vídeo lleva su peso debajo de la calidad. Cuando es una
     estimación del motor, va con «≈» para no hacerla pasar por una cifra
     exacta del sitio.
  2. La opción de MP3 también lleva peso: la conversión sale a 320 kbps
     constantes, así que depende solo de la duración.

El fin de línea de cada archivo se detecta y se conserva tal cual.
"""

from pathlib import Path

ROOT = Path(__file__).parent
APP = ROOT / "backend" / "static" / "app.js"
INDEX = ROOT / "backend" / "static" / "index.html"

# ---------------------------------------------------------------- app.js ----

ELS_OLD = """    videoFormats: $("video-formats"),
"""

ELS_NEW = """    videoFormats: $("video-formats"),
    mp3Sub: $("mp3-sub"),
"""

SUB_OLD = """      const sub = document.createElement("span");
      sub.className = "chip-sub";
      sub.textContent = f.filesize ? humanSize(f.filesize) : (f.muted ? "requiere FFmpeg" : "con audio");
"""

SUB_NEW = """      const sub = document.createElement("span");
      sub.className = "chip-sub";
      // El peso va en la propia opción: se elige sabiendo lo que ocupa. El
      // «≈» avisa de que es una estimación nuestra y no una cifra del sitio.
      sub.textContent = f.filesize
        ? `${f.estimated ? "≈ " : ""}${humanSize(f.filesize)}`
        : (f.muted ? "requiere FFmpeg" : "con audio");
"""

MP3_OLD = """    el.result.classList.remove("hidden");
    el.result.scrollIntoView({ behavior: "smooth", block: "nearest" });
"""

MP3_NEW = """    // El MP3 también tiene peso, y se estima solo con la duración: la
    // conversión sale a 320 kbps constantes haga lo que haga el original.
    if (el.mp3Sub) {
      el.mp3Sub.textContent = info.duration
        ? `≈ ${humanSize((320 * 1000 / 8) * info.duration)}`
        : "solo audio";
    }

    el.result.classList.remove("hidden");
    el.result.scrollIntoView({ behavior: "smooth", block: "nearest" });
"""

# ------------------------------------------------------------- index.html ----

CHIP_OLD = """            <span class="chip-sub">solo audio</span>
"""

CHIP_NEW = """            <span class="chip-sub" id="mp3-sub">solo audio</span>
"""

PATCHES = [
    (APP, "app.js · registro del chip de MP3", ELS_OLD, ELS_NEW),
    (APP, "app.js · peso en cada formato", SUB_OLD, SUB_NEW),
    (APP, "app.js · peso estimado del MP3", MP3_OLD, MP3_NEW),
    (INDEX, "index.html · id del chip de MP3", CHIP_OLD, CHIP_NEW),
]


def main() -> int:
    files = {}
    endings = {}

    for path, name, old, new in PATCHES:
        if path not in files:
            with open(path, "rb") as fh:
                raw = fh.read()
            # Cada archivo usa su propio fin de línea: se respeta el que tenga.
            endings[path] = "\r\n" if b"\r\n" in raw else "\n"
            files[path] = raw.decode("utf-8")
            print(f"fin de linea de {path.name}: {repr(endings[path])}")

        eol = endings[path]
        old_eol = old.replace("\n", eol)
        new_eol = new.replace("\n", eol)
        found = files[path].count(old_eol)

        if found == 0:
            print(f"ERROR: no se encontro el bloque {name}")
            return 1
        if found > 1:
            print(f"ERROR: el bloque {name} aparece {found} veces")
            return 1

        files[path] = files[path].replace(old_eol, new_eol)
        print(f"ok: {name}")

    for path, text in files.items():
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        print(f"escrito: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
