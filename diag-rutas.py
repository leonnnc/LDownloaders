"""Diagnostico temporal: por que no aparecen las rutas del editor."""

import inspect
import re
import sys

sys.path.insert(0, "backend")

import app.main as m  # noqa: E402

print("total de rutas en la app:", len(m.app.routes))
for i, route in enumerate(m.app.routes):
    print(" ", i, type(route).__name__, repr(getattr(route, "path", None)))

print()
print("lineas relevantes del modulo main:")
src = inspect.getsource(m).splitlines()
for n, line in enumerate(src, 1):
    if re.match(r"^\s*app\s*=", line) or "include_router" in line:
        print(" ", n, "|", line)
