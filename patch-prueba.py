"""Alinea smoke_resilience.py con el contrato nuevo de A1.

La prueba afirmaba que un enlace que no existe cuenta como fallo de la
plataforma (`failure >= 1`). Eso es justo lo que A1 cambia: un enlace muerto no
es una avería de YouTube. Ahora se comprueba lo contrario, con deltas para que
el resultado no dependa de lo que haya hecho el servidor antes.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILE = ROOT / "smoke_resilience.py"

OLD = '''    # Un enlace roto a propósito: genera tráfico de fallo y métricas reales.
    bad = call("/api/parse", {"url": "https://www.youtube.com/watch?v=00000000000"})
    detail = bad.get("detail", "")
    check("un enlace inválido devuelve un mensaje entendible",
          bool(detail) and len(detail) < 300, detail[:110])

    # Ahora sí: hay tráfico real que debe haberse contabilizado.
    metrics = call("/api/metrics")
    counters = metrics.get("counters", {})
    check("metrics registra el fallo de análisis",
          counters.get("parse.fail", 0) >= 1, f"parse.fail={counters.get('parse.fail')}")
    check("metrics mide la salud por plataforma",
          any(p.get("platform") == "youtube" and p.get("failure", 0) >= 1
              for p in metrics.get("platforms", [])),
          f"{len(metrics.get('platforms', []))} plataformas medidas")
'''

NEW = '''    # Foto previa: el resultado de la prueba no debe depender de lo que haya
    # hecho el servidor antes (canarios, otras pruebas, descargas reales).
    antes = call("/api/metrics")
    fallos_antes = (antes.get("counters", {}) or {}).get("parse.fail", 0)
    invalidos_antes = (antes.get("invalid_requests", {}) or {}).get("parse", 0)
    usuario_antes = next(
        (p.get("user_errors", 0) for p in antes.get("platforms", [])
         if p.get("platform") == "youtube"), 0)

    # Un enlace roto a propósito: no es una avería, es un enlace que no existe.
    bad = call("/api/parse", {"url": "https://www.youtube.com/watch?v=00000000000"})
    detail = bad.get("detail", "")
    check("un enlace inválido devuelve un mensaje entendible",
          bool(detail) and len(detail) < 300, detail[:110])

    metrics = call("/api/metrics")
    counters = metrics.get("counters", {})
    invalidos = metrics.get("invalid_requests", {})
    youtube = next(
        (p for p in metrics.get("platforms", []) if p.get("platform") == "youtube"), {})

    # Un enlace que no existe no dice nada de YouTube: se cuenta aparte y deja
    # intactas la tasa de éxito y la salud de la plataforma. Si contara, cinco
    # enlaces muertos pegados por un visitante bastarían para que el panel
    # anunciara «Sistema caído» y aconsejara reiniciar un servicio sano.
    check("metrics separa el enlace inválido del fallo del servicio",
          invalidos.get("parse", 0) > invalidos_antes
          and counters.get("parse.fail", 0) == fallos_antes,
          f"parse.invalid={invalidos.get('parse')} (antes {invalidos_antes}) · "
          f"parse.fail={counters.get('parse.fail')} (antes {fallos_antes})")

    check("metrics mide la salud por plataforma",
          bool(youtube)
          and youtube.get("failure", 0) == 0
          and youtube.get("user_errors", 0) > usuario_antes,
          f"youtube: fallos={youtube.get('failure')} · "
          f"errores_de_usuario={youtube.get('user_errors')} (antes {usuario_antes})")
'''


def main() -> int:
    text = FILE.read_text(encoding="utf-8")
    if text.count(OLD) != 1:
        print(f"ABORTADO: coincidencias={text.count(OLD)} (se esperaba 1)")
        return 1

    FILE.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    print("aplicado: smoke_resilience.py alineado con A1")

    try:
        ast.parse(FILE.read_text(encoding="utf-8"), filename=str(FILE))
        print("  ok (python) smoke_resilience.py")
    except SyntaxError as exc:
        print("  ERROR de sintaxis:", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
