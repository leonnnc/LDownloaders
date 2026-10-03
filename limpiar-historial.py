"""Quita del historial las entradas que no pueden ser descargas reales.

Criterio estricto, para no borrar nada del usuario:

* host `example.com` o `ejemplo.com` — dominios reservados de prueba.
* identificador de video `000000000099` — el sondeo del informe del 28-sep.

Todo lo demas se conserva, incluidas las descargas del video del canario: un
falso positivo borraria una entrada autentica, y eso no se puede deshacer.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HISTORY = Path(__file__).resolve().parent / "backend" / "history.jsonl"
FAKE_HOSTS = {"example.com", "ejemplo.com"}
FAKE_URL_MARKERS = ("000000000099",)

if not HISTORY.is_file():
    print("no hay historial que limpiar")
    sys.exit(0)

lines = HISTORY.read_text(encoding="utf-8").splitlines()
kept: list[str] = []
removed: list[str] = []
rotas = 0

for line in lines:
    if not line.strip():
        continue
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        rotas += 1
        removed.append("(linea ilegible) " + line[:80])
        continue

    host = (entry.get("host") or "").lower()
    url = entry.get("url") or ""
    if host in FAKE_HOSTS or any(m in url for m in FAKE_URL_MARKERS):
        removed.append(f"{entry.get('job_id', '?')[:8]} | {entry.get('status')} | {url}")
    else:
        kept.append(json.dumps(entry, ensure_ascii=False))

shutil.copy2(HISTORY, HISTORY.with_suffix(".jsonl.bak"))
HISTORY.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")

print(f"entradas antes : {len(lines)}")
print(f"eliminadas     : {len(removed)}  (copia de seguridad en history.jsonl.bak)")
print(f"entradas ahora : {len(kept)}")
if rotas:
    print(f"lineas ilegibles eliminadas: {rotas}")
print()
for line in removed:
    print("  -", line)
