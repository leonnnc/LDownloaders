#!/usr/bin/env python3
"""Deja fuera del repositorio las capturas de trabajo sueltas en la raíz.

Las capturas que se hacen para revisar un cambio (peso del archivo, corte,
etc.) viven en la raíz del proyecto y no son parte del servicio. Las de
`android-widget/capturas/` sí se quedan, que documentan la app.
"""

from pathlib import Path

TARGET = Path(__file__).parent / ".gitignore"

OLD = """# Capturas de trabajo: sirven para revisar un cambio, no para el repositorio.
captura-*.png
"""

NEW = """# Capturas de trabajo en la raíz del proyecto: sirven para revisar un cambio, no
# para el repositorio. Las de android-widget/capturas/ sí se quedan, que
# documentan la app.
/*.png
"""


def main() -> int:
    with open(TARGET, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()

    eol = "\r\n" if "\r\n" in text else "\n"
    old = OLD.replace("\n", eol)
    new = NEW.replace("\n", eol)

    found = text.count(old)
    if found == 0:
        if "/*.png" in text:
            print("ya estaba: no se toca nada")
            return 0
        print("ERROR: no se encontro el bloque de capturas")
        return 1
    if found > 1:
        print(f"ERROR: el bloque aparece {found} veces")
        return 1

    text = text.replace(old, new)
    with open(TARGET, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)

    print("reglas de capturas actualizadas:")
    print(NEW)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
