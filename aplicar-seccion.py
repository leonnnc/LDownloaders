#!/usr/bin/env python3
"""Mover y quitar van por sección, no por trozo de dentro.

Después de cortar, la línea de tiempo enseña una sola sección aunque por dentro sean dos
tramos. Como la sección tiene una sola cabecera, arrastrarla movía solo el primer trozo y
la sección se partía en dos; y la ✕ quitaba medio trozo. Ahora las dos cosas se llevan la
sección entera, que es lo que se ve.

Una sección es lo que ya se dibuja pegado: mismo audio, mismo volumen y sin fundido en la
junta. Los trozos con volúmenes distintos siguen siendo piezas aparte, y se mueven y se
quitan por separado, como es lógico.
"""

from pathlib import Path

ROOT = Path(__file__).parent
JS = ROOT / "backend" / "static" / "editor.js"
HTML = ROOT / "backend" / "static" / "editor.html"

J_SECTION_OLD = """  function renderTimeline() {
    const empty = state.clips.length === 0;
"""

J_SECTION_NEW = """  // Los límites de la sección a la que pertenece un tramo: hacia atrás y hacia adelante,
  // mientras la junta no signifique nada. Lo usan el mover y el quitar, para que las dos
  // cosas se lleven lo que se ve como una sola pieza.
  function sectionBounds(index) {
    let first = index;
    while (first > 0 && sameRun(state.clips[first - 1], state.clips[first])) first -= 1;
    let last = index;
    while (last < state.clips.length - 1 && sameRun(state.clips[last], state.clips[last + 1])) last += 1;
    return [first, last];
  }

  function renderTimeline() {
    const empty = state.clips.length === 0;
"""

J_REMOVE_OLD = """  function removeClip(clipId) {
    commit(() => {
      const index = state.clips.findIndex((clip) => clip.id === clipId);
      state.clips = state.clips.filter((clip) => clip.id !== clipId);
      // Quitar un tramo tampoco tiene por qué dejar dos bloques donde había uno.
      if (index !== -1) joinAround(index);
      if (state.selectedId === clipId) state.selectedId = null;
    });
"""

J_REMOVE_NEW = """  function removeClip(clipId) {
    commit(() => {
      const index = state.clips.findIndex((clip) => clip.id === clipId);
      if (index === -1) return;
      // Se va la sección entera: la ✕ que se ve es la de la sección, no la de un trozo
      // de dentro que no se ve.
      const [first, last] = sectionBounds(index);
      const gone = new Set(state.clips.slice(first, last + 1).map((clip) => clip.id));
      state.clips = state.clips.filter((clip) => !gone.has(clip.id));
      // Y los lados que quedan se vuelven a unir si eran el mismo audio.
      joinAround(first);
      if (state.selectedId && gone.has(state.selectedId)) state.selectedId = null;
    });
"""

J_MOVE_OLD = """  function moveClip(dragId, targetId, after) {
    const from = state.clips.findIndex((clip) => clip.id === dragId);
    const to = state.clips.findIndex((clip) => clip.id === targetId);
    if (from < 0 || to < 0 || from === to) return;
    commit(() => {
      const [moved] = state.clips.splice(from, 1);
      let index = state.clips.findIndex((clip) => clip.id === targetId);
      if (after) index += 1;
      state.clips.splice(index, 0, moved);
      state.selectedId = moved.id;
    });
  }
"""

J_MOVE_NEW = """  function moveClip(dragId, targetId, after) {
    const from = state.clips.findIndex((clip) => clip.id === dragId);
    const to = state.clips.findIndex((clip) => clip.id === targetId);
    if (from < 0 || to < 0 || from === to) return;

    // Se mueve la sección entera: si se moviera solo su primer trozo, la sección se
    // partiría en dos sin que nadie lo haya pedido.
    const [first, last] = sectionBounds(from);
    // Soltarla sobre sí misma no mueve nada.
    if (to >= first && to <= last) return;

    commit(() => {
      const moved = state.clips.splice(first, last - first + 1);
      let index = state.clips.findIndex((clip) => clip.id === targetId);
      if (index < 0) index = state.clips.length;
      if (after) index += 1;
      state.clips.splice(index, 0, ...moved);
      state.selectedId = moved[0].id;
    });
  }
"""

H_HELP_OLD = """        cabecera ni raya en la junta, así que cortar no va troceando la línea de tiempo.
"""

H_HELP_NEW = """        cabecera ni raya en la junta, así que cortar no va troceando la línea de tiempo: quitarla
        con la ✕ o moverla desde su cabecera se lleva la sección entera.
"""

REPLACEMENTS = [
    (JS, "editor.js · limites de la seccion", J_SECTION_OLD, J_SECTION_NEW),
    (JS, "editor.js · quitar la seccion entera", J_REMOVE_OLD, J_REMOVE_NEW),
    (JS, "editor.js · mover la seccion entera", J_MOVE_OLD, J_MOVE_NEW),
    (HTML, "editor.html · ayuda de la seccion", H_HELP_OLD, H_HELP_NEW),
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
