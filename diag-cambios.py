"""Comprobacion de los cambios del bloque 1 contra el servidor con token (8021).

Cubre: A1 (metricas sin culpa del usuario), A2 (una sola fuente de estado),
B1 (el editor exige el token en lo que cuesta), B3 (guardia de disco) y
B6 (comparacion en tiempo constante en /api/pairing).
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8021"
TOKEN = "token-de-prueba-1234567890"
OK: list[str] = []
BAD: list[str] = []


def call(path: str, method: str = "GET", payload=None, token: str | None = None, raw: bytes | None = None):
    url = BASE + path
    data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    if raw is not None:
        req.add_header("Content-Type", "application/octet-stream")
    if token:
        req.add_header("X-Admin-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=90) as res:
            body = res.read().decode("utf-8", "replace")
            try:
                return res.status, json.loads(body)
            except json.JSONDecodeError:
                return res.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def check(label: str, condition: bool, detail: str) -> None:
    (OK if condition else BAD).append(f"{label}: {detail}")
    print(("  OK   " if condition else "  FALLO") + f"  {label}: {detail}")


print("== B1 · el editor exige el token donde cuesta CPU o disco ==")
status, _ = call("/api/audio/upload", method="POST", raw=b"x" * 200)
check("subida sin token", status == 401, f"HTTP {status}")
status, _ = call("/api/audio/render", method="POST", payload={"format": "mp3", "clips": []})
check("exportar sin token", status == 401, f"HTTP {status}")
status, body = call("/api/audio/upload", method="POST", raw=b"x" * 200, token=TOKEN)
check("subida con token pasa la guardia", status not in (401, 403), f"HTTP {status} (esperado 415/400)")
status, _ = call("/api/audio/info")
check("lectura sigue abierta", status == 200, f"/api/audio/info -> HTTP {status}")

print("== B6 · /api/pairing en tiempo constante ==")
status, body = call("/api/pairing")
leaks = isinstance(body, dict) and bool(body.get("token"))
check("sin token no entrega la credencial", status == 200 and not leaks, f"HTTP {status}, token expuesto={leaks}")
status, body = call("/api/pairing", token=TOKEN)
entrega = isinstance(body, dict) and bool(body.get("token"))
check("con token si la entrega", status == 200 and entrega, f"HTTP {status}, token entregado={entrega}")

print("== A2 · /api/health y /api/monitor dicen lo mismo ==")
_, health = call("/api/health")
_, monitor = call("/api/monitor")
check("mismo veredicto", health.get("status") == monitor.get("status"),
      f"health={health.get('status')} monitor={monitor.get('status')}")
check("health trae etiqueta y motivos", bool(health.get("status_label")) and "reasons" in health,
      f"label={health.get('status_label')!r}")
check("health trae el disco", isinstance(health.get("disk"), dict) and "free_mb" in health["disk"],
      f"disk={health.get('disk')}")

print("== B3 · guardia de disco antes de encolar ==")
status, body = call("/api/download", method="POST",
                    payload={"url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ", "kind": "mp4"})
detail = body.get("detail") if isinstance(body, dict) else str(body)
check("sin espacio libre no se encola", status == 507, f"HTTP {status} · {detail}")

print("== A1 · un enlace muerto no es un fallo de la plataforma ==")
_, antes = call("/api/metrics")
estados_antes = {p["platform"]: p["state"] for p in antes.get("platforms", [])}
muertos = [f"https://www.youtube.com/watch?v=aaaaaaaaa{i}" for i in range(5)]
codigos = [call("/api/parse", method="POST", payload={"url": u})[0] for u in muertos]
check("los enlaces muertos se rechazan", all(c == 422 for c in codigos), f"HTTP {codigos}")
_, despues = call("/api/metrics")
youtube = next((p for p in despues.get("platforms", []) if p["platform"] == "youtube"), {})
print(f"     youtube -> state={youtube.get('state')} rate={youtube.get('success_rate')} "
      f"fallos={youtube.get('failure')} errores_de_usuario={youtube.get('user_errors')}")
check("youtube no queda como caido", youtube.get("state") not in ("caido", "degradado"),
      f"state={youtube.get('state')} (antes: {estados_antes.get('youtube')})")
check("el fallo se anota como error del usuario", (youtube.get("user_errors") or 0) >= 5,
      f"user_errors={youtube.get('user_errors')}  fallos_de_plataforma={youtube.get('failure')}")
check("la tasa de la plataforma no se ensucia", youtube.get("success_rate") == 1.0,
      f"success_rate={youtube.get('success_rate')}")
_, health2 = call("/api/health")
check("el diagnostico global sigue sano", health2.get("status") == "ok",
      f"status={health2.get('status')} · {health2.get('reasons')} · reiniciar={health2.get('restart_advised')}")

print()
print(f"RESULTADO: {len(OK)} correctas, {len(BAD)} fallidas")
for line in BAD:
    print("  FALLO ->", line)
sys.exit(1 if BAD else 0)
