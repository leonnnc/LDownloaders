#!/usr/bin/env python3
"""Blinda dos tipos de archivo que no deben acabar en el repositorio.

* `*.bak` — copias de seguridad locales. La de `backend/history.jsonl` lleva
  direcciones IP y enlaces, es decir datos personales de quien usó el
  servicio, y la regla actual solo cubre el nombre exacto, no la copia.
* `captura-*.png` — capturas de trabajo para revisar un cambio, igual que las
  `shot-*.png` que ya se quedaban fuera.
"""

from pathlib import Path

TARGET = Path(__file__).parent / ".gitignore"

BLOCK = """
# Copias de seguridad locales: pueden llevar datos de uso (la del historial
# tiene direcciones IP) y no forman parte del servicio.
*.bak

# Capturas de trabajo: sirven para revisar un cambio, no para el repositorio.
captura-*.png
"""


def main() -> int:
    with open(TARGET, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()

    if "captura-*.png" in text and "*.bak" in text:
        print("ya estaba: no se toca nada")
        return 0

    if not text.endswith("\n"):
        text += "\n"
    text += BLOCK.lstrip("\n").replace("\n", "\n") if False else BLOCK

    with open(TARGET, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)

    print("reglas anadidas:")
    print(BLOCK)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
