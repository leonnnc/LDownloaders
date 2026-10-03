#!/usr/bin/env python3
"""Cuenta secciones visibles, no trozos por dentro.

Después de cortar, la línea de tiempo son varias piezas dibujadas como una sola.
La tarjeta de exportar seguía anunciando «3 tramos» cuando en pantalla se ve uno:
ahora cuenta lo que se ve.
"""

from pathlib import Path

ROOT = Path(__file__).parent
JS = ROOT / "backend" / "static" / "editor.js"

OLD = """    const count = state.clips.length;
    el.exportNote.textContent = count
      ? `${count} tramo${count > 1 ? "s" : ""} · ${fmtTime(total())} de salida`
      : "Añade algún tramo para poder exportar.";
"""

NEW = """    const count = state.clips.length;
    // Se cuentan las secciones que se ven, no los trozos de dentro: después de cortar
    // hay varias piezas dibujadas como una sola, y anunciar «3 tramos» cuando en la
    // línea de tiempo se ve uno solo confunde.
    const sections = state.clips.filter(
      (clip, index) => index === 0 || !sameRun(state.clips[index - 1], clip)
    ).length;
    el.exportNote.textContent = count
      ? `${sections} tramo${sections > 1 ? "s" : ""} · ${fmtTime(total())} de salida`
      : "Añade algún tramo para poder exportar.";
"""


def main() -> int:
    with open(JS, "rb") as fh:
        raw = fh.read()
    eol = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8")

    old = OLD.replace("\n", eol)
    new = NEW.replace("\n", eol)
    found = text.count(old)

    if found == 0:
        print("ERROR: no se encontro el bloque del contador")
        return 1
    if found > 1:
        print(f"ERROR: el bloque aparece {found} veces")
        return 1

    with open(JS, "w", encoding="utf-8", newline="") as fh:
        fh.write(text.replace(old, new))
    print(f"escrito: {JS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
