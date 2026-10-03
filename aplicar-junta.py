#!/usr/bin/env python3
"""Los tiradores de recorte no van en una junta interior.

Después de cortar, el trozo que sigue queda seleccionado, así que sus tiradores se
ven. El del inicio cae justo en la junta: empezar ahí un arrastre para elegir una
parte nueva lo capturaba el tirador y recortaba el tramo en vez de elegir, que es
justo lo que uno va a hacer después de cortar.

Un tirador solo tiene sentido en un borde de la sección: en una junta interior no es
un extremo, así que se deja sin tirador. El resto de tiradores (los de los bordes de
verdad) siguen igual.
"""

from pathlib import Path

ROOT = Path(__file__).parent
JS = ROOT / "backend" / "static" / "editor.js"

OLD = """      // Tiradores: el tramo se recorta arrastrando sus bordes, aquí mismo.
      for (const edge of ["start", "end"]) {
        const handle = document.createElement("span");
"""

NEW = """      // Tiradores: el tramo se recorta arrastrando sus bordes, aquí mismo. Van solo en
      // los bordes de la sección: en una junta interior —el trozo que continúa a otro
      // del mismo audio— no, porque ahí no hay un extremo que recortar, y además el
      // tirador se comía el arrastre con el que se iba a elegir la parte siguiente
      // justo en el punto donde se acaba de cortar.
      for (const edge of ["start", "end"]) {
        if (edge === "start" && following) continue;
        if (edge === "end" && followed) continue;
        const handle = document.createElement("span");
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
        print("ERROR: no se encontro el bloque de los tiradores")
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
