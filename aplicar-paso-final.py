#!/usr/bin/env python3
"""Quita el paso manual de «Guardar archivo».

Elegir la calidad ya lanza la descarga y la barra de progreso acaba sola en
el archivo guardado, así que el botón grande al final no se usaba para nada.
Se cambia por un enlace discreto que queda ahí solo como red de seguridad:
los navegadores pueden bloquear una descarga automática, y sin ese enlace no
habría forma de recuperarla sin volver a analizar el enlace.

El fin de línea de cada archivo se detecta y se conserva tal cual.
"""

from pathlib import Path

ROOT = Path(__file__).parent
INDEX = ROOT / "backend" / "static" / "index.html"
STYLE = ROOT / "backend" / "static" / "style.css"

# ------------------------------------------------------------- index.html ----

BUTTON_OLD = """      <a class="btn btn-success hidden" id="download-link" download>Guardar archivo</a>
"""

BUTTON_NEW = """      <a class="dl-fallback hidden" id="download-link" download>¿No se guardó? Guárdalo aquí</a>
"""

HERO_OLD = """        Pega el enlace, elige la calidad y guarda el archivo.
"""

HERO_NEW = """        Pega el enlace, elige la calidad y el archivo se guarda solo.
"""

STEP_OLD = """          <span class="howto-text">Guarda el archivo</span>
"""

STEP_NEW = """          <span class="howto-text">Se guarda solo</span>
"""

# -------------------------------------------------------------- style.css ----

CSS_OLD = """#progress-card .btn { margin-top: 16px; width: 100%; }
"""

CSS_NEW = """#progress-card .btn { margin-top: 16px; width: 100%; }

/* El enlace de respaldo no es un botón ni un paso: el archivo ya se guarda
   solo al elegir la calidad. Está por si el navegador bloquea la descarga
   automática, que es lo único que dejaría al usuario sin el archivo. */
.dl-fallback {
  display: inline-block;
  margin-top: 16px;
  font-size: 12.5px;
  color: var(--text-3);
}
.dl-fallback:hover { color: var(--accent); text-decoration: underline; }
"""

PATCHES = [
    (INDEX, "index.html · botón final a enlace discreto", BUTTON_OLD, BUTTON_NEW),
    (INDEX, "index.html · texto de presentación", HERO_OLD, HERO_NEW),
    (INDEX, "index.html · tercer paso", STEP_OLD, STEP_NEW),
    (STYLE, "style.css · estilo del enlace de respaldo", CSS_OLD, CSS_NEW),
]


def main() -> int:
    files = {}
    endings = {}

    for path, name, old, new in PATCHES:
        if path not in files:
            with open(path, "rb") as fh:
                raw = fh.read()
            endings[path] = "\r\n" if b"\r\n" in raw else "\n"
            files[path] = raw.decode("utf-8")

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
