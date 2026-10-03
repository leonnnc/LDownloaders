#!/usr/bin/env python3
"""El Codespace arranca el servidor solo, y sin instalar las dependencias dos veces.

Dos cosas que hacían que la URL no abriera:

  1. El servidor solo se lanzaba desde `postAttachCommand`, que corre cuando el cliente
     de VS Code se conecta. Si el Codespace se crea y no llegas a conectar (o el cliente
     se queda colgado), el puerto 8000 aparece reenviado igualmente —lo declara
     `forwardPorts`, aunque no haya nada escuchando—, así que la URL existe pero da
     error. Ahora también se lanza desde `postStartCommand`, que corre en cada arranque
     de la máquina, sin depender del cliente.

  2. Las dependencias se instalaban dos veces: `postCreateCommand` las ponía en el Python
     del sistema y `.devcontainer/start.sh` creaba después un `.venv` y las volvía a
     instalar todas antes de arrancar. Ahora el `.venv` se crea ya en `postCreateCommand`,
     así que `start.sh` lo encuentra hecho y arranca directo.

La guía gana un paso para cuando aún así no responda: comprobar el servidor y lanzarlo a
mano, con el registro en `servidor.log`.
"""

from pathlib import Path

ROOT = Path(__file__).parent
JSON = ROOT / ".devcontainer" / "devcontainer.json"
GUIDE = ROOT / "DESPLIEGUE.md"

JSON_OLD = """  "postCreateCommand": "sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg || true; python -m pip install --quiet --upgrade pip; python -m pip install --quiet -r backend/requirements.txt",
  "postAttachCommand": "bash .devcontainer/start.sh",
"""

JSON_NEW = """  "postCreateCommand": "sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg || true; python3 -m venv .venv; .venv/bin/python -m pip install --quiet --upgrade pip; .venv/bin/python -m pip install --quiet -r backend/requirements.txt",
  "postAttachCommand": "bash .devcontainer/start.sh",
  "postStartCommand": "nohup bash .devcontainer/start.sh > servidor.log 2>&1 &",
"""

GUIDE_OLD = """5. Se para solo tras un rato inactivo. **Se reinicia desde la pestaña Codespaces**, no
   desde la URL: la dirección cambia cada vez que creas un Codespace nuevo.
"""

GUIDE_NEW = """5. Se para solo tras un rato inactivo. **Se reinicia desde la pestaña Codespaces**, no
   desde la URL: la dirección cambia cada vez que creas un Codespace nuevo.
6. Si la URL no abre, mira primero si el servidor está en marcha. En la terminal del
   Codespace:

   ```bash
   curl -s -o /dev/null -w "%{http_code}\\n" http://127.0.0.1:8000/api/health
   ```

   Un `200` significa que el servicio está bien y el problema es el reenvío del puerto
   (pestaña **PORTS**, que el 8000 esté y con la visibilidad que quieras). Si no
   contesta, lánzalo a mano con `bash .devcontainer/start.sh` y mira lo que va saliendo:
   el registro se guarda además en `servidor.log`. Lo que más tarda es la primera
   instalación de dependencias.
"""

REPLACEMENTS = [
    (JSON, "devcontainer.json · arranque y venv", JSON_OLD, JSON_NEW),
    (GUIDE, "DESPLIEGUE.md · paso 6", GUIDE_OLD, GUIDE_NEW),
]


def read(path: Path):
    eol = "\r\n" if b"\r\n" in path.read_bytes() else "\n"
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read(), eol


def main() -> int:
    pending = []
    for path, name, old, new in REPLACEMENTS:
        text, eol = read(path)
        old_n = old.replace("\n", eol)
        new_n = new.replace("\n", eol)
        count = text.count(old_n)
        if count != 1:
            print(f"ERROR: {name}: el bloque aparece {count} veces (se esperaba 1)")
            return 1
        if new_n in text:
            print(f"ya estaba: {name}")
            continue
        pending.append((path, name, text.replace(old_n, new_n), eol))

    for path, name, text, eol in pending:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        print("escrito:", name)
    print(f"cambios aplicados: {len(pending)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
