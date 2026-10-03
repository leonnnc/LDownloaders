#!/usr/bin/env python3
"""El corte deja de verse como dos bloques.

Por dentro siguen siendo dos tramos, y no puede ser de otra forma: entre los dos
está justo la parte que se quitó, y un tramo es un rango seguido del archivo. Lo
que cambia es cómo se dibujan: el trozo que sigue a otro del mismo audio se pega
a él, pierde su cabecera y sus etiquetas, y la junta deja de tener borde. Así,
después de cortar, la línea de tiempo se sigue viendo como una sola sección de
música.

Se dibuja pegado solo cuando unirlos tendría sentido: mismo audio, mismo
volumen y sin fundido en la junta. Si los dos lados acaban con volúmenes
distintos, se quedan separados, porque ahí la junta sí significa algo.

El fin de línea de cada archivo se detecta y se conserva tal cual.
"""

from pathlib import Path

ROOT = Path(__file__).parent
JS = ROOT / "backend" / "static" / "editor.js"
HTML = ROOT / "backend" / "static" / "editor.html"
CSS = ROOT / "backend" / "static" / "editor.css"
README = ROOT / "README.md"

# --------------------------------------------------------------- editor.js --

J_HELPER_OLD = """  function renderTimeline() {
    const empty = state.clips.length === 0;
"""

J_HELPER_NEW = """  /* ¿Estos dos tramos seguidos son el mismo trozo de música partido en dos? Lo son
     cuando salen del mismo audio, llevan el mismo volumen y no hay fundido en la
     junta: justo como queda un tramo después de cortarle una parte del medio. En ese
     caso el segundo se dibuja como continuación del primero, pegado y sin cabecera
     propia, para que el corte no parezca trocear la línea de tiempo. Unirlos por
     dentro no se puede: entre los dos está la parte que se quitó. */
  function sameRun(before, after) {
    return before.assetId === after.assetId
      && before.gainDb === after.gainDb
      && before.fadeOut === 0
      && after.fadeIn === 0;
  }

  function renderTimeline() {
    const empty = state.clips.length === 0;
"""

J_FOREACH_OLD = """    state.clips.forEach((clip) => {
      const asset = assetOf(clip.assetId);
      const width = Math.max(14, clipDuration(clip) * scale);

      const block = document.createElement("article");
      block.className = "tl-clip";
      block.dataset.id = clip.id;
      if (clip.id === state.selectedId) block.classList.add("selected");
"""

J_FOREACH_NEW = """    state.clips.forEach((clip, index) => {
      const asset = assetOf(clip.assetId);
      const width = Math.max(14, clipDuration(clip) * scale);
      // El que continúa a otro se pega a él; el que abre la sección, además, suelta
      // su borde derecho para que entre los dos no se vea ninguna raya.
      const following = index > 0 && sameRun(state.clips[index - 1], clip);
      const followed = index < state.clips.length - 1 && sameRun(clip, state.clips[index + 1]);

      const block = document.createElement("article");
      block.className = "tl-clip";
      block.dataset.id = clip.id;
      if (following) block.classList.add("tl-clip-cont");
      if (followed) block.classList.add("tl-clip-prev-cont");
      if (clip.id === state.selectedId) block.classList.add("selected");
"""

J_HEAD_OLD = """      const head = document.createElement("div");
      head.className = "tl-clip-head";
      // La cabecera es la que se arrastra para reordenar: la onda queda libre para
      // elegir una parte con el ratón, que es lo que se hace mucho más a menudo.
      head.draggable = true;
      const name = document.createElement("span");
      name.className = "tl-clip-name";
      name.textContent = asset ? asset.name : "audio";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "tl-clip-x";
      remove.textContent = "✕";
      remove.setAttribute("aria-label", "Quitar este tramo");
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        removeClip(clip.id);
      });
      head.append(name, remove);
"""

J_HEAD_NEW = """      const head = document.createElement("div");
      head.className = "tl-clip-head";
      if (following) {
        // Una continuación no repite el nombre ni lleva ✕: el nombre y el quitar son
        // los del tramo que abre la sección. La cabecera se deja vacía, pero se deja:
        // su alto es el que mantiene la onda en su sitio.
        head.setAttribute("aria-hidden", "true");
      } else {
        // La cabecera es la que se arrastra para reordenar: la onda queda libre para
        // elegir una parte con el ratón, que es lo que se hace mucho más a menudo.
        head.draggable = true;
        const name = document.createElement("span");
        name.className = "tl-clip-name";
        name.textContent = asset ? asset.name : "audio";
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "tl-clip-x";
        remove.textContent = "✕";
        remove.setAttribute("aria-label", "Quitar este tramo");
        remove.addEventListener("click", (event) => {
          event.stopPropagation();
          removeClip(clip.id);
        });
        head.append(name, remove);
      }
"""

J_FOOT_OLD = """      const foot = document.createElement("div");
      foot.className = "tl-clip-foot";
      for (const badge of clipBadges(clip)) {
        const span = document.createElement("span");
        span.className = `tl-badge${badge.css ? ` ${badge.css}` : ""}`;
        span.textContent = badge.text;
        foot.appendChild(span);
      }
      block.appendChild(foot);
"""

J_FOOT_NEW = """      const foot = document.createElement("div");
      foot.className = "tl-clip-foot";
      // En una continuación no se repiten las etiquetas: son las mismas que las del
      // tramo que abre la sección, y repetirlas marcaría justo la junta.
      if (!following) {
        for (const badge of clipBadges(clip)) {
          const span = document.createElement("span");
          span.className = `tl-badge${badge.css ? ` ${badge.css}` : ""}`;
          span.textContent = badge.text;
          foot.appendChild(span);
        }
      }
      block.appendChild(foot);
"""

# -------------------------------------------------------------- editor.css --

CSS_BEFORE = ".tl-clip.selected {"

CSS_BLOCK = """/* Después de cortar quedan dos tramos —entre ellos está justo lo que se quitó—,
   pero no se dibujan como dos: el que continúa al de al lado se pega a él, sin
   borde ni esquina por la junta, y su cabecera queda vacía. El alto se fija aquí
   para que el hueco mida lo mismo que una cabecera con su nombre y su ✕ (4+4 de
   relleno, 16 del botón y 1 del borde de abajo): así la onda no se desplaza. */
.tl-clip-cont {
  border-left: 0;
  border-top-left-radius: 0;
  border-bottom-left-radius: 0;
}
.tl-clip-prev-cont {
  border-right: 0;
  border-top-right-radius: 0;
  border-bottom-right-radius: 0;
}
.tl-clip-head { min-height: 25px; }
"""

# ------------------------------------------------------------- editor.html --

H_HELP_OLD = """        elegida, <strong>✂ Cortar</strong> la saca del montaje, lo de detrás se corre hacia atrás
        para que no quede hueco, y los dos lados vuelven a quedar pegados en un solo tramo
        cuando eran el mismo audio: cortar no trocea la línea de tiempo. <kbd>Supr</kbd> hace lo
        mismo. Arrastrando sobre la regla con <kbd>Mayús</kbd> se elige la parte ahí arriba, y sus
        bordes se pegan a la guía roja cuando pasan cerca, que es la forma de clavar el corte en
        el instante exacto.
"""

H_HELP_NEW = """        elegida, <strong>✂ Cortar</strong> la saca del montaje y lo de detrás se corre hacia atrás
        para que no quede hueco: los dos lados se ven como una sola sección de música, sin
        cabecera ni raya en la junta, así que cortar no va troceando la línea de tiempo.
        <kbd>Supr</kbd> hace lo mismo. Arrastrando sobre la regla con <kbd>Mayús</kbd> se elige la
        parte ahí arriba, y sus bordes se pegan a la guía roja cuando pasan cerca, que es la
        forma de clavar el corte en el instante exacto.
"""

# ---------------------------------------------------------------- README ----

R_STEPS_OLD = """   montaje la parte elegida, corre hacia atrás lo que venía después (sin hueco de
   silencio) y vuelve a pegar los dos lados en un solo tramo cuando eran el mismo
   audio, así que cortar no va troceando la línea de tiempo. El volumen
"""

R_STEPS_NEW = """   montaje la parte elegida y corre hacia atrás lo que venía después, sin hueco de
   silencio: los dos lados quedan pegados y se ven como una sola sección de música,
   así que cortar no va troceando la línea de tiempo. El volumen
"""

R_BULLET_OLD = """* **El corte busca dentro del archivo** (`-ss` en la entrada y `-t` para el tramo),
  así que recortar el minuto 50 no obliga a decodificar los 49 anteriores.
"""

R_BULLET_NEW = """* **El corte busca dentro del archivo** (`-ss` en la entrada y `-t` para el tramo),
  así que recortar el minuto 50 no obliga a decodificar los 49 anteriores.
* **Cortar no trocea la línea de tiempo, aunque por dentro sí la trocee**: entre los
  dos tramos está justo lo que se quitó, así que unirlos en uno solo devolvería el
  audio borrado. Lo que se hace es dibujar el segundo pegado al primero y sin
  cabecera propia, de modo que la sección se ve entera. Si los dos lados acaban con
  volúmenes distintos, se dibujan separados: ahí la junta sí significa algo.
"""

REPLACEMENTS = [
    (JS, "editor.js · regla de continuación", J_HELPER_OLD, J_HELPER_NEW),
    (JS, "editor.js · clases de la junta", J_FOREACH_OLD, J_FOREACH_NEW),
    (JS, "editor.js · cabecera de la continuación", J_HEAD_OLD, J_HEAD_NEW),
    (JS, "editor.js · etiquetas de la continuación", J_FOOT_OLD, J_FOOT_NEW),
    (HTML, "editor.html · ayuda del corte", H_HELP_OLD, H_HELP_NEW),
    (README, "README · paso de escucha", R_STEPS_OLD, R_STEPS_NEW),
    (README, "README · decisión del corte", R_BULLET_OLD, R_BULLET_NEW),
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

    # El CSS entra por línea, sin depender de la indentación exacta del archivo.
    css_text, css_eol = read(CSS)
    lines = css_text.split(css_eol)
    hits = [i for i, line in enumerate(lines) if line.strip() == CSS_BEFORE]
    if len(hits) != 1:
        print(f"ERROR: el anclaje del CSS aparece {len(hits)} veces")
        return 1
    block = CSS_BLOCK.replace("\n", css_eol).split(css_eol)
    lines[hits[0]:hits[0]] = block
    cache[CSS] = css_eol.join(lines)
    eols[CSS] = css_eol
    print("ok: editor.css · junta sin raya")

    for path, text in cache.items():
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        print(f"escrito: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
