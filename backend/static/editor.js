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
    tlTime: $("tl-time"),
    tlEmpty: $("tl-empty"),
    btnPlay: $("btn-play"),
    btnStart: $("btn-start"),
    btnEnd: $("btn-end"),
    btnBack5: $("btn-back5"),
    btnBack10: $("btn-back10"),
    btnFwd5: $("btn-fwd5"),
    btnFwd10: $("btn-fwd10"),
    btnLoop: $("btn-loop"),
    speed: $("speed"),
    scrub: $("scrub"),
    clipCard: $("clip-card"),
    clipTitle: $("clip-title"),
    wave: $("wave"),
    inStart: $("in-start"),
    inEnd: $("in-end"),
    btnFull: $("btn-full"),
    btnSplit: $("btn-split"),
    btnDuplicate: $("btn-duplicate"),
    btnListenClip: $("btn-listen-clip"),
    inGain: $("in-gain"),
    outGain: $("out-gain"),
    inFadeIn: $("in-fadein"),
    outFadeIn: $("out-fadein"),
    inFadeOut: $("in-fadeout"),
    outFadeOut: $("out-fadeout"),
    btnMute: $("btn-mute"),
    btnGainAll: $("btn-gain-all"),
    btnFadeAll: $("btn-fade-all"),
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

  const API = {
    async request(path, options = {}) {
      const res = await fetch(path, options);
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

  function pixelsPerSecond() {
    const width = el.tlScroll.clientWidth || 900;
    const length = total();
    if (!length) return 12;
    return Math.min(48, Math.max(3, (width - 2) / length));
  }

  /* ---------------- forma de onda ---------------- */

  function fitCanvas(canvas, width, height) {
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(width));
    const h = Math.max(1, Math.round(height));
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.width = `${w}px`;
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
      state.clips = state.clips.filter((clip) => clip.id !== clipId);
      if (state.selectedId === clipId) state.selectedId = null;
    });
    keepSelectionValid();
    refresh();
  }

  function duplicateClip(clipId) {
    const index = state.clips.findIndex((clip) => clip.id === clipId);
    if (index < 0) return;
    commit(() => {
      const copy = { ...state.clips[index], id: uid() };
      state.clips.splice(index + 1, 0, copy);
      state.selectedId = copy.id;
    });
  }

  function splitAtCursor() {
    const position = locate(state.cursor);
    if (!position) return;
    const { clip, offset } = position;
    if (offset < 0.1 || offset > clipDuration(clip) - 0.1) {
      showError("Coloca el cursor dentro del tramo (no en el borde) para cortarlo.");
      return;
    }
    commit(() => {
      const at = clip.start + offset;
      const second = { ...clip, id: uid(), start: at };
      clip.end = at;
      const index = state.clips.findIndex((item) => item.id === clip.id);
      state.clips.splice(index + 1, 0, second);
      state.selectedId = second.id;
    });
  }

  function refresh() {
    renderTimeline();
    renderInspector();
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

  function renderTimeline() {
    const empty = state.clips.length === 0;
    el.tlEmpty.classList.toggle("hidden", !empty);
    el.tlClips.innerHTML = "";

    const scale = pixelsPerSecond();
    const length = total();
    el.tlRuler.style.width = `${Math.max(1, length * scale)}px`;
    el.tlClips.style.width = `${Math.max(1, length * scale)}px`;
    renderRuler(length, scale);

    state.clips.forEach((clip) => {
      const asset = assetOf(clip.assetId);
      const width = Math.max(14, clipDuration(clip) * scale);

      const block = document.createElement("article");
      block.className = "tl-clip";
      block.dataset.id = clip.id;
      block.draggable = true;
      if (clip.id === state.selectedId) block.classList.add("selected");
      if (clip.gainDb <= -60) block.classList.add("muted");
      block.style.width = `${width}px`;
      block.title = `${asset ? asset.name : "audio"} · ${fmtTime(clip.start)} → ${fmtTime(clip.end)}`;

      const head = document.createElement("div");
      head.className = "tl-clip-head";
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

      const canvas = document.createElement("canvas");
      canvas.className = "tl-clip-wave";
      block.append(head, canvas);

      const foot = document.createElement("div");
      foot.className = "tl-clip-foot";
      for (const badge of clipBadges(clip)) {
        const span = document.createElement("span");
        span.className = `tl-badge${badge.css ? ` ${badge.css}` : ""}`;
        span.textContent = badge.text;
        foot.appendChild(span);
      }
      block.appendChild(foot);

      block.addEventListener("click", () => {
        state.selectedId = clip.id;
        renderTimeline();
        renderInspector();
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

  function renderRuler(length, scale) {
    el.tlRuler.innerHTML = "";
    if (!length) return;
    const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800];
    const step = steps.find((value) => value * scale >= 64) || steps[steps.length - 1];
    for (let t = 0; t <= length + 0.001; t += step) {
      const tick = document.createElement("span");
      tick.className = "tl-tick";
      tick.style.left = `${t * scale}px`;
      tick.textContent = fmtTime(t);
      el.tlRuler.appendChild(tick);
    }
  }

  function moveClip(dragId, targetId, after) {
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

  function updatePlayhead() {
    const scale = pixelsPerSecond();
    el.tlPlayhead.style.transform = `translateX(${state.cursor * scale}px)`;
    el.tlPlayhead.style.opacity = state.clips.length ? "0.85" : "0";
  }

  function updateTransport() {
    el.tlTime.textContent = `${fmtTime(state.cursor)} / ${fmtTime(total())}`;
    el.btnPlay.textContent = state.playing ? "❚❚" : "▶";
    el.btnPlay.setAttribute("aria-label", state.playing ? "Pausar" : "Reproducir");
    const count = state.clips.length;
    el.exportNote.textContent = count
      ? `${count} tramo${count > 1 ? "s" : ""} · ${fmtTime(total())} de salida`
      : "Añade algún tramo para poder exportar.";
    el.btnExport.disabled = count === 0;
    el.btnSplit.disabled = !state.selectedId;
    el.btnDuplicate.disabled = !state.selectedId;
    el.btnListenClip.disabled = !state.selectedId;
    el.btnMute.disabled = !state.selectedId;
    el.btnGainAll.disabled = !state.selectedId;
    el.btnFadeAll.disabled = !state.selectedId;

    // Controles de reproducción: sin montaje no hay nada que mover.
    const empty = count === 0;
    for (const button of [
      el.btnPlay, el.btnStart, el.btnEnd,
      el.btnBack5, el.btnBack10, el.btnFwd5, el.btnFwd10,
    ]) {
      button.disabled = empty;
    }
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

  // Moverse por el montaje: es lo que hacen los botones de ±5 y ±10 segundos.
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

  /* ---------------- inspector del tramo ---------------- */

  function renderInspector() {
    const clip = clipOf(state.selectedId);
    el.clipCard.classList.toggle("hidden", !clip);
    if (!clip) return;

    const asset = assetOf(clip.assetId);
    el.clipTitle.textContent = asset ? asset.name : "audio";
    el.inStart.value = clip.start.toFixed(2);
    el.inEnd.value = clip.end.toFixed(2);
    el.inGain.value = String(clip.gainDb);
    el.outGain.textContent = clip.gainDb <= -60 ? "silencio" : `${clip.gainDb} dB`;
    el.inFadeIn.value = String(clip.fadeIn);
    el.outFadeIn.textContent = `${clip.fadeIn} s`;
    el.inFadeOut.value = String(clip.fadeOut);
    el.outFadeOut.textContent = `${clip.fadeOut} s`;
    drawInspectorWave(clip);
  }

  function drawInspectorWave(clip) {
    const asset = assetOf(clip.assetId);
    const data = state.peaks.get(clip.assetId);
    const width = el.wave.parentElement.clientWidth || 600;
    const height = 140;

    if (!asset || !data) {
      drawPeaks(el.wave, null, 1, 0, 0, { width, height });
      return;
    }

    drawPeaks(el.wave, data.peaks, data.per_second, 0, asset.duration, {
      width,
      height,
      color: cssVar("--accent"),
      alpha: 0.85,
    });

    // Fuera del tramo elegido se sombrea: lo que no se exporta tiene que verse.
    const ctx = el.wave.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const startX = (clip.start / asset.duration) * width;
    const endX = (clip.end / asset.duration) * width;

    ctx.fillStyle = cssVar("--surface");
    ctx.globalAlpha = 0.72;
    ctx.fillRect(0, 0, startX, height);
    ctx.fillRect(endX, 0, width - endX, height);
    ctx.globalAlpha = 1;

    for (const x of [startX, endX]) {
      ctx.fillStyle = cssVar("--accent");
      ctx.fillRect(Math.max(0, Math.min(width - 2, x - 1)), 0, 2, height);
      ctx.fillRect(Math.max(0, Math.min(width - 10, x - 5)), 0, 10, 6);
    }
  }

  function applyRange(startValue, endValue) {
    const clip = clipOf(state.selectedId);
    const asset = clip && assetOf(clip.assetId);
    if (!clip || !asset) return;
    const before = snapshot();
    let start = Math.max(0, Math.min(Number(startValue) || 0, asset.duration));
    let end = Math.max(0, Math.min(Number(endValue) || 0, asset.duration));
    if (end - start < 0.05) {
      showError("El fin tiene que quedar después del inicio.");
      renderInspector();
      return;
    }
    clip.start = start;
    clip.end = end;
    clip.fadeIn = Math.min(clip.fadeIn, clipDuration(clip));
    clip.fadeOut = Math.min(clip.fadeOut, Math.max(0, clipDuration(clip) - clip.fadeIn));
    pushHistory(before);
    refresh();
  }

  /* ---------------- escucha ----------------
     Cada tramo se reproduce con su volumen y sus fundidos aplicados en vivo.
     No se decodifica el audio entero: se usan los propios elementos <audio>
     con un nodo de ganancia, así que un MP3 de una hora no llena la memoria. */

  const audioEngine = {
    ctx: null,
    nodes: new Map(),
    started: null,
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
    };

    const stopClip = () => {
      for (const entry of audioEngine.nodes.values()) {
        if (!entry.element.paused) entry.element.pause();
      }
      active = null;
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
  el.btnBack5.addEventListener("click", () => nudge(-5));
  el.btnBack10.addEventListener("click", () => nudge(-10));
  el.btnFwd5.addEventListener("click", () => nudge(5));
  el.btnFwd10.addEventListener("click", () => nudge(10));
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
  el.btnSplit.addEventListener("click", splitAtCursor);
  el.btnDuplicate.addEventListener("click", () => state.selectedId && duplicateClip(state.selectedId));
  el.btnListenClip.addEventListener("click", () => {
    const clip = clipOf(state.selectedId);
    if (!clip) return;
    stopPlayback();
    const entry = nodeFor(clip.assetId);
    entry.element.currentTime = clip.start;
    gainFor(entry, clip.gainDb);
    entry.element.play().catch(() => {});
  });

  el.btnFull.addEventListener("click", () => {
    const clip = clipOf(state.selectedId);
    const asset = clip && assetOf(clip.assetId);
    if (!asset) return;
    applyRange(0, asset.duration);
  });

  el.inStart.addEventListener("change", () => {
    const clip = clipOf(state.selectedId);
    if (clip) applyRange(el.inStart.value, clip.end);
  });
  el.inEnd.addEventListener("change", () => {
    const clip = clipOf(state.selectedId);
    if (clip) applyRange(clip.start, el.inEnd.value);
  });

  // Los deslizadores se aplican en vivo, pero a la pila de deshacer va un solo
  // paso por gesto: se guarda el estado al empezar a arrastrar.
  function trackGesture() {
    state.gestureBefore = snapshot();
  }
  function commitGesture() {
    if (!state.gestureBefore) return;
    pushHistory(state.gestureBefore);
    state.gestureBefore = null;
  }

  for (const slider of [el.inGain, el.inFadeIn, el.inFadeOut]) {
    slider.addEventListener("pointerdown", trackGesture);
    slider.addEventListener("keydown", trackGesture);
  }

  el.inGain.addEventListener("input", () => {
    const clip = clipOf(state.selectedId);
    if (!clip) return;
    clip.gainDb = Number(el.inGain.value);
    el.outGain.textContent = clip.gainDb <= -60 ? "silencio" : `${clip.gainDb} dB`;
    refresh();
  });
  el.inGain.addEventListener("change", commitGesture);

  el.inFadeIn.addEventListener("input", () => {
    const clip = clipOf(state.selectedId);
    if (!clip) return;
    clip.fadeIn = Math.min(Number(el.inFadeIn.value), clipDuration(clip));
    el.outFadeIn.textContent = `${clip.fadeIn} s`;
    refresh();
  });
  el.inFadeIn.addEventListener("change", commitGesture);

  el.inFadeOut.addEventListener("input", () => {
    const clip = clipOf(state.selectedId);
    if (!clip) return;
    clip.fadeOut = Math.min(Number(el.inFadeOut.value), clipDuration(clip));
    el.outFadeOut.textContent = `${clip.fadeOut} s`;
    refresh();
  });
  el.inFadeOut.addEventListener("change", commitGesture);

  el.btnMute.addEventListener("click", () => {
    const clip = clipOf(state.selectedId);
    if (!clip) return;
    commit(() => {
      clip.gainDb = clip.gainDb <= -60 ? 0 : -60;
    });
  });

  el.btnGainAll.addEventListener("click", () => {
    const clip = clipOf(state.selectedId);
    if (!clip) return;
    commit(() => {
      for (const item of state.clips) item.gainDb = clip.gainDb;
    });
  });

  el.btnFadeAll.addEventListener("click", () => {
    const clip = clipOf(state.selectedId);
    if (!clip) return;
    commit(() => {
      for (const item of state.clips) {
        item.fadeIn = Math.min(clip.fadeIn, clipDuration(item));
        item.fadeOut = Math.min(clip.fadeOut, clipDuration(item));
      }
    });
  });

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
    const rect = el.tlRuler.getBoundingClientRect();
    const scale = pixelsPerSecond();
    seek((event.clientX - rect.left) / scale);
  });

  el.tlRuler.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      seek(state.cursor + (event.key === "ArrowRight" ? 1 : -1));
    }
  });

  // El cursor también se puede arrastrar sobre la regla.
  el.tlRuler.addEventListener("pointerdown", (event) => {
    el.tlRuler.setPointerCapture(event.pointerId);
    const rect = el.tlRuler.getBoundingClientRect();
    const scale = pixelsPerSecond();
    const move = (ev) => seek((ev.clientX - rect.left) / scale);
    move(event);
    el.tlRuler.addEventListener("pointermove", move);
    const up = () => {
      el.tlRuler.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointerup", up);
  });

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
      if (state.selectedId) {
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
    }
  });

  window.addEventListener("resize", () => {
    if (state.clips.length) renderTimeline();
    const clip = clipOf(state.selectedId);
    if (clip) drawInspectorWave(clip);
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

  /* ---------------- tramo: tiradores de la onda ---------------- */

  function wavePointer(event) {
    const clip = clipOf(state.selectedId);
    const asset = clip && assetOf(clip.assetId);
    if (!clip || !asset) return;
    const rect = el.wave.getBoundingClientRect();
    const x = Math.max(0, Math.min(event.clientX - rect.left, rect.width));
    const time = (x / rect.width) * asset.duration;

    const startPx = (clip.start / asset.duration) * rect.width;
    const endPx = (clip.end / asset.duration) * rect.width;
    const nearStart = Math.abs(x - startPx);
    const nearEnd = Math.abs(x - endPx);

    if (event.type === "pointerdown") {
      if (Math.min(nearStart, nearEnd) > 14) return;
      state.gestureBefore = snapshot();
      el.wave.dataset.handle = nearStart <= nearEnd ? "start" : "end";
      el.wave.setPointerCapture(event.pointerId);
    }

    const handle = el.wave.dataset.handle;
    if (!handle) return;

    if (handle === "start") {
      clip.start = Math.max(0, Math.min(time, clip.end - 0.05));
    } else {
      clip.end = Math.min(asset.duration, Math.max(time, clip.start + 0.05));
    }
    clip.fadeIn = Math.min(clip.fadeIn, clipDuration(clip));
    clip.fadeOut = Math.min(clip.fadeOut, clipDuration(clip));
    renderInspector();
    renderTimeline();
    updateTransport();
  }

  el.wave.addEventListener("pointerdown", wavePointer);
  el.wave.addEventListener("pointermove", (event) => {
    if (el.wave.dataset.handle) wavePointer(event);
  });
  el.wave.addEventListener("pointerup", () => {
    delete el.wave.dataset.handle;
    commitGesture();
  });
  el.wave.addEventListener("pointercancel", () => {
    delete el.wave.dataset.handle;
    commitGesture();
  });

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
