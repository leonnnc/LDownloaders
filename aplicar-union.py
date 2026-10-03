#!/usr/bin/env python3
"""Que el corte no trocee la línea de tiempo.

Al cortar, los dos trozos que quedan a los lados eran uno solo antes: se vuelven
a unir en un único tramo, así que tras el corte la línea de tiempo sigue siendo
la misma sección de música y no un bloque de más por cada corte.

Solo se pegan cuando unirlos no cambia lo que suena: el mismo audio, contiguos
dentro del archivo, el mismo volumen y sin fundidos en la junta (que es justo
como los deja el corte). Si el volumen de cada lado es distinto, se quedan
separados, porque unirlos cambiaría lo que suena.

Lo mismo se aplica al quitar un tramo entero (la ✕ o Supr), que tampoco tiene
por qué dejar dos bloques donde había uno.

El fin de línea de cada archivo se detecta y se conserva tal cual.
"""

from pathlib import Path

ROOT = Path(__file__).parent
JS = ROOT / "backend" / "static" / "editor.js"
HTML = ROOT / "backend" / "static" / "editor.html"
README = ROOT / "README.md"

# --------------------------------------------------------------- editor.js --

J_CUT_OLD = """    commit(() => {
      const pieces = selectionPieces(range);
      const gone = new Set(pieces.map((clip) => clip.id));
      state.clips = state.clips.filter((clip) => !gone.has(clip.id));
      state.selectedId = null;
    });

    keepSelectionValid();
    seek(at);
  }

  // El botón de cortar solo se puede pulsar cuando hay una parte elegida: sin
"""

J_CUT_NEW = """    commit(() => {
      const pieces = selectionPieces(range);
      const gone = new Set(pieces.map((clip) => clip.id));
      // Dónde queda el hueco: los trozos de dentro van seguidos, así que el primero
      // marca el sitio exacto donde van a quedar pegados los dos lados.
      const hole = state.clips.findIndex((clip) => gone.has(clip.id));
      state.clips = state.clips.filter((clip) => !gone.has(clip.id));
      joinAround(hole);
      state.selectedId = null;
    });

    keepSelectionValid();
    seek(at);
  }

  /* Los dos lados del corte eran un solo tramo antes de cortar, así que se vuelven a
     unir: si no, cada corte dejaría un bloque de más y la línea de tiempo acabaría
     llena de trozos. Solo se pegan cuando unirlos no cambia lo que suena: el mismo
     audio, contiguos dentro del archivo, el mismo volumen y sin fundidos en la junta
     (que es justo como los deja el corte). */
  function joinAround(index) {
    const before = state.clips[index - 1];
    const after = state.clips[index];
    if (!before || !after) return;
    if (before.assetId !== after.assetId) return;
    if (before.gainDb !== after.gainDb) return;
    if (before.fadeOut !== 0 || after.fadeIn !== 0) return;
    if (Math.abs(after.start - before.end) > MIN_SEL) return;

    // El de delante se queda con todo: el fundido de entrada era suyo, y el de
    // salida pasa a ser el que traía el trozo de detrás.
    before.end = after.end;
    before.fadeOut = after.fadeOut;
    state.clips.splice(index, 1);
  }

  // El botón de cortar solo se puede pulsar cuando hay una parte elegida: sin
"""

J_REMOVE_OLD = """  function removeClip(clipId) {
    commit(() => {
      state.clips = state.clips.filter((clip) => clip.id !== clipId);
      if (state.selectedId === clipId) state.selectedId = null;
    });
"""

J_REMOVE_NEW = """  function removeClip(clipId) {
    commit(() => {
      const index = state.clips.findIndex((clip) => clip.id === clipId);
      state.clips = state.clips.filter((clip) => clip.id !== clipId);
      // Quitar un tramo tampoco tiene por qué dejar dos bloques donde había uno.
      if (index !== -1) joinAround(index);
      if (state.selectedId === clipId) state.selectedId = null;
    });
"""

# ------------------------------------------------------------- editor.html --

H_HELP_OLD = """        elige el tramo entero y <kbd>Esc</kbd> quita la selección. Con una parte ya
        elegida, <strong>✂ Cortar</strong> la saca del montaje y los tramos de detrás se corren
        hacia atrás para que no quede hueco; <kbd>Supr</kbd> hace lo mismo. Arrastrando sobre la
        regla con <kbd>Mayús</kbd> se elige la parte ahí arriba, y sus bordes se pegan a la guía
        roja cuando pasan cerca, que es la forma de clavar el corte en el instante exacto.
"""

H_HELP_NEW = """        elige el tramo entero y <kbd>Esc</kbd> quita la selección. Con una parte ya
        elegida, <strong>✂ Cortar</strong> la saca del montaje, lo de detrás se corre hacia atrás
        para que no quede hueco, y los dos lados vuelven a quedar pegados en un solo tramo
        cuando eran el mismo audio: cortar no trocea la línea de tiempo. <kbd>Supr</kbd> hace lo
        mismo. Arrastrando sobre la regla con <kbd>Mayús</kbd> se elige la parte ahí arriba, y sus
        bordes se pegan a la guía roja cuando pasan cerca, que es la forma de clavar el corte en
        el instante exacto.
"""

# ---------------------------------------------------------------- README ----

R_STEPS_OLD = """   y una barra de posición arrastrable. Al lado está **✂ Cortar**, que saca del
   montaje la parte elegida y corre hacia atrás lo que venía después, así que no
   queda un hueco de silencio. El volumen
"""

R_STEPS_NEW = """   y una barra de posición arrastrable. Al lado está **✂ Cortar**, que saca del
   montaje la parte elegida, corre hacia atrás lo que venía después (sin hueco de
   silencio) y vuelve a pegar los dos lados en un solo tramo cuando eran el mismo
   audio, así que cortar no va troceando la línea de tiempo. El volumen
"""

REPLACEMENTS = [
    (JS, "editor.js · el corte vuelve a unir", J_CUT_OLD, J_CUT_NEW),
    (JS, "editor.js · quitar un tramo también une", J_REMOVE_OLD, J_REMOVE_NEW),
    (HTML, "editor.html · ayuda de la unión", H_HELP_OLD, H_HELP_NEW),
    (README, "README · paso de escucha", R_STEPS_OLD, R_STEPS_NEW),
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
