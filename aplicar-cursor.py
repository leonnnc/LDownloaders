#!/usr/bin/env python3
"""Cortar deja de plantar el cursor en la junta.

La línea roja es el cursor de reproducción (la guía con la que se afina el corte). Al
cortar, el código lo movía al punto donde empezaba la parte quitada, así que quedaba
plantado justo en la junta recién hecha y parecía una marca del corte. Ahora el cursor
se queda donde el usuario lo tenía: cortar no lo mueve.

Se sigue pasando por `seek` con su propia posición para que, si el montaje se quedó más
corto que el cursor, se recoja en vez de quedarse fuera de la línea.
"""

from pathlib import Path

ROOT = Path(__file__).parent
JS = ROOT / "backend" / "static" / "editor.js"

OLD_A = """    // El cursor se queda donde empezaba el corte: es el punto que se está mirando.
    const at = Math.max(0, Math.min(range.start, total()));

    // La selección se suelta antes de repintar: eso ya no está en el montaje, y
"""

NEW_A = """    // La selección se suelta antes de repintar: eso ya no está en el montaje, y
"""

OLD_B = """    keepSelectionValid();
    seek(at);
  }
"""

NEW_B = """    keepSelectionValid();
    // El cursor se queda donde estaba: cortar no tiene por qué plantar la guía roja en
    // la junta recién hecha, que parece una marca del corte y no lo es. Se llama a seek
    // con su propia posición para que, si el montaje se quedó más corto, el cursor se
    // recoja en vez de quedarse fuera de la línea.
    seek(state.cursor);
  }
"""


def main() -> int:
    with open(JS, "rb") as fh:
        raw = fh.read()
    eol = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8")

    for name, old, new in (("bloque del cursor", OLD_A, NEW_A), ("final del corte", OLD_B, NEW_B)):
        old_eol = old.replace("\n", eol)
        new_eol = new.replace("\n", eol)
        found = text.count(old_eol)
        if found == 0:
            print(f"ERROR: no se encontro {name}")
            return 1
        if found > 1:
            print(f"ERROR: {name} aparece {found} veces")
            return 1
        text = text.replace(old_eol, new_eol)
        print(f"ok: {name}")

    with open(JS, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    print(f"escrito: {JS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
