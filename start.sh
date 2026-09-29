#!/usr/bin/env bash
# Arranca el servidor de desarrollo en macOS / Linux.
# Uso:  ./start.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"

if [ ! -x "$VENV/bin/python" ]; then
  echo "Creando entorno virtual..."
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install --upgrade pip --quiet
  "$VENV/bin/python" -m pip install -r "$ROOT/backend/requirements.txt"
fi

echo "Servidor en http://127.0.0.1:8000"
cd "$ROOT/backend"
# --no-proxy-headers: uvicorn lo trae activado por defecto y reescribe la IP del
# cliente con X-Forwarded-For, que el cliente puede inventarse. Con esto la IP
# es la del par real y el límite de peticiones no se puede saltar.
exec "$VENV/bin/python" -m uvicorn app.main:app --reload \
  --host 127.0.0.1 --port 8000 --no-proxy-headers
