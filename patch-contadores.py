"""Segunda pasada de A1: los contadores globales tampoco deben contar
los enlaces invalidos del usuario.

La primera pasada limpio la ventana por plataforma, pero `parse.fail` seguia
subiendo y `snapshot()` calcula la tasa global con ese contador: cinco enlaces
muertos dejaban el diagnostico global en «caido» igualmente.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "backend" / "app"

PATCHES: list[tuple[Path, str, str, str]] = []


def patch(path: Path, old: str, new: str, label: str) -> None:
    PATCHES.append((path, old, new, label))


# ---------------------------------------------------------------------------
# downloader.py · el helper tambien lleva el contador
# ---------------------------------------------------------------------------
patch(
    APP / "downloader.py",
    '''def _record_failure(platform: str, exc: Exception | None, message: str) -> None:
    """Apunta el fallo donde corresponde: la plataforma o el enlace del usuario.

    Un enlace que no existe, un video privado o uno borrado no dicen nada del
    sitio. Antes se anotaban como fallo de la plataforma, y cinco enlaces malos
    bastaban para que el panel anunciara «Sistema caído» y aconsejara
    reiniciar un servicio que estaba perfecto (y para disparar una alerta al
    webhook que no correspondía a ninguna avería).

    Ante la duda se cuenta como fallo de la plataforma: solo se exculpa lo que
    se reconoce como permanente, con el mismo criterio que la lista de errores
    que no se reintentan. Así no se tapa una avería real.
    """
    if exc is not None and _is_permanent_error(exc):
        metrics.record_user_error(platform, message)
        return
    metrics.record(platform, False, message)
''',
    '''def _record_failure(
    platform: str,
    exc: Exception | None,
    message: str,
    operation: str,
) -> bool:
    """Apunta el fallo donde corresponde: la plataforma o el enlace del usuario.

    Un enlace que no existe, un video privado o uno borrado no dicen nada del
    sitio. Antes se anotaban como fallo de la plataforma, y cinco enlaces malos
    bastaban para que el panel anunciara «Sistema caído» y aconsejara
    reiniciar un servicio que estaba perfecto (y para disparar una alerta al
    webhook que no correspondía a ninguna avería).

    Por eso hay dos contadores y no uno:

    * `{operation}.invalid` — el enlace que trajo el usuario. Se informa, pero
      no entra en la tasa de éxito ni en la ventana de la plataforma: si
      entrara, la tasa global bajaría igual y el diagnóstico volvería a
      anunciar una avería inexistente.
    * `{operation}.fail` — el fallo es del motor o del sitio, y sí cuenta.

    Ante la duda se cuenta como fallo de la plataforma: solo se exculpa lo que
    se reconoce como permanente, con el mismo criterio que la lista de errores
    que no se reintentan. Así no se tapa una avería real.

    Devuelve True si el fallo cuenta contra la plataforma.
    """
    if exc is not None and _is_permanent_error(exc):
        metrics.record_user_error(platform, message)
        metrics.inc(f"{operation}.invalid")
        return False

    metrics.record(platform, False, message)
    metrics.inc(f"{operation}.fail")
    return True
''',
    "A1 downloader: el helper lleva su propio contador",
)

patch(
    APP / "downloader.py",
    """    except EngineError as exc:
        _record_failure(platform, exc, str(exc))
        metrics.inc("parse.fail")
        raise
""",
    """    except EngineError as exc:
        _record_failure(platform, exc, str(exc), "parse")
        raise
""",
    "A1 downloader: parse (EngineError) sin doble contador",
)

patch(
    APP / "downloader.py",
    """    except Exception as exc:  # noqa: BLE001
        _record_failure(platform, exc, str(exc))
        metrics.inc("parse.fail")
        raise EngineError(_human_error(exc)) from exc
""",
    """    except Exception as exc:  # noqa: BLE001
        _record_failure(platform, exc, str(exc), "parse")
        raise EngineError(_human_error(exc)) from exc
""",
    "A1 downloader: parse (inesperado) sin doble contador",
)

patch(
    APP / "downloader.py",
    """    # Todas las estrategias fallaron.
    _record_failure(platform, last_error, str(last_error))
    metrics.inc("download.fail")
""",
    """    # Todas las estrategias fallaron.
    _record_failure(platform, last_error, str(last_error), "download")
""",
    "A1 downloader: descarga sin doble contador",
)

# ---------------------------------------------------------------------------
# metrics.py · publicar los contadores nuevos sin tocar `requests`
# ---------------------------------------------------------------------------
patch(
    APP / "metrics.py",
    """            "overall_success_rate": round(overall_ok / total, 3) if total else None,
""",
    """            # Enlaces inválidos del usuario. Van aparte de `requests` a
            # propósito: `status.evaluate()` cuenta las muestras de `requests`
            # para decidir si la tasa merece crédito, y un enlace muerto no es
            # una muestra válida del estado del servicio.
            "invalid_requests": {
                "parse": counters.get("parse.invalid", 0),
                "download": counters.get("download.invalid", 0),
            },
            "overall_success_rate": round(overall_ok / total, 3) if total else None,
""",
    "A1 metrics: publicar los contadores de enlace invalido",
)


def main() -> int:
    errors: list[str] = []
    for path, old, _new, label in PATCHES:
        text = path.read_text(encoding="utf-8")
        found = text.count(old)
        if found != 1:
            errors.append(f"[{label}] coincidencias={found} (se esperaba 1) en {path.name}")

    if errors:
        print("ABORTADO:")
        for err in errors:
            print("  -", err)
        return 1

    for path, old, new, label in PATCHES:
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        print("aplicado:", label)

    print("\nvalidacion:")
    for path in sorted({p for p, _o, _n, _l in PATCHES}):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            print("  ok (python)", path.name)
        except SyntaxError as exc:
            print("  ERROR de sintaxis:", path, exc)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
