#!/usr/bin/env python3
"""Quita el párrafo de ayuda que va debajo de la línea de tiempo.

El bloque explicaba los gestos y los atajos, pero ocupaba media pantalla y el usuario lo
ve de más. Los controles siguen explicados en el README.
"""

from pathlib import Path

ROOT = Path(__file__).parent
HTML = ROOT / "backend" / "static" / "editor.html"

OLD = """      <p class="muted small">
        <strong>Arrastra sobre la onda</strong> para elegir una parte de la música: queda sombreada
        y con la cajita de volumen encima, que se sube o se baja arrastrándola hacia arriba o hacia
        abajo (con <kbd>Mayús</kbd>, más fina; la rueda sobre ella también sirve). Un doble clic
        elige el tramo entero y <kbd>Esc</kbd> quita la selección. Con una parte ya
        elegida, <strong>✂ Cortar</strong> la saca del montaje y lo de detrás se corre hacia atrás
        para que no quede hueco: los dos lados se ven como una sola sección de música, sin
        cabecera ni raya en la junta, así que cortar no va troceando la línea de tiempo: quitarla
        con la ✕ o moverla desde su cabecera se lleva la sección entera.
        <kbd>Supr</kbd> hace lo mismo. Arrastrando sobre la regla con <kbd>Mayús</kbd> se elige la
        parte ahí arriba, y sus bordes se pegan a la guía roja cuando pasan cerca, que es la
        forma de clavar el corte en el instante exacto.
        Un clic suelto elige el tramo (la ✕ lo quita), sus bordes se arrastran para recortarlo y su
        cabecera se arrastra para cambiar el orden.
        <kbd>Espacio</kbd> reproduce o pausa, <kbd>←</kbd> <kbd>→</kbd> mueven un segundo
        (<kbd>Mayús</kbd> los hace diez), <kbd>Inicio</kbd> y <kbd>Fin</kbd> van a los extremos,
        <kbd>Supr</kbd> corta la parte elegida y <kbd>Ctrl</kbd>+<kbd>Z</kbd> deshace.
        La <strong>rueda</strong> sobre la línea de tiempo acerca y aleja en el punto que señalas
        (hasta ver un segundo en la pantalla) y <kbd>Mayús</kbd>+rueda (o el botón central,
        arrastrando) desplaza la vista. Si al arrastrar para elegir llegas al borde, la vista se
        corre sola para que puedas seguir eligiendo.
      </p>
"""


def main() -> int:
    with open(HTML, "rb") as fh:
        raw = fh.read()
    eol = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8")

    old = OLD.replace("\n", eol)
    found = text.count(old)
    if found == 0:
        print("ERROR: no se encontro el parrafo de ayuda")
        return 1
    if found > 1:
        print(f"ERROR: el parrafo aparece {found} veces")
        return 1

    with open(HTML, "w", encoding="utf-8", newline="") as fh:
        fh.write(text.replace(old, ""))
    print("parrafo de ayuda quitado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
