#!/usr/bin/env python3
"""Cambia los saltos de ±5 y ±10 segundos por un botón de cortar.

Lo que hace:

  1. Fuera los botones −10, −5, +5 y +10 de la fila de edición. En su lugar
     entra **✂ Cortar**, que quita del montaje la parte elegida.
  2. El corte se apoya en lo que ya existía: la parte se elige arrastrando
     sobre la onda, o arrastrando sobre la regla con Mayús. Lo elegido sale del
     montaje y los tramos de detrás se corren hacia atrás, así que no queda un
     hueco de silencio. Se deshace con Ctrl + Z.
  3. La guía roja (el cursor) pasa a servir para clavar el corte: los bordes de
     la parte elegida se pegan a la guía cuando caen cerca.
  4. Supr corta la parte elegida; si no hay parte, sigue llevándose el tramo
     seleccionado entero, como antes.

El fin de línea de cada archivo se detecta y se conserva tal cual.
"""

from pathlib import Path

ROOT = Path(__file__).parent
JS = ROOT / "backend" / "static" / "editor.js"
HTML = ROOT / "backend" / "static" / "editor.html"
CSS = ROOT / "backend" / "static" / "editor.css"
README = ROOT / "README.md"

# ------------------------------------------------------------- editor.html --

H_COMMENT_OLD = """      <!-- Controles de reproducción. Cada botón tiene su atajo de teclado. -->
"""

H_COMMENT_NEW = """      <!-- Controles de reproducción y el corte. Cada botón tiene su atajo de teclado. -->
"""

H_BUTTONS_OLD = """        <button class="nav-btn" type="button" id="btn-back10"
                title="Retroceder 10 s (Mayús + ←)" aria-label="Retroceder diez segundos">−10</button>
        <button class="nav-btn" type="button" id="btn-back5"
                title="Retroceder 5 s" aria-label="Retroceder cinco segundos">−5</button>
        <button class="nav-btn nav-btn-play" type="button" id="btn-play"
                title="Reproducir o pausar (Espacio)" aria-label="Reproducir">▶</button>
        <button class="nav-btn" type="button" id="btn-fwd5"
                title="Avanzar 5 s" aria-label="Avanzar cinco segundos">+5</button>
        <button class="nav-btn" type="button" id="btn-fwd10"
                title="Avanzar 10 s (Mayús + →)" aria-label="Avanzar diez segundos">+10</button>
"""

H_BUTTONS_NEW = """        <button class="nav-btn nav-btn-play" type="button" id="btn-play"
                title="Reproducir o pausar (Espacio)" aria-label="Reproducir">▶</button>
        <button class="nav-btn nav-btn-cut" type="button" id="btn-cut" disabled
                title="Cortar la parte elegida (Supr)"
                aria-label="Cortar la parte elegida">✂ Cortar</button>
"""

H_HELP_OLD = """        elige el tramo entero y <kbd>Esc</kbd> quita la selección.
"""

H_HELP_NEW = """        elige el tramo entero y <kbd>Esc</kbd> quita la selección. Con una parte ya
        elegida, <strong>✂ Cortar</strong> la saca del montaje y los tramos de detrás se corren
        hacia atrás para que no quede hueco; <kbd>Supr</kbd> hace lo mismo. Arrastrando sobre la
        regla con <kbd>Mayús</kbd> se elige la parte ahí arriba, y sus bordes se pegan a la guía
        roja cuando pasan cerca, que es la forma de clavar el corte en el instante exacto.
"""

H_KEYS_OLD = """        <kbd>Supr</kbd> borra el tramo elegido y <kbd>Ctrl</kbd>+<kbd>Z</kbd> deshace.
"""

H_KEYS_NEW = """        <kbd>Supr</kbd> corta la parte elegida y <kbd>Ctrl</kbd>+<kbd>Z</kbd> deshace.
"""

# --------------------------------------------------------------- editor.js --

J_EL_OLD = """    btnBack5: $("btn-back5"),
    btnBack10: $("btn-back10"),
    btnFwd5: $("btn-fwd5"),
    btnFwd10: $("btn-fwd10"),
"""

J_EL_NEW = """    btnCut: $("btn-cut"),
"""

J_LISTENERS_OLD = """  el.btnBack5.addEventListener("click", () => nudge(-5));
  el.btnBack10.addEventListener("click", () => nudge(-10));
  el.btnFwd5.addEventListener("click", () => nudge(5));
  el.btnFwd10.addEventListener("click", () => nudge(10));
"""

J_LISTENERS_NEW = """  el.btnCut.addEventListener("click", cutSelection);
"""

J_TRANSPORT_OLD = """    const empty = count === 0;
    for (const button of [
      el.btnPlay, el.btnStart, el.btnEnd,
      el.btnBack5, el.btnBack10, el.btnFwd5, el.btnFwd10,
    ]) {
      button.disabled = empty;
    }
"""

J_TRANSPORT_NEW = """    const empty = count === 0;
    for (const button of [el.btnPlay, el.btnStart, el.btnEnd]) {
      button.disabled = empty;
    }
    updateCutButton();
"""

J_NUDGE_OLD = """  // Moverse por el montaje: es lo que hacen los botones de ±5 y ±10 segundos.
"""

J_NUDGE_NEW = """  // Moverse por el montaje: lo hacen las flechas ← y → (con Mayús, diez segundos).
"""

J_CUT_OLD = """  function refresh() {
    renderTimeline();
    updateTransport();
  }
"""

J_CUT_NEW = """  /* ---------------- cortar la parte elegida ----------------
     Es lo contrario de añadir: lo sombreado desaparece del montaje y los tramos de
     detrás se corren hacia atrás, así que no queda un hueco de silencio. Se apoya en
     lo mismo que el volumen de la parte elegida —partir el montaje justo por sus
     bordes y quedarse con los trozos de dentro—, solo que esos trozos se van. */

  function cutSelection() {
    const range = state.selection;
    if (!range || !state.clips.length) return;
    if (range.end - range.start < MIN_SEL) return;

    // El cursor se queda donde empezaba el corte: es el punto que se está mirando.
    const at = Math.max(0, Math.min(range.start, total()));

    // La selección se suelta antes de repintar: eso ya no está en el montaje, y
    // dejar el sombreado un instante daría a entender lo contrario.
    state.selection = null;

    commit(() => {
      const pieces = selectionPieces(range);
      const gone = new Set(pieces.map((clip) => clip.id));
      state.clips = state.clips.filter((clip) => !gone.has(clip.id));
      state.selectedId = null;
    });

    keepSelectionValid();
    seek(at);
  }

  // El botón de cortar solo se puede pulsar cuando hay una parte elegida: sin
  // selección no hay nada que cortar, y dejarlo activo daría a entender que sí.
  function updateCutButton() {
    const range = state.selection;
    el.btnCut.disabled = !range || range.end - range.start < MIN_SEL || !state.clips.length;
  }

  function refresh() {
    renderTimeline();
    updateTransport();
  }
"""

J_SEL_HIDE_OLD = """    if (!range || !length || range.end - range.start < 0.001) {
      el.tlSelect.classList.add("hidden");
      el.tlGain.classList.add("hidden");
      return;
    }
"""

J_SEL_HIDE_NEW = """    if (!range || !length || range.end - range.start < 0.001) {
      el.tlSelect.classList.add("hidden");
      el.tlGain.classList.add("hidden");
      updateCutButton();
      return;
    }
"""

J_SEL_END_OLD = """    el.tlGain.classList.remove("hidden");
  }
"""

J_SEL_END_NEW = """    el.tlGain.classList.remove("hidden");
    updateCutButton();
  }
"""

J_DELETE_OLD = """    } else if (event.key === "Delete" || event.key === "Backspace") {
      if (state.selectedId) {
        event.preventDefault();
        removeClip(state.selectedId);
      }
    } else if (event.key === "Home") {
"""

J_DELETE_NEW = """    } else if (event.key === "Delete" || event.key === "Backspace") {
      // Con una parte elegida, Supr la corta; si no hay parte, se lleva el tramo
      // seleccionado entero, que es lo que hacía antes.
      if (state.selection && state.selection.end - state.selection.start >= MIN_SEL) {
        event.preventDefault();
        cutSelection();
      } else if (state.selectedId) {
        event.preventDefault();
        removeClip(state.selectedId);
      }
    } else if (event.key === "Home") {
"""

J_SNAP_OLD = """  /* ------------- elegir una parte, con el ratón sobre la onda -------------
     Arrastrar sobre la onda elige un trozo de música; un clic suelto sigue eligiendo
     el tramo (y su cabecera sigue siendo lo que se arrastra para reordenar). */
"""

J_SNAP_NEW = """  /* ------------- elegir una parte, con el ratón sobre la onda -------------
     Arrastrar sobre la onda elige un trozo de música; un clic suelto sigue eligiendo
     el tramo (y su cabecera sigue siendo lo que se arrastra para reordenar). */

  // Margen de enganche con la guía roja: unos pocos píxeles, para clavar el corte
  // donde está la guía sin renunciar a arrastrar a pulso.
  const SNAP_PX = 8;

  // El borde se pega a la guía roja si cae cerca. Colocar la guía es la forma de
  // fijar un instante exacto, y el arrastre no tiene esa puntería: sin esto habría
  // que acertar el píxel justo.
  function snapToGuide(time) {
    if (!state.clips.length) return time;
    const guide = state.cursor;
    return Math.abs(time - guide) * pixelsPerSecond() <= SNAP_PX ? guide : time;
  }
"""

J_ANCHOR_OLD = """    const anchor = timeAtClientX(event.clientX);
    const originX = event.clientX;
    let dragged = false;
"""

J_ANCHOR_NEW = """    const anchor = snapToGuide(timeAtClientX(event.clientX));
    const originX = event.clientX;
    let dragged = false;
"""

J_MOVE_OLD = """      dragged = true;
      const time = timeAtClientX(ev.clientX);
      state.selection = {
"""

J_MOVE_NEW = """      dragged = true;
      const time = snapToGuide(timeAtClientX(ev.clientX));
      state.selection = {
"""

J_RULER_CLICK_OLD = """  el.tlRuler.addEventListener("click", (event) => {
    seek(timeAtClientX(event.clientX));
  });
"""

J_RULER_CLICK_NEW = """  el.tlRuler.addEventListener("click", (event) => {
    // Con Mayús la regla sirve para elegir la parte, no para mover el cursor.
    if (event.shiftKey) return;
    seek(timeAtClientX(event.clientX));
  });
"""

J_RULER_DOWN_OLD = """  // El cursor también se puede arrastrar sobre la regla.
  el.tlRuler.addEventListener("pointerdown", (event) => {
    el.tlRuler.setPointerCapture(event.pointerId);
    // El tiempo se recalcula en cada movimiento en vez de congelar el rectángulo:
    // con el zoom puesto la vista se desplaza sola y el punto bajo el dedo cambia.
    const move = (ev) => seek(timeAtClientX(ev.clientX));
    move(event);
    el.tlRuler.addEventListener("pointermove", move);
    const up = () => {
      el.tlRuler.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointerup", up);
  });
"""

J_RULER_DOWN_NEW = """  // El cursor también se puede arrastrar sobre la regla. Con Mayús, en cambio, la
  // regla elige la parte desde donde se pulsa hasta donde se suelta: es la forma de
  // marcar el corte ahí arriba, sobre la propia guía.
  el.tlRuler.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    el.tlRuler.setPointerCapture(event.pointerId);

    if (event.shiftKey && state.clips.length) {
      const anchor = snapToGuide(timeAtClientX(event.clientX));
      const originX = event.clientX;
      const extend = (ev) => {
        if (Math.abs(ev.clientX - originX) < 3) return;
        const time = snapToGuide(timeAtClientX(ev.clientX));
        state.selection = {
          start: Math.max(0, Math.min(anchor, time)),
          end: Math.min(total(), Math.max(anchor, time)),
        };
        renderSelection();
      };
      const end = () => {
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
      window.addEventListener("pointerup", end);
      window.addEventListener("pointercancel", end);
      return;
    }

    // El tiempo se recalcula en cada movimiento en vez de congelar el rectángulo:
    // con el zoom puesto la vista se desplaza sola y el punto bajo el dedo cambia.
    const move = (ev) => seek(timeAtClientX(ev.clientX));
    move(event);
    el.tlRuler.addEventListener("pointermove", move);
    const up = () => {
      el.tlRuler.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointerup", up);
  });
"""

# -------------------------------------------------------------- editor.css --

CSS_AFTER = ".speed-field {"

CSS_BLOCK = """
/* El botón de cortar: la acción de edición del montaje, al lado del de reproducir.
   Se queda apagado mientras no haya una parte elegida. */
.transport .nav-btn-cut {
  border-color: var(--accent);
  color: var(--accent);
}
.transport .nav-btn-cut:hover:not(:disabled) { background: var(--accent-soft); }
.transport .nav-btn-cut:disabled { border-color: var(--border); color: var(--text-3); }
"""

# ---------------------------------------------------------------- README ----
# Las continuaciones de las listas del README van con tres espacios.

R_STEPS_OLD = """   ir al inicio y al final, saltos de ±5 y ±10 segundos, repetir el montaje sin
   parar, velocidad de 0,75× a 2× y una barra de posición arrastrable. El volumen
"""

R_STEPS_NEW = """   ir al inicio y al final, repetir el montaje sin parar, velocidad de 0,75× a 2×
   y una barra de posición arrastrable. Al lado está **✂ Cortar**, que saca del
   montaje la parte elegida y corre hacia atrás lo que venía después, así que no
   queda un hueco de silencio. El volumen
"""

R_KEYS_OLD = """   diez con <kbd>Mayús</kbd>), <kbd>Inicio</kbd> y <kbd>Fin</kbd>, <kbd>Supr</kbd>
   y <kbd>Ctrl</kbd>+<kbd>Z</kbd>.
"""

R_KEYS_NEW = """   diez con <kbd>Mayús</kbd>), <kbd>Inicio</kbd> y <kbd>Fin</kbd>, <kbd>Supr</kbd>
   (que corta la parte elegida) y <kbd>Ctrl</kbd>+<kbd>Z</kbd>.
"""

R_RULE_OLD = """   los bordes de lo elegido, así que lo que se oye y se ve es lo que va a salir. La línea de tiempo
"""

R_RULE_NEW = """   los bordes de lo elegido, así que lo que se oye y se ve es lo que va a salir. Arrastrando
   sobre la regla con <kbd>Mayús</kbd> se elige la parte ahí arriba, y sus bordes se pegan a la
   guía roja si pasan cerca. La línea de tiempo
"""

REPLACEMENTS = [
    (HTML, "editor.html · comentario de la fila", H_COMMENT_OLD, H_COMMENT_NEW),
    (HTML, "editor.html · botones de salto por Cortar", H_BUTTONS_OLD, H_BUTTONS_NEW),
    (HTML, "editor.html · ayuda del corte", H_HELP_OLD, H_HELP_NEW),
    (HTML, "editor.html · tecla Supr", H_KEYS_OLD, H_KEYS_NEW),
    (JS, "editor.js · registro de elementos", J_EL_OLD, J_EL_NEW),
    (JS, "editor.js · listeners", J_LISTENERS_OLD, J_LISTENERS_NEW),
    (JS, "editor.js · estado del transporte", J_TRANSPORT_OLD, J_TRANSPORT_NEW),
    (JS, "editor.js · comentario de nudge", J_NUDGE_OLD, J_NUDGE_NEW),
    (JS, "editor.js · corte y estado del botón", J_CUT_OLD, J_CUT_NEW),
    (JS, "editor.js · selección oculta", J_SEL_HIDE_OLD, J_SEL_HIDE_NEW),
    (JS, "editor.js · selección visible", J_SEL_END_OLD, J_SEL_END_NEW),
    (JS, "editor.js · tecla Supr", J_DELETE_OLD, J_DELETE_NEW),
    (JS, "editor.js · enganche a la guía", J_SNAP_OLD, J_SNAP_NEW),
    (JS, "editor.js · anclaje del arrastre", J_ANCHOR_OLD, J_ANCHOR_NEW),
    (JS, "editor.js · borde del arrastre", J_MOVE_OLD, J_MOVE_NEW),
    (JS, "editor.js · clic en la regla", J_RULER_CLICK_OLD, J_RULER_CLICK_NEW),
    (JS, "editor.js · Mayús en la regla", J_RULER_DOWN_OLD, J_RULER_DOWN_NEW),
    (README, "README · paso de escucha", R_STEPS_OLD, R_STEPS_NEW),
    (README, "README · teclas del editor", R_KEYS_OLD, R_KEYS_NEW),
    (README, "README · elegir sobre la regla", R_RULE_OLD, R_RULE_NEW),
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

    # El estilo del botón nuevo se inserta por línea, sin depender de la
    # indentación exacta del archivo.
    css_text, css_eol = read(CSS)
    lines = css_text.split(css_eol)
    hits = [i for i, line in enumerate(lines) if line.strip() == CSS_AFTER]
    if len(hits) != 1:
        print(f"ERROR: el anclaje del CSS aparece {len(hits)} veces")
        return 1
    block = CSS_BLOCK.replace("\n", css_eol).split(css_eol)
    lines[hits[0]:hits[0]] = block
    cache[CSS] = css_eol.join(lines)
    eols[CSS] = css_eol
    print("ok: editor.css · estilo del botón de cortar")

    for path, text in cache.items():
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        print(f"escrito: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
