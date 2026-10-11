#!/usr/bin/env bash
# ============================================================================
# preparar-servidor.sh - deja listo un servidor Linux para el descargador.
#
# Pensado para Ubuntu 24.04 (aarch64) de Oracle Cloud Always Free, pero sirve
# para cualquier VPS con Ubuntu: no hace nada especifico de Oracle salvo
# recordarte abrir los puertos tambien en la consola (eso no se puede hacer
# desde aqui).
#
# Uso:
#   ./preparar-servidor.sh                                  # prueba rapida por IP (puerto 8000)
#   ./preparar-servidor.sh -d descargador.tudominio.com     # produccion con HTTPS automatico
#   ./preparar-servidor.sh -d tu.dominio.com -w https://hooks.slack.com/services/XXX/YYY/ZZZ
#
# Es idempotente: si lo vuelves a lanzar, no repite lo que ya esta hecho y NO
# regenera el token de administracion.
# ============================================================================

set -euo pipefail

REPO="https://github.com/leonnnc/LDownloaders.git"
DEST="$HOME/LDownloaders"
DOMINIO=""
WEBHOOK=""

info()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m    ok  %s\033[0m\n' "$*"; }
aviso() { printf '\033[1;33m    !   %s\033[0m\n' "$*"; }
die()   { printf '\n\033[1;31m  ERROR: %s\033[0m\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    -d|--dominio) DOMINIO="${2:-}"; shift 2 ;;
    -w|--webhook) WEBHOOK="${2:-}"; shift 2 ;;
    -h|--help)    sed -n '2,18p' "$0"; exit 0 ;;
    *) die "Opcion no reconocida: $1 (usa -h para la ayuda)" ;;
  esac
done

# --- 0. Comprobaciones previas ---------------------------------------------
info "Comprobando el entorno"
[ "$(id -u)" -ne 0 ] || die "No lo ejecutes como root: usa tu usuario normal (el que tiene sudo)."
command -v sudo >/dev/null || die "Falta sudo. Esta imagen no parece Ubuntu."

ARQ="$(uname -m)"
case "$ARQ" in
  aarch64|arm64) ok "Arquitectura $ARQ (Ampere ARM): todas las dependencias tienen rueda precompilada" ;;
  x86_64)        ok "Arquitectura $ARQ" ;;
  *)             aviso "Arquitectura $ARQ poco habitual; sigue, pero puede requerir compilar algo" ;;
esac

if [ -n "$DOMINIO" ]; then
  MODO="produccion"
  ok "Modo produccion: https://$DOMINIO (Caddy pedira el certificado)"
  aviso "El registro DNS A de $DOMINIO tiene que apuntar YA a la IP de este servidor"
else
  MODO="prueba"
  ok "Modo prueba: se publica el puerto 8000 (sin HTTPS)"
  aviso "En la consola de OCI hay que abrir tambien el 8000/tcp para entrar desde fuera"
fi

# --- 1. Paquetes base -------------------------------------------------------
info "Instalando paquetes base"
sudo apt-get update -qq
sudo apt-get install -y -qq ca-certificates curl git openssl python3 >/dev/null
ok "ca-certificates, curl, git, openssl, python3"

# --- 2. Docker --------------------------------------------------------------
info "Comprobando Docker"
if command -v docker >/dev/null 2>&1; then
  ok "Docker ya instalado: $(docker --version)"
else
  curl -fsSL https://get.docker.com | sudo sh
  ok "Docker instalado"
fi
if ! sudo docker compose version >/dev/null 2>&1; then
  sudo apt-get install -y -qq docker-compose-plugin >/dev/null
fi
sudo docker compose version >/dev/null 2>&1 || die "No se pudo dejar listo 'docker compose'"
ok "$(sudo docker compose version | head -1)"

# --- 3. Cortafuegos del sistema operativo ----------------------------------
# En OCI hay DOS cortafuegos: la Security List de la consola y el del propio
# sistema. Aqui se abre el del sistema; el de la consola es cosa tuya.
info "Cortafuegos del sistema operativo"
BLOQUEA=0
if sudo iptables -S INPUT 2>/dev/null | grep -qE '^-P INPUT (DROP|REJECT)'; then
  BLOQUEA=1
elif sudo iptables -S INPUT 2>/dev/null | grep -qE '(-j DROP|-j REJECT)'; then
  BLOQUEA=1
fi

if [ "$BLOQUEA" = "0" ]; then
  ok "La cadena INPUT no descarta por defecto: no hay nada que abrir aqui"
else
  PUERTOS="22 80 443"
  if [ "$MODO" = "prueba" ]; then PUERTOS="$PUERTOS 8000"; fi
  for PUERTO in $PUERTOS; do
    if sudo iptables -S INPUT 2>/dev/null | grep -q -- "--dport $PUERTO "; then
      ok "El puerto $PUERTO ya estaba abierto"
    else
      # Posicion 6: por delante de las reglas de descarte que traen las imagenes
      # de OCI, pero detras de las basicas (lo, established, icmp).
      sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport "$PUERTO" -j ACCEPT
      ok "Puerto $PUERTO abierto"
    fi
  done
  if command -v netfilter-persistent >/dev/null 2>&1; then
    sudo netfilter-persistent save >/dev/null
    ok "Reglas guardadas para que sobrevivan a un reinicio"
  else
    aviso "Falta netfilter-persistent: las reglas se perderan al reiniciar"
    aviso "  sudo apt install -y iptables-persistent   y vuelve a lanzar este script"
  fi
fi

# --- 4. Traer el proyecto ---------------------------------------------------
info "Trayendo el proyecto"
if [ -d "$DEST/.git" ]; then
  git -C "$DEST" pull --ff-only
  ok "Repositorio actualizado en $DEST"
else
  git clone "$REPO" "$DEST"
  ok "Clonado en $DEST"
fi
cd "$DEST"

# --- 5. Configuracion -------------------------------------------------------
info "Preparando deploy/produccion.env"
if [ -f deploy/produccion.env ]; then
  ok "Ya existia: no se toca (tu token sigue siendo el mismo)"
else
  cp deploy/produccion.env.example deploy/produccion.env
  chmod 600 deploy/produccion.env
  ok "Creado a partir del ejemplo"
fi

WEBHOOK="$WEBHOOK" python3 - <<'PY'
import os, secrets, pathlib
ruta = pathlib.Path("deploy/produccion.env")
lineas = ruta.read_text(encoding="utf-8").splitlines()

def fijar(clave, valor, solo_si_vacio=True):
    for i, l in enumerate(lineas):
        if l.startswith(clave + "="):
            actual = l.split("=", 1)[1].strip()
            if actual and solo_si_vacio:
                return
            lineas[i] = clave + "=" + valor
            return
    lineas.append(clave + "=" + valor)

fijar("VDL_ADMIN_TOKEN", secrets.token_hex(24))
webhook = os.environ.get("WEBHOOK", "").strip()
if webhook:
    fijar("VDL_ALERT_WEBHOOK", webhook, solo_si_vacio=False)
ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
PY
ok "Token y webhook aplicados"

TOKEN="$(grep -m1 '^VDL_ADMIN_TOKEN=' deploy/produccion.env | cut -d= -f2-)"
[ -n "$TOKEN" ] || die "El archivo deploy/produccion.env quedo sin VDL_ADMIN_TOKEN"
ok "Token de administracion listo"

if [ -z "$(grep -m1 '^VDL_ALERT_WEBHOOK=' deploy/produccion.env | cut -d= -f2-)" ]; then
  aviso "VDL_ALERT_WEBHOOK esta vacio: si algo se rompe, no te vas a enterar"
  aviso "  Editalo con:  nano $DEST/deploy/produccion.env"
fi

# --- 6. Levantar ------------------------------------------------------------
NOTA_PUERTOS=""
PASO_2="Prueba una descarga real con un enlace tuyo"
if [ "$MODO" = "produccion" ]; then
  info "Levantando con HTTPS para $DOMINIO"
  sudo -E DOMAIN="$DOMINIO" docker compose -f deploy/docker-compose.prod.yml up -d --build
  URL="https://$DOMINIO"
  SONDA="https://$DOMINIO/api/health"
  LOGS="sudo docker compose -f $DEST/deploy/docker-compose.prod.yml logs --tail=50"
else
  info "Levantando en modo prueba (puerto 8000)"
  sudo docker compose up -d --build
  IP_PUBLICA="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo 'TU_IP')"
  URL="http://$IP_PUBLICA:8000"
  SONDA="http://127.0.0.1:8000/api/health"
  LOGS="sudo docker compose logs --tail=50"
  NOTA_PUERTOS=" (y 8000 para esta prueba)"
  PASO_2="Cuando termines de probar: cierra el 8000 y pasa al modo dominio con -d tu.dominio.com"
fi

# --- 7. Comprobar que responde ---------------------------------------------
info "Esperando a que el servicio responda"
SALUD=""
for _ in $(seq 1 30); do
  SALUD="$(curl -fsS --max-time 5 "$SONDA" 2>/dev/null || true)"
  if [ -n "$SALUD" ]; then break; fi
  sleep 2
done

if [ -n "$SALUD" ]; then
  ok "El servicio responde en $SONDA"
  printf '%s' "$SALUD" | python3 -m json.tool 2>/dev/null | head -20 || printf '%s\n' "$SALUD"
else
  aviso "Todavia no responde. Mira los registros:"
  echo "    $LOGS"
fi

# --- 8. Resumen -------------------------------------------------------------
cat <<FIN

----------------------------------------------------------------------
 Listo.
   Web        : $URL
   Panel      : $URL/monitor
   Token admin: $TOKEN
   Proyecto   : $DEST
   Config     : $DEST/deploy/produccion.env

 Falta (no se puede hacer desde aqui):
   1. Consola de OCI, Security List de la VCN: abrir desde 0.0.0.0/0
      los puertos 80 y 443$NOTA_PUERTOS
   2. $PASO_2
   3. $URL/api/health debe decir "status": "ok"

 Guarda el token en tu gestor de contrasenas antes de cerrar esta sesion.
----------------------------------------------------------------------
FIN
