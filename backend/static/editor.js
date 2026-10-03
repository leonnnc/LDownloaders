/* =========================================================
   Editor de audio — lógica del cliente.

   El montaje (qué tramo de qué audio, en qué orden, con qué volumen y qué
   fundidos) vive aquí, en el navegador, y no se manda al servidor hasta que se
   exporta. Editar, por tanto, es instantáneo: mover un tirador no toca la red.

   La escucha es real y no una maqueta: cada tramo se reproduce con su volumen y
   sus fundidos aplicados en vivo con Web Audio, en el mismo orden del montaje,
   así que lo que se oye es lo que va a salir.
   ========================================================= */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const el = {
    error: $("editor-error"),
    warn: $("editor-warn"),
    loading: $("editor-loading"),
    sourcesCard: $("sources-card"),
    limits: $("limits-note"),
    fileInput: $("file-input"),
    btnRecent: $("btn-recent"),
    recentBox: $("recent-box"),
    recentList: $("recent-list"),
    btnRecentClose: $("btn-recent-close"),
    uploadProgress: $("upload-progress"),
    uploadBar: $("upload-bar"),
    uploadText: $("upload-text"),
    assetList: $("asset-list"),
    assetsEmpty: $("assets-empty"),
    timelineCard: $("timeline-card"),
    timeline: $("timeline"),
    tlScroll: $("tl-scroll"),
    tlRuler: $("tl-ruler"),
    tlClips: $("tl-clips"),
    tlPlayhead: $("tl-playhead"),
    tlGrab: $("tl-grab"),
    tlTime: $("tl-time"),
    tlEmpty: $("tl-empty"),
    tlEmptyText: $("tl-empty-text"),
    btnEmptyAdd: $("btn-empty-add"),
    btnPlay: $("btn-play"),
    btnStart: $("btn-start"),
    btnEnd: $("btn-end"),
    btnCut: $("btn-cut"),
    btnLoop: $("btn-loop"),
    speed: $("speed"),
    scrub: $("scrub"),
    tlSelect: $("tl-select"),
    tlGain: $("tl-gain"),
    tlGainValue: $("tl-gain-value"),
    tlGainHint: $("tl-gain-hint"),
    exportCard: $("export-card"),
    outName: $("out-name"),
    outFormat: $("out-format"),
    bitrateWrap: $("bitrate-wrap"),
    outBitrate: $("out-bitrate"),
    btnExport: $("btn-export"),
    exportNote: $("export-note"),
    btnClear: $("btn-clear"),
    exportProgress: $("export-progress"),
    exportBar: $("export-bar"),
    exportText: $("export-text"),
    exportLink: $("export-link"),
    dropOverlay: $("drop-overlay"),
  };

  const state = {
    info: null,        // límites y formatos que ofrece el servidor
    assets: [],        // audios cargados
    clips: [],         // montaje en orden: [{id, assetId, start, end, gainDb, fadeIn, fadeOut}]
    selectedId: null,
    cursor: 0,         // posición del cursor, en segundos del montaje
    playing: false,
    loop: false,       // repetir el montaje al llegar al final
    rate: 1,           // velocidad de reproducción
    zoom: 1,           // ampliación de la línea de tiempo respecto a «ajustar»
    draggingCursor: false, // el usuario arrastra la línea con el ratón
    selection: null,   // parte elegida arrastrando sobre la onda: {start, end} del montaje
    gainGesture: null, // el ajuste de volumen en curso sobre esa parte
    scrubbing: false,  // el usuario está arrastrando la barra de posición
    anchorWall: 0,     // reloj del sistema al empezar a reproducir
    anchorCursor: 0,   // punto del montaje donde empezó esa reproducción
    peaks: new Map(),  // assetId -> {per_second, peaks}
    undo: [],
    redo: [],
    dragId: null,
    gestureBefore: null,
  };

  let rafId = null;
  let pollId = null;

  /* ---------------- utilidades ---------------- */

  function fmtTime(seconds) {
    const total = Math.max(0, Math.round(seconds || 0));
    const s = total % 60;
    const m = Math.floor(total / 60) % 60;
    const h = Math.floor(total / 3600);
    const pad = (v) => String(v).padStart(2, "0");
    return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
  }

  function fmtSize(bytes) {
    if (!bytes) return "—";
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    let n = bytes;
    while (n >= 1024 && i < units.length - 1) {
      n /= 1024;
      i++;
    }
    return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
  }

  function dbToGain(db) {
    return db <= -60 ? 0 : Math.pow(10, db / 20);
  }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function showError(message) {
    el.error.textContent = message;
    el.error.classList.remove("hidden");
  }

  function clearError() {
    el.error.classList.add("hidden");
    el.error.textContent = "";
  }

  function uid() {
    return Math.random().toString(36).slice(2, 10);
  }

  /* ---------------- servidor ---------------- */

  // Misma clave que el panel (/monitor): el token se escribe una vez en el
  // panel y el editor lo hereda, porque las dos páginas comparten origen.
  const TOKEN_KEY = "vdl_admin_token";

  function authHeaders() {
    try {
      const value = localStorage.getItem(TOKEN_KEY);
      return value ? { "X-Admin-Token": value } : {};
    } catch (err) {
      return {};
    }
  }

  const API = {
    async request(path, options = {}) {
      const res = await fetch(path, {
        ...options,
        headers: { ...authHeaders(), ...(options.headers || {}) },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
      return data;
    },
    get(path) {
      return API.request(path);
    },
    post(path, body) {
      return API.request(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    },
    del(path) {
      return API.request(path, { method: "DELETE" });
    },
    // Subida con progreso: con fetch no hay forma de saber cuánto va.
    upload(file, onProgress) {
      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        const query = `?name=${encodeURIComponent(file.name || "audio")}`;
        xhr.open("POST", `/api/audio/upload${query}`);
        xhr.setRequestHeader("Content-Type", "application/octet-stream");
        const adminToken = authHeaders()["X-Admin-Token"];
        if (adminToken) xhr.setRequestHeader("X-Admin-Token", adminToken);
        xhr.upload.addEventListener("progress", (event) => {
          if (event.lengthComputable) onProgress(event.loaded / event.total);
        });
        xhr.addEventListener("load", () => {
          let body = {};
          try {
            body = JSON.parse(xhr.responseText);
          } catch (err) {
            body = {};
          }
          if (xhr.status >= 200 && xhr.status < 300) resolve(body);
          else reject(new Error(body.detail || `Error ${xhr.status}`));
        });
        xhr.addEventListener("error", () => reject(new Error("Se cortó la subida.")));
        xhr.send(file);
      });
    },
  };

  /* ---------------- deshacer / rehacer ----------------
     Se guarda una copia del montaje antes de cada cambio. Con listas pequeñas
     (decenas de tramos) copiar es más simple y más seguro que ir registrando
     operaciones inversas una por una. */

  function snapshot() {
    return JSON.parse(JSON.stringify(state.clips));
  }

  function pushHistory(before) {
    state.undo.push(before || snapshot());
    if (state.undo.length > 60) state.undo.shift();
    state.redo.length = 0;
  }

  function commit(mutator) {
    const before = snapshot();
    mutator();
    pushHistory(before);
    refresh();
  }

  function undo() {
    if (!state.undo.length) return;
    state.redo.push(snapshot());
    state.clips = state.undo.pop();
    keepSelectionValid();
    refresh();
  }

  function redo() {
    if (!state.redo.length) return;
    state.undo.push(snapshot());
    state.clips = state.redo.pop();
    keepSelectionValid();
    refresh();
  }

  function keepSelectionValid() {
    if (!state.clips.some((clip) => clip.id === state.selectedId)) {
      state.selectedId = state.clips.length ? state.clips[state.clips.length - 1].id : null;
    }
    state.cursor = Math.min(state.cursor, total());
  }

  /* ---------------- medidas del montaje ---------------- */

  function assetOf(assetId) {
    return state.assets.find((item) => item.id === assetId) || null;
  }

  function clipOf(clipId) {
    return state.clips.find((clip) => clip.id === clipId) || null;
  }

  function clipDuration(clip) {
    return Math.max(0, (clip.end ?? 0) - clip.start);
  }

  function total() {
    return state.clips.reduce((sum, clip) => sum + clipDuration(clip), 0);
  }

  // Dónde cae un instante del montaje: qué tramo y con cuánto retraso dentro de él.
  function locate(time) {
    let acc = 0;
    for (const clip of state.clips) {
      const duration = clipDuration(clip);
      if (time < acc + duration || (time === acc && duration === 0)) {
        return { clip, offset: Math.max(0, time - acc) };
      }
      acc += duration;
    }
    return null;
  }

  /* ---------------- escala y zoom de la línea de tiempo ----------------
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

  function fitScale() {
    const width = el.tlScroll.clientWidth || 900;
    const length = total();
    if (!length) return 12;
    return (width - 2) / length;
  }

  function maxScale() {
    const length = total();
    if (!length) return fitScale();
    const view = el.tlScroll.clientWidth || 900;
    // El tope útil es ver un segundo en la vista; los demás solo evitan dispares.
    return Math.max(
      fitScale(),
      Math.min(ZOOM_MAX_PPS, view / ZOOM_MIN_SPAN, ZOOM_MAX_WIDTH / length)
    );
  }

  function maxZoom() {
    const base = fitScale();
    if (!base) return 1;
    return Math.max(1, maxScale() / base);
  }

  function pixelsPerSecond() {
    const base = fitScale();
    if (!total()) return base;
    return Math.min(maxScale(), Math.max(base, base * state.zoom));
  }

  // Capturar el puntero deja el arrastre pegado aunque el ratón salga del elemento.
  // Va protegido: con un puntero que el navegador no reconoce lanza excepción, y sin
  // esto el arrastre ni siquiera empezaría.
  function capturePointer(element, pointerId) {
    try {
      element.setPointerCapture(pointerId);
    } catch (err) {
      // Sin captura el arrastre sigue funcionando con los eventos de window.
    }
  }

  // Instante del montaje que cae bajo un punto de la pantalla. Se mide contra el
  // contenido ya desplazado: con el zoom puesto, el montaje no cabe entero y el
  // rectángulo del contenedor se queda corto, así que hay que sumar lo desplazado.
  function timeAtClientX(clientX) {
    const rect = el.tlScroll.getBoundingClientRect();
    return Math.max(0, (clientX - rect.left + el.tlScroll.scrollLeft) / pixelsPerSecond());
  }

  // Cambia la escala sin perder de vista el punto que se está mirando: el instante
  // ancla se queda donde estaba (bajo el ratón, bajo el cursor o en el centro).
  function setZoom(next, anchorTime, anchorX) {
    const before = pixelsPerSecond();
    const view = el.tlScroll.clientWidth || 0;
    const time = anchorTime == null ? (el.tlScroll.scrollLeft + view / 2) / before : anchorTime;
    const at = anchorX == null ? view / 2 : anchorX;

    state.zoom = Math.min(maxZoom(), Math.max(1, next));
    const after = pixelsPerSecond();
    updateTransport();
    if (Math.abs(after - before) < 0.0001) return;

    renderTimeline();
    el.tlScroll.scrollLeft = Math.max(0, time * after - at);
  }

  function zoomBy(factor, anchorTime, anchorX) {
    setZoom(state.zoom * factor, anchorTime, anchorX);
  }

  // Con el montaje ampliado el cursor puede salirse por los lados: se desplaza la
  // vista lo justo para no perderlo. Si el montaje entero cabe, no hay nada que hacer.
  function keepCursorVisible(x) {
    if (state.zoom <= 1.001) return;
    if (state.draggingCursor) return; // mientras se arrastra la línea manda el ratón
    const view = el.tlScroll.clientWidth || 0;
    if (!view) return;
    const margin = view * 0.15;
    const left = el.tlScroll.scrollLeft;
    if (x < left + margin || x > left + view - margin) {
      el.tlScroll.scrollLeft = Math.max(0, x - view / 3);
    }
  }

  /* ---------------- forma de onda ---------------- */

  /* Ancho máximo del lienzo de una onda. Con el zoom a tope un tramo puede medir
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
    canvas.style.height = `${h}px`;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h };
  }

  function drawPeaks(canvas, peaks, perSecond, fromTime, toTime, opts) {
    const options = opts || {};
    const { ctx, w, h } = fitCanvas(canvas, options.width, options.height);
    ctx.clearRect(0, 0, w, h);

    const center = h / 2;
    if (!peaks || !peaks.length || toTime <= fromTime) return;

    ctx.strokeStyle = options.color || cssVar("--accent");
    ctx.globalAlpha = options.alpha == null ? 1 : options.alpha;
    ctx.lineWidth = 1;
    ctx.beginPath();

    const span = toTime - fromTime;
    for (let x = 0; x < w; x++) {
      const t0 = fromTime + (x / w) * span;
      const t1 = fromTime + ((x + 1) / w) * span;
      let i0 = Math.floor(t0 * perSecond);
      let i1 = Math.ceil(t1 * perSecond) - 1;
      if (i0 < 0) i0 = 0;
      if (i1 >= peaks.length) i1 = peaks.length - 1;
      if (i1 < i0) continue;

      // Con audio muy largo pueden caer cientos de puntos en un píxel: se
      // muestrean para que dibujar siga siendo instantáneo.
      const step = Math.max(1, Math.ceil((i1 - i0 + 1) / 40));
      let low = 1;
      let high = -1;
      for (let i = i0; i <= i1; i += step) {
        const pair = peaks[i];
        if (!pair) continue;
        if (pair[0] < low) low = pair[0];
        if (pair[1] > high) high = pair[1];
      }
      if (high < low) continue;

      const y1 = center - high * center * 0.94;
      const y2 = center - low * center * 0.94;
      ctx.moveTo(x + 0.5, y1);
      ctx.lineTo(x + 0.5, Math.max(y2, y1 + 0.6));
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  async function peaksFor(assetId) {
    if (state.peaks.has(assetId)) return state.peaks.get(assetId);
    const data = await API.get(`/api/audio/asset/${assetId}/peaks`);
    state.peaks.set(assetId, data);
    return data;
  }

  /* ---------------- audios de trabajo ---------------- */

  function renderAssets() {
    el.assetList.innerHTML = "";
    el.assetsEmpty.classList.toggle("hidden", state.assets.length > 0);

    for (const asset of state.assets) {
      const card = document.createElement("div");
      card.className = "asset-card";

      const info = document.createElement("div");
      info.className = "asset-info";
      const name = document.createElement("p");
      name.className = "asset-name";
      name.textContent = asset.name;
      name.title = asset.name;
      const meta = document.createElement("p");
      meta.className = "asset-meta";
      meta.textContent = [
        fmtTime(asset.duration),
        fmtSize(asset.size),
        asset.codec ? String(asset.codec).toUpperCase() : null,
        asset.channels === 1 ? "mono" : asset.channels === 2 ? "estéreo" : null,
      ]
        .filter(Boolean)
        .join("  ·  ");
      info.append(name, meta);

      const actions = document.createElement("div");
      actions.className = "asset-actions";

      const add = document.createElement("button");
      add.type = "button";
      add.className = "btn btn-mini";
      add.textContent = "A la línea";
      add.addEventListener("click", () => addClip(asset.id));

      const listen = document.createElement("button");
      listen.type = "button";
      listen.className = "btn btn-mini";
      listen.textContent = "Escuchar";
      listen.addEventListener("click", () => previewAsset(asset.id, listen));

      const del = document.createElement("button");
      del.type = "button";
      del.className = "btn btn-mini";
      del.textContent = "Borrar";
      del.addEventListener("click", () => deleteAsset(asset));

      actions.append(add, listen, del);
      card.append(info, actions);
      el.assetList.appendChild(card);
    }
    if (!state.clips.length) syncEmptyState();
  }

  async function loadSources() {
    const [info] = await Promise.all([API.get("/api/audio/info")]);
    state.info = info;
    state.assets = info.assets || [];

    el.limits.textContent = `máx ${info.max_upload_mb} MB · hasta ${info.max_minutes} min · ${info.assets_count}/${info.max_assets} cargados`;
    el.outFormat.innerHTML = "";
    for (const format of info.formats || []) {
      const option = document.createElement("option");
      option.value = format.id;
      option.textContent = format.label;
      option.dataset.bitrate = format.bitrate ? "1" : "";
      el.outFormat.appendChild(option);
    }
    if (!(info.formats || []).length) {
      el.btnExport.disabled = true;
      el.exportNote.textContent = "Este FFmpeg no puede generar ningún formato.";
    }
    syncFormatFields();
    renderAssets();
  }

  async function uploadFiles(files) {
    const list = Array.from(files || []);
    if (!list.length) return;

    el.uploadProgress.classList.remove("hidden");
    clearError();

    for (const file of list) {
      el.uploadBar.style.width = "0%";
      el.uploadText.textContent = `Subiendo ${file.name}…`;
      try {
        const asset = await API.upload(file, (ratio) => {
          el.uploadBar.style.width = `${Math.round(ratio * 100)}%`;
        });
        state.assets.unshift(asset);
        await peaksFor(asset.id);
        renderAssets();
        el.uploadText.textContent = `${asset.name} listo (${fmtTime(asset.duration)}).`;
      } catch (err) {
        el.uploadText.textContent = "";
        showError(`${file.name}: ${err.message}`);
      }
    }

    el.uploadProgress.classList.add("hidden");
  }

  async function loadRecent() {
    el.recentList.innerHTML = "";
    el.recentBox.classList.remove("hidden");
    try {
      const data = await API.get("/api/audio/sources");
      if (!(data.items || []).length) {
        const empty = document.createElement("p");
        empty.className = "muted small";
        empty.textContent = "No hay descargas recientes en MP3 (duran 15 minutos).";
        el.recentList.appendChild(empty);
        return;
      }
      for (const item of data.items) {
        const row = document.createElement("div");
        row.className = "recent-row";
        const label = document.createElement("span");
        label.textContent = `${item.title} · ${fmtSize(item.filesize)}`;
        const bring = document.createElement("button");
        bring.type = "button";
        bring.className = "btn btn-mini";
        bring.textContent = "Traer al editor";
        bring.addEventListener("click", async () => {
          bring.disabled = true;
          bring.textContent = "Trayendo…";
          try {
            const asset = await API.post(`/api/audio/from-job/${item.job_id}`);
            state.assets.unshift(asset);
            await peaksFor(asset.id);
            renderAssets();
            el.recentBox.classList.add("hidden");
          } catch (err) {
            showError(err.message);
            bring.disabled = false;
            bring.textContent = "Traer al editor";
          }
        });
        row.append(label, bring);
        el.recentList.appendChild(row);
      }
    } catch (err) {
      showError(err.message);
    }
  }

  async function deleteAsset(asset) {
    if (!window.confirm(`¿Borrar «${asset.name}» del servidor?`)) return;
    try {
      await API.del(`/api/audio/asset/${asset.id}`);
      state.assets = state.assets.filter((item) => item.id !== asset.id);
      state.peaks.delete(asset.id);
      commit(() => {
        state.clips = state.clips.filter((clip) => clip.assetId !== asset.id);
      });
      renderAssets();
    } catch (err) {
      showError(err.message);
    }
  }

  /* ---------------- montaje ---------------- */

  function addClip(assetId) {
    const asset = assetOf(assetId);
    if (!asset) return;
    if (state.clips.length >= (state.info?.max_clips || 60)) {
      showError(`El montaje admite ${state.info.max_clips} tramos como máximo.`);
      return;
    }
    commit(() => {
      const clip = {
        id: uid(),
        assetId,
        start: 0,
        end: asset.duration,
        gainDb: 0,
        fadeIn: 0,
        fadeOut: 0,
      };
      state.clips.push(clip);
      state.selectedId = clip.id;
    });
  }

  function removeClip(clipId) {
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
    keepSelectionValid();
    refresh();
  }

  /* ---------------- selección sobre la onda ----------------
     Arrastrar sobre la onda elige un trozo de música. Para darle un volumen no hace
     falta ninguna estructura nueva: el montaje ya son tramos con su ganancia, así que
     la parte elegida se «cuece» partiendo el montaje justo en sus bordes y ajustando
     los tramos que quedan dentro. Lo que se ve es lo que va a salir al exportar. */

  const MIN_SEL = 0.05; // segundos: una parte más corta que esto no es nada

  // Parte en dos el tramo que contiene ese instante del montaje. Si el instante ya cae
  // en un corte, no hace nada. Los fundidos se reparten: el trozo de delante se queda
  // con el de entrada y el de detrás con el de salida.
  function splitAt(time) {
    const hit = locate(time);
    if (!hit) return false;
    const { clip, offset } = hit;
    if (offset < MIN_SEL || offset > clipDuration(clip) - MIN_SEL) return false;

    const at = clip.start + offset;
    const second = { ...clip, id: uid(), start: at, fadeIn: 0 };
    clip.end = at;
    clip.fadeOut = 0;
    const index = state.clips.findIndex((item) => item.id === clip.id);
    state.clips.splice(index + 1, 0, second);
    return true;
  }

  // Los tramos que tocan ese rango, sin partir nada (para contar cuántos hará falta).
  function clipsInRange(range) {
    let acc = 0;
    const found = [];
    for (const clip of state.clips) {
      const from = acc;
      const to = acc + clipDuration(clip);
      acc = to;
      if (to > range.start + MIN_SEL && from < range.end - MIN_SEL) found.push(clip);
    }
    return found;
  }

  // Los tramos que quedan dentro del rango, partiéndolo si hace falta. Es el paso que
  // convierte «esta parte suena más bajo» en algo que FFmpeg puede escribir.
  function selectionPieces(range) {
    splitAt(range.start);
    splitAt(range.end);

    let acc = 0;
    const pieces = [];
    for (const clip of state.clips) {
      const from = acc;
      acc += clipDuration(clip);
      if (from >= range.start - MIN_SEL && acc <= range.end + MIN_SEL) pieces.push(clip);
    }
    return pieces;
  }

  // Dónde cae un tramo completo en el montaje (lo usa el doble clic).
  function clipRange(clipId) {
    let acc = 0;
    for (const clip of state.clips) {
      const from = acc;
      acc += clipDuration(clip);
      if (clip.id === clipId) return { start: from, end: acc };
    }
    return null;
  }

  /* ---------------- cortar la parte elegida ----------------
     Es lo contrario de añadir: lo sombreado desaparece del montaje y los tramos de
     detrás se corren hacia atrás, así que no queda un hueco de silencio. Se apoya en
     lo mismo que el volumen de la parte elegida —partir el montaje justo por sus
     bordes y quedarse con los trozos de dentro—, solo que esos trozos se van. */

  function cutSelection() {
    const range = state.selection;
    if (!range || !state.clips.length) return;
    if (range.end - range.start < MIN_SEL) return;

    // La selección se suelta antes de repintar: eso ya no está en el montaje, y
    // dejar el sombreado un instante daría a entender lo contrario.
    state.selection = null;

    commit(() => {
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
    // El cursor se queda donde estaba: cortar no tiene por qué plantar la guía roja en
    // la junta recién hecha, que parece una marca del corte y no lo es. Se llama a seek
    // con su propia posición para que, si el montaje se quedó más corto, el cursor se
    // recoja en vez de quedarse fuera de la línea.
    seek(state.cursor);
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
  // selección no hay nada que cortar, y dejarlo activo daría a entender que sí.
  function updateCutButton() {
    const range = state.selection;
    el.btnCut.disabled = !range || range.end - range.start < MIN_SEL || !state.clips.length;
  }

  function refresh() {
    renderTimeline();
    updateTransport();
  }

  /* ---------------- línea de tiempo ---------------- */

  function clipBadges(clip) {
    const badges = [];
    if (clip.gainDb <= -60) badges.push({ text: "silencio", css: "mute" });
    else if (Math.abs(clip.gainDb) >= 0.5) badges.push({ text: `${clip.gainDb > 0 ? "+" : ""}${clip.gainDb} dB`, css: "gain" });
    if (clip.fadeIn > 0) badges.push({ text: `entra ${clip.fadeIn}s` });
    if (clip.fadeOut > 0) badges.push({ text: `sale ${clip.fadeOut}s` });
    return badges;
  }

  // La línea vacía es el único momento en que no hay nada que hacer con el ratón: en
  // vez de dejar los controles apagados y una nota diminuta, se dice qué falta y se
  // ofrece el paso siguiente —traer el audio ya cargado— con un botón de verdad.
  function syncEmptyState() {
    const asset = state.assets[0] || null;
    el.tlEmptyText.textContent = asset
      ? `«${asset.name}» está cargado y todavía no está en la línea de tiempo.`
      : "Todavía no hay ningún audio cargado.";
    el.btnEmptyAdd.textContent = asset ? "Traerlo a la línea" : "Ir a cargar un audio";
  }

  /* ¿Estos dos tramos seguidos son el mismo trozo de música partido en dos? Lo son
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

  // Los límites de la sección a la que pertenece un tramo: hacia atrás y hacia adelante,
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
    if (empty) {
      state.zoom = 1; // sin montaje no hay nada que ampliar
      state.selection = null;
    }
    el.tlEmpty.classList.toggle("hidden", !empty);
    if (empty) syncEmptyState();
    el.tlClips.innerHTML = "";

    const scale = pixelsPerSecond();
    const length = total();
    el.tlRuler.style.width = `${Math.max(1, length * scale)}px`;
    el.tlClips.style.width = `${Math.max(1, length * scale)}px`;
    renderRuler(length, scale);

    state.clips.forEach((clip, index) => {
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
      if (clip.gainDb <= -60) block.classList.add("muted");
      block.style.width = `${width}px`;
      block.title = `${asset ? asset.name : "audio"} · ${fmtTime(clip.start)} → ${fmtTime(clip.end)}`;

      const head = document.createElement("div");
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

      const canvas = document.createElement("canvas");
      canvas.className = "tl-clip-wave";
      block.append(head, canvas);

      // Tiradores: el tramo se recorta arrastrando sus bordes, aquí mismo. Van solo en
      // los bordes de la sección: en una junta interior —el trozo que continúa a otro
      // del mismo audio— no, porque ahí no hay un extremo que recortar, y además el
      // tirador se comía el arrastre con el que se iba a elegir la parte siguiente
      // justo en el punto donde se acaba de cortar.
      for (const edge of ["start", "end"]) {
        if (edge === "start" && following) continue;
        if (edge === "end" && followed) continue;
        const handle = document.createElement("span");
        handle.className = `tl-clip-handle ${edge}`;
        handle.setAttribute("role", "separator");
        handle.setAttribute("aria-label", edge === "start"
          ? "Recortar el inicio del tramo"
          : "Recortar el final del tramo");
        handle.addEventListener("pointerdown", (event) => beginTrim(event, clip.id, edge));
        handle.addEventListener("dragstart", (event) => event.preventDefault());
        block.appendChild(handle);
      }

      const foot = document.createElement("div");
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

      block.addEventListener("click", () => {
        state.selectedId = clip.id;
        renderTimeline();
      });

      // Reordenar: es la forma de decidir en qué orden se unen los audios.
      block.addEventListener("dragstart", (event) => {
        state.dragId = clip.id;
        block.classList.add("dragging");
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", clip.id);
      });
      block.addEventListener("dragend", () => {
        state.dragId = null;
        block.classList.remove("dragging");
      });
      block.addEventListener("dragover", (event) => {
        if (!state.dragId || state.dragId === clip.id) return;
        event.preventDefault();
        block.classList.add("drop-target");
      });
      block.addEventListener("dragleave", () => block.classList.remove("drop-target"));
      block.addEventListener("drop", (event) => {
        event.preventDefault();
        block.classList.remove("drop-target");
        moveClip(state.dragId, clip.id, event.clientX > block.getBoundingClientRect().left + block.offsetWidth / 2);
      });

      el.tlClips.appendChild(block);

      // La onda se dibuja cuando ya hay datos; si aún no, se pide y se repinta.
      const peaks = state.peaks.get(clip.assetId);
      if (peaks) drawClipWave(canvas, clip, peaks, width);
      else {
        peaksFor(clip.assetId)
          .then((data) => {
            if (block.isConnected) drawClipWave(canvas, clip, data, width);
          })
          .catch(() => {});
      }
    });

    updatePlayhead();
    renderSelection();
  }

  function drawClipWave(canvas, clip, data, width) {
    const height = window.innerWidth <= 620 ? 38 : 46;
    drawPeaks(canvas, data.peaks, data.per_second, clip.start, clip.end, {
      width,
      height,
      color: clip.gainDb <= -60 ? cssVar("--text-3") : cssVar("--accent"),
      alpha: clip.gainDb <= -60 ? 0.5 : 0.9,
    });

    // Los fundidos se enseñan encima, para no tener que imaginárselos.
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

  function renderRuler(length, scale) {
    el.tlRuler.innerHTML = "";
    if (!length) return;
    const steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800];
    const step = steps.find((value) => value * scale >= 64) || steps[steps.length - 1];
    // Con el zoom a tope la línea de tiempo mide cientos de miles de píxeles: se crean
    // solo las marcas que se ven, más un margen a cada lado, en vez de las miles que
    // caben en el total. La regla sigue midiendo lo mismo —el ancho lo fija
    // renderTimeline—; lo que cambia es cuántas marcas se crean.
    const view = el.tlScroll.clientWidth || 900;
    const from = Math.max(0, (el.tlScroll.scrollLeft - view) / scale);
    const to = Math.min(length, (el.tlScroll.scrollLeft + view * 2) / scale);
    for (let t = Math.ceil(from / step) * step; t <= to + 0.001; t += step) {
      const tick = document.createElement("span");
      tick.className = "tl-tick";
      tick.style.left = `${t * scale}px`;
      // Al ampliar, la regla baja de un segundo y «0:01» se repetiría: con pasos
      // cortos se enseña el decimal.
      tick.textContent = step < 1 ? `${t.toFixed(1)} s` : fmtTime(t);
      el.tlRuler.appendChild(tick);
    }
  }

  function moveClip(dragId, targetId, after) {
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

  function updatePlayhead() {
    const scale = pixelsPerSecond();
    const x = state.cursor * scale;
    const hasClips = state.clips.length > 0;
    el.tlPlayhead.style.transform = `translateX(${x}px)`;
    el.tlPlayhead.style.opacity = hasClips ? "0.85" : "0";
    // La línea es fina: para poder agarrarla con el ratón lleva encima una zona ancha.
    el.tlGrab.style.transform = `translateX(${x}px)`;
    el.tlGrab.classList.toggle("hidden", !hasClips);
    keepCursorVisible(x);
  }

  function updateTransport() {
    el.tlTime.textContent = `${fmtTime(state.cursor)} / ${fmtTime(total())}`;
    el.btnPlay.textContent = state.playing ? "❚❚" : "▶";
    el.btnPlay.setAttribute("aria-label", state.playing ? "Pausar" : "Reproducir");
    const count = state.clips.length;
    // Se cuentan las secciones que se ven, no los trozos de dentro: después de cortar
    // hay varias piezas dibujadas como una sola, y anunciar «3 tramos» cuando en la
    // línea de tiempo se ve uno solo confunde.
    const sections = state.clips.filter(
      (clip, index) => index === 0 || !sameRun(state.clips[index - 1], clip)
    ).length;
    el.exportNote.textContent = count
      ? `${sections} tramo${sections > 1 ? "s" : ""} · ${fmtTime(total())} de salida`
      : "Añade algún tramo para poder exportar.";
    el.btnExport.disabled = count === 0;

    // Controles de reproducción: sin montaje no hay nada que mover.
    const empty = count === 0;
    for (const button of [el.btnPlay, el.btnStart, el.btnEnd]) {
      button.disabled = empty;
    }
    updateCutButton();
    el.btnLoop.disabled = empty;
    el.speed.disabled = empty;
    el.scrub.disabled = empty;
    el.scrub.max = String(total() || 0);

    // El reloj de reproducción repinta 60 veces por segundo: no se toca la barra
    // mientras el usuario la arrastra, ni se escribe si el valor no ha cambiado.
    const position = String(Math.min(state.cursor, total()));
    if (!state.scrubbing && el.scrub.value !== position) el.scrub.value = position;
    el.btnLoop.classList.toggle("active", state.loop);
    el.btnLoop.setAttribute("aria-pressed", state.loop ? "true" : "false");
  }

  // Moverse por el montaje: lo hacen las flechas ← y → (con Mayús, diez segundos).
  function nudge(delta) {
    if (!state.clips.length) return;
    seek(state.cursor + delta);
  }

  function seek(time) {
    state.cursor = Math.max(0, Math.min(time, total()));
    updatePlayhead();
    updateTransport();
    if (state.playing) startPlayback(state.cursor);
  }

  /* ---------------- volumen de la parte elegida ----------------
     La cajita se arrastra hacia arriba o hacia abajo y el volumen de esa parte cambia.
     El primer movimiento es el que parte el montaje (una sola vez); después solo cambia
     el número, así que se puede subir y bajar sin trocear el montaje a cada píxel, y
     todo el gesto acaba siendo un único paso de deshacer. */

  function fmtDb(value) {
    if (value <= -60) return "silencio";
    return `${value > 0 ? "+" : ""}${value} dB`;
  }

  function beginGainGesture() {
    if (state.gainGesture) return true;
    const range = state.selection;
    if (!range || !state.clips.length) return false;

    const touched = clipsInRange(range);
    if (!touched.length) return false;

    // Cada tramo tocado puede partirse en dos: se avisa antes de llegar al tope.
    if (state.clips.length + touched.length * 2 > 55) {
      showError("El montaje ya tiene muchos tramos: no caben más cortes para esta parte.");
      return false;
    }

    const before = snapshot();
    state.gainGesture = {
      before,
      pieces: selectionPieces(range),
      base: new Map(),
    };
    for (const clip of state.gainGesture.pieces) {
      state.gainGesture.base.set(clip.id, clip.gainDb);
    }
    // Se repinta una sola vez, ya con los tramos partidos: a partir de aquí solo se
    // tocan las etiquetas.
    renderTimeline();
    return true;
  }

  function gainResultLabel(pieces) {
    if (!pieces.length) return "";
    const values = pieces.map((clip) => clip.gainDb);
    const low = Math.min.apply(null, values);
    const high = Math.max.apply(null, values);
    if (low === high) return low <= -60 ? "queda en silencio" : `queda en ${fmtDb(low)}`;
    return `queda entre ${fmtDb(low)} y ${fmtDb(high)}`;
  }

  // Solo se refrescan las etiquetas del tramo: rehacer la onda entera a cada píxel del
  // arrastre costaría mucho para nada.
  function updateClipBadges(clip) {
    const block = el.tlClips.querySelector(`[data-id="${clip.id}"]`);
    if (!block) return;
    block.classList.toggle("muted", clip.gainDb <= -60);
    const foot = block.querySelector(".tl-clip-foot");
    if (!foot) return;
    foot.innerHTML = "";
    for (const badge of clipBadges(clip)) {
      const span = document.createElement("span");
      span.className = `tl-badge${badge.css ? ` ${badge.css}` : ""}`;
      span.textContent = badge.text;
      foot.appendChild(span);
    }
  }

  function previewGain(deltaDb) {
    const gesture = state.gainGesture;
    if (!gesture) return;
    for (const clip of gesture.pieces) {
      const base = gesture.base.get(clip.id) || 0;
      clip.gainDb = Math.max(-60, Math.min(24, Math.round(base + deltaDb)));
      updateClipBadges(clip);
      // Si esa parte está sonando ahora mismo, el cambio se oye al instante.
      if (audioEngine.playingClip === clip.id) gainFor(nodeFor(clip.assetId), clip.gainDb);
    }
    el.tlGainValue.textContent = `${deltaDb > 0 ? "+" : ""}${deltaDb} dB`;
    el.tlGainHint.textContent = gainResultLabel(gesture.pieces);
    el.tlGain.setAttribute("aria-valuenow", String(deltaDb));
  }

  function finishGainGesture() {
    const gesture = state.gainGesture;
    state.gainGesture = null;
    el.tlGainValue.textContent = "0 dB";
    el.tlGainHint.textContent = "arrastra ↕";
    el.tlGain.setAttribute("aria-valuenow", "0");
    if (!gesture) return;
    pushHistory(gesture.before);
    refresh();
  }

  // La parte elegida se sombrea y la cajita se pega a la zona visible: da igual lo
  // desplazado que esté el montaje, siempre queda a mano.
  function renderSelection() {
    const range = state.selection;
    const length = total();
    if (!range || !length || range.end - range.start < 0.001) {
      el.tlSelect.classList.add("hidden");
      el.tlGain.classList.add("hidden");
      updateCutButton();
      return;
    }

    const scale = pixelsPerSecond();
    const start = Math.max(0, Math.min(range.start, length));
    const end = Math.max(start, Math.min(range.end, length));
    const left = start * scale;
    const width = Math.max(2, (end - start) * scale);

    el.tlSelect.style.transform = `translateX(${left}px)`;
    el.tlSelect.style.width = `${width}px`;
    el.tlSelect.classList.remove("hidden");

    const view = el.tlScroll.clientWidth || 0;
    const half = 62; // medio ancho de la cajita
    const center = left + width / 2;
    const min = el.tlScroll.scrollLeft + half;
    const max = el.tlScroll.scrollLeft + Math.max(view, half * 2) - half;
    el.tlGain.style.transform = `translateX(${Math.max(min, Math.min(center, max)) - half}px)`;
    el.tlGain.classList.remove("hidden");
    updateCutButton();
  }

  /* ---------------- escucha ----------------
     Cada tramo se reproduce con su volumen y sus fundidos aplicados en vivo.
     No se decodifica el audio entero: se usan los propios elementos <audio>
     con un nodo de ganancia, así que un MP3 de una hora no llena la memoria. */

  const audioEngine = {
    ctx: null,
    nodes: new Map(),
    started: null,
    playingClip: null, // tramo que suena ahora, para oír los cambios de volumen en vivo
  };

  function audioContext() {
    if (!audioEngine.ctx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      audioEngine.ctx = new Ctx();
    }
    if (audioEngine.ctx.state === "suspended") audioEngine.ctx.resume().catch(() => {});
    return audioEngine.ctx;
  }

  function nodeFor(assetId) {
    if (audioEngine.nodes.has(assetId)) return audioEngine.nodes.get(assetId);

    const element = new Audio(`/api/audio/asset/${assetId}`);
    element.preload = "auto";
    element.crossOrigin = "anonymous";

    let gain = null;
    let connected = false;
    const ctx = audioContext();
    if (ctx) {
      try {
        const source = ctx.createMediaElementSource(element);
        gain = ctx.createGain();
        source.connect(gain);
        gain.connect(ctx.destination);
        connected = true;
      } catch (err) {
        connected = false; // sin Web Audio se usa el volumen del propio elemento
      }
    }

    const entry = { element, gain, connected };
    audioEngine.nodes.set(assetId, entry);
    return entry;
  }

  function stopPlayback() {
    state.playing = false;
    for (const entry of audioEngine.nodes.values()) {
      entry.element.pause();
    }
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
    audioEngine.started = null;
    audioEngine.playingClip = null;
    updateTransport();
    updatePlayhead();
  }

  function gainFor(entry, db) {
    const value = dbToGain(db);
    if (entry.connected) entry.gain.gain.value = value;
    else entry.element.volume = Math.min(1, value);
  }

  // Los fundidos se oyen: se programan sobre el nodo de ganancia en el reloj del
  // contexto de audio, que es exacto, en vez de ir a saltos desde el bucle de
  // dibujo. Si el navegador no tiene Web Audio, queda el volumen fijo.
  function applyGainAutomation(entry, clip, offset) {
    const target = dbToGain(clip.gainDb);
    const ctx = audioEngine.ctx;
    if (!entry.connected || !ctx) {
      gainFor(entry, clip.gainDb);
      return;
    }

    const gain = entry.gain.gain;
    const now = ctx.currentTime + 0.02;  // programar en el pasado no surte efecto
    const remaining = Math.max(0, clipDuration(clip) - offset);
    const floor = 0.0001;                // 0 exacto deja la rampa sin recorrido

    gain.cancelScheduledValues(now);
    gain.setValueAtTime(Math.max(floor, target), now);

    // Si la reproducción arranca dentro del fundido de entrada, se entra a media rampa.
    if (clip.fadeIn > 0 && offset < clip.fadeIn) {
      gain.setValueAtTime(Math.max(floor, (offset / clip.fadeIn) * target), now);
      gain.linearRampToValueAtTime(Math.max(floor, target), now + (clip.fadeIn - offset));
    }

    if (clip.fadeOut > 0) {
      if (remaining <= clip.fadeOut) {
        gain.setValueAtTime(Math.max(floor, (remaining / clip.fadeOut) * target), now);
        gain.linearRampToValueAtTime(floor, now + remaining);
      } else {
        const start = now + (remaining - clip.fadeOut);
        gain.setValueAtTime(Math.max(floor, target), start);
        gain.linearRampToValueAtTime(floor, start + clip.fadeOut);
      }
    }
  }

  function startPlayback(fromTime) {
    stopPlayback();
    const position = locate(fromTime);
    if (!position) {
      seek(0);
      return;
    }

    state.playing = true;
    updateTransport();

    // El cursor se mide con el reloj del sistema, escalado por la velocidad: a
    // 2×, un segundo de reloj son dos segundos de montaje.
    state.anchorWall = performance.now();
    state.anchorCursor = fromTime;
    let current = position;
    let active = null;

    const startClip = (clip, offset) => {
      const entry = nodeFor(clip.assetId);
      entry.element.playbackRate = state.rate;
      entry.element.currentTime = Math.max(0, clip.start + offset);
      applyGainAutomation(entry, clip, offset);
      entry.element.play().catch(() => {});
      active = clip.id;
      audioEngine.playingClip = clip.id;
    };

    const stopClip = () => {
      for (const entry of audioEngine.nodes.values()) {
        if (!entry.element.paused) entry.element.pause();
      }
      active = null;
      audioEngine.playingClip = null;
    };

    startClip(current.clip, current.offset);
    audioEngine.started = { stop: stopClip };

    const tick = () => {
      if (!state.playing) return;
      state.cursor =
        state.anchorCursor + ((performance.now() - state.anchorWall) / 1000) * state.rate;

      const position2 = locate(state.cursor);
      if (!position2) {
        // Con «repetir» activo, el montaje vuelve a empezar en lugar de pararse.
        if (state.loop) {
          stopClip();
          state.anchorCursor = 0;
          state.anchorWall = performance.now();
          state.cursor = 0;
          const first = locate(0);
          if (!first) {
            stopPlayback();
            return;
          }
          startClip(first.clip, first.offset);
          updatePlayhead();
          updateTransport();
          rafId = requestAnimationFrame(tick);
          return;
        }
        state.cursor = total();
        stopPlayback();
        updatePlayhead();
        return;
      }
      if (position2.clip.id !== active) {
        stopClip();
        startClip(position2.clip, position2.offset);
      }
      updatePlayhead();
      updateTransport();
      rafId = requestAnimationFrame(tick);
    };

    rafId = requestAnimationFrame(tick);
  }

  function togglePlay() {
    if (state.playing) {
      stopPlayback();
      return;
    }
    if (!state.clips.length) return;
    if (state.cursor >= total() - 0.01) state.cursor = 0;
    startPlayback(state.cursor);
  }

  // Cambiar la velocidad a mitad de reproducción no puede dar un salto: se
  // vuelve a anclar el reloj en el punto exacto donde estábamos.
  function setRate(rate) {
    state.rate = rate;
    if (!state.playing) return;
    state.anchorCursor = state.cursor;
    state.anchorWall = performance.now();
    for (const entry of audioEngine.nodes.values()) {
      entry.element.playbackRate = rate;
    }
  }

  function toggleLoop() {
    state.loop = !state.loop;
    updateTransport();
  }

  async function previewAsset(assetId, button) {
    const asset = assetOf(assetId);
    if (!asset) return;
    const entry = nodeFor(assetId);

    if (!entry.element.paused) {
      entry.element.pause();
      button.textContent = "Escuchar";
      return;
    }

    stopPlayback();
    entry.element.currentTime = 0;
    gainFor(entry, 0);
    await entry.element.play().catch(() => showError("El navegador no dejó reproducir."));
    button.textContent = "Parar";
    entry.element.addEventListener(
      "ended",
      () => {
        button.textContent = "Escuchar";
      },
      { once: true }
    );
  }

  /* ---------------- exportar ---------------- */

  function syncFormatFields() {
    const option = el.outFormat.selectedOptions[0];
    const acceptsBitrate = option ? option.dataset.bitrate === "1" : true;
    el.bitrateWrap.style.display = acceptsBitrate ? "" : "none";
  }

  async function exportMontage() {
    if (!state.clips.length) return;
    clearError();
    el.btnExport.disabled = true;
    el.exportProgress.classList.remove("hidden");
    el.exportLink.classList.add("hidden");
    el.exportBar.style.width = "0%";
    el.exportText.textContent = "Preparando…";

    const payload = {
      clips: state.clips.map((clip) => ({
        asset_id: clip.assetId,
        start: Number(clip.start.toFixed(3)),
        end: Number(clip.end.toFixed(3)),
        gain_db: Number(clip.gainDb.toFixed(2)),
        fade_in: Number(clip.fadeIn.toFixed(2)),
        fade_out: Number(clip.fadeOut.toFixed(2)),
      })),
      format: el.outFormat.value || "mp3",
      bitrate: Number(el.outBitrate.value) || 192,
      name: (el.outName.value || "audio-editado").trim(),
    };

    try {
      const render = await API.post("/api/audio/render", payload);
      pollRender(render.render_id);
    } catch (err) {
      el.btnExport.disabled = false;
      el.exportProgress.classList.add("hidden");
      showError(err.message);
    }
  }

  function pollRender(renderId) {
    clearInterval(pollId);
    const tick = async () => {
      try {
        const data = await API.get(`/api/audio/render/${renderId}`);
        if (data.status === "done") {
          clearInterval(pollId);
          el.exportBar.style.width = "100%";
          el.exportText.textContent = `${data.filename} · ${fmtSize(data.filesize)} · ${fmtTime(data.duration)}`;
          el.exportLink.href = data.download_url;
          el.exportLink.setAttribute("download", data.filename || "");
          el.exportLink.classList.remove("hidden");
          el.btnExport.disabled = false;
        } else if (data.status === "error") {
          clearInterval(pollId);
          el.exportProgress.classList.add("hidden");
          el.btnExport.disabled = false;
          showError(data.error || "La exportación falló.");
        } else {
          el.exportBar.style.width = `${Math.max(3, data.progress || 0)}%`;
          el.exportText.textContent =
            data.status === "queued" ? "En cola…" : `Codificando… ${Math.round(data.progress || 0)}%`;
        }
      } catch (err) {
        clearInterval(pollId);
        el.btnExport.disabled = false;
        showError(err.message);
      }
    };
    tick();
    pollId = setInterval(tick, 600);
  }

  /* ---------------- eventos ---------------- */

  el.fileInput.addEventListener("change", () => {
    uploadFiles(el.fileInput.files);
    el.fileInput.value = "";
  });

  el.btnRecent.addEventListener("click", () => {
    if (el.recentBox.classList.contains("hidden")) loadRecent();
    else el.recentBox.classList.add("hidden");
  });
  el.btnRecentClose.addEventListener("click", () => el.recentBox.classList.add("hidden"));

  el.btnPlay.addEventListener("click", togglePlay);
  el.btnStart.addEventListener("click", () => seek(0));
  el.btnEnd.addEventListener("click", () => seek(total()));
  el.btnCut.addEventListener("click", cutSelection);
  el.btnLoop.addEventListener("click", toggleLoop);
  el.speed.addEventListener("change", () => setRate(Number(el.speed.value) || 1));

  // Barra de posición. Al agarrarla se pausa el audio y se mueve el cursor sin
  // reiniciar la reproducción en cada píxel; al soltarla, si sonaba, sigue.
  let resumeAfterScrub = false;
  el.scrub.addEventListener("pointerdown", () => {
    state.scrubbing = true;
    resumeAfterScrub = state.playing;
    if (state.playing) stopPlayback();
  });
  el.scrub.addEventListener("input", () => {
    const value = Number(el.scrub.value) || 0;
    if (!state.scrubbing) {
      seek(value);  // movida con el teclado: se aplica directa
      return;
    }
    state.cursor = Math.max(0, Math.min(value, total()));
    updatePlayhead();
    el.tlTime.textContent = `${fmtTime(state.cursor)} / ${fmtTime(total())}`;
  });
  for (const event of ["pointerup", "pointercancel", "change"]) {
    el.scrub.addEventListener(event, () => {
      if (!state.scrubbing) return;
      state.scrubbing = false;
      if (resumeAfterScrub) {
        resumeAfterScrub = false;
        startPlayback(state.cursor);
      } else {
        updateTransport();
      }
    });
  }
  // Un gesto largo (arrastrar un borde para recortar, subir el volumen de una parte
  // elegida) va a la pila de deshacer como un solo paso: se guarda al empezar.
  function commitGesture() {
    if (!state.gestureBefore) return;
    pushHistory(state.gestureBefore);
    state.gestureBefore = null;
  }

  el.btnClear.addEventListener("click", () => {
    if (!state.clips.length) return;
    if (!window.confirm("¿Vaciar la línea de tiempo? Los audios siguen cargados.")) return;
    commit(() => {
      state.clips = [];
      state.selectedId = null;
      state.cursor = 0;
    });
    keepSelectionValid();
    refresh();
  });

  el.btnExport.addEventListener("click", exportMontage);
  el.outFormat.addEventListener("change", syncFormatFields);

  el.tlRuler.addEventListener("click", (event) => {
    // Con Mayús la regla sirve para elegir la parte, no para mover el cursor.
    if (event.shiftKey) return;
    seek(timeAtClientX(event.clientX));
  });

  el.tlRuler.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      seek(state.cursor + (event.key === "ArrowRight" ? 1 : -1));
    }
  });

  // El cursor también se puede arrastrar sobre la regla. Con Mayús, en cambio, la
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
      // En la regla también se corre la vista al llegar al borde.
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

  // La línea del cursor se agarra y se arrastra con el ratón por la regla, que es donde
  // está su tirador. Antes la zona de agarre bajaba por todo el alto y tapaba la onda
  // justo en la junta que deja un corte, así que ahí el arrastre movía el cursor en vez
  // de elegir la parte: parecía que ya no se podía seleccionar.
  el.tlGrab.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !state.clips.length) return;
    event.preventDefault();
    event.stopPropagation();

    const fromX = event.clientX;
    const fromCursor = state.cursor;
    state.draggingCursor = true;
    capturePointer(el.tlGrab, event.pointerId);

    const move = (ev) => seek(fromCursor + (ev.clientX - fromX) / pixelsPerSecond());
    const end = () => {
      state.draggingCursor = false;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  });

  // El botón central del ratón desplaza la vista, como en cualquier editor. Sin esto,
  // con el zoom puesto habría que tirar de la barra de desplazamiento.
  el.tlScroll.addEventListener("pointerdown", (event) => {
    if (event.button !== 1 || !state.clips.length) return;
    event.preventDefault();

    const fromX = event.clientX;
    const fromScroll = el.tlScroll.scrollLeft;
    capturePointer(el.tlScroll, event.pointerId);

    const move = (ev) => {
      el.tlScroll.scrollLeft = Math.max(0, fromScroll - (ev.clientX - fromX));
    };
    const end = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  });

  /* ------------- elegir una parte, con el ratón sobre la onda -------------
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

  /* ------------- correr la vista mientras se elige una parte -------------
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
    if (event.target.closest(".tl-clip-head") || event.target.closest(".tl-clip-handle")) return;

    const anchor = snapToGuide(timeAtClientX(event.clientX));
    const originX = event.clientX;
    let dragged = false;
    capturePointer(el.tlClips, event.pointerId);

    const move = (ev) => {
      if (!dragged && Math.abs(ev.clientX - originX) < 3) return;
      dragged = true;
      const time = snapToGuide(timeAtClientX(ev.clientX));
      state.selection = {
        start: Math.max(0, Math.min(anchor, time)),
        end: Math.min(total(), Math.max(anchor, time)),
      };
      renderSelection();
    };

    // Al llegar al borde, la vista se corre y la selección sigue creciendo.
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
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  });

  // Doble clic sobre un tramo: lo elige entero, que es la otra forma de decir «todo esto».
  el.tlClips.addEventListener("dblclick", (event) => {
    const block = event.target.closest(".tl-clip");
    if (!block) return;
    const range = clipRange(block.dataset.id);
    if (!range) return;
    state.selectedId = block.dataset.id;
    state.selection = range;
    renderTimeline();
  });

  /* ------------- la cajita del volumen de esa parte -------------
     Se arrastra hacia arriba o hacia abajo: 4 px por dB, y con Mayús 20 px por dB para
     afinar. La rueda sobre ella mueve de dB en dB y las flechas también. */

  el.tlGain.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !state.selection) return;
    event.preventDefault();
    event.stopPropagation();
    capturePointer(el.tlGain, event.pointerId);

    const fromY = event.clientY;
    let applied = 0;
    let blocked = false;

    const move = (ev) => {
      if (blocked) return;
      const step = ev.shiftKey ? 20 : 4;
      const delta = Math.round((fromY - ev.clientY) / step);
      if (delta === applied) return;
      if (delta === 0) {
        applied = 0;
        return;
      }
      if (!state.gainGesture && !beginGainGesture()) {
        blocked = true;
        return;
      }
      applied = delta;
      previewGain(delta);
    };

    const end = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      finishGainGesture();
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  });

  // La rueda sobre la cajita no debe llegar a la línea de tiempo: allí acerca y aleja.
  el.tlGain.addEventListener("wheel", (event) => {
    if (!state.selection) return;
    event.preventDefault();
    event.stopPropagation();
    if (!beginGainGesture()) return;
    previewGain(event.deltaY > 0 ? -1 : 1);
    finishGainGesture();
  }, { passive: false });

  el.tlGain.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    if (!state.selection) return;
    event.preventDefault();
    event.stopPropagation();
    const step = event.shiftKey ? 5 : 1;
    if (!beginGainGesture()) return;
    previewGain(event.key === "ArrowUp" ? step : -step);
    finishGainGesture();
  });

  // Si la vista se desplaza, se recolocan la cajita y las marcas de la regla: con el
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

  /* ---------------- zoom de la línea de tiempo ---------------- */

  // El botón del recuadro vacío: trae el audio cargado o lleva a la tarjeta de audios.
  el.btnEmptyAdd.addEventListener("click", () => {
    const asset = state.assets[0];
    if (asset) addClip(asset.id);
    else el.sourcesCard.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  // La rueda, sin más, sobre la línea de tiempo: hacia arriba acerca y hacia abajo
  // aleja, siempre anclada al punto que señala el ratón. Mayús + rueda (o una rueda
  // horizontal, como la del trackpad) desplaza la vista. La rueda llega en ráfagas de
  // decenas por segundo, así que se agrupa en un solo repintado por fotograma.

  // Un ratón de verdad manda «líneas», no píxeles (deltaMode 1): sin traducirlas, el
  // zoom apenas se movería.
  function wheelPixels(event, axis) {
    const value = axis === "x" ? event.deltaX : event.deltaY;
    if (!value) return 0;
    if (event.deltaMode === 1) return value * 33;
    if (event.deltaMode === 2) return value * (el.tlScroll.clientHeight || 400);
    return value;
  }

  let wheelZoomFrame = 0;
  let wheelZoomSum = 0;
  let wheelZoomAnchor = null;

  el.tlScroll.addEventListener("wheel", (event) => {
    if (!state.clips.length) return; // sin montaje no hay nada que ampliar ni que mover

    const dx = wheelPixels(event, "x");
    const dy = wheelPixels(event, "y");

    // Rueda horizontal o Mayús: desplazar la vista, que es lo que se espera.
    if (event.shiftKey || Math.abs(dx) > Math.abs(dy)) {
      event.preventDefault();
      el.tlScroll.scrollLeft = Math.max(0, el.tlScroll.scrollLeft + dx + dy);
      return;
    }

    // Si la rueda no va a cambiar nada (ya está en el tope), que la página se desplace.
    const next = Math.min(maxZoom(), Math.max(1, state.zoom * Math.pow(1.0015, -dy)));
    if (Math.abs(next - state.zoom) < 0.0005 && !wheelZoomFrame) return;
    event.preventDefault();

    // El ancla se fija al empezar la ráfaga: es el punto que el usuario está mirando.
    if (!wheelZoomAnchor) {
      wheelZoomAnchor = {
        time: timeAtClientX(event.clientX),
        at: event.clientX - el.tlScroll.getBoundingClientRect().left,
      };
    }
    wheelZoomSum += dy;
    if (wheelZoomFrame) return;
    wheelZoomFrame = window.requestAnimationFrame(() => {
      wheelZoomFrame = 0;
      const sum = wheelZoomSum;
      const anchor = wheelZoomAnchor;
      wheelZoomSum = 0;
      wheelZoomAnchor = null;
      // 1.0015 por píxel de rueda: un giro suave no salta de golpe.
      setZoom(state.zoom * Math.pow(1.0015, -sum), anchor.time, anchor.at);
    });
  }, { passive: false });

  document.addEventListener("keydown", (event) => {
    const tag = (event.target.tagName || "").toLowerCase();
    const typing = tag === "input" || tag === "textarea" || tag === "select";

    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
      event.preventDefault();
      if (event.shiftKey) redo();
      else undo();
      return;
    }
    if (typing) return;

    if (event.code === "Space") {
      event.preventDefault();
      togglePlay();
    } else if (event.key === "Delete" || event.key === "Backspace") {
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
      event.preventDefault();
      seek(0);
    } else if (event.key === "End") {
      event.preventDefault();
      seek(total());
    } else if (event.key === "ArrowRight") {
      // Con Mayús el salto es de diez segundos, como en cualquier reproductor.
      nudge(event.shiftKey ? 10 : 1);
    } else if (event.key === "ArrowLeft") {
      nudge(event.shiftKey ? -10 : -1);
    } else if (event.key === "+" || event.key === "=") {
      // El cursor se queda donde estaba: es lo que se está mirando.
      event.preventDefault();
      zoomBy(ZOOM_STEP, state.cursor, null);
    } else if (event.key === "-" || event.key === "_") {
      event.preventDefault();
      zoomBy(1 / ZOOM_STEP, state.cursor, null);
    } else if (event.key === "0") {
      event.preventDefault();
      setZoom(1);
    } else if (event.key === "Escape") {
      if (!state.selection) return;
      event.preventDefault();
      state.selection = null;
      renderTimeline();
    }
  });

  window.addEventListener("resize", () => {
    if (state.clips.length) renderTimeline();
  });

  /* ---------------- arrastrar archivos a la ventana ---------------- */

  let dragDepth = 0;
  window.addEventListener("dragenter", (event) => {
    if (!event.dataTransfer || !Array.from(event.dataTransfer.types || []).includes("Files")) return;
    dragDepth++;
    el.dropOverlay.classList.remove("hidden");
  });
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) el.dropOverlay.classList.add("hidden");
  });
  window.addEventListener("drop", (event) => {
    if (!event.dataTransfer || !event.dataTransfer.files.length) return;
    event.preventDefault();
    dragDepth = 0;
    el.dropOverlay.classList.add("hidden");
    uploadFiles(event.dataTransfer.files);
  });

  /* ------------- tramo: tiradores sobre la línea de tiempo -------------
     El recorte se hace en el propio tramo: se arrastran sus bordes y la onda se
     redibuja con la parte que se va a exportar. La onda grande que había en los
     controles se retiró porque era esta misma onda dibujada dos veces. */

  function beginTrim(event, clipId, edge) {
    const clip = clipOf(clipId);
    const asset = clip && assetOf(clip.assetId);
    if (!clip || !asset) return;

    event.preventDefault();
    event.stopPropagation();

    state.selectedId = clipId;
    state.gestureBefore = snapshot();

    const fromX = event.clientX;
    const from = edge === "start" ? clip.start : clip.end;
    const scale = pixelsPerSecond();

    const move = (moveEvent) => {
      const delta = (moveEvent.clientX - fromX) / scale;
      const value = from + delta;
      if (edge === "start") clip.start = Math.max(0, Math.min(value, clip.end - 0.05));
      else clip.end = Math.min(asset.duration, Math.max(value, clip.start + 0.05));
      clip.fadeIn = Math.min(clip.fadeIn, clipDuration(clip));
      clip.fadeOut = Math.min(clip.fadeOut, clipDuration(clip));
      renderTimeline();
      updateTransport();
    };

    const end = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      commitGesture();
      refresh();
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);

    renderTimeline();
  }

  /* ---------------- arranque ---------------- */

  async function init() {
    try {
      await loadSources();
    } catch (err) {
      el.loading.classList.add("hidden");
      el.warn.classList.remove("hidden");
      el.warn.textContent =
        "El editor de audio no está disponible en este servidor. " +
        (err.message.includes("404") ? "Puede estar desactivado." : err.message);
      return;
    }

    el.loading.classList.add("hidden");
    for (const card of [el.sourcesCard, el.timelineCard, el.exportCard]) {
      card.classList.remove("hidden");
    }
    refresh();

    // Si la dirección trae ?job=..., se trae ese audio directamente.
    const params = new URLSearchParams(window.location.search);
    const job = params.get("job");
    if (job) {
      try {
        const asset = await API.post(`/api/audio/from-job/${job}`);
        state.assets.unshift(asset);
        await peaksFor(asset.id);
        renderAssets();
        addClip(asset.id);
        el.outName.value = (asset.name || "").replace(/\.[^.]+$/, "");
      } catch (err) {
        showError(err.message);
      }
    }
  }

  init();
})();
