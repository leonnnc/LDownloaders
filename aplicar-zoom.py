#!/usr/bin/env python3
"""Selección que no se atasca, desplazamiento al arrastrar y zoom hasta un segundo.

Tres cosas que iban juntas:

  1. **El asa del cursor tapaba la junta.** La zona de agarre de la guía roja medía
     15 px y bajaba por todo el alto, así que caía encima de la onda. Después de un
     corte el cursor se queda justo en la junta, de modo que el siguiente arrastre
     empezaba sobre el asa y movía el cursor en vez de elegir la parte: parecía que
     ya no se podía seleccionar. Ahora el asa es solo la regla, y sobre la onda el
     arrastre siempre elige.

  2. **Al llegar al borde, la vista se corre sola** mientras se elige una parte, así
     que se puede seguir seleccionando más allá de lo que se ve. Con el zoom puesto
     dos segundos ya no caben en la pantalla y sin esto no habría forma de elegir un
     tramo largo.

  3. **El zoom llega hasta ver un segundo.** Antes se quedaba en unos 45 s porque el
     tope era el ancho del lienzo. Ahora el tope útil es un segundo en la vista, la
     onda se dibuja en un lienzo más corto y el CSS lo estira, y la regla dibuja solo
     las marcas que se ven (con el zoom a tope cabrían miles). De paso, los picos se
     guardan con más detalle para que la onda aguante el zoom.

El fin de línea de cada archivo se detecta y se conserva tal cual.
"""

from pathlib import Path

ROOT = Path(__file__).parent
JS = ROOT / "backend" / "static" / "editor.js"
HTML = ROOT / "backend" / "static" / "editor.html"
CSS = ROOT / "backend" / "static" / "editor.css"
README = ROOT / "README.md"
CONFIG = ROOT / "backend" / "app" / "config.py"
EDITOR = ROOT / "backend" / "app" / "editor.py"

# --------------------------------------------------------------- editor.js --

J_ZOOM_DOC_OLD = """  /* ---------------- escala y zoom de la línea de tiempo ----------------
     «Ajustar» (zoom = 1) es ver el montaje entero de una vez: la escala sale de
     repartir el ancho disponible entre la duración total. A partir de ahí el
     usuario amplía, y dos topes evitan que la vista se vuelva inmanejable: ni se
     pasa de ZOOM_MAX_PPS px/s de detalle ni de ZOOM_MAX_WIDTH px de contenido
     (con más, cada onda sería un lienzo enorme que el navegador no dibuja bien). */

  const ZOOM_STEP = 1.25;        // cuánto cambia el zoom en cada pulsación
  const ZOOM_MAX_PPS = 240;      // px por segundo con el máximo detalle
  const ZOOM_MAX_WIDTH = 10000;  // px de ancho máximo del contenido
"""

J_ZOOM_DOC_NEW = """  /* ---------------- escala y zoom de la línea de tiempo ----------------
     «Ajustar» (zoom = 1) es ver el montaje entero de una vez: la escala sale de
     repartir el ancho disponible entre la duración total. A partir de ahí el usuario
     amplía hasta ver un segundo en la vista (ZOOM_MIN_SPAN), que es el tope que de
     verdad sirve para clavar un corte: más allá no se gana nada. Los otros dos topes
     son de seguridad: ZOOM_MAX_PPS px/s de detalle y ZOOM_MAX_WIDTH px de contenido.
     Con el zoom a tope el montaje puede medir cientos de miles de píxeles, así que la
     onda se dibuja en un lienzo más corto y el CSS lo estira (ver CANVAS_MAX_PX) y la
     regla dibuja solo las marcas que se ven. */

  const ZOOM_STEP = 1.25;         // cuánto cambia el zoom en cada pulsación
  const ZOOM_MIN_SPAN = 1;        // segundos que se ven con el zoom a tope
  const ZOOM_MAX_PPS = 1200;      // px por segundo, por si la vista es muy ancha
  const ZOOM_MAX_WIDTH = 4000000; // px de ancho máximo del contenido
"""

J_MAXSCALE_OLD = """  function maxScale() {
    const length = total();
    if (!length) return fitScale();
    return Math.max(fitScale(), Math.min(ZOOM_MAX_PPS, ZOOM_MAX_WIDTH / length));
  }
"""

J_MAXSCALE_NEW = """  function maxScale() {
    const length = total();
    if (!length) return fitScale();
    const view = el.tlScroll.clientWidth || 900;
    // El tope útil es ver un segundo en la vista; los demás solo evitan dispares.
    return Math.max(
      fitScale(),
      Math.min(ZOOM_MAX_PPS, view / ZOOM_MIN_SPAN, ZOOM_MAX_WIDTH / length)
    );
  }
"""

J_CANVAS_OLD = """  function fitCanvas(canvas, width, height) {
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(width));
    const h = Math.max(1, Math.round(height));
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.width = `${w}px`;
"""

J_CANVAS_NEW = """  /* Ancho máximo del lienzo de una onda. Con el zoom a tope un tramo puede medir
     cientos de miles de píxeles y los navegadores no dibujan lienzos así (su tope son
     32.767 px por lado, y con pantallas de alta densidad el lienzo se pide al doble).
     Se pinta más corto y el CSS lo estira: a ese zoom la onda ya es una escalera por
     la resolución de los picos, así que estirarla no quita nada que se notara. */
  const CANVAS_MAX_PX = 30000;

  function fitCanvas(canvas, width, height) {
    const dpr = window.devicePixelRatio || 1;
    const cssW = Math.max(1, Math.round(width));
    const w = Math.min(cssW, CANVAS_MAX_PX);
    const h = Math.max(1, Math.round(height));
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.width = `${cssW}px`;
"""

J_FADE_OLD = """    // Los fundidos se enseñan encima, para no tener que imaginárselos.
    const ctx = canvas.getContext("2d");
    const duration = clipDuration(clip);
    if (!duration) return;
    const scale = width / duration;
    ctx.fillStyle = cssVar("--surface");
    ctx.globalAlpha = 0.75;
    if (clip.fadeIn > 0) {
      const w = Math.min(width, clip.fadeIn * scale);
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(w, 0);
      ctx.lineTo(0, height);
      ctx.closePath();
      ctx.fill();
    }
    if (clip.fadeOut > 0) {
      const w = Math.min(width, clip.fadeOut * scale);
      ctx.beginPath();
      ctx.moveTo(width, 0);
      ctx.lineTo(width - w, 0);
      ctx.lineTo(width, height);
      ctx.closePath();
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
"""

J_FADE_NEW = """    // Los fundidos se enseñan encima, para no tener que imaginárselos.
    const ctx = canvas.getContext("2d");
    const duration = clipDuration(clip);
    if (!duration) return;
    // Se miden con el mismo ancho de lienzo que la onda: si la onda se dibujó más
    // corta y el CSS la estira, un fundido medido con el ancho de pantalla quedaría a
    // otra escala que la música que tapa.
    const wide = Math.min(width, CANVAS_MAX_PX);
    const scale = wide / duration;
    ctx.fillStyle = cssVar("--surface");
    ctx.globalAlpha = 0.75;
    if (clip.fadeIn > 0) {
      const w = Math.min(wide, clip.fadeIn * scale);
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(w, 0);
      ctx.lineTo(0, height);
      ctx.closePath();
      ctx.fill();
    }
    if (clip.fadeOut > 0) {
      const w = Math.min(wide, clip.fadeOut * scale);
      ctx.beginPath();
      ctx.moveTo(wide, 0);
      ctx.lineTo(wide - w, 0);
      ctx.lineTo(wide, height);
      ctx.closePath();
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
"""

J_RULER_OLD = """    const step = steps.find((value) => value * scale >= 64) || steps[steps.length - 1];
    for (let t = 0; t <= length + 0.001; t += step) {
"""

J_RULER_NEW = """    const step = steps.find((value) => value * scale >= 64) || steps[steps.length - 1];
    // Con el zoom a tope la línea de tiempo mide cientos de miles de píxeles: se crean
    // solo las marcas que se ven, más un margen a cada lado, en vez de las miles que
    // caben en el total. La regla sigue midiendo lo mismo —el ancho lo fija
    // renderTimeline—; lo que cambia es cuántas marcas se crean.
    const view = el.tlScroll.clientWidth || 900;
    const from = Math.max(0, (el.tlScroll.scrollLeft - view) / scale);
    const to = Math.min(length, (el.tlScroll.scrollLeft + view * 2) / scale);
    for (let t = Math.ceil(from / step) * step; t <= to + 0.001; t += step) {
"""

J_SCROLL_OLD = """  // Si la vista se desplaza, la cajita se recoloca para no quedarse fuera.
  el.tlScroll.addEventListener("scroll", () => {
    if (state.selection) renderSelection();
  });
"""

J_SCROLL_NEW = """  // Si la vista se desplaza, se recolocan la cajita y las marcas de la regla: con el
  // zoom a tope solo se dibujan las marcas que se ven, así que hay que rehacerlas al
  // correr la vista. Va por fotograma para no rehacer el DOM en cada evento.
  let rulerFrame = null;
  el.tlScroll.addEventListener("scroll", () => {
    if (state.selection) renderSelection();
    if (rulerFrame == null) {
      rulerFrame = requestAnimationFrame(() => {
        rulerFrame = null;
        renderRuler(total(), pixelsPerSecond());
      });
    }
  });
"""

J_EDGE_OLD = """  el.tlClips.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !state.clips.length) return;
"""

J_EDGE_NEW = """  /* ------------- correr la vista mientras se elige una parte -------------
     Al arrastrar cerca de un borde, la vista se corre sola y la parte elegida sigue
     creciendo bajo el puntero. Sin esto, con el zoom puesto no se podría elegir más de
     lo que cabe en la pantalla: habría que soltar y empezar otra vez. */

  const EDGE_PX = 56;      // a qué distancia del borde empieza a correr
  const EDGE_SPEED = 20;   // px por fotograma con el puntero pegado al borde

  // Cuánto corre la vista según lo cerca que esté el puntero del borde: cero en el
  // centro y más cuanto más se pega, para que se sienta proporcional.
  function edgeStep(clientX) {
    const rect = el.tlScroll.getBoundingClientRect();
    const at = clientX - rect.left;
    if (at < EDGE_PX) {
      return -Math.max(1, Math.round(((EDGE_PX - at) / EDGE_PX) * EDGE_SPEED));
    }
    if (at > rect.width - EDGE_PX) {
      return Math.max(1, Math.round(((at - (rect.width - EDGE_PX)) / EDGE_PX) * EDGE_SPEED));
    }
    return 0;
  }

  // Bucle por fotograma mientras el puntero esté en el borde. `onFrame` vuelve a medir
  // la parte elegida: el puntero no se ha movido, lo que se mueve es el contenido bajo
  // él, así que hay que recalcular con la misma posición.
  function autoScroller(onFrame) {
    let x = 0;
    let frame = null;
    const tick = () => {
      frame = null;
      const step = edgeStep(x);
      if (!step) return;
      const before = el.tlScroll.scrollLeft;
      el.tlScroll.scrollLeft = Math.max(0, before + step);
      if (el.tlScroll.scrollLeft === before) return;
      onFrame(x);
      frame = requestAnimationFrame(tick);
    };
    return {
      at(clientX) {
        x = clientX;
        if (frame == null) frame = requestAnimationFrame(tick);
      },
      stop() {
        if (frame != null) cancelAnimationFrame(frame);
        frame = null;
      },
    };
  }

  el.tlClips.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !state.clips.length) return;
"""

J_TRACK_OLD = """    const end = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      // Un clic suelto no deja selección: elige el tramo, como siempre.
      if (!dragged || (state.selection && state.selection.end - state.selection.start < MIN_SEL)) {
        state.selection = null;
      }
      renderTimeline();
    };

    window.addEventListener("pointermove", move);
"""

J_TRACK_NEW = """    // Al llegar al borde, la vista se corre y la selección sigue creciendo.
    const scroller = autoScroller(move);
    const track = (ev) => {
      move(ev);
      scroller.at(ev.clientX);
    };

    const end = () => {
      scroller.stop();
      window.removeEventListener("pointermove", track);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      // Un clic suelto no deja selección: elige el tramo, como siempre.
      if (!dragged || (state.selection && state.selection.end - state.selection.start < MIN_SEL)) {
        state.selection = null;
      }
      renderTimeline();
    };

    window.addEventListener("pointermove", track);
"""

J_RULER_TRACK_OLD = """      const end = () => {
        el.tlRuler.removeEventListener("pointermove", extend);
        window.removeEventListener("pointerup", end);
        window.removeEventListener("pointercancel", end);
        // Un roce sin arrastre no deja selección: se queda como estaba.
        if (state.selection && state.selection.end - state.selection.start < MIN_SEL) {
          state.selection = null;
        }
        renderTimeline();
      };
      el.tlRuler.addEventListener("pointermove", extend);
"""

J_RULER_TRACK_NEW = """      // En la regla también se corre la vista al llegar al borde.
      const scroller = autoScroller(extend);
      const track = (ev) => {
        extend(ev);
        scroller.at(ev.clientX);
      };
      const end = () => {
        scroller.stop();
        el.tlRuler.removeEventListener("pointermove", track);
        window.removeEventListener("pointerup", end);
        window.removeEventListener("pointercancel", end);
        // Un roce sin arrastre no deja selección: se queda como estaba.
        if (state.selection && state.selection.end - state.selection.start < MIN_SEL) {
          state.selection = null;
        }
        renderTimeline();
      };
      el.tlRuler.addEventListener("pointermove", track);
"""

J_GRAB_DOC_OLD = """  // La línea del cursor se agarra y se arrastra con el ratón, a lo largo de toda su
  // altura y también por encima de los tramos: no hay que apuntar a la regla.
"""

J_GRAB_DOC_NEW = """  // La línea del cursor se agarra y se arrastra con el ratón por la regla, que es donde
  // está su tirador. Antes la zona de agarre bajaba por todo el alto y tapaba la onda
  // justo en la junta que deja un corte, así que ahí el arrastre movía el cursor en vez
  // de elegir la parte: parecía que ya no se podía seleccionar.
"""

# -------------------------------------------------------------- editor.css --

CSS_GRAB_OLD = """/* Zona de agarre del cursor: la línea es fina, así que se arrastra con un margen
   ancho a los lados. Va por encima de los tramos para no tener que apuntar. */
.tl-grab {
  position: absolute;
  top: 0;
  bottom: 0;
  left: -7px;
  width: 15px;
"""

CSS_GRAB_NEW = """/* Zona de agarre del cursor: la línea es fina, así que se arrastra con un margen
   ancho a los lados. Ocupa solo la regla: antes bajaba por todo el alto y tapaba la
   onda justo en la junta que deja un corte, que es donde uno va a elegir la parte
   siguiente. */
.tl-grab {
  position: absolute;
  top: 0;
  height: 22px;
  left: -7px;
  width: 15px;
"""

# ------------------------------------------------------------- editor.html --

H_HELP_OLD = """        La <strong>rueda</strong> sobre la línea de tiempo acerca y aleja en el punto que señalas,
        y <kbd>Mayús</kbd>+rueda (o el botón central, arrastrando) desplaza la vista.
"""

H_HELP_NEW = """        La <strong>rueda</strong> sobre la línea de tiempo acerca y aleja en el punto que señalas
        (hasta ver un segundo en la pantalla) y <kbd>Mayús</kbd>+rueda (o el botón central,
        arrastrando) desplaza la vista. Si al arrastrar para elegir llegas al borde, la vista se
        corre sola para que puedas seguir eligiendo.
"""

# ---------------------------------------------------------------- README ----

R_ZOOM_OLD = """   directamente con el ratón. Con el teclado: <kbd>+</kbd> y <kbd>−</kbd> acercan y
   alejan, y <kbd>0</kbd> vuelve a ver el montaje entero.
"""

R_ZOOM_NEW = """   directamente con el ratón, y al arrastrar cerca de un borde la vista se corre sola
   para poder seguir eligiendo más allá de lo que se ve. Con el teclado: <kbd>+</kbd> y
   <kbd>−</kbd> acercan y alejan (hasta dejar un segundo en la pantalla, que es el tope)
   y <kbd>0</kbd> vuelve a ver el montaje entero.
"""

# -------------------------------------------------------------- config.py ---

C_PEAKS_OLD = 'EDITOR_PEAKS_PER_SECOND = _env_int("VDL_EDITOR_PEAKS_PER_SECOND", 20)'
C_PEAKS_NEW = 'EDITOR_PEAKS_PER_SECOND = _env_int("VDL_EDITOR_PEAKS_PER_SECOND", 60)'

# -------------------------------------------------------------- editor.py ---

E_PEAKS_OLD = """    cached = path.parent / "peaks.json"
    if cached.is_file():
        try:
            data = json.loads(cached.read_text(encoding="utf-8"))
            if data.get("peaks"):
                return data
        except (OSError, json.JSONDecodeError):
            pass  # caché ilegible: se recalcula

    # Para archivos largos se baja la resolución en vez de devolver un JSON
    # enorme: la onda se dibuja en unos cientos de píxeles de ancho.
    per_second = config.EDITOR_PEAKS_PER_SECOND
    if duration and duration > 0:
        per_second = max(2.0, min(per_second, 6000.0 / duration))
    per_second = round(per_second, 3)
"""

E_PEAKS_NEW = """    # Para archivos largos se baja la resolución en vez de devolver un JSON enorme, pero
    # se mantiene alta de sobra para el zoom: con 60.000 puntos el archivo son unos
    # cientos de KB y un segundo de música sigue teniendo detalle al ampliar.
    per_second = config.EDITOR_PEAKS_PER_SECOND
    if duration and duration > 0:
        per_second = max(2.0, min(per_second, 60000.0 / duration))
    per_second = round(per_second, 3)

    cached = path.parent / "peaks.json"
    if cached.is_file():
        try:
            data = json.loads(cached.read_text(encoding="utf-8"))
            # La caché vale solo si trae la resolución de ahora: al cambiarla se
            # recalcula, en vez de servir una onda más basta de la cuenta.
            if data.get("peaks") and data.get("per_second") == per_second:
                return data
        except (OSError, json.JSONDecodeError):
            pass  # caché ilegible: se recalcula
"""

REPLACEMENTS = [
    (JS, "editor.js · topes del zoom", J_ZOOM_DOC_OLD, J_ZOOM_DOC_NEW),
    (JS, "editor.js · escala máxima", J_MAXSCALE_OLD, J_MAXSCALE_NEW),
    (JS, "editor.js · tope del lienzo", J_CANVAS_OLD, J_CANVAS_NEW),
    (JS, "editor.js · fundidos a la misma escala", J_FADE_OLD, J_FADE_NEW),
    (JS, "editor.js · regla solo con lo visible", J_RULER_OLD, J_RULER_NEW),
    (JS, "editor.js · regla al desplazar", J_SCROLL_OLD, J_SCROLL_NEW),
    (JS, "editor.js · correr la vista al elegir", J_EDGE_OLD, J_EDGE_NEW),
    (JS, "editor.js · arrastre con corrimiento (onda)", J_TRACK_OLD, J_TRACK_NEW),
    (JS, "editor.js · arrastre con corrimiento (regla)", J_RULER_TRACK_OLD, J_RULER_TRACK_NEW),
    (JS, "editor.js · comentario del asa", J_GRAB_DOC_OLD, J_GRAB_DOC_NEW),
    (CSS, "editor.css · asa solo en la regla", CSS_GRAB_OLD, CSS_GRAB_NEW),
    (HTML, "editor.html · ayuda del zoom", H_HELP_OLD, H_HELP_NEW),
    (README, "README · zoom y corrimiento", R_ZOOM_OLD, R_ZOOM_NEW),
    (CONFIG, "config.py · resolución de los picos", C_PEAKS_OLD, C_PEAKS_NEW),
    (EDITOR, "editor.py · caché de picos por resolución", E_PEAKS_OLD, E_PEAKS_NEW),
]


def read(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    return raw.decode("utf-8"), ("\r\n" if b"\r\n" in raw else "\n")


def main() -> int:
    cache = {}
    eols = {}

    for path, name, old, new in REPLACEMENTS:
        if path not in cache:
            cache[path], eols[path] = read(path)

        eol = eols[path]
        old_eol = old.replace("\n", eol)
        new_eol = new.replace("\n", eol)
        found = cache[path].count(old_eol)

        if found == 0:
            print(f"ERROR: no se encontro el bloque {name}")
            return 1
        if found > 1:
            print(f"ERROR: el bloque {name} aparece {found} veces")
            return 1

        cache[path] = cache[path].replace(old_eol, new_eol)
        print(f"ok: {name}")

    for path, text in cache.items():
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        print(f"escrito: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
