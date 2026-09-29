# Antes de subir a producción

Revisión del estado del proyecto y lista de lo que falta. Está ordenada por lo
que puede hacerte daño de verdad, no por dificultad.

Fecha de la revisión: 22 de septiembre de 2026.

---

## 1. Lo que se corrigió en esta pasada

Cinco fallos que estaban en el código y ya no están. Todos con prueba que los
cubre, para que no vuelvan sin que nadie se entere: `python smoke_seguridad.py`.

### 1.1 El recolector de basura borraba descargas en curso

`purge_expired` medía el tiempo desde la creación del trabajo y no miraba el
estado. Una descarga de más de 15 minutos (el TTL por defecto) veía su directorio
borrado a media escritura y el trabajo quedaba marcado como «vencido» aunque
siguiera bajando. En Linux el subproceso continúa escribiendo en un archivo ya
desvinculado: ese espacio deja de poder recuperarse hasta que termina.

Ahora un trabajo en curso nunca se toca antes de `VDL_ACTIVE_JOB_MAX_MINUTES`
(120 por defecto), que es el plazo que se le da a una descarga larga y legítima.
Un trabajo activo que pase de ese plazo sí se limpia: si lleva dos horas
«procesando», está colgado.

### 1.2 Cualquiera podía tumbar una plataforma entera

El circuito se abría con cualquier error que no estuviera en la lista de
«permanentes conocidos». Cinco enlaces de YouTube inexistentes bastaban para
dejar YouTube bloqueado tres minutos **para todos los usuarios**, y encima
fallan en menos de un segundo, así que no costaba nada hacerlo.

Ahora el circuito solo se abre con señales de que el sitio o la red fallan
(timeouts, conexión rechazada, errores 5xx, 429). Un error del enlace que trajo
el usuario no cuenta. De detectar que un sitio cambió de formato se encargan los
canarios, que es justo lo que hacen bien.

### 1.3 SSRF: los enlaces internos pasaban el filtro

`validate_url` solo comparaba el texto del dominio contra una lista corta. No
resolvía DNS ni miraba rangos de IP, así que pasaban `192.168.1.1`,
`169.254.169.254` (los metadatos de AWS/GCP/Azure, que pueden entregar
credenciales de la instancia) y `localhost.localdomain`.

Ahora se resuelve el dominio y se comprueban las direcciones resultantes,
incluido el espacio CGNAT (`100.64.0.0/10`) que la librería estándar de Python
**no** marca como privado. Queda un resquicio teórico de DNS rebinding
(documentado en el código).

### 1.4 El límite de peticiones se saltaba con una cabecera

Se creía `X-Forwarded-For` sin comprobar quién llamaba, así que bastaba enviar
una IP inventada distinta en cada petición para no llegar nunca al 429. Y no era
solo el código: **`--forwarded-allow-ips='*'` en el servicio systemd** decía
«me creo esa cabecera venga de quien venga».

Hay una trampa añadida que conviene conocer: **uvicorn trae el manejo de
cabeceras de proxy activado por defecto**, así que reescribe la IP del cliente
antes de que la vea la aplicación. Por eso el arreglo no bastaba con tocar
Python. Ahora todos los arranques usan `--no-proxy-headers` y la confianza se
declara en un solo sitio, `VDL_TRUSTED_PROXIES`, con la IP del par como valor por
defecto.

### 1.5 Otros cuatro, menores pero reales

| Qué | Dónde | Arreglo |
|---|---|---|
| Error 500 con los sitios que no informan miniaturas | `downloader.parse` | `_last_thumbnail` tolera la lista vacía |
| `format_id` sin validar, acababa dentro del selector de yt-dlp | `DownloadRequest` | Patrón y longitud máxima |
| El token se comparaba con `!=` (filtra por tiempo) | `require_admin` | `hmac.compare_digest` |
| El diccionario del límite de peticiones crecía sin fin | `rate_limit` | Barrido de entradas caducadas |

Además: un fallo inesperado ahora devuelve JSON en vez de un «Internal Server
Error» sin cuerpo, y los identificadores de formato con un error permanente
(«Incomplete YouTube ID») ya no se reintentan tres veces, así que un enlace
inválido responde en décimas de segundo en vez de en siete segundos.

---

## 2. Imprescindible antes de publicar

### 2.1 Define `VDL_ADMIN_TOKEN`, o no hay panel

Sin el token, `/api/admin/*` devuelve 404 (a propósito) **y `/api/history` queda
abierto sin protección**, con direcciones IP y enlaces de todo el mundo. El
propio ejemplo de producción lo trae vacío: es el error más fácil de cometer.

```bash
openssl rand -hex 24   # y a deploy/produccion.env
```

Comprueba después que `/api/history` responde 401 sin cabecera.

### 2.2 Acota `VDL_ALLOWED_DOMAINS`

Vacío significa «acepto cualquier sitio que yt-dlp soporte», es decir, un proxy
abierto. Eso te trae dos problemas: se usa tu servidor y tu ancho de banda para
lo que a ti no te interesa, y la responsabilidad por lo que descarguen otros.

El ejemplo ya trae una lista razonable; ajústala a lo que de verdad ofreces.

### 2.3 Resuelve la salida a Internet: proxies

**Este es el problema real que decide si el servicio vive semanas o meses.** En
una IP de centro de datos, YouTube, Instagram y TikTok bloquean en cuestión de
días. El propio `config.py` lo dice: «sin proxy, el servicio funciona en local y
muere en producción».

Necesitas proxies residenciales o móviles, y `VDL_PROXY_MAP` para repartirlos por
plataforma: si uno se quema en Facebook, YouTube sigue funcionando por otro.

### 2.4 Ponte un aviso

`VDL_ALERT_WEBHOOK` a Slack, Discord o n8n. Con el webhook vacío, el sistema
detecta las caídas y las guarda en un panel que nadie mira. El valor de todo el
sistema de canarios depende de esto.

### 2.5 Vigila el disco

`VDL_MAX_FILESIZE_MB` y `VDL_FILE_TTL_MINUTES` son la diferencia entre un
servicio que aguanta y uno que un día deja de aceptar peticiones. En el compose
de producción ya está puesto a 2048 MB; el valor del ejemplo (500 MB) es más
conservador. Además, pon una alerta del sistema al 80% de disco.

---

## 3. Recomendado

### 3.1 Un solo proceso

Los datos viven en memoria: el límite de peticiones, los circuitos, las métricas,
los trabajos y la marca de 24 horas de descargas por IP. Con dos procesos o dos
instancias, cada uno lleva su cuenta y los límites se multiplican (y el bloqueo
de 24 horas deja de funcionar, porque el proceso que no lo sabe lo deja pasar).

Si necesitas escalar, el siguiente paso es Redis para el límite, el
registro de trabajos y la marca de 24 horas.

### 3.2 Las métricas se pierden al reiniciar

La tasa de éxito y la salud por plataforma son en memoria: tras un reinicio el
panel arranca de cero y «Tasa de éxito: 100% con 0 muestras». Para tener
histórico de verdad hace falta persistirlas.

### 3.3 Completa los canarios

`canaries.json` solo tiene YouTube. Con un único canario, la validación de una
actualización de yt-dlp depende de que YouTube esté bien: si YouTube falla en ese
momento, se revierte una versión buena. Añade un canario por plataforma que
ofrezcas de verdad.

### 3.4 Copias de seguridad y datos personales

`history.jsonl` y los registros guardan direcciones IP y enlaces. Son datos
personales: decide cuánto tiempo conservarlos, exclúyelos de las copias que no
toquen, y tenlo en cuenta en la política de privacidad (ya existe la página).

Si no los necesitas, `VDL_HISTORY_ENABLED=false` elimina el problema de raíz.

### 3.5 El carrusel enseña actividad

`VDL_GALLERY_ENABLED=true` publica en la portada los títulos y miniaturas de las
últimas descargas de todo el mundo. Es un detalle agradable y una fuga de
información sobre qué se está descargando. Es una decisión tuya, pero conviene
tomarla a conciencia.

### 3.6 Endurecimiento del contenedor y del servicio

Lo que ya está bien: contenedor sin privilegios con `appuser`, unidad systemd con
`ProtectSystem=strict`, `PrivateTmp` y `NoNewPrivileges`, healthcheck, límites de
memoria, y `ADMIN-TOKEN.txt`, `*.jks`, `produccion.env` e `history.jsonl` fuera de
git (comprobado: no hay ningún secreto versionado).

Lo que falta: rotación de registros (`server.log` crece sin límite; en Docker usa
`logging: driver: json-file` con `max-size`), y límite de CPU además del de
memoria, porque el trabajo pesado aquí es `ffmpeg`.

### 3.7 Aviso legal y de uso

Un descargador público tiene dos frentes abiertos que conviene mirar antes de
publicar: las condiciones de tu proveedor de hosting (muchos prohíben
expresamente este tipo de servicio) y el derecho de autor del contenido. Las
páginas de términos y privacidad ya están escritas; que no se queden sin revisar.

---

## 4. Lo que sigue sin resolverse

Honestidad sobre los límites, para que no te sorprenda:

- **DNS rebinding**: la comprobación de SSRF resuelve el dominio antes de
  descargar, pero yt-dlp vuelve a resolver después. Un dominio malicioso podría
  pasar la comprobación y luego apuntar a la red interna. Cerrarlo exigiría fijar
  la IP en la descarga, y yt-dlp no lo permite.
- **El límite de peticiones es por proceso**, no compartido (ver 3.1).
- **La limpieza de entradas del límite** solo se activa por encima de 10 000
  claves; con tráfico muy alto habría que bajarlo.
- **`/api/monitor` y `/api/gallery` son públicos** por diseño, porque el panel y
  la portada los necesitan sin token. `/api/monitor` no expone el token ni el
  historial, pero sí el estado y los últimos errores.
- **El APK 1.1 no se ha probado en un teléfono real.** Compila, firma y el
  manifiesto es correcto, pero ni el enlace `vdl://pair` ni el botón
  «Reconectar» se han visto funcionar en un dispositivo. Es lo primero que hay
  que comprobar al instalarlo.
- **No hay descubrimiento automático en red (mDNS)**: si cambia la IP del
  servidor, la app no puede adivinar la nueva sola. Se arregla abriendo el panel
  en el móvil y pulsando «Configurar la app en este teléfono».

---

## 5. Cómo comprobar que todo sigue en pie

Las cuatro pruebas del proyecto, en orden:

```bash
python smoke_seguridad.py     # las defensas (este documento)
python smoke_resilience.py    # circuitos, reintentos y reversión
python smoke_monitor.py <token>   # el panel y las acciones de administración
python smoke_test.py          # una descarga MP4 y una conversión MP3 reales
```

`smoke_monitor.py` necesita el servidor corriendo con `VDL_ADMIN_TOKEN` y que le
pases el mismo token; si no, fallan las acciones de administración con un 401
(que es lo correcto: están protegidas).
