// Klipku UI (Milestone 1, tanpa framework).
const $ = (id) => document.getElementById(id);
const K = window.klipku;
let B = null; // { url, token }
let filePath = null;
let current = null; // sesi yang sedang dibuka
let es = null; // EventSource progres
let settingsCache = null;

const FORMAT_LABEL = { face916: 'Face 9:16', gaming916: 'Gaming', blurbg916: 'Blur BG', square: 'Square', landscape: 'Landscape' };
const FORMAT_HINT = {
  face916: 'Crop 9:16 mengikuti wajah. Dua orang dalam satu frame: dibagi atas-bawah.',
  gaming916: 'Atas: facecam (dicari otomatis). Bawah: gameplay dari tengah layar, penuh tanpa bar hitam.',
  blurbg916: 'Video utuh di tengah, latar belakang blur.',
  square: 'Kotak 1:1.',
  landscape: 'Tetap 16:9.',
};
const STAGES = ['fetch', 'transcribe', 'detect', 'render'];

// ---------- util ----------

async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(B.url + path, {
    method,
    headers: { 'X-Klipku-Token': B.token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${res.status}`);
  return data;
}
const media = (path) => `${B.url}${path}${path.includes('?') ? '&' : '?'}t=${B.token}`;

let toastTimer;
function toast(msg, bad = false) {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'toast' + (bad ? ' bad' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add('hidden'), bad ? 7000 : 3500);
}

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined) e.append(k);
  return e;
}

function chips(id, onChange) {
  const box = $(id);
  box.addEventListener('click', (ev) => {
    const b = ev.target.closest('button');
    if (!b) return;
    box.querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
    onChange && onChange(b.dataset.v);
  });
  return () => box.querySelector('button.on').dataset.v;
}

function fmtDur(s) {
  if (!s && s !== 0) return '';
  s = Math.round(s);
  return s >= 3600 ? `${Math.floor(s / 3600)}j ${Math.floor((s % 3600) / 60)}m` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

// ---------- navigasi ----------

function show(view) {
  document.querySelectorAll('.view').forEach((v) => v.classList.toggle('on', v.id === `view-${view}`));
  document.querySelectorAll('.nav').forEach((n) => n.classList.toggle('on', n.dataset.view === view || (view === 'session' && n.dataset.view === 'history')));
  if (view !== 'session' && view !== 'editor' && es) { es.close(); es = null; }
  if (view !== 'editor') { const v = $('ed-video'); if (v) v.pause(); }
  if (view === 'history') loadHistory();
  if (view === 'settings') loadSettings();
}
document.querySelectorAll('.nav').forEach((n) => n.addEventListener('click', () => show(n.dataset.view)));

// ---------- buat klip ----------

const getFormat = chips('format', (v) => {
  $('format-hint').textContent = FORMAT_HINT[v];
  $('split-box').classList.toggle('hidden', v !== 'gaming916');
});
const getDuration = chips('duration');
$('format-hint').textContent = FORMAT_HINT.face916;
$('split').addEventListener('input', () => { $('split-val').textContent = `${$('split').value}%`; });

function setFile(p) {
  filePath = p;
  $('drop').classList.toggle('has', !!p);
  $('drop-text').textContent = p || 'atau tarik file video ke sini (MP4, MOV, MKV)';
  $('clear-file').classList.toggle('hidden', !p);
  if (p) $('url').value = '';
}
$('pick').onclick = async () => { const p = await K.pickVideo(); if (p) setFile(p); };
$('clear-file').onclick = () => setFile(null);
$('url').addEventListener('input', () => { if ($('url').value.trim()) setFile(null); });
const drop = $('drop');
['dragenter', 'dragover'].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((e) => drop.addEventListener(e, () => drop.classList.remove('over')));
drop.addEventListener('drop', (ev) => {
  ev.preventDefault();
  const f = ev.dataTransfer.files[0];
  if (!f) return;
  if (!/\.(mp4|mov|mkv|webm|m4v|avi)$/i.test(f.name)) return toast('Format file belum didukung. Pakai MP4, MOV, MKV, WEBM.', true);
  setFile(K.pathForFile(f));
});
// cegah jendela membuka file yang di-drop di luar kotak
['dragover', 'drop'].forEach((e) => window.addEventListener(e, (ev) => ev.preventDefault()));

$('run').onclick = async () => {
  const url = $('url').value.trim();
  if (!url && !filePath) return toast('Tempel link video atau pilih file dulu.', true);
  const count = Math.max(1, Math.min(10, parseInt($('count').value, 10) || 5));
  $('run').disabled = true;
  try {
    const s = await api('/sessions', {
      method: 'POST',
      body: {
        url: url || null,
        file_path: url ? null : filePath,
        clip_count: count,
        clip_duration: getDuration(),
        format: getFormat(),
        resolution: $('res').value,
        subtitle_on: $('subs').checked,
        gaming_split: Number($('split').value) / 100,
        caption_style_id: $('style').value || null,
        title_overlay: $('title-ov').checked,
      },
    });
    $('url').value = '';
    setFile(null);
    openSession(s.id);
  } catch (e) {
    toast(e.message, true);
  } finally {
    $('run').disabled = false;
  }
};

async function refreshRunNote() {
  try {
    const st = await api('/settings');
    settingsCache = st;
    const p = st.settings.provider;
    const missing = [];
    const offline = st.settings.transcribe_mode === 'offline';
    if (!offline && !st.keys.groq) missing.push('Groq (untuk transkrip)');
    if (offline && !st.settings.whisper[st.settings.whisper_model].installed) missing.push('model transkrip offline (unduh di Pengaturan)');
    if (p !== 'groq' && !st.keys[p]) missing.push(st.providers[p].label);
    $('run-note').textContent = missing.length
      ? `Belum siap: ${missing.join(', ')}. Lengkapi di Pengaturan.`
      : `AI: ${st.providers[p].label} · ${st.settings.model} · Transkrip ${offline ? 'offline' : 'online'}`;
  } catch { /* backend belum siap */ }
}

// ---------- sesi: progres + galeri ----------

async function openSession(id) {
  current = id;
  show('session');
  $('gallery').innerHTML = '';
  $('s-log').textContent = '';
  await renderSession();
  if (es) es.close();
  es = new EventSource(media(`/sessions/${id}/events`));
  es.onmessage = (m) => onEvent(JSON.parse(m.data));
}

let renderTimer = null;
function scheduleRender() {
  clearTimeout(renderTimer);
  renderTimer = setTimeout(renderSession, 250);
}

function onEvent(e) {
  if (e.kind === 'log') {
    const pre = $('s-log');
    pre.textContent += e.text + '\n';
    pre.scrollTop = pre.scrollHeight;
  } else if (e.kind === 'progress') {
    setSteps(e.stage, e.pct);
  } else {
    scheduleRender();
  }
}

function setSteps(stage, pct) {
  const idx = STAGES.indexOf(stage);
  $('steps').querySelectorAll('li').forEach((li, i) => {
    li.classList.toggle('on', i === idx);
    li.classList.toggle('done', idx > i || stage === 'done');
  });
  const base = idx < 0 ? (stage === 'done' ? 100 : 0) : (idx * 100) / STAGES.length;
  const part = idx < 0 ? 0 : ((pct || 0) / STAGES.length);
  $('bar-fill').style.width = `${Math.min(100, base + part)}%`;
}

async function renderSession() {
  if (!current) return;
  let s;
  try { s = await api(`/sessions/${current}`); } catch (e) { return toast(e.message, true); }
  $('s-title').textContent = s.title || s.source_url || 'Sesi';
  $('s-meta').textContent = [s.width && `${s.width}×${s.height}`, s.duration_s && fmtDur(s.duration_s), s.source_url].filter(Boolean).join(' · ');
  const running = ['queued', 'fetch', 'transcribe', 'detect', 'render'].includes(s.stage);
  $('s-cancel').classList.toggle('hidden', !running);
  $('s-render').disabled = running || !s.clips.length;
  $('s-folder').dataset.path = s.result_dir;
  setSteps(s.stage, s.stage === 'done' ? 100 : 0);
  const err = $('s-error');
  const showErr = s.error && (s.stage === 'error' || s.stage === 'done');
  err.classList.toggle('hidden', !showErr);
  err.textContent = showErr ? (s.stage === 'error' ? `Gagal: ${s.error}` : s.error) : '';
  if (s.stage === 'cancelled') { err.classList.remove('hidden'); err.textContent = 'Dibatalkan.'; }

  const g = $('gallery');
  const playing = [...g.querySelectorAll('video')].some((v) => !v.paused);
  if (playing && !running) return; // jangan ganggu video yang sedang diputar
  g.innerHTML = '';
  if (!s.clips.length) {
    g.append(el('div', { class: 'empty' }, running ? 'Momen akan muncul di sini setelah AI selesai membaca transkrip.' : 'Belum ada klip.'));
    return;
  }
  for (const c of s.clips) g.append(clipCard(c, running));
}

function clipCard(c, running) {
  const v = c.status === 'done'
    ? el('video', { src: media(`/clips/${c.id}/video`) + `&v=${encodeURIComponent(c.updated_at)}`, controls: true, preload: 'metadata', poster: c.thumb_path ? media(`/clips/${c.id}/thumb`) + `&v=${encodeURIComponent(c.updated_at)}` : null })
    : el('div', { class: 'ph' }, c.status === 'failed' ? `Gagal render: ${c.error || ''}` : c.status === 'rendering' ? 'Sedang render…' : c.status === 'tracking' ? 'Mencari wajah…' : 'Menunggu render');
  const fmtSel = el('select', { 'aria-label': 'Format klip' },
    ...Object.entries(FORMAT_LABEL).map(([k, lbl]) => el('option', { value: k, selected: k === c.format }, lbl)));
  const subs = el('label', { class: 'switch' }, el('input', { type: 'checkbox', checked: !!c.subtitle_on }), 'Subtitle');
  const face = el('label', { class: 'switch' }, el('input', { type: 'checkbox', checked: !!c.face_tracking }), 'Ikuti wajah');
  const rerender = el('button', {
    class: 'ghost sm', disabled: running,
    onclick: async () => {
      try {
        await api(`/clips/${c.id}`, { method: 'PATCH', body: { format: fmtSel.value, subtitle_on: subs.querySelector('input').checked, face_tracking: face.querySelector('input').checked } });
        await api(`/clips/${c.id}/render`, { method: 'POST' });
        toast(`Klip ${c.rank} masuk antrian render.`);
        scheduleRender();
      } catch (e) { toast(e.message, true); }
    },
  }, 'Render ulang');
  const openBtn = c.output_path ? el('button', { class: 'ghost sm', onclick: () => K.showItem(c.output_path) }, 'Tampilkan file') : null;
  const editBtn = el('button', { class: 'ghost sm', disabled: running, onclick: () => openEditor(c.id) }, 'Edit caption & judul');
  return el('div', { class: 'clip' },
    v,
    el('div', { class: 'body' },
      el('div', { class: 'row between' },
        el('div', { class: 'score' }, el('b', {}, String(c.score_total)), el('span', {}, `Hook ${c.score_hook} · Flow ${c.score_flow} · Value ${c.score_value}`)),
        el('span', { class: `badge ${c.status}` }, c.status)),
      el('div', { class: 'title' }, c.hook_title),
      c.reason ? el('div', { class: 'hint' }, c.reason) : null,
      el('div', { class: 'hint' }, `${fmtDur(c.start_s)}–${fmtDur(c.end_s)} · ${Math.round(c.end_s - c.start_s)} dtk · ${FORMAT_LABEL[c.format]}`),
      trackNote(c) ? el('div', { class: 'hint' }, trackNote(c)) : null,
      el('div', { class: 'actions' }, editBtn, fmtSel, subs, face, rerender, openBtn)));
}

function trackNote(c) {
  const t = c.tracking;
  if (!t || !c.face_tracking) return '';
  if (c.format === 'gaming916') return t.facecam ? 'Facecam ketemu' : 'Facecam tidak ketemu: bagian atas pakai area tengah';
  if (!['face916', 'square'].includes(c.format)) return '';
  const parts = [`${t.shots} shot`, `${t.face_shots} dengan wajah`];
  if (c.format === 'face916' && t.dual_shots) parts.push(`${t.dual_shots} dibagi 2 orang`);
  return parts.join(' · ');
}

$('back').onclick = () => show('history');
$('s-folder').onclick = () => K.openPath($('s-folder').dataset.path);
$('s-cancel').onclick = async () => { await api(`/sessions/${current}/cancel`, { method: 'POST' }); toast('Membatalkan…'); };
$('s-render').onclick = async () => {
  try { await api(`/sessions/${current}/render-all`, { method: 'POST' }); scheduleRender(); } catch (e) { toast(e.message, true); }
};

// ---------- history ----------

async function loadHistory() {
  const box = $('history-list');
  box.innerHTML = '';
  let data;
  try { data = await api('/sessions'); } catch (e) { return toast(e.message, true); }
  if (!data.sessions.length) {
    box.append(el('div', { class: 'empty' }, 'Belum ada sesi. Mulai dari menu Buat klip.'));
    return;
  }
  for (const s of data.sessions) {
    const img = s.cover_clip ? el('img', { src: media(`/clips/${s.cover_clip}/thumb`), alt: '' }) : el('div', { class: 'noimg' });
    const del = el('button', {
      class: 'ghost sm',
      onclick: async (ev) => {
        ev.stopPropagation();
        if (!confirm('Hapus sesi ini dari History? File klip yang sudah jadi tetap ada di folder hasil.')) return;
        await api(`/sessions/${s.id}`, { method: 'DELETE' });
        loadHistory();
      },
    }, 'Hapus');
    box.append(el('div', { class: 'item', onclick: () => openSession(s.id) },
      img,
      el('div', { class: 'meta' },
        el('div', {}, s.title || s.source_url || s.source_path || 'Tanpa judul'),
        el('div', { class: 'hint' }, `${s.created_at.replace('T', ' ').slice(0, 16)} · ${s.done_count} klip jadi · ${s.stage}${s.error ? ' · ' + s.error : ''}`)),
      del));
  }
}

// ---------- pengaturan ----------

async function loadSettings() {
  let st;
  try { st = await api('/settings'); } catch (e) { return toast(e.message, true); }
  settingsCache = st;
  fillTranscribe(st.settings);
  fillWatermark(st.settings);
  loadYtdlp();
  const prov = $('provider');
  prov.innerHTML = '';
  for (const [k, p] of Object.entries(st.providers)) prov.append(el('option', { value: k, selected: k === st.settings.provider }, p.label));
  fillModels(st.settings.provider);
  $('model').value = st.settings.model;
  $('outdir').textContent = st.settings.output_dir_effective;

  const keys = $('keys');
  keys.innerHTML = '';
  for (const [k, p] of Object.entries(st.providers)) {
    const inp = el('input', { type: 'password', placeholder: st.keys[k] ? 'tersimpan (isi untuk mengganti)' : 'tempel API key', autocomplete: 'off' });
    const stat = el('span', { class: `st ${st.keys[k] ? 'ok' : 'hint'}` }, st.keys[k] ? 'tersimpan' : 'kosong');
    keys.append(el('div', { class: 'keyrow' },
      el('span', {}, p.label, ' ', el('a', { href: '#', class: 'hint', onclick: (ev) => { ev.preventDefault(); K.openExternal(p.key_url); } }, 'ambil key')),
      inp,
      el('button', { class: 'ghost sm', onclick: async () => {
        try { await api(`/settings/keys/${k}`, { method: 'PUT', body: { key: inp.value } }); toast(inp.value ? `Key ${p.label} disimpan.` : `Key ${p.label} dihapus.`); loadSettings(); refreshRunNote(); } catch (e) { toast(e.message, true); }
      } }, 'Simpan'),
      el('button', { class: 'ghost sm', onclick: async () => {
        stat.textContent = 'mengecek…';
        const r = await api(`/settings/test-key/${k}`, { method: 'POST' });
        stat.textContent = r.message; stat.className = `st ${r.ok ? 'ok' : 'bad'}`;
      } }, 'Tes'),
      stat));
  }
}

function fillModels(provider) {
  const dl = $('models');
  dl.innerHTML = '';
  (settingsCache.providers[provider].models || []).forEach((m) => dl.append(el('option', { value: m })));
}

$('provider').addEventListener('change', async () => {
  const p = $('provider').value;
  fillModels(p);
  const model = settingsCache.providers[p].models[0];
  $('model').value = model;
  await api('/settings', { method: 'PUT', body: { provider: p, model } });
  toast('Provider diganti.');
  refreshRunNote();
});
$('model').addEventListener('change', async () => {
  const m = $('model').value.trim();
  if (!m) return;
  await api('/settings', { method: 'PUT', body: { model: m } });
  toast('Model disimpan.');
  refreshRunNote();
});
$('pick-out').onclick = async () => {
  const p = await K.pickFolder();
  if (!p) return;
  try { await api('/settings', { method: 'PUT', body: { output_dir: p } }); loadSettings(); toast('Folder hasil diganti.'); } catch (e) { toast(e.message, true); }
};

$('selftest').onclick = async () => {
  const b = $('selftest');
  b.disabled = true; b.textContent = 'Menguji…';
  const t = $('selftest-out');
  t.innerHTML = '';
  try {
    const r = await api('/selftest', { method: 'POST' });
    for (const x of r.results) t.append(el('tr', {}, el('td', { class: x.ok ? 'ok' : 'bad' }, x.ok ? '✓' : '✗'), el('td', {}, x.test, el('div', { class: 'hint' }, x.detail))));
    t.append(el('tr', {}, el('td', { class: r.ok ? 'ok' : 'bad' }, r.ok ? '✓' : '✗'), el('td', {}, r.ok ? 'Semua lulus' : 'Ada yang gagal')));
  } catch (e) { toast(e.message, true); }
  b.disabled = false; b.textContent = 'Jalankan tes';
};

// ---------- preset caption & font ----------

let STYLES = [];
let FONTS = [];
const FONT_FILE = {
  'Poppins ExtraBold': 'Poppins-ExtraBold.ttf', 'Poppins SemiBold': 'Poppins-SemiBold.ttf',
  Anton: 'Anton-Regular.ttf', 'Bebas Neue': 'BebasNeue-Regular.ttf', 'Archivo Black': 'ArchivoBlack-Regular.ttf',
};
const loadedFonts = new Set();
function ensureFont(family) {
  if (loadedFonts.has(family) || !FONT_FILE[family]) return;
  loadedFonts.add(family);
  const f = new FontFace(family, `url(${media('/fonts/' + FONT_FILE[family])})`);
  f.load().then((ff) => { document.fonts.add(ff); drawOverlay(); }).catch(() => loadedFonts.delete(family));
}

async function loadStyles() {
  try {
    const r = await api('/caption-styles');
    STYLES = r.styles;
    FONTS = r.fonts;
    STYLES.forEach((x) => ensureFont(x.font_family));
    ensureFont('Poppins ExtraBold');
    const sel = $('style');
    const keep = sel.value || r.default;
    sel.innerHTML = '';
    STYLES.forEach((x) => sel.append(el('option', { value: x.id, selected: x.id === keep }, x.name)));
  } catch { /* belum siap */ }
}

// ---------- Caption Editor ----------

const ED = { clip: null, words: [], overrides: {}, styleId: null, session: null, raf: 0 };

function groupWords(ws, maxWords) {
  const groups = [];
  let cur = [];
  ws.forEach((w, i) => {
    cur.push(w);
    const nxt = ws[i + 1];
    if (cur.length >= maxWords || /[.,?!…;:]$/.test(w.t) || (nxt && nxt.s - w.e > 0.5) || !nxt) {
      groups.push(cur);
      cur = [];
    }
  });
  return groups;
}

function edStyle() { return STYLES.find((x) => x.id === ED.styleId) || STYLES[0]; }
function edActiveWords() {
  return ED.words
    .map((w) => ({ ...w, t: (ED.overrides[w.i] ?? w.orig).trim() }))
    .filter((w) => w.t);
}

async function openEditor(cid) {
  let r;
  try { r = await api(`/clips/${cid}/words`); } catch (e) { return toast(e.message, true); }
  ED.clip = r.clip;
  ED.words = r.words;
  ED.overrides = JSON.parse(r.clip.caption_overrides || '{}');
  ED.styleId = r.clip.caption_style_id || (r.style && r.style.id);
  ED.titles = [];
  if (!STYLES.length) await loadStyles();
  show('editor');
  const c = ED.clip;
  $('ed-head').textContent = `Edit klip ${c.rank}`;
  $('ed-title').value = c.hook_title;
  $('ed-title-ov').checked = !!c.title_overlay;
  $('ed-subs').checked = !!c.subtitle_on;
  $('ed-titles').innerHTML = '';
  const stage = $('ed-stage');
  stage.className = 'stage' + (c.format === 'square' ? ' square' : c.format === 'landscape' ? ' landscape' : c.format === 'blurbg916' ? ' blur' : '');
  const v = $('ed-video');
  v.src = media(`/sessions/${c.session_id}/source`);
  v.onloadedmetadata = () => { v.currentTime = c.start_s; drawOverlay(); };
  v.onerror = () => toast('Video sumber tidak bisa diputar (mungkin sudah dihapus). Caption tetap bisa diedit.', true);
  $('ed-seek').max = (c.end_s - c.start_s).toFixed(2);
  renderPresets();
  renderWords();
  fillCustomForm(edStyle());
  drawOverlay();
}

function edRel() { return Math.max(0, $('ed-video').currentTime - ED.clip.start_s); }

function drawOverlay() {
  if (!ED.clip || !$('view-editor').classList.contains('on')) return;
  const c = ED.clip;
  const st = edStyle();
  if (!st) return;
  const t = edRel();
  const stage = $('ed-stage');
  const H = stage.clientHeight;
  const land = c.format === 'landscape';
  const px = st.font_size * (land ? 0.75 * H / 1080 : H / 1920);
  const cap = $('ed-ov-cap');
  cap.innerHTML = '';
  let y = st.position_y;
  if (c.format === 'gaming916') y = c.gaming_split || 0.4;
  cap.style.top = `${y * 100}%`;
  if ($('ed-subs').checked) {
    const groups = groupWords(edActiveWords(), Math.max(1, st.words_per_line || 3));
    const dur = c.end_s - c.start_s;
    for (let gi = 0; gi < groups.length; gi++) {
      const g = groups[gi];
      const nxt = gi + 1 < groups.length ? groups[gi + 1][0].s : dur;
      const hold = Math.min(nxt, g[g.length - 1].e + 0.4);
      if (t < g[0].s || t >= hold) continue;
      let active = g.length - 1;
      for (let k = 0; k < g.length; k++) if (t >= g[k].s) active = k;
      const line = el('span');
      g.forEach((w, k) => {
        const txt = st.uppercase ? w.t.toUpperCase() : w.t;
        line.append(el('span', { style: `color:${k === active ? st.highlight_color : st.color}` }, txt), k < g.length - 1 ? ' ' : '');
      });
      Object.assign(line.style, {
        fontFamily: `"${st.font_family}"`, fontSize: `${px}px`, fontWeight: st.bold ? '700' : '400',
        color: st.color,
      });
      const o = Math.max(0, st.outline_px * (land ? 0.75 * H / 1080 : H / 1920));
      if (st.box) {
        Object.assign(line.style, { background: st.outline_color, padding: `${o * 0.6}px ${o}px`, boxDecorationBreak: 'clone' });
      } else if (o > 0) {
        line.style.webkitTextStroke = `${o * 2}px ${st.outline_color}`;
        line.style.paintOrder = 'stroke fill';
      }
      cap.append(line);
      break;
    }
  }
  const ti = $('ed-ov-title');
  const showTitle = $('ed-title-ov').checked && t < 3 && $('ed-title').value.trim();
  ti.classList.toggle('hidden', !showTitle);
  if (showTitle) {
    ti.innerHTML = '';
    ti.style.top = `${(c.format === 'gaming916' ? 0.06 : 0.10) * 100}%`;
    const sp = el('span', {}, $('ed-title').value.trim());
    sp.style.fontSize = `${76 * (land ? 0.75 * H / 1080 : H / 1920)}px`;
    ti.append(sp);
  }
  $('ed-seek').value = t.toFixed(2);
  $('ed-time').textContent = `${fmtDur(t)} / ${fmtDur(c.end_s - c.start_s)}`;
  document.querySelectorAll('#ed-words input').forEach((inp) => {
    inp.classList.toggle('now', t >= Number(inp.dataset.s) && t < Number(inp.dataset.e));
  });
}

function edLoop() {
  const v = $('ed-video');
  if (ED.clip && v.currentTime >= ED.clip.end_s) {
    v.pause();
    v.currentTime = ED.clip.start_s;
  }
  drawOverlay();
  if (!v.paused) ED.raf = requestAnimationFrame(edLoop);
}

$('ed-play').onclick = () => {
  const v = $('ed-video');
  if (v.paused) {
    if (v.currentTime < ED.clip.start_s || v.currentTime >= ED.clip.end_s) v.currentTime = ED.clip.start_s;
    v.play().catch(() => {});
  } else v.pause();
};
$('ed-video').addEventListener('play', () => { $('ed-play').textContent = 'Jeda'; ED.raf = requestAnimationFrame(edLoop); });
$('ed-video').addEventListener('pause', () => { $('ed-play').textContent = 'Putar'; cancelAnimationFrame(ED.raf); drawOverlay(); });
$('ed-video').addEventListener('seeked', drawOverlay);
$('ed-seek').addEventListener('input', () => { $('ed-video').currentTime = ED.clip.start_s + Number($('ed-seek').value); });
['ed-title', 'ed-title-ov', 'ed-subs'].forEach((id) => $(id).addEventListener('input', drawOverlay));
window.addEventListener('resize', drawOverlay);

function renderWords() {
  const box = $('ed-words');
  box.innerHTML = '';
  for (const w of ED.words) {
    const val = ED.overrides[w.i] ?? w.orig;
    const inp = el('input', { value: val, placeholder: w.orig, 'data-s': w.s, 'data-e': w.e, 'aria-label': `Kata ${w.orig}`, title: `Asli: ${w.orig}` });
    const fit = () => { inp.style.width = `calc(${Math.max(3, (inp.value || w.orig).length + 1)}ch + 14px)`; };
    const mark = () => {
      const v = inp.value;
      inp.classList.toggle('edited', v.trim() !== '' && v !== w.orig);
      inp.classList.toggle('deleted', v.trim() === '');
    };
    inp.addEventListener('focus', () => { $('ed-video').currentTime = ED.clip.start_s + w.s + 0.01; });
    inp.addEventListener('input', () => {
      if (inp.value === w.orig) delete ED.overrides[w.i];
      else ED.overrides[w.i] = inp.value;
      fit(); mark(); drawOverlay();
    });
    fit(); mark();
    box.append(inp);
  }
}
$('ed-reset').onclick = () => { ED.overrides = {}; renderWords(); drawOverlay(); };

function renderPresets() {
  const box = $('ed-presets');
  box.innerHTML = '';
  for (const st of STYLES.filter((x) => x.id !== '__draft')) {
    const sample = el('span', {}, st.uppercase ? 'KATA ' : 'Kata ', el('span', { style: `color:${st.highlight_color}` }, st.uppercase ? 'AKTIF' : 'aktif'));
    Object.assign(sample.style, { fontFamily: `"${st.font_family}"`, fontSize: '17px', color: st.color });
    if (st.box) Object.assign(sample.style, { background: st.outline_color, padding: '2px 6px' });
    else { sample.style.webkitTextStroke = `3px ${st.outline_color}`; sample.style.paintOrder = 'stroke fill'; }
    const b = el('button', { class: st.id === ED.styleId ? 'on' : '', onclick: () => { ED.styleId = st.id; renderPresets(); fillCustomForm(st); drawOverlay(); } },
      sample, el('span', { class: 'nm' }, st.name));
    if (!st.builtin) {
      b.append(el('span', {
        class: 'del', title: 'Hapus preset', role: 'button',
        onclick: async (ev) => {
          ev.stopPropagation();
          if (!confirm(`Hapus preset "${st.name}"?`)) return;
          try { await api(`/caption-styles/${st.id}`, { method: 'DELETE' }); await loadStyles(); if (ED.styleId === st.id) ED.styleId = STYLES[0].id; renderPresets(); drawOverlay(); } catch (e) { toast(e.message, true); }
        },
      }, '×'));
    }
    box.append(b);
  }
}

function fillCustomForm(st) {
  if (!st) return;
  const f = $('cs-font');
  f.innerHTML = '';
  FONTS.forEach((n) => f.append(el('option', { value: n, selected: n === st.font_family }, n)));
  $('cs-size').value = st.font_size;
  $('cs-color').value = st.color.toLowerCase();
  $('cs-hi').value = st.highlight_color.toLowerCase();
  $('cs-outline').value = st.outline_color.toLowerCase();
  $('cs-opx').value = st.outline_px;
  $('cs-wpl').value = st.words_per_line;
  $('cs-pos').value = Math.round(st.position_y * 100);
  $('cs-upper').checked = !!st.uppercase;
  $('cs-box').checked = !!st.box;
  $('cs-name').value = '';
}

function customFromForm() {
  return {
    font_family: $('cs-font').value, font_size: Number($('cs-size').value),
    color: $('cs-color').value.toUpperCase(), highlight_color: $('cs-hi').value.toUpperCase(),
    outline_color: $('cs-outline').value.toUpperCase(), outline_px: Number($('cs-opx').value),
    words_per_line: Number($('cs-wpl').value), position_y: Number($('cs-pos').value) / 100,
    uppercase: $('cs-upper').checked ? 1 : 0, box: $('cs-box').checked ? 1 : 0, bold: 0,
  };
}
// edit form = preview langsung (sementara, belum disimpan)
['cs-font', 'cs-size', 'cs-color', 'cs-hi', 'cs-outline', 'cs-opx', 'cs-wpl', 'cs-pos', 'cs-upper', 'cs-box'].forEach((id) =>
  $(id).addEventListener('input', () => {
    const tmp = { ...edStyle(), ...customFromForm(), id: '__draft', name: 'draft' };
    ensureFont(tmp.font_family);
    STYLES = STYLES.filter((x) => x.id !== '__draft').concat(tmp);
    ED.prevStyle = ED.styleId === '__draft' ? ED.prevStyle : ED.styleId;
    ED.styleId = '__draft';
    drawOverlay();
  }));
$('cs-save').onclick = async () => {
  const name = $('cs-name').value.trim();
  if (!name) return toast('Isi nama preset dulu.', true);
  try {
    const st = await api('/caption-styles', { method: 'POST', body: { name, ...customFromForm() } });
    await loadStyles();
    ED.styleId = st.id;
    renderPresets();
    drawOverlay();
    toast(`Preset "${name}" disimpan.`);
  } catch (e) { toast(e.message, true); }
};

$('ed-regen').onclick = async () => {
  const b = $('ed-regen');
  b.disabled = true; b.textContent = 'Membuat…';
  try {
    const r = await api(`/clips/${ED.clip.id}/regenerate-title`, { method: 'POST', body: {} });
    const box = $('ed-titles');
    box.innerHTML = '';
    r.titles.forEach((t) => box.append(el('button', { class: 'sm', onclick: () => { $('ed-title').value = t; drawOverlay(); } }, t)));
  } catch (e) { toast(e.message, true); }
  b.disabled = false; b.textContent = 'Buat ulang dengan AI';
};

async function edSave(render) {
  if (ED.styleId === '__draft') return toast('Simpan preset baru dulu, atau pilih salah satu preset.', true);
  try {
    await api(`/clips/${ED.clip.id}`, {
      method: 'PATCH',
      body: {
        hook_title: $('ed-title').value, title_overlay: $('ed-title-ov').checked, subtitle_on: $('ed-subs').checked,
        caption_style_id: ED.styleId, caption_overrides: ED.overrides,
      },
    });
    if (render) {
      await api(`/clips/${ED.clip.id}/render`, { method: 'POST' });
      toast(`Klip ${ED.clip.rank} masuk antrian render.`);
      openSession(ED.clip.session_id);
    } else toast('Tersimpan.');
  } catch (e) { toast(e.message, true); }
}
$('ed-save').onclick = () => edSave(false);
$('ed-render').onclick = () => edSave(true);
$('ed-back').onclick = () => openSession(ED.clip.session_id);

// ---------- Pengaturan: transkrip offline ----------

let modelTimer = null;
function fillTranscribe(st) {
  $('tr-mode').value = st.transcribe_mode || 'online';
  const sel = $('tr-model');
  sel.innerHTML = '';
  Object.entries(st.whisper).forEach(([k, m]) => sel.append(el('option', { value: k, selected: k === st.whisper_model }, m.label)));
  renderModels(st.whisper);
}

function renderModels(ws) {
  const box = $('tr-models');
  box.innerHTML = '';
  let busy = false;
  for (const [k, m] of Object.entries(ws)) {
    let right;
    if (m.installed) {
      right = el('span', { class: 'row' }, el('span', { class: 'ok' }, 'terpasang'),
        el('button', { class: 'ghost sm', onclick: async () => {
          if (!confirm(`Hapus model ${m.label}?`)) return;
          renderModels(await api(`/models/whisper/${k}`, { method: 'DELETE' }));
        } }, 'Hapus'));
    } else if (m.downloading) {
      busy = true;
      const bar = el('div', { class: 'bar' }, el('div', { style: `width:${m.pct}%` }));
      right = el('span', { class: 'row' }, bar, el('span', { class: 'hint' }, `${m.pct}%`));
    } else {
      right = el('span', { class: 'row' }, m.error ? el('span', { class: 'bad hint' }, m.error) : null,
        el('button', { class: 'ghost sm', onclick: async () => {
          renderModels(await api(`/models/whisper/${k}/download`, { method: 'POST' }));
        } }, 'Unduh'));
    }
    box.append(el('div', { class: 'model' }, el('span', { class: 'nm' }, m.label), right));
  }
  clearTimeout(modelTimer);
  if (busy) modelTimer = setTimeout(async () => renderModels(await api('/models/whisper')), 1500);
}

$('tr-mode').addEventListener('change', async () => {
  const mode = $('tr-mode').value;
  const st = await api('/settings', { method: 'PUT', body: { transcribe_mode: mode } });
  const m = st.settings.whisper[st.settings.whisper_model];
  toast(mode === 'offline' && !m.installed ? 'Mode offline aktif. Unduh modelnya dulu sebelum Run pipeline.' : 'Mode transkrip disimpan.');
  refreshRunNote();
});
$('tr-model').addEventListener('change', async () => {
  await api('/settings', { method: 'PUT', body: { whisper_model: $('tr-model').value } });
  toast('Model offline dipilih.');
  refreshRunNote();
});

// ---------- Pengaturan: watermark ----------

function fillWatermark(st) {
  const w = st.watermark || {};
  $('wm-mode').value = w.mode || 'off';
  $('wm-pos').value = w.position || 'tr';
  $('wm-text').value = w.text || '';
  $('wm-op').value = Math.round((w.opacity ?? 0.85) * 100);
  $('wm-size').value = Math.round((w.size ?? 0.18) * 100);
  const img = $('wm-preview');
  img.classList.toggle('hidden', !st.watermark_image);
  if (st.watermark_image) img.src = media('/settings/watermark-image') + `&v=${Date.now()}`;
  wmUi();
}
function wmUi() {
  const mode = $('wm-mode').value;
  document.querySelectorAll('.wm-text').forEach((e) => e.classList.toggle('hidden', mode !== 'text'));
  document.querySelectorAll('.wm-img').forEach((e) => e.classList.toggle('hidden', mode !== 'image'));
  $('wm-op-val').textContent = `${$('wm-op').value}%`;
  $('wm-size-val').textContent = `${$('wm-size').value}% lebar video`;
}
['wm-mode', 'wm-op', 'wm-size'].forEach((id) => $(id).addEventListener('input', wmUi));
$('wm-pick').onclick = async () => {
  const p = await K.pickImage();
  if (!p) return;
  try {
    await api('/settings/watermark-image', { method: 'PUT', body: { path: p } });
    const img = $('wm-preview');
    img.src = media('/settings/watermark-image') + `&v=${Date.now()}`;
    img.classList.remove('hidden');
    toast('Logo disimpan.');
  } catch (e) { toast(e.message, true); }
};
$('wm-save').onclick = async () => {
  const mode = $('wm-mode').value;
  if (mode === 'text' && !$('wm-text').value.trim()) return toast('Isi teks watermark dulu.', true);
  if (mode === 'image' && $('wm-preview').classList.contains('hidden')) return toast('Pilih gambar logo dulu.', true);
  try {
    await api('/settings', { method: 'PUT', body: { watermark: {
      mode, text: $('wm-text').value.trim(), position: $('wm-pos').value,
      opacity: Number($('wm-op').value) / 100, size: Number($('wm-size').value) / 100,
    } } });
    toast(mode === 'off' ? 'Watermark dimatikan.' : 'Watermark disimpan.');
  } catch (e) { toast(e.message, true); }
};

// ---------- Pengaturan: yt-dlp ----------

async function loadYtdlp() {
  $('yt-ver').textContent = 'Mengecek versi…';
  try {
    const r = await api('/engine/ytdlp');
    $('yt-ver').textContent = r.latest
      ? (r.active === r.latest ? `Versi ${r.active} (terbaru)` : `Versi ${r.active}, terbaru ${r.latest}`)
      : `Versi ${r.active} (tidak bisa cek versi terbaru)`;
  } catch { $('yt-ver').textContent = 'Versi tidak diketahui'; }
}
$('yt-update').onclick = async () => {
  const b = $('yt-update');
  b.disabled = true; b.textContent = 'Mengupdate…';
  try {
    const r = await api('/engine/update-ytdlp', { method: 'POST' });
    if (!r.updated) toast(`yt-dlp sudah versi terbaru (${r.latest}).`);
    else if (confirm(`yt-dlp ${r.latest} terpasang. Buka ulang Klipku sekarang supaya dipakai?`)) K.relaunch();
    else toast('Update berlaku setelah Klipku dibuka ulang.');
  } catch (e) { toast(e.message, true); }
  b.disabled = false; b.textContent = 'Update yt-dlp';
  loadYtdlp();
};

// ---------- start ----------

async function init() {
  B = await K.backendInfo();
  if (!B) { toast('Mesin app gagal start. Tutup lalu buka lagi Klipku.', true); return; }
  for (let i = 0; i < 60; i++) {
    try {
      const h = await api('/health');
      $('ver').textContent = `v${h.app}`;
      refreshRunNote();
      loadStyles();
      return;
    } catch { await new Promise((r) => setTimeout(r, 500)); }
  }
  toast('Mesin app tidak merespons. Buka Pengaturan > Tes mesin.', true);
}
init();
