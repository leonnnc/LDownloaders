"""Prueba de las defensas del servicio.

Cubre los cuatro fallos que se corrigieron tras la revisión, para que no
vuelvan sin que nadie se entere:

1. SSRF: enlaces que apuntan a la red interna (incluidos los metadatos de las
   nubes y el espacio CGNAT).
2. Límite de peticiones: que la IP del cliente no se pueda falsear con la
   cabecera X-Forwarded-For.
3. Circuit breaker: que los errores del enlace que trae el usuario (un video
   borrado) no puedan tumbar un sitio entero para los demás.
4. Recolector de basura: que no borre un archivo a medio descargar.

No necesita red externa salvo donde se indica. El servidor debe estar
corriendo en http://127.0.0.1:8000 para la parte HTTP.

Uso:  python smoke_seguridad.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from app import config  # noqa: E402
from app.downloader import EngineError, _human_error, _is_permanent_error, _last_thumbnail  # noqa: E402
from app.jobs import JobStore  # noqa: E402
from app.resilience import _looks_like_site_failure  # noqa: E402

BASE = "http://127.0.0.1:8000"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(f"  [{'OK  ' if ok else 'FALLA'}] {name}" + (f"  — {detail}" if detail else ""))


def post(path: str, payload: dict, headers: dict | None = None) -> tuple[int, dict]:
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(), headers=h, method="POST"
    )
    try:
        with OPENER.open(req, timeout=90) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"detail": body[:200]}
    except Exception as e:  # red, timeout
        return 0, {"detail": str(e)}


# ===========================================================================
# 1. SSRF
# ===========================================================================
def test_ssrf() -> None:
    print("\n=== 1. SSRF: destinos de red interna ===")

    from app.main import _is_blocked_ip

    for ip, esperado in [
        ("127.0.0.1", True),
        ("10.1.2.3", True),
        ("192.168.1.10", True),
        ("172.16.5.5", True),
        ("169.254.169.254", True),   # metadatos de AWS/GCP/Azure
        ("100.64.0.1", True),        # CGNAT: Python NO lo marca como privado
        ("0.0.0.0", True),
        ("fc00::1", True),
        ("fe80::1", True),
        ("2606:4700::1111", False),  # Cloudflare: público
        ("8.8.8.8", False),
    ]:
        check(f"_is_blocked_ip({ip}) -> {esperado}", _is_blocked_ip(ip) == esperado)

    # Y a través de la API, que es lo que ve un atacante.
    for label, url in [
        ("loopback", "http://127.0.0.1:8000/api/health"),
        ("IP privada", "http://192.168.1.1/"),
        ("metadatos de nube", "http://169.254.169.254/latest/meta-data/"),
        ("CGNAT", "http://100.64.0.1/"),
        ("nombre .local", "http://algo.local/"),
        ("localhost.localdomain", "http://localhost.localdomain/"),
    ]:
        code, _ = post("/api/parse", {"url": url})
        check(f"la API rechaza {label}", code == 400, f"HTTP {code}")


# ===========================================================================
# 2. Límite de peticiones: falsificación de la IP
# ===========================================================================
def test_rate_limit_ip() -> None:
    print("\n=== 2. Límite de peticiones: la IP no se puede falsear ===")

    from starlette.requests import Request

    from app.main import _client_ip

    def fake_request(peer: str, xff: str | None = None) -> Request:
        headers = []
        if xff is not None:
            headers.append((b"x-forwarded-for", xff.encode()))
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/parse",
                "headers": headers,
                "client": (peer, 51234),
                "query_string": b"",
                "scheme": "http",
            }
        )

    original = config.TRUSTED_PROXIES
    try:
        # Sin proxy declarado: manda la IP del par, pase lo que pase.
        config.TRUSTED_PROXIES = []
        check(
            "sin proxy declarado se ignora X-Forwarded-For",
            _client_ip(fake_request("203.0.113.9", "1.2.3.4")) == "203.0.113.9",
            _client_ip(fake_request("203.0.113.9", "1.2.3.4")),
        )

        # Con proxy declarado: se cree la ÚLTIMA entrada (la que añade el
        # proxy), no la primera (que la pone el cliente y puede ser mentira).
        config.TRUSTED_PROXIES = ["203.0.113.0/24"]
        check(
            "con proxy declarado se usa la última IP de la cadena",
            _client_ip(fake_request("203.0.113.9", "9.9.9.9, 198.51.100.7"))
            == "198.51.100.7",
            _client_ip(fake_request("203.0.113.9", "9.9.9.9, 198.51.100.7")),
        )
        check(
            "una IP que no es del proxy sigue sin creerse",
            _client_ip(fake_request("198.51.100.4", "9.9.9.9")) == "198.51.100.4",
            _client_ip(fake_request("198.51.100.4", "9.9.9.9")),
        )
    finally:
        config.TRUSTED_PROXIES = original


# ===========================================================================
# 3. Circuit breaker contra errores del enlace
# ===========================================================================
def test_circuit() -> None:
    print("\n=== 3. Clasificación de fallos (circuito) ===")

    casos = [
        ("timeout", TimeoutError("timed out"), True),
        ("conexión rechazada", ConnectionError("connection refused"), True),
        (
            "red caída",
            EngineError(
                "No se pudo descargar",
                raw="ERROR: Unable to download webpage: <urlopen error getaddrinfo failed>",
            ),
            True,
        ),
        (
            "error 503 del sitio",
            EngineError("Falla el sitio", raw="ERROR: HTTP Error 503: Service Unavailable"),
            True,
        ),
        (
            "video borrado",
            EngineError("El video no existe", raw="ERROR: Video unavailable"),
            False,
        ),
        (
            "ID truncado",
            EngineError(
                "Enlace inválido",
                raw="ERROR: [youtube:truncated_id] x: Incomplete YouTube ID x. looks truncated.",
            ),
            False,
        ),
    ]
    for label, exc, esperado in casos:
        obtenido = _looks_like_site_failure(exc)
        check(
            f"«{label}» -> {'del sitio (abre circuito)' if esperado else 'del enlace (no lo abre)'}",
            obtenido == esperado,
            f"obtenido={obtenido}",
        )

    print("\n  -- errores permanentes (no se reintentan) --")
    for label, raw, esperado in [
        ("video borrado", "ERROR: Video unavailable", True),
        ("ID truncado", "ERROR: looks truncated. Incomplete YouTube ID", True),
        ("video privado", "ERROR: Private video", True),
        ("solo miembros", "ERROR: This video is members-only", True),
        ("timeout (transitorio)", "ERROR: Unable to download webpage: timed out", False),
        ("error 503 (transitorio)", "ERROR: HTTP Error 503", False),
    ]:
        exc = EngineError("mensaje amable", raw=raw)
        check(f"«{label}» permanente={esperado}", _is_permanent_error(exc) == esperado)
        # El mensaje que ve el usuario debe seguir siendo claro.
        check(f"«{label}» da un mensaje entendible", bool(_human_error(exc).strip()))


# ===========================================================================
# 4. Recolector de basura
# ===========================================================================
def test_cleanup() -> None:
    print("\n=== 4. La limpieza no borra descargas en curso ===")

    original_ttl = config.FILE_TTL_MINUTES
    original_max = config.ACTIVE_JOB_MAX_MINUTES
    try:
        config.FILE_TTL_MINUTES = 15
        config.ACTIVE_JOB_MAX_MINUTES = 120

        store = JobStore()
        job = store.create(url="https://ejemplo.com/v.mp4", kind="mp4")
        workdir = store.workdir(job.id)
        (workdir / "v.mp4.part").write_bytes(b"x" * 1024)

        # Activo y con 20 minutos (más que el TTL, menos que el tope).
        store.update(job.id, status="processing")
        job.created_at = time.time() - 20 * 60
        store.purge_expired()
        check("el directorio de una descarga en curso sigue ahí", workdir.is_dir())
        check("y el trabajo sigue en curso, no «vencido»", job.status == "processing",
              f"estado={job.status}")

        # Un activo colgado de verdad (más del tope) sí se limpia.
        job.created_at = time.time() - 200 * 60
        store.purge_expired()
        check("un trabajo activo colgado (200 min) sí se limpia",
              not workdir.is_dir() and job.status == "expired", f"estado={job.status}")

        # Un terminado y vencido se limpia con normalidad.
        job2 = store.create(url="https://ejemplo.com/o.mp4", kind="mp4")
        dir2 = store.workdir(job2.id)
        (dir2 / "o.mp4").write_bytes(b"y" * 512)
        store.update(job2.id, status="done", finished_at=time.time() - 20 * 60)
        store.purge_expired()
        check("un trabajo terminado y vencido se limpia", not dir2.is_dir())
    finally:
        config.FILE_TTL_MINUTES = original_ttl
        config.ACTIVE_JOB_MAX_MINUTES = original_max


# ===========================================================================
# 5. Datos que vienen del sitio
# ===========================================================================
def test_thumbnails() -> None:
    print("\n=== 5. Miniaturas ausentes (antes daban error 500) ===")
    check("lista vacía -> None", _last_thumbnail({"thumbnails": []}) is None)
    check("clave ausente -> None", _last_thumbnail({}) is None)
    check("lista con datos -> la última",
          _last_thumbnail({"thumbnails": [{"url": "a"}, {"url": "b"}]}) == "b")


# ===========================================================================
# 6. Endpoints: validación y entrega
# ===========================================================================
def test_endpoints() -> None:
    print("\n=== 6. Endpoints ===")

    for bad, label in [
        ("137+bestaudio[ext=m4a]", "corchetes"),
        ("137,best", "coma"),
        ("a" * 80, "demasiado largo"),
    ]:
        code, _ = post("/api/download", {"url": "https://example.com/", "kind": "mp4",
                                         "format_id": bad})
        check(f"rechaza un format_id con {label}", code == 422, f"HTTP {code}")

    code, _ = post("/api/parse", {"url": "ftp://example.com/v.mp4"})
    check("rechaza un esquema que no sea http(s)", code == 400, f"HTTP {code}")

    try:
        with OPENER.open(BASE + "/api/file/no-existe", timeout=30) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    check("un archivo inexistente da 404", code == 404, f"HTTP {code}")


def main() -> int:
    test_ssrf()
    test_rate_limit_ip()
    test_circuit()
    test_cleanup()
    test_thumbnails()
    test_endpoints()

    ok = sum(1 for _, passed in results if passed)
    print("\n" + "=" * 62)
    print(f"RESULTADO: {ok}/{len(results)} pruebas OK")
    for name, passed in results:
        if not passed:
            print(f"  FALLA: {name}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
