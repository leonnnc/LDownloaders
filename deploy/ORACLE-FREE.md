# Despliegue en Oracle Cloud Always Free

Guía específica para levantar el descargador en la capa gratuita de Oracle.
Complementa a [`../DESPLIEGUE.md`](../DESPLIEGUE.md), que es la guía general: aquí solo está lo
que cambia por ser Oracle y por ser ARM.

**Resumen en una línea:** máquina ARM (Ampere A1) con Ubuntu 24.04, dos cortafuegos que hay que
abrir, `docker compose -f deploy/docker-compose.prod.yml up -d --build` y listo.

---

## 0. Antes de empezar: la decisión de fondo

Un VPS de Oracle tiene **IP de datacenter**. YouTube, Facebook, Instagram y TikTok bloquean esas
IP en cuestión de días: verás «login required», «sign in to confirm you're not a bot» o respuestas
vacías que **no son un fallo de tu código**. En el README y en `DESPLIEGUE.md` §5 está el detalle;
aquí lo repito porque es lo que decide si el despliegue sirve o no:

- Para **probarlo tú** (que la web responde, el editor funciona, el panel dice la verdad): sirve perfecto.
- Para **abrirlo al público**: necesitarás `VDL_PROXY` (proxies residenciales, se cobran por GB) o
  los sitios grandes caerán uno detrás de otro.

Despliega primero **sin** proxies, mide los GB que se descargan al día y decide después.

---

## 1. Crear la instancia en la consola de OCI

| Campo | Valor | Por qué |
|---|---|---|
| Imagen | **Ubuntu 24.04 (aarch64)** | Es donde mejor funciona Docker y lo que espera `DESPLIEGUE.md` |
| Forma (shape) | **`VM.Standard.A1.Flex`** — Ampere ARM | Es la que entra en Always Free. Pide 2 OCPU / 12 GB si te deja; 1 OCPU / 6 GB también sobra |
| Red | Con **IP pública** | Sin ella no hay forma de llegar por HTTP |
| Clave SSH | Sube tu clave pública | Es la única entrada; no hay contraseña |
| Almacenamiento de arranque | 50-100 GB | Los archivos temporales y las imágenes Docker pesan |

> **Cuidado con la forma «micro AMD» (VM.Standard.E2.1.Micro).** También entra en Always Free, pero
> tiene **1 GB de RAM y 1/8 de OCPU**: no aguanta el `docker build` ni una conversión con FFmpeg.
> No es una opción para este servicio.

> **Sobre la cuota Always Free de Ampere A1:** según los reportes de mediados de 2026 la asignación
> gratuita se redujo respecto a los 4 OCPU / 24 GB que se citaban antes. Confirma en la consola qué
> te ofrece hoy tu cuenta antes de dimensionar.

> **«Out of host capacity»** es un error habitual al crear instancias A1 en algunas regiones: no es
> tu cuenta, es que no hay hueco. Prueba otro *availability domain*, otra región, o reintenta más tarde.

---

## 2. Los dos cortafuegos (el paso que más tiempo se pierde)

En Oracle hay **dos** filtros independientes y hay que abrir el puerto en los dos. Si solo abres uno,
la web no responde y parece que el servicio está roto.

### 2.1 En la consola de OCI — Security List / NSG

En **Networking → Virtual Cloud Networks → tu VCN → Security Lists** (o en el NSG de la instancia),
añade reglas **Ingress** desde `0.0.0.0/0`:

| Puerto | Para qué |
|---|---|
| 22/tcp | SSH (normalmente ya está) |
| 80/tcp | HTTP: lo necesita Caddy para el reto de Let's Encrypt |
| 443/tcp | HTTPS |
| 8000/tcp | **Solo si vas a probar por IP**, antes de tener dominio. Cierra esta regla después |

### 2.2 En la máquina — el cortafuegos del sistema operativo

Las imágenes de OCI suelen traer reglas propias que descartan todo menos SSH. Compruébalo primero:

```bash
sudo iptables -L INPUT -n --line-numbers
```

Si ves una política `DROP` sin reglas para 80/443 (y 8000), ábrelas:

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8000 -j ACCEPT   # solo para pruebas por IP
sudo netfilter-persistent save
```

(`netfilter-persistent save` deja las reglas puestas tras un reinicio. Si el paquete no está:
`sudo apt install -y iptables-persistent`.)

Si prefieres `ufw`, ábrelo igual y no mezcles los dos:

```bash
sudo ufw allow 22,80,443/tcp && sudo ufw allow 8000/tcp && sudo ufw enable
```

---

## 3. Preparar el servidor

Todo esto con la sesión SSH abierta (el usuario por defecto en Ubuntu de OCI es `ubuntu`):

```bash
sudo apt update && sudo apt upgrade -y
curl -fsSL https://get.docker.com | sudo sh
sudo apt install -y docker-compose-plugin git
sudo usermod -aG docker $USER      # y vuelve a entrar por SSH para que surta efecto

# Comprobación previa del entorno
ffmpeg -version 2>/dev/null | head -1 || echo "FFmpeg no está en el sistema (el contenedor lo trae igual)"
docker --version && docker compose version && uname -m
```

`uname -m` debe decir `aarch64`. **Comprobado:** todas las dependencias del proyecto tienen rueda
precompilada para `manylinux2014_aarch64` (`yt-dlp`, `fastapi`, `pydantic-core`, `uvicorn`,
`httptools`, `pyyaml`, `watchfiles`, `websockets` e `imageio-ffmpeg`), así que en la máquina ARM
**no se compila nada**: la instalación y el `docker build` son rápidos.

---

## 4. Traer el proyecto

```bash
git clone https://github.com/leonnnc/LDownloaders.git
cd LDownloaders
```

> Ojo con el nombre: es **`LDownloaders`**, con «s» al final. En `DESPLIEGUE.md` §3.2 y en el mensaje
> de `/app.apk` figura una URL con el nombre viejo (`Ldownloader`), que hoy redirige a otro
> repositorio desactualizado.

---

## 5. Configurar

```bash
cp deploy/produccion.env.example deploy/produccion.env
nano deploy/produccion.env
```

Lo mínimo que hay que rellenar (el archivo ya trae el resto razonado):

```bash
VDL_ADMIN_TOKEN=$(openssl rand -hex 24)      # genera uno NUEVO, no reutilices el de tu PC
VDL_ALERT_WEBHOOK=https://hooks.slack.com/services/TU/WEBHOOK     # imprescindible
VDL_ALLOWED_DOMAINS=facebook.com,fb.watch,instagram.com,tiktok.com  # si no, eres un proxy abierto
VDL_MAX_FILESIZE_MB=500                     # ya viene: un 4K largo llena el disco
VDL_RESTART_AFTER_UPDATE=true               # ya lo pone el compose: sin esto la actualización no se aplica
```

Guarda el token: lo necesitarás en `/monitor` para administrar desde el móvil.

---

## 6. Levantar

### 6.1 Primero, prueba rápida por IP (sin dominio)

Para confirmar que la máquina y los puertos están bien, antes de tocar DNS:

```bash
sudo docker compose up -d --build          # compose de desarrollo: publica el 8000 directamente
curl -s localhost:8000/api/health | python3 -m json.tool
```

Si responde, abre `http://TU_IP_PUBLICA:8000` desde tu PC. Cuando termines, bájalo con
`sudo docker compose down` y **cierra el puerto 8000** en la Security List.

### 6.2 Con dominio y HTTPS automático

1. En tu DNS, un registro **A**: `descargador.tudominio.com → TU_IP_PUBLICA`.
2. Espera a que propague y levanta el stack de producción:

```bash
DOMAIN=descargador.tudominio.com sudo -E docker compose -f deploy/docker-compose.prod.yml up -d --build
```

Caddy pide el certificado solo. El compose ya trae lo que hace falta para que esto funcione bien en
un servidor: `restart: unless-stopped`, el volumen `downloads` para `/data`, el tope de 2 GB de RAM
para la API y —importante— `VDL_TRUSTED_PROXIES: 172.28.0.0/16`, que coincide con la subred fija de
la red `web` donde vive Caddy, así que el límite por IP sigue funcionando con el proxy delante.

```bash
curl -s https://descargador.tudominio.com/api/health | python3 -m json.tool
sudo docker compose -f deploy/docker-compose.prod.yml logs -f api
```

---

## 7. Cerrar las puertas traseras antes de compartir la URL

Esto es específico de tu proyecto y de lo que salió en la revisión del 11-oct: con el servicio
expuesto y **sin** tocar código, tres cosas quedan a la vista de cualquiera que aterrice:

| Ruta | Qué enseña hoy | Cómo cerrarla sin tocar código |
|---|---|---|
| `/api/history` | IP y enlace completo de todas las descargas | `VDL_ADMIN_TOKEN` puesto (ya lo pide) **y** basic auth en Caddy |
| `/api/monitor` | Circuitos, alertas, errores crudos y configuración | Basic auth en Caddy |
| `/api/audio/*` | El editor: exportar gasta tu CPU | Basic auth en Caddy, o `VDL_EDITOR_ENABLED=false` |

Como Caddy ya está delante, lo más rápido es proteger esas rutas en el borde. Genera la clave
hasheada:

```bash
sudo docker run --rm caddy:2-alpine caddy hash-password --plaintext 'TU_CLAVE_LARGA'
```

Y añade esto al bloque `{$DOMAIN}` de `deploy/Caddyfile`, **antes** del `reverse_proxy`:

```caddyfile
	@privado path /monitor /api/monitor /api/history /api/widget /api/audio/*
	basic_auth @privado {
		admin TU_HASH_GENERADO_ARRIBA
	}
```

(En versiones antiguas de Caddy la directiva se llama `basicauth`; en `caddy:2-alpine` actual,
`basic_auth`.) Recarga con `sudo docker compose -f deploy/docker-compose.prod.yml restart caddy`.

Ten en cuenta que el widget de Android pega contra `/api/widget`: si lo proteges, tendrás que
configurarlo con esas credenciales o dejar esa ruta fuera de la lista.

---

## 8. Comprobarlo de verdad

```bash
# Estado general
curl -s https://descargador.tudominio.com/api/health | python3 -m json.tool

# ¿Y el motor? Un análisis real (esto es lo que dirá si Oracle sirve para esto)
curl -s -X POST https://descargador.tudominio.com/api/parse \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=aqz-KE-bpKQ"}' | head -c 400

# Recursos y disco
sudo docker stats --no-stream
df -h /var/lib/docker
```

Las pruebas de humo del repositorio están fijadas a `http://127.0.0.1:8000` en cuatro de los cinco
archivos, así que en el servidor hay que correrlas **dentro** de la máquina y contra el contenedor
publicado, no contra el dominio.

---

## 9. Mantenimiento

| Cuándo | Qué |
|---|---|
| Nada | **`yt-dlp` se actualiza solo** cada 12 h, se valida con canarios y revierte si empeora. `VDL_RESTART_AFTER_UPDATE=true` hace que Docker lo levante con la versión nueva |
| Al cambiar el código | `git pull && DOMAIN=tu.dominio.com sudo -E docker compose -f deploy/docker-compose.prod.yml up -d --build` |
| Semanal | `/api/metrics` — tasa de éxito por plataforma |
| Mensual | `sudo docker system prune -a` (libera imágenes viejas; en 50 GB de disco importa) |
| Si llega una alerta | `/monitor` → qué plataforma cayó y qué circuito está abierto |
| Copia de seguridad | Solo dos archivos: `deploy/produccion.env` y `backend/canaries.json`. Y en tu PC, `monitor-release.jks` |

---

## 10. Cuotas y coste

- La instancia A1 dentro de la asignación **Always Free** no se cobra. Todo lo que salga de esa
  asignación (más OCPU, más disco, IP reservadas de pago) sí.
- El **tráfico saliente** es la partida que crece en un descargador: un 4K son cientos de MB por
  archivo. Confirma en la consola la franja de egreso incluida en tu cuenta antes de abrir el
  servicio al público, y ponle un ojo la primera semana.
- Un `delete` accidental de la instancia no se recupera; el proyecto no guarda nada que merezca
  copia salvo la configuración, pero apunta el `produccion.env` fuera del servidor.

---

## 11. Antes de dar por bueno el despliegue

- [ ] `https://tu-dominio/api/health` responde `status: ok`
- [ ] El candado de HTTPS es válido
- [ ] `/api/parse` devuelve formatos con un enlace real (**si aquí falla por «bot», es la IP de Oracle, no el código**)
- [ ] El webhook de alertas ha recibido un aviso de prueba
- [ ] `VDL_ADMIN_TOKEN` es distinto del que usas en local
- [ ] `/api/history` y `/api/monitor` no se abren sin credenciales
- [ ] El botón de reiniciar del panel funciona **y el servicio vuelve solo**
- [ ] Una descarga completa desde el móvil, fuera de tu red
- [ ] El puerto 8000 (si lo abriste para probar) está cerrado otra vez
