// Klipku UI (tanpa framework).
const $ = (id) => document.getElementById(id);
const K = window.klipku;

// Konfigurasi Kreator untuk layar awal & tombol "Dukung Kreator".
const CREATOR = {
  youtubeHandle: '@misterisia',
  youtubeUrl: 'https://www.youtube.com/@misterisia?sub_confirmation=1',
  saweriaUrl: 'https://saweria.co/rayacill',
};

let B = null; // { url, token }
let APP_VERSION = '';
let filePath = null;
let current = null; // id sesi yang sedang dibuka
let es = null; // EventSource progres
let settingsCache = null;
let ready = { ok: false, missing: [] };

const FORMAT_LABEL = { face916: 'Face 9:16', gaming916: 'Gaming', blurbg916: 'Blur BG', square: 'Square', landscape: 'Landscape' };
const STAGES = ['fetch', 'transcribe', 'detect', 'render'];
const STAGE_LABEL = { queued: 'Menunggu antrian', fetch: 'Ambil video', transcribe: 'Transkrip', detect: 'Cari momen', render: 'Render', done: 'Selesai', error: 'Gagal', cancelled: 'Dibatalkan' };
// bobot tiap tahap terhadap total progres (render paling lama)
const WEIGHT = { fetch: 0.15, transcribe: 0.30, detect: 0.10, render: 0.45 };
const RUNNING = ['queued', 'fetch', 'transcribe', 'detect', 'render'];

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
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) e.append(k);
  return e;
}

// grup tombol pilih-satu (format, durasi, dsb.)
function choice(id, onChange) {
  const box = $(id);
  const set = (v) => box.querySelectorAll('button').forEach((x) => {
    const on = x.dataset.v === v;
    x.classList.toggle('on', on);
    if (x.getAttribute('role') === 'radio' || box.getAttribute('role') === 'radiogroup') x.setAttribute('aria-checked', String(on));
  });
  box.addEventListener('click', (ev) => {
    const b = ev.target.closest('button');
    if (!b || !box.contains(b)) return;
    set(b.dataset.v);
    onChange && onChange(b.dataset.v);
  });
  return { get: () => (box.querySelector('button.on') || {}).dataset?.v, set };
}

function fmtDur(s) {
  if (!s && s !== 0) return '';
  s = Math.round(s);
  return s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
function fmtElapsed(sec) {
  sec = Math.max(0, Math.round(sec));
  const m = Math.floor(sec / 60);
  return m ? `${m} mnt ${sec % 60} dtk` : `${sec} dtk`;
}
function overallPct(stage, pct) {
  if (stage === 'done') return 100;
  const i = STAGES.indexOf(stage);
  if (i < 0) return 0;
  const before = STAGES.slice(0, i).reduce((a, s) => a + WEIGHT[s], 0);
  return Math.min(100, (before + WEIGHT[stage] * (pct || 0) / 100) * 100);
}

// ---------- navigasi ----------

function show(view) {
  document.querySelectorAll('.view').forEach((v) => v.classList.toggle('on', v.id === `view-${view}`));
  const navKey = view === 'session' || view === 'editor' ? 'history' : view;
  document.querySelectorAll('.nav').forEach((n) => {
    const on = n.dataset.view === navKey;
    n.classList.toggle('on', on);
    if (on) n.setAttribute('aria-current', 'page'); else n.removeAttribute('aria-current');
  });
  if (view !== 'session' && view !== 'editor' && es) { es.close(); es = null; }
  if (view !== 'editor') { const v = $('ed-video'); if (v) v.pause(); }
  if (view === 'history') loadHistory();
  if (view === 'settings') loadSettings();
  if (view === 'create') refreshRunNote();
  document.querySelector('main').scrollTop = 0;
}
document.querySelectorAll('.nav').forEach((n) => n.addEventListener('click', () => show(n.dataset.view)));

// ---------- 1. sumber ----------

function setFile(p) {
  filePath = p;
  const name = p ? p.split(/[\\/]/).pop() : '';
  $('drop').classList.toggle('has-file', !!p);
  $('file-chip').classList.toggle('hidden', !p);
  $('source-alt').classList.toggle('hidden', !!p);
  $('file-name').textContent = name;
  $('file-name').title = p || '';
  if (p) $('url').value = '';
  updateRunButton();
}
$('pick').onclick = async () => { const p = await K.pickVideo(); if (p) setFile(p); };
$('clear-file').onclick = () => { setFile(null); $('url').focus(); };
$('url').addEventListener('input', () => { if ($('url').value.trim()) setFile(null); updateRunButton(); });
const drop = $('drop');
['dragenter', 'dragover'].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((e) => drop.addEventListener(e, () => drop.classList.remove('over')));
drop.addEventListener('drop', (ev) => {
  ev.preventDefault();
  const f = ev.dataTransfer.files[0];
  if (!f) return;
  if (!/\.(mp4|mov|mkv|webm|m4v|avi)$/i.test(f.name)) return toast('Format file belum didukung. Pakai MP4, MOV, MKV, atau WEBM.', true);
  setFile(K.pathForFile(f));
});
['dragover', 'drop'].forEach((e) => window.addEventListener(e, (ev) => ev.preventDefault()));

// ---------- 2. format ----------

const fmtChoice = choice('format', (v) => $('split-box').classList.toggle('hidden', v !== 'gaming916'));
$('split').addEventListener('input', () => { $('split-val').textContent = `${$('split').value}%`; });

// ---------- 3. caption (preset dipilih di sini, dirender oleh renderPresets) ----------

let createStyleId = null;
$('subs').addEventListener('change', () => $('create-presets').classList.toggle('dim', !$('subs').checked));

// ---------- opsi lainnya ----------

const durChoice = choice('duration', updateMoreSummary);
function updateMoreSummary() {
  const d = durChoice.get();
  const label = d === '30-60' ? '30–60 dtk' : `${d} dtk`;
  const compile = $('compile-mode').checked;
  $('count').disabled = compile;
  $('compile-hint').textContent = compile
    ? 'Semua potongan yang cocok di seluruh video akan dijahit jadi 1 klip.'
    : 'Kosongkan untuk cari momen viral otomatis seperti biasa.';
  const n = Math.max(1, Math.min(10, parseInt($('count').value, 10) || 5));
  $('more-sum').textContent = `Durasi ${label} · ${compile ? '1 klip kompilasi' : `${n} klip`} · ${$('res').selectedOptions[0].textContent}`;
}
['count', 'res'].forEach((id) => $(id).addEventListener('input', updateMoreSummary));
$('compile-mode').addEventListener('change', updateMoreSummary);

// ---------- kesiapan + tombol utama ----------

function updateRunButton() {
  const hasSource = !!($('url').value.trim() || filePath);
  $('run').disabled = !hasSource || !ready.ok;
  $('run').title = !hasSource ? 'Tempel link atau pilih file dulu' : !ready.ok ? 'Lengkapi pengaturan dulu' : '';
}

async function refreshRunNote() {
  try {
    const st = await api('/settings');
    settingsCache = st;
    const p = st.settings.provider;
    const offline = st.settings.transcribe_mode === 'offline';
    const missing = [];
    if (!offline && !st.keys.groq) missing.push('API key Groq untuk transkrip');
    if (offline && !st.settings.whisper[st.settings.whisper_model].installed) missing.push('model transkrip offline');
    if (!st.keys[p] && !(p === 'groq' && missing.length)) missing.push(`API key ${st.providers[p].label}`);
    ready = { ok: !missing.length, missing };
    const box = $('ready');
    box.innerHTML = '';
    if (missing.length) {
      box.append(el('span', { class: 'warn' }, `Belum siap: ${missing.join(', ')}.`),
        el('button', { class: 'ghost sm', onclick: () => show('settings') }, 'Buka Pengaturan'));
    } else {
      box.append(el('span', {}, `AI ${st.providers[p].label} · transkrip ${offline ? 'offline' : 'online'} · hasil ke ${st.settings.output_dir_effective}`));
    }
  } catch { /* backend belum siap */ }
  updateRunButton();
}

$('run').onclick = async () => {
  const url = $('url').value.trim();
  if (!url && !filePath) return toast('Tempel link video atau pilih file dulu.', true);
  $('run').disabled = true;
  try {
    const compile = $('compile-mode').checked;
    const s = await api('/sessions', {
      method: 'POST',
      body: {
        url: url || null,
        file_path: url ? null : filePath,
        clip_count: compile ? 1 : Math.max(1, Math.min(10, parseInt($('count').value, 10) || 5)),
        clip_duration: durChoice.get(),
        format: fmtChoice.get(),
        resolution: $('res').value,
        subtitle_on: $('subs').checked,
        gaming_split: Number($('split').value) / 100,
        caption_style_id: createStyleId,
        title_overlay: $('title-ov').checked,
        hook_clip_on: $('hook-clip').checked,
        prompt: $('prompt').value.trim() || null,
        compile_mode: compile,
      },
    });
    $('url').value = '';
    $('prompt').value = '';
    $('compile-mode').checked = false;
    updateMoreSummary();
    setFile(null);
    openSession(s.id);
    pollActive();
  } catch (e) {
    toast(e.message, true);
  } finally {
    updateRunButton();
  }
};

// ---------- sesi: progres + galeri ----------

const RUN = { start: null, stage: null, pct: 0, detail: '', timer: null };

async function openSession(id) {
  current = id;
  Object.assign(RUN, { start: null, stage: null, pct: 0, detail: '' });
  show('session');
  $('gallery').innerHTML = '';
  $('s-log').textContent = '';
  await renderSession();
  if (es) es.close();
  es = new EventSource(media(`/sessions/${id}/events`));
  es.onmessage = (m) => onEvent(JSON.parse(m.data));
  clearInterval(RUN.timer);
  RUN.timer = setInterval(tickTime, 1000);
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
    return;
  }
  if (e.kind === 'stage') {
    if (RUNNING.includes(e.stage) && (!RUN.start || ['done', 'error', 'cancelled'].includes(RUN.stage))) RUN.start = e.ts;
    RUN.stage = e.stage;
    if (!RUNNING.includes(e.stage)) RUN.end = e.ts;
    else RUN.end = null;
    RUN.pct = 0;
    RUN.detail = '';
    drawProgress();
    scheduleRender();
    return;
  }
  if (e.kind === 'progress') {
    if (!RUN.start) RUN.start = e.ts;
    RUN.stage = e.stage;
    RUN.pct = e.pct;
    RUN.detail = e.detail || '';
    drawProgress();
    return;
  }
  scheduleRender();
}

function tickTime() {
  if (!RUN.start) { $('p-time').textContent = ''; return; }
  const end = RUN.end || Date.now() / 1000;
  $('p-time').textContent = `${RUNNING.includes(RUN.stage) ? 'Berjalan' : 'Waktu'} ${fmtElapsed(end - RUN.start)}`;
}

function drawProgress(session) {
  const stage = RUN.stage || (session && session.stage) || 'queued';
  const running = RUNNING.includes(stage);
  const box = $('progress');
  box.classList.toggle('done', stage === 'done');
  box.classList.toggle('error', stage === 'error');
  box.classList.toggle('cancelled', stage === 'cancelled');
  box.classList.toggle('compact', stage === 'done');
  const total = stage === 'done' ? 100 : overallPct(stage, RUN.pct);
  $('p-pct').textContent = running || stage === 'done' ? `${Math.floor(total)}%` : STAGE_LABEL[stage];
  $('bar-fill').style.width = `${total}%`;
  if (ACTIVE_ID && ACTIVE_ID === current && running) {
    $('aj-pct').textContent = `${Math.floor(total)}%`;
    $('aj-bar').style.width = `${total}%`;
  }
  let label = STAGE_LABEL[stage] || stage;
  if (running && STAGES.includes(stage)) label = `${label} · ${Math.floor(RUN.pct || 0)}%`;
  if (stage === 'done' && session) {
    const ok = session.clips.filter((c) => c.status === 'done').length;
    label = `${ok} klip selesai`;
  }
  if (!running && stage !== 'done') $('p-pct').textContent = '';
  $('p-stage').textContent = label;
  $('p-detail').textContent = running ? RUN.detail : '';
  const idx = STAGES.indexOf(stage);
  $('steps').querySelectorAll('li').forEach((li, i) => {
    li.classList.toggle('on', i === idx);
    li.classList.toggle('done', idx > i || stage === 'done');
  });
  $('s-cancel').classList.toggle('hidden', !running);
  $('s-retry').classList.toggle('hidden', !['error', 'cancelled'].includes(stage));
  tickTime();
}

async function renderSession() {
  if (!current) return;
  let s;
  try { s = await api(`/sessions/${current}`); } catch (e) { return toast(e.message, true); }
  if (!RUN.stage || !RUNNING.includes(RUN.stage)) RUN.stage = s.stage;
  $('s-title').textContent = s.title || s.source_url || 'Sesi';
  $('s-title').title = s.title || s.source_url || '';
  $('s-meta').textContent = [s.width && `${s.width}×${s.height}`, s.duration_s && fmtDur(s.duration_s),
    s.created_at && s.created_at.replace('T', ' ').slice(0, 16)].filter(Boolean).join(' · ');
  const running = RUNNING.includes(s.stage);
  $('s-render').disabled = running || !s.clips.length;
  $('s-folder').dataset.path = s.result_dir;
  drawProgress(s);
  const err = $('s-error');
  const failed = s.clips.filter((c) => c.status === 'failed').length;
  const msg = s.stage === 'error' ? s.error : (s.stage === 'done' && failed ? `${failed} klip gagal dirender. Buka kartunya untuk detail, lalu render ulang.` : '');
  err.classList.toggle('hidden', !msg);
  err.textContent = msg || '';

  const g = $('gallery');
  const playing = [...g.querySelectorAll('video')].some((v) => !v.paused);
  if (playing && !running) return; // jangan ganggu video yang sedang diputar
  g.innerHTML = '';
  if (!s.clips.length) {
    if (!running) g.append(el('div', { class: 'empty' }, el('p', {}, s.stage === 'error' ? 'Belum ada klip karena proses berhenti sebelum AI memilih momen.' : 'Belum ada klip.')));
    return;
  }
  for (const c of s.clips) g.append(clipCard(c, running));
}

function clipSegs(c) {
  try {
    const arr = Array.isArray(c.segments) ? c.segments : JSON.parse(c.segments || '[]');
    return Array.isArray(arr) ? arr.filter((x) => x && Number(x.e) > Number(x.s)) : [];
  } catch { return []; }
}

function clipCard(c, running) {
  const shape = c.format === 'square' ? ' square' : c.format === 'landscape' ? ' landscape' : '';
  const ver = encodeURIComponent(c.updated_at);
  let inner;
  if (c.status === 'done') {
    inner = el('video', { src: `${media(`/clips/${c.id}/video`)}&v=${ver}`, controls: true, preload: 'metadata',
      poster: c.thumb_path ? `${media(`/clips/${c.id}/thumb`)}&v=${ver}` : null });
  } else {
    const txt = c.status === 'failed' ? 'Render gagal' : c.status === 'rendering' ? 'Sedang render…'
      : c.status === 'tracking' ? 'Mencari wajah…' : c.status === 'cancelled' ? 'Dibatalkan' : 'Menunggu giliran';
    inner = el('div', { class: 'ph' }, el('span', { class: c.status === 'failed' ? 'state-err' : '' }, txt),
      c.status === 'failed' && c.error ? el('span', { class: 'sub' }, c.error.slice(0, 140)) : null);
  }
  const segs = clipSegs(c);
  const dur = segs.length ? segs.reduce((a, x) => a + (Number(x.e) - Number(x.s)), 0) : (c.end_s - c.start_s);
  const metaTxt = segs.length
    ? `${Math.round(dur)} dtk · ${segs.length} potongan · ${FORMAT_LABEL[c.format]}`
    : `${Math.round(dur)} dtk · ${FORMAT_LABEL[c.format]} · mulai ${fmtDur(c.start_s)}`;
  const note = trackNote(c);
  return el('article', { class: `clip ${c.status}` },
    el('div', { class: `media${shape}` }, inner,
      el('span', { class: 'rank' }, `#${c.rank}`),
      el('span', { class: 'score', title: `Hook ${c.score_hook} · Flow ${c.score_flow} · Value ${c.score_value}` }, String(c.score_total))),
    el('div', { class: 'title', title: c.reason || '' }, c.hook_title),
    el('div', { class: 'meta' }, metaTxt),
    note ? el('div', { class: 'sub' }, note) : null,
    el('div', { class: 'actions' },
      el('button', { class: 'primary sm', disabled: running, onclick: () => openEditor(c.id) }, 'Edit'),
      c.output_path ? el('button', { class: 'ghost sm', onclick: () => K.showItem(c.output_path) }, 'Tampilkan file') : null,
      c.status === 'failed' ? el('button', { class: 'ghost sm', disabled: running, onclick: () => rerenderClip(c) }, 'Render ulang') : null));
}

async function rerenderClip(c) {
  try {
    await api(`/clips/${c.id}/render`, { method: 'POST' });
    toast(`Klip #${c.rank} masuk antrian render.`);
    scheduleRender();
  } catch (e) { toast(e.message, true); }
}

function trackNote(c) {
  const tags = [];
  const segs = clipSegs(c);
  if (segs.length) tags.push(`✂️ Kompilasi ${segs.length} potongan`);
  if (c.heat_score >= 55) tags.push(`🔥 Most Replayed ${c.heat_score}%`);
  if (c.hook_clip_on) tags.push('⚡ Hook 5 dtk');
  if (c.format === 'gaming916') {
    try {
      const gc = JSON.parse(c.gaming_crop || '{}');
      if (gc.mode === 'manual') {
        tags.push('Crop manual');
        return tags.join(' · ');
      }
    } catch { /* abaikan */ }
  }
  const t = c.tracking;
  if (t && c.face_tracking) {
    if (c.format === 'gaming916') tags.push(t.facecam ? 'Facecam ketemu' : 'Facecam tengah');
    else if (['face916', 'square'].includes(c.format)) {
      if (t.face_shots < t.shots) tags.push(`${t.shots - t.face_shots} shot tanpa wajah`);
      if (c.format === 'face916' && t.dual_shots) tags.push(`${t.dual_shots} shot 2 orang`);
    }
  }
  return tags.join(' · ');
}

$('back').onclick = () => show('history');
$('s-folder').onclick = () => K.openPath($('s-folder').dataset.path);
$('s-cancel').onclick = async () => { await api(`/sessions/${current}/cancel`, { method: 'POST' }); toast('Membatalkan…'); };
$('s-retry').onclick = async () => {
  try { await api(`/sessions/${current}/retry`, { method: 'POST' }); RUN.start = null; scheduleRender(); pollActive(); } catch (e) { toast(e.message, true); }
};
$('s-render').onclick = async () => {
  try { await api(`/sessions/${current}/render-all`, { method: 'POST' }); RUN.start = null; scheduleRender(); pollActive(); } catch (e) { toast(e.message, true); }
};

// ---------- indikator proses di sidebar ----------

let activeTimer = null;
let ACTIVE_ID = null;
async function pollActive() {
  clearTimeout(activeTimer);
  let a = null;
  try { a = (await api('/active')).session; } catch { /* abaikan */ }
  const box = $('active-job');
  box.classList.toggle('hidden', !a);
  ACTIVE_ID = a ? a.id : null;
  if (a) {
    // sesi yang sedang dibuka: pakai angka dari stream supaya sama dengan panel progres
    const pct = a.id === current && RUN.stage ? Math.floor(overallPct(RUN.stage, RUN.pct)) : Math.floor(overallPct(a.stage, a.pct));
    $('aj-pct').textContent = `${pct}%`;
    $('aj-bar').style.width = `${pct}%`;
    $('aj-title').textContent = a.title || a.source_url || 'Video';
    box.onclick = () => openSession(a.id);
    activeTimer = setTimeout(pollActive, 1000);
  }
}

// ---------- history ----------

function statusChip(s) {
  if (RUNNING.includes(s.stage)) return el('span', { class: 'st run' }, 'Sedang diproses');
  if (s.stage === 'done') return el('span', { class: 'st done' }, `Selesai · ${s.done_count} klip`);
  if (s.stage === 'error') return el('span', { class: 'st error', title: s.error || '' }, 'Gagal');
  if (s.stage === 'cancelled') return el('span', { class: 'st cancelled' }, 'Dibatalkan');
  return el('span', { class: 'st' }, s.stage);
}

async function loadHistory() {
  const box = $('history-list');
  box.innerHTML = '';
  let data;
  try { data = await api('/sessions'); } catch (e) { return toast(e.message, true); }
  if (!data.sessions.length) {
    box.append(el('div', { class: 'empty' }, el('p', {}, 'Belum ada sesi. Klip yang kamu buat akan tersimpan di sini.'),
      el('button', { class: 'primary', onclick: () => show('create') }, 'Buat klip pertama')));
    return;
  }
  for (const s of data.sessions) {
    const img = s.cover_clip ? el('img', { src: media(`/clips/${s.cover_clip}/thumb`), alt: '' }) : el('div', { class: 'noimg' });
    const del = el('button', {
      class: 'ghost sm', 'aria-label': 'Hapus sesi',
      onclick: async (ev) => {
        ev.stopPropagation();
        if (!confirm('Hapus sesi ini dari History? File klip yang sudah jadi tetap ada di folder hasil.')) return;
        await api(`/sessions/${s.id}`, { method: 'DELETE' });
        loadHistory();
      },
    }, 'Hapus');
    const when = new Date(s.created_at).toLocaleString('id-ID', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
    box.append(el('div', { class: 'item', role: 'button', tabindex: '0', onclick: () => openSession(s.id),
      onkeydown: (ev) => { if (ev.key === 'Enter') openSession(s.id); } },
      img,
      el('div', { class: 'meta' },
        el('div', { class: 't' }, s.title || s.source_url || s.source_path || 'Tanpa judul'),
        el('div', { class: 'hint' }, [when, s.duration_s ? `video ${fmtDur(s.duration_s)}` : null].filter(Boolean).join(' · '))),
      statusChip(s),
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
  if (APP_VERSION && !$('app-ver-status').dataset.checked) {
    $('app-ver-status').textContent = `Versi ${APP_VERSION}`;
  }
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
    if (!createStyleId || !STYLES.some((x) => x.id === createStyleId)) createStyleId = r.default;
    renderCreatePresets();
  } catch { /* belum siap */ }
}

// ---------- Caption Editor ----------

const ED = {
  clip: null, words: [], overrides: {}, styleId: null, session: null, raf: 0,
  source: { width: 1920, height: 1080, duration_s: 0 }, facecam: null,
  gamingCrop: { mode: 'auto', facecam: null, gameplay: null },
  start_s: 0, end_s: 30, segments: [], curSeg: 0, wordsTimer: null,
};

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

// ---------- Gaming Manual Crop ----------

function gcSourceAR() {
  const v = $('ed-video');
  const w = (v && v.videoWidth) || ED.source.width || 1920;
  const h = (v && v.videoHeight) || ED.source.height || 1080;
  return w / Math.max(1, h);
}
function gcSplit() { return Math.max(0.25, Math.min(0.60, Number($('ed-split').value || 40) / 100)); }
function gcNormRatio(which) {
  const split = gcSplit();
  const outAR = 9 / 16;
  const targetPixelRatio = which === 'facecam' ? (outAR / split) : (outAR / (1 - split));
  return targetPixelRatio / gcSourceAR();
}
function gcFitBox(box, which) {
  const nr = gcNormRatio(which);
  let [x, y, w, h] = box;
  const cx = x + w / 2;
  const cy = y + h / 2;
  h = Math.max(0.08, Math.min(1, h));
  w = h * nr;
  if (w > 1) { w = 1; h = w / nr; }
  if (w < 0.08) { w = 0.08; h = Math.min(1, w / nr); w = h * nr; }
  x = Math.max(0, Math.min(1 - w, cx - w / 2));
  y = Math.max(0, Math.min(1 - h, cy - h / 2));
  return [x, y, w, h];
}
function gcDefaultBoxes() {
  const nrFc = gcNormRatio('facecam');
  const nrGp = gcNormRatio('gameplay');
  let fc;
  if (ED.facecam && ED.facecam.rect && ED.facecam.rect.length === 4) {
    const [x0, y0, x1, y1] = ED.facecam.rect;
    fc = gcFitBox([x0, y0, Math.max(0.12, x1 - x0), Math.max(0.12, y1 - y0)], 'facecam');
  } else if (ED.facecam && typeof ED.facecam.cx === 'number') {
    const h = Math.max(0.22, Math.min(0.45, (ED.facecam.h || 0.12) * 2.5));
    const w = Math.min(0.9, h * nrFc);
    fc = gcFitBox([ED.facecam.cx - w / 2, ED.facecam.cy - h * 0.45, w, h], 'facecam');
  } else {
    const h = 0.30;
    const w = Math.min(0.5, h * nrFc);
    fc = gcFitBox([0.03, 0.04, w, h], 'facecam');
  }
  const gpH = Math.min(1, 1 / Math.max(nrGp, 1));
  const gpW = gpH * nrGp;
  const gp = gcFitBox([(1 - gpW) / 2, (1 - gpH) / 2, gpW, gpH], 'gameplay');
  return { facecam: fc, gameplay: gp };
}
function gcEnsureBoxes() {
  const defs = gcDefaultBoxes();
  if (!Array.isArray(ED.gamingCrop.facecam) || ED.gamingCrop.facecam.length !== 4) ED.gamingCrop.facecam = defs.facecam;
  else ED.gamingCrop.facecam = gcFitBox(ED.gamingCrop.facecam, 'facecam');
  if (!Array.isArray(ED.gamingCrop.gameplay) || ED.gamingCrop.gameplay.length !== 4) ED.gamingCrop.gameplay = defs.gameplay;
  else ED.gamingCrop.gameplay = gcFitBox(ED.gamingCrop.gameplay, 'gameplay');
}
function gcIsManual() { return ED.format === 'gaming916' && ED.gamingCrop.mode === 'manual'; }

function gcRenderBoxes() {
  if (!gcIsManual()) return;
  gcEnsureBoxes();
  $('gc-board').style.aspectRatio = String(gcSourceAR());
  for (const [key, id] of [['facecam', 'gc-box-fc'], ['gameplay', 'gc-box-gp']]) {
    const [x, y, w, h] = ED.gamingCrop[key];
    const b = $(id);
    b.style.left = `${x * 100}%`;
    b.style.top = `${y * 100}%`;
    b.style.width = `${w * 100}%`;
    b.style.height = `${h * 100}%`;
  }
}

function gcDrawCanvases() {
  const manual = gcIsManual();
  const prev = $('ed-gaming-preview');
  prev.classList.toggle('hidden', !manual);
  if (!manual) return;
  gcEnsureBoxes();
  const v = $('ed-video');
  const hasVideo = v && v.readyState >= 2 && v.videoWidth > 0 && v.videoHeight > 0;

  // 1. Papan crop (video sumber penuh)
  const board = $('gc-board');
  const c1 = $('gc-canvas');
  const bw = Math.max(2, Math.round(board.clientWidth * (window.devicePixelRatio || 1)));
  const bh = Math.max(2, Math.round(board.clientHeight * (window.devicePixelRatio || 1)));
  if (c1.width !== bw || c1.height !== bh) { c1.width = bw; c1.height = bh; }
  const ctx1 = c1.getContext('2d');
  ctx1.clearRect(0, 0, bw, bh);
  if (hasVideo) {
    ctx1.drawImage(v, 0, 0, bw, bh);
    // sedikit redupkan keseluruhan supaya kotak crop makin jelas terbaca
    ctx1.fillStyle = 'rgba(5, 8, 15, 0.34)';
    ctx1.fillRect(0, 0, bw, bh);
    // tampilkan area crop dengan kecerahan penuh
    for (const key of ['gameplay', 'facecam']) {
      const [x, y, w, h] = ED.gamingCrop[key];
      ctx1.drawImage(v, x * v.videoWidth, y * v.videoHeight, w * v.videoWidth, h * v.videoHeight,
        x * bw, y * bh, w * bw, h * bh);
    }
  }

  // 2. Preview 9:16 langsung (atas Facecam, bawah Gameplay)
  const stage = $('ed-stage');
  const pw = Math.max(2, Math.round(stage.clientWidth * (window.devicePixelRatio || 1)));
  const ph = Math.max(2, Math.round(stage.clientHeight * (window.devicePixelRatio || 1)));
  if (prev.width !== pw || prev.height !== ph) { prev.width = pw; prev.height = ph; }
  const ctx2 = prev.getContext('2d');
  ctx2.fillStyle = '#06090f';
  ctx2.fillRect(0, 0, pw, ph);
  if (hasVideo) {
    const split = gcSplit();
    const topH = Math.round(ph * split);
    const botH = ph - topH;
    const [fx, fy, fw, fh] = ED.gamingCrop.facecam;
    const [gx, gy, gw, gh] = ED.gamingCrop.gameplay;
    ctx2.drawImage(v, fx * v.videoWidth, fy * v.videoHeight, fw * v.videoWidth, fh * v.videoHeight, 0, 0, pw, topH);
    ctx2.drawImage(v, gx * v.videoWidth, gy * v.videoHeight, gw * v.videoWidth, gh * v.videoHeight, 0, topH, pw, botH);
    ctx2.fillStyle = 'rgba(255, 255, 255, 0.18)';
    ctx2.fillRect(0, topH - 1, pw, 2);
  }
}

function gcSetupInteractions() {
  const board = $('gc-board');
  let drag = null;
  board.addEventListener('pointerdown', (ev) => {
    const boxEl = ev.target.closest('.crop-box');
    if (!boxEl) return;
    ev.preventDefault();
    const which = boxEl.dataset.box;
    const handle = ev.target.dataset.h || 'move';
    board.querySelectorAll('.crop-box').forEach((b) => b.classList.toggle('active', b === boxEl));
    const rect = board.getBoundingClientRect();
    drag = {
      which, handle, rect,
      startX: ev.clientX, startY: ev.clientY,
      orig: [...ED.gamingCrop[which]],
      nr: gcNormRatio(which),
    };
    boxEl.setPointerCapture(ev.pointerId);
  });
  board.addEventListener('pointermove', (ev) => {
    if (!drag) return;
    const { which, handle, rect, startX, startY, orig, nr } = drag;
    const [ox, oy, ow, oh] = orig;
    const dx = (ev.clientX - startX) / Math.max(1, rect.width);
    const dy = (ev.clientY - startY) / Math.max(1, rect.height);
    if (handle === 'move') {
      const nx = Math.max(0, Math.min(1 - ow, ox + dx));
      const ny = Math.max(0, Math.min(1 - oh, oy + dy));
      ED.gamingCrop[which] = [nx, ny, ow, oh];
    } else {
      const ax = handle.includes('w') ? ox + ow : ox;
      const ay = handle.includes('n') ? oy + oh : oy;
      const maxW = handle.includes('w') ? ax : 1 - ax;
      const maxH = handle.includes('n') ? ay : 1 - ay;
      const px = Math.max(0, Math.min(1, (ev.clientX - rect.left) / Math.max(1, rect.width)));
      const py = Math.max(0, Math.min(1, (ev.clientY - rect.top) / Math.max(1, rect.height)));
      const candW = handle.includes('w') ? ax - px : px - ax;
      const candH = handle.includes('n') ? ay - py : py - ay;
      let nh = Math.max(candH, candW / nr);
      const minH = Math.max(0.08, 0.08 / nr);
      const capH = Math.min(maxH, maxW / nr);
      nh = Math.max(Math.min(minH, capH), Math.min(capH, nh));
      const nw = nh * nr;
      const nx = handle.includes('w') ? ax - nw : ax;
      const ny = handle.includes('n') ? ay - nh : ay;
      ED.gamingCrop[which] = [Math.max(0, nx), Math.max(0, ny), nw, nh];
    }
    gcRenderBoxes();
    gcDrawCanvases();
  });
  const endDrag = () => { drag = null; };
  board.addEventListener('pointerup', endDrag);
  board.addEventListener('pointercancel', endDrag);

  document.querySelectorAll('[data-fc-corner]').forEach((btn) => {
    btn.addEventListener('click', () => {
      gcEnsureBoxes();
      const [, , w, h] = ED.gamingCrop.facecam;
      const m = 0.02;
      const c = btn.dataset.fcCorner;
      const x = c.includes('r') ? Math.max(0, 1 - w - m) : m;
      const y = c.includes('b') ? Math.max(0, 1 - h - m) : m;
      ED.gamingCrop.facecam = [x, y, w, h];
      gcRenderBoxes();
      gcDrawCanvases();
    });
  });
  $('gc-reset').onclick = () => {
    const defs = gcDefaultBoxes();
    ED.gamingCrop.facecam = defs.facecam;
    ED.gamingCrop.gameplay = defs.gameplay;
    gcRenderBoxes();
    gcDrawCanvases();
  };
  $('gc-apply-all').onclick = async () => {
    if (!ED.clip) return;
    gcEnsureBoxes();
    const b = $('gc-apply-all');
    b.disabled = true;
    try {
      const r = await api(`/sessions/${ED.clip.session_id}/gaming-crop`, {
        method: 'PUT',
        body: { gaming_crop: ED.gamingCrop, gaming_split: gcSplit(), set_format: true },
      });
      toast(`Crop Gaming diterapkan ke semua ${r.updated} klip di sesi ini.`);
    } catch (e) { toast(e.message, true); }
    b.disabled = false;
  };
}
gcSetupInteractions();

function fmtDurDec(s) {
  if (!Number.isFinite(s)) return '0:00.0';
  s = Math.max(0, s);
  const whole = Math.floor(s);
  const tenths = Math.floor((s - whole) * 10 + 1e-6);
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const sec = String(whole % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}.${tenths}` : `${m}:${sec}.${tenths}`;
}

function edIsCompiled() { return Array.isArray(ED.segments) && ED.segments.length > 0; }
function edSourceDur() {
  const v = $('ed-video');
  const vd = v && Number.isFinite(v.duration) && v.duration > 0 ? v.duration : 0;
  return Math.max(vd, Number(ED.source.duration_s) || 0, ED.end_s || 0, 10);
}
function edTotalDur() {
  if (edIsCompiled()) return Math.max(0.5, ED.segments.reduce((a, x) => a + Math.max(0, x.e - x.s), 0));
  return Math.max(3, ED.end_s - ED.start_s);
}
function edMapRelToSrc(relT) {
  if (!edIsCompiled()) return { srcT: Math.max(ED.start_s, Math.min(ED.end_s, ED.start_s + relT)), segIdx: 0 };
  let acc = 0;
  for (let i = 0; i < ED.segments.length; i++) {
    const sg = ED.segments[i];
    const d = Math.max(0.1, sg.e - sg.s);
    if (relT <= acc + d || i === ED.segments.length - 1) {
      return { srcT: Math.max(sg.s, Math.min(sg.e, sg.s + (relT - acc))), segIdx: i };
    }
    acc += d;
  }
  return { srcT: ED.segments[0].s, segIdx: 0 };
}
function edRel() {
  const v = $('ed-video');
  const ct = v ? v.currentTime : 0;
  if (!edIsCompiled()) return Math.max(0, Math.min(edTotalDur(), ct - ED.start_s));
  let idx = Math.max(0, Math.min(ED.segments.length - 1, ED.curSeg || 0));
  const cur = ED.segments[idx];
  if (!(ct >= cur.s - 0.15 && ct <= cur.e + 0.15)) {
    const found = ED.segments.findIndex((sg) => ct >= sg.s - 0.15 && ct <= sg.e + 0.15);
    if (found >= 0) { idx = found; ED.curSeg = found; }
  }
  let acc = 0;
  for (let i = 0; i < idx; i++) acc += Math.max(0, ED.segments[i].e - ED.segments[i].s);
  const sg = ED.segments[idx];
  return Math.max(0, acc + Math.max(0, Math.min(sg.e - sg.s, ct - sg.s)));
}

function edSyncPeakBounds() {
  if (!ED.clip) return;
  const total = edTotalDur();
  const hookDur = Math.min(5, Math.max(2.5, total * 0.25));
  const peakMin = edIsCompiled() ? ED.segments[0].s : ED.start_s;
  const peakMax = edIsCompiled()
    ? Math.max(peakMin, ED.segments[ED.segments.length - 1].e - 2.5)
    : Math.max(ED.start_s, ED.end_s - hookDur);
  const cur = Number($('ed-peak').value);
  $('ed-peak').min = peakMin.toFixed(1);
  $('ed-peak').max = peakMax.toFixed(1);
  if (!Number.isFinite(cur) || cur < peakMin || cur > peakMax) {
    const defPeak = ED.clip.peak_s != null ? ED.clip.peak_s : (peakMin + (peakMax - peakMin) * 0.65);
    $('ed-peak').value = Math.max(peakMin, Math.min(peakMax, defPeak)).toFixed(1);
  }
}

function edRefreshWords() {
  clearTimeout(ED.wordsTimer);
  ED.wordsTimer = setTimeout(async () => {
    if (!ED.clip) return;
    try {
      const q = edIsCompiled()
        ? `?segments=${encodeURIComponent(JSON.stringify(ED.segments))}`
        : `?start_s=${ED.start_s.toFixed(3)}&end_s=${ED.end_s.toFixed(3)}`;
      const r = await api(`/clips/${ED.clip.id}/words${q}`);
      ED.words = r.words || [];
      renderWords();
      drawOverlay();
    } catch { /* abaikan error sementara */ }
  }, 150);
}

function edRenderDurUi() {
  if (!ED.clip) return;
  const compiled = edIsCompiled();
  $('ed-trim-box').classList.toggle('hidden', compiled);
  $('ed-segs-box').classList.toggle('hidden', !compiled);
  const tot = edTotalDur();
  const srcD = edSourceDur();
  if (!compiled) {
    $('ed-dur-badge').textContent = `${tot.toFixed(1)} dtk · sumber ${fmtDur(srcD)}`;
    $('ed-start-val').textContent = fmtDurDec(ED.start_s);
    $('ed-end-val').textContent = fmtDurDec(ED.end_s);
  } else {
    $('ed-dur-badge').textContent = `${ED.segments.length} potongan · total ${tot.toFixed(1)} dtk`;
    const box = $('ed-segs-list');
    box.innerHTML = '';
    ED.segments.forEach((sg, idx) => {
      const d = Math.max(0.5, sg.e - sg.s);
      const adjust = (edge, delta) => {
        const lim = edSourceDur();
        if (edge === 's') {
          const ns = Math.max(0, Math.min(sg.e - 0.8, sg.s + delta));
          sg.s = Math.round(ns * 100) / 100;
        } else {
          const ne = Math.min(lim, Math.max(sg.s + 0.8, sg.e + delta));
          sg.e = Math.round(ne * 100) / 100;
        }
        ED.curSeg = idx;
        $('ed-video').currentTime = edge === 's' ? sg.s : Math.max(sg.s, sg.e - 1.5);
        edOnRangeUpdated();
      };
      box.append(el('div', { class: `seg-item${idx === ED.curSeg ? ' now' : ''}`, 'data-seg': String(idx) },
        el('span', { class: 'seg-idx tnum' }, `#${idx + 1}`),
        el('div', { class: 'seg-meta' },
          el('span', { class: 'seg-time tnum' }, `${fmtDurDec(sg.s)} – ${fmtDurDec(sg.e)} (${d.toFixed(1)} dtk)`),
          sg.note ? el('span', { class: 'seg-note', title: sg.note }, sg.note) : null),
        el('div', { class: 'seg-acts' },
          el('button', { type: 'button', class: 'ghost xs', title: 'Putar potongan ini', onclick: () => {
            ED.curSeg = idx;
            const v = $('ed-video');
            v.currentTime = sg.s;
            v.play().catch(() => {});
            edRenderDurUi();
          } }, '▶'),
          el('button', { type: 'button', class: 'ghost xs', title: 'Mundurkan awal 2 detik', onclick: () => adjust('s', -2) }, 'Awal −2s'),
          el('button', { type: 'button', class: 'ghost xs', title: 'Majukan awal 2 detik', onclick: () => adjust('s', 2) }, '+2s'),
          el('button', { type: 'button', class: 'ghost xs', title: 'Pendekkan akhir 2 detik', onclick: () => adjust('e', -2) }, 'Akhir −2s'),
          el('button', { type: 'button', class: 'ghost xs', title: 'Perpanjang akhir 2 detik', onclick: () => adjust('e', 2) }, '+2s'),
          el('button', { type: 'button', class: 'ghost xs', title: 'Hapus potongan ini', disabled: ED.segments.length <= 1, onclick: () => {
            if (ED.segments.length <= 1) return;
            ED.segments.splice(idx, 1);
            ED.curSeg = Math.max(0, Math.min(ED.curSeg, ED.segments.length - 1));
            edOnRangeUpdated();
          } }, '×'))));
    });
  }
  $('ed-seek').max = tot.toFixed(2);
  edSyncPeakBounds();
  edHookUi();
}

function edOnRangeUpdated() {
  if (edIsCompiled()) {
    ED.start_s = Math.min(...ED.segments.map((x) => x.s));
    ED.end_s = Math.max(...ED.segments.map((x) => x.e));
  }
  edRenderDurUi();
  edRefreshWords();
  drawOverlay();
}

document.querySelectorAll('[data-trim]').forEach((btn) => {
  btn.addEventListener('click', () => {
    if (!ED.clip || edIsCompiled()) return;
    const edge = btn.dataset.trim;
    const delta = Number(btn.dataset.delta);
    const lim = edSourceDur();
    if (edge === 'start') {
      const ns = Math.max(0, Math.min(ED.end_s - 3, ED.start_s + delta));
      if (Math.abs(ns - ED.start_s) < 0.05) return toast(delta > 0 ? 'Durasi minimal 3 detik.' : 'Sudah di awal video.', true);
      ED.start_s = Math.round(ns * 100) / 100;
      $('ed-video').currentTime = ED.start_s;
    } else {
      const ne = Math.min(lim, Math.max(ED.start_s + 3, ED.end_s + delta));
      if (Math.abs(ne - ED.end_s) < 0.05) return toast(delta < 0 ? 'Durasi minimal 3 detik.' : 'Sudah di akhir video.', true);
      ED.end_s = Math.round(ne * 100) / 100;
      $('ed-video').currentTime = Math.max(ED.start_s, ED.end_s - 2.0);
    }
    edOnRangeUpdated();
  });
});

$('ed-set-start').onclick = () => {
  if (!ED.clip || edIsCompiled()) return;
  const ct = $('ed-video').currentTime;
  const ns = Math.max(0, Math.min(ED.end_s - 3, ct));
  ED.start_s = Math.round(ns * 100) / 100;
  edOnRangeUpdated();
  toast(`Awal klip diatur ke ${fmtDurDec(ED.start_s)}.`);
};
$('ed-set-end').onclick = () => {
  if (!ED.clip || edIsCompiled()) return;
  const ct = $('ed-video').currentTime;
  const lim = edSourceDur();
  const ne = Math.min(lim, Math.max(ED.start_s + 3, ct));
  ED.end_s = Math.round(ne * 100) / 100;
  edOnRangeUpdated();
  toast(`Akhir klip diatur ke ${fmtDurDec(ED.end_s)}.`);
};
$('ed-seg-add').onclick = () => {
  if (!ED.clip || !edIsCompiled()) return;
  const lim = edSourceDur();
  const s = Math.max(0, Math.min(lim - 1.5, $('ed-video').currentTime || 0));
  const e = Math.min(lim, s + 5.0);
  ED.segments.push({ s: Math.round(s * 100) / 100, e: Math.round(e * 100) / 100, note: 'Potongan tambahan' });
  ED.segments.sort((a, b) => a.s - b.s);
  ED.curSeg = ED.segments.findIndex((x) => Math.abs(x.s - Math.round(s * 100) / 100) < 0.05);
  if (ED.curSeg < 0) ED.curSeg = 0;
  edOnRangeUpdated();
  toast('Potongan 5 detik ditambahkan.');
};

async function openEditor(cid) {
  let r;
  try { r = await api(`/clips/${cid}/words`); } catch (e) { return toast(e.message, true); }
  ED.clip = r.clip;
  ED.words = r.words;
  ED.overrides = JSON.parse(r.clip.caption_overrides || '{}');
  ED.styleId = r.clip.caption_style_id || (r.style && r.style.id);
  ED.source = r.source || { width: 1920, height: 1080, duration_s: r.clip.end_s };
  ED.facecam = r.facecam || null;
  ED.start_s = Number(r.clip.start_s);
  ED.end_s = Number(r.clip.end_s);
  ED.segments = clipSegs(r.clip).map((x) => ({ s: Number(x.s), e: Number(x.e), note: x.note || '' }));
  ED.curSeg = 0;
  let gc = {};
  try { gc = JSON.parse(r.clip.gaming_crop || '{}'); } catch { gc = {}; }
  ED.gamingCrop = {
    mode: gc.mode === 'manual' ? 'manual' : 'auto',
    facecam: Array.isArray(gc.facecam) ? gc.facecam : null,
    gameplay: Array.isArray(gc.gameplay) ? gc.gameplay : null,
  };
  ED.titles = [];
  if (!STYLES.length) await loadStyles();
  show('editor');
  const c = ED.clip;
  $('ed-head').textContent = `Edit klip #${c.rank}`;
  ED.format = c.format;
  edFmt.set(c.format);
  edGmode.set(ED.gamingCrop.mode);
  $('ed-face').checked = !!c.face_tracking;
  $('ed-split').value = Math.round((c.gaming_split || 0.4) * 100);
  $('ed-hook-clip').checked = !!c.hook_clip_on;
  $('ed-peak').value = c.peak_s != null ? Number(c.peak_s).toFixed(1) : '';
  edRenderDurUi();
  edFormatUi();
  $('ed-title').value = c.hook_title;
  $('ed-title-ov').checked = !!c.title_overlay;
  $('ed-subs').checked = !!c.subtitle_on;
  $('ed-titles').innerHTML = '';
  edStageShape();
  const v = $('ed-video');
  v.src = media(`/sessions/${c.session_id}/source`);
  v.onloadedmetadata = () => {
    if (Number.isFinite(v.duration) && v.duration > 0 && !ED.source.duration_s) ED.source.duration_s = v.duration;
    v.currentTime = edIsCompiled() ? ED.segments[0].s : ED.start_s;
    edRenderDurUi();
    gcRenderBoxes();
    drawOverlay();
  };
  v.onloadeddata = () => { gcRenderBoxes(); drawOverlay(); };
  v.onerror = () => toast('Video sumber tidak bisa diputar (mungkin sudah dihapus). Caption tetap bisa diedit.', true);
  renderPresets();
  renderWords();
  fillCustomForm(edStyle());
  drawOverlay();
}

const edFmt = choice('ed-format', (v) => { ED.format = v; edFormatUi(); });
const edGmode = choice('ed-gmode', (v) => {
  ED.gamingCrop.mode = v === 'manual' ? 'manual' : 'auto';
  if (ED.gamingCrop.mode === 'manual') gcEnsureBoxes();
  edFormatUi();
});
function edStageShape() {
  const f = ED.format;
  $('ed-stage').className = 'stage' + (f === 'square' ? ' square' : f === 'landscape' ? ' landscape' : f === 'blurbg916' ? ' blur' : '');
}
function edHookUi() {
  const c = ED.clip;
  if (!c) return;
  const on = $('ed-hook-clip').checked;
  $('ed-peak-box').classList.toggle('hidden', !on);
  if (!on) return;
  const total = edTotalDur();
  const hookDur = Math.min(5, Math.max(2.5, total * 0.25));
  const hs = Number($('ed-peak').value || ED.start_s);
  const he = hs + hookDur;
  $('ed-peak-val').textContent = fmtDur(hs);
  if (edIsCompiled()) {
    $('ed-peak-info').textContent = `0:00–${fmtDur(hookDur)} teaser momen puncak (${fmtDur(hs)}–${fmtDur(he)}) → lanjut ${ED.segments.length} potongan kompilasi`;
    return;
  }
  const mainDur = Math.max(3, total - hookDur);
  const ms = hs >= 3 ? Math.max(0, hs - mainDur) : ED.start_s;
  const me = hs >= 3 ? hs : ms + mainDur;
  $('ed-peak-info').textContent =
    `0:00–${fmtDur(hookDur)} momen viral (${fmtDur(hs)}–${fmtDur(he)}) → lanjut ${Math.round(me - ms)} dtk sebelumnya (${fmtDur(ms)}–${fmtDur(me)})`;
}
function edFormatUi() {
  const f = ED.format;
  const isGaming = f === 'gaming916';
  const manual = gcIsManual();
  $('ed-split-box').classList.toggle('hidden', !isGaming);
  $('ed-gmode-box').classList.toggle('hidden', !isGaming);
  $('ed-crop-box').classList.toggle('hidden', !manual);
  $('ed-split-val').textContent = `${$('ed-split').value}%`;
  $('ed-face').disabled = !['face916', 'square', 'gaming916'].includes(f) || manual;
  $('ed-stage-hint').textContent = manual
    ? 'Preview langsung sesuai area Facecam (atas) dan Gameplay (bawah) yang kamu pilih.'
    : 'Preview memakai video sumber dengan crop tengah. Posisi wajah dan layout 2 orang terlihat di hasil render.';
  edStageShape();
  edHookUi();
  if (manual) gcRenderBoxes();
  drawOverlay();
}
$('ed-split').addEventListener('input', edFormatUi);
$('ed-hook-clip').addEventListener('change', edHookUi);
$('ed-peak').addEventListener('input', () => {
  edHookUi();
  const v = $('ed-video');
  if (v && ED.clip) v.currentTime = Number($('ed-peak').value);
});

function drawOverlay() {
  if (!ED.clip || !$('view-editor').classList.contains('on')) return;
  gcDrawCanvases();
  const st = edStyle();
  if (!st) return;
  const t = edRel();
  const dur = edTotalDur();
  const stage = $('ed-stage');
  const H = stage.clientHeight;
  const land = ED.format === 'landscape';
  const px = st.font_size * (land ? 0.75 * H / 1080 : H / 1920);
  const cap = $('ed-ov-cap');
  cap.innerHTML = '';
  let y = st.position_y;
  if (ED.format === 'gaming916') y = Number($('ed-split').value) / 100;
  cap.style.top = `${y * 100}%`;
  if ($('ed-subs').checked) {
    const groups = groupWords(edActiveWords(), Math.max(1, st.words_per_line || 3));
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
    ti.style.top = `${(ED.format === 'gaming916' ? 0.06 : 0.10) * 100}%`;
    const sp = el('span', {}, $('ed-title').value.trim());
    sp.style.fontSize = `${76 * (land ? 0.75 * H / 1080 : H / 1920)}px`;
    ti.append(sp);
  }
  $('ed-seek').value = t.toFixed(2);
  $('ed-time').textContent = `${fmtDur(t)} / ${fmtDur(dur)}`;
  document.querySelectorAll('#ed-words input').forEach((inp) => {
    inp.classList.toggle('now', t >= Number(inp.dataset.s) && t < Number(inp.dataset.e));
  });
  if (edIsCompiled()) {
    document.querySelectorAll('#ed-segs-list .seg-item').forEach((row, i) => {
      row.classList.toggle('now', i === ED.curSeg);
    });
  }
}

function edLoop() {
  const v = $('ed-video');
  if (ED.clip) {
    if (edIsCompiled()) {
      const sg = ED.segments[ED.curSeg] || ED.segments[0];
      if (v.currentTime >= sg.e) {
        if (ED.curSeg + 1 < ED.segments.length) {
          ED.curSeg += 1;
          v.currentTime = ED.segments[ED.curSeg].s;
        } else {
          v.pause();
          ED.curSeg = 0;
          v.currentTime = ED.segments[0].s;
        }
      }
    } else if (v.currentTime >= ED.end_s) {
      v.pause();
      v.currentTime = ED.start_s;
    }
  }
  drawOverlay();
  if (!v.paused) ED.raf = requestAnimationFrame(edLoop);
}

$('ed-play').onclick = () => {
  const v = $('ed-video');
  if (v.paused) {
    if (edIsCompiled()) {
      const sg = ED.segments[ED.curSeg] || ED.segments[0];
      if (v.currentTime < sg.s || v.currentTime >= sg.e) {
        ED.curSeg = 0;
        v.currentTime = ED.segments[0].s;
      }
    } else if (v.currentTime < ED.start_s || v.currentTime >= ED.end_s) {
      v.currentTime = ED.start_s;
    }
    v.play().catch(() => {});
  } else v.pause();
};
$('ed-video').addEventListener('play', () => { $('ed-play').textContent = 'Jeda'; ED.raf = requestAnimationFrame(edLoop); });
$('ed-video').addEventListener('pause', () => { $('ed-play').textContent = 'Putar'; cancelAnimationFrame(ED.raf); drawOverlay(); });
$('ed-video').addEventListener('seeked', drawOverlay);
$('ed-seek').addEventListener('input', () => {
  const m = edMapRelToSrc(Number($('ed-seek').value));
  ED.curSeg = m.segIdx;
  $('ed-video').currentTime = m.srcT;
});
['ed-title', 'ed-title-ov', 'ed-subs'].forEach((id) => $(id).addEventListener('input', drawOverlay));
window.addEventListener('resize', () => { gcRenderBoxes(); drawOverlay(); });

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
    inp.addEventListener('focus', () => {
      if (w.seg != null) ED.curSeg = w.seg;
      $('ed-video').currentTime = (w.src_s != null ? w.src_s : ED.start_s + w.s) + 0.01;
    });
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

function presetButton(st, on, onPick) {
  const sample = el('span', {}, st.uppercase ? 'KATA ' : 'Kata ', el('span', { style: `color:${st.highlight_color}` }, st.uppercase ? 'AKTIF' : 'aktif'));
  Object.assign(sample.style, { fontFamily: `"${st.font_family}"`, fontSize: '17px', color: st.color });
  if (st.box) Object.assign(sample.style, { background: st.outline_color, padding: '2px 6px' });
  else { sample.style.webkitTextStroke = `3px ${st.outline_color}`; sample.style.paintOrder = 'stroke fill'; }
  return el('button', { class: on ? 'on' : '', role: 'radio', 'aria-checked': String(on), onclick: onPick },
    sample, el('span', { class: 'nm' }, st.name));
}

function renderCreatePresets() {
  const box = $('create-presets');
  box.innerHTML = '';
  for (const st of STYLES.filter((x) => x.id !== '__draft')) {
    box.append(presetButton(st, st.id === createStyleId, () => { createStyleId = st.id; renderCreatePresets(); }));
  }
}

function renderPresets() {
  const box = $('ed-presets');
  box.innerHTML = '';
  for (const st of STYLES.filter((x) => x.id !== '__draft')) {
    const b = presetButton(st, st.id === ED.styleId, () => { ED.styleId = st.id; renderPresets(); fillCustomForm(st); drawOverlay(); });
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
  if (gcIsManual()) gcEnsureBoxes();
  const payload = {
    hook_title: $('ed-title').value, title_overlay: $('ed-title-ov').checked, subtitle_on: $('ed-subs').checked,
    caption_style_id: ED.styleId, caption_overrides: ED.overrides,
    format: ED.format, face_tracking: $('ed-face').checked, gaming_split: Number($('ed-split').value) / 100,
    gaming_crop: ED.gamingCrop,
    hook_clip_on: $('ed-hook-clip').checked,
    peak_s: Number($('ed-peak').value),
    start_s: ED.start_s,
    end_s: ED.end_s,
  };
  if (edIsCompiled()) payload.segments = ED.segments;
  try {
    await api(`/clips/${ED.clip.id}`, { method: 'PATCH', body: payload });
    if (render) {
      await api(`/clips/${ED.clip.id}/render`, { method: 'POST' });
      toast(`Klip #${ED.clip.rank} masuk antrian render.`);
      pollActive();
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
      const bar = el('span', { class: 'meter' }, el('span', { style: `width:${m.pct}%` }));
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

// ---------- Pengaturan: auto-update Klipku di dalam aplikasi ----------

const APP_UP = { info: null, dl: null, timer: null };

function fmtMb(bytes) {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderAppUpdate() {
  const info = APP_UP.info;
  const dl = APP_UP.dl || { state: 'idle' };
  const st = $('app-ver-status');
  const dlBtn = $('app-download-update');
  const instBtn = $('app-install-update');
  const box = $('app-up-box');
  const txt = $('app-up-text');
  const pct = $('app-up-pct');
  const bar = $('app-up-bar');
  const cancelBtn = $('app-up-cancel');
  const sideBtn = $('side-update');
  const sideLbl = $('side-update-lbl');

  const hasUp = Boolean(info && info.has_update && info.latest);
  const busy = dl.state === 'downloading' || dl.state === 'verifying';
  const ready = dl.state === 'ready' && Boolean(dl.installer_path);

  if (hasUp || busy || ready) {
    sideBtn.classList.remove('hidden');
    if (dl.state === 'downloading') sideLbl.textContent = `Mengunduh ${Math.floor(dl.pct || 0)}%`;
    else if (dl.state === 'verifying') sideLbl.textContent = 'Memverifikasi…';
    else if (ready) sideLbl.textContent = `Pasang v${dl.version || info.latest}`;
    else sideLbl.textContent = `Update v${info.latest}`;
  } else {
    sideBtn.classList.add('hidden');
  }

  if (info) {
    st.dataset.checked = '1';
    if (hasUp) {
      const sz = info.asset_size ? ` · ${fmtMb(info.asset_size)}` : '';
      st.textContent = `Tersedia v${info.latest} (terpasang v${info.current}${sz})`;
      st.className = 'ok';
    } else if (!info.latest) {
      st.textContent = `Versi ${info.current} · ${info.message || 'Belum ada rilis di GitHub'}`;
      st.className = 'hint';
    } else {
      st.textContent = `Versi ${info.current} (sudah terbaru)`;
      st.className = 'ok';
    }
  }

  dlBtn.classList.toggle('hidden', !hasUp || busy || ready);
  if (hasUp) dlBtn.textContent = info.can_download ? 'Update sekarang' : 'Buka rilis ↗';
  instBtn.classList.toggle('hidden', !ready);
  $('app-check-update').disabled = busy;

  const showBox = busy || ready || dl.state === 'error';
  box.classList.toggle('hidden', !showBox);
  cancelBtn.classList.toggle('hidden', dl.state !== 'downloading');
  if (dl.state === 'downloading') {
    const prog = dl.total ? `${fmtMb(dl.bytes || 0)} / ${fmtMb(dl.total)}` : fmtMb(dl.bytes || 0);
    txt.textContent = `Mengunduh Klipku v${dl.version} · ${prog}`;
    txt.className = 'hint';
    pct.textContent = `${Math.floor(dl.pct || 0)}%`;
    bar.style.width = `${dl.pct || 0}%`;
  } else if (dl.state === 'verifying') {
    txt.textContent = `Memverifikasi checksum SHA256 v${dl.version}…`;
    txt.className = 'hint';
    pct.textContent = '99%';
    bar.style.width = '99%';
  } else if (ready) {
    txt.textContent = `Klipku v${dl.version} siap dipasang (terverifikasi SHA256).`;
    txt.className = 'ok hint';
    pct.textContent = '100%';
    bar.style.width = '100%';
  } else if (dl.state === 'error') {
    txt.textContent = dl.error || 'Unduhan pembaruan gagal.';
    txt.className = 'bad hint';
    pct.textContent = '';
    bar.style.width = '0%';
  }
}

async function pollAppUpdate() {
  clearTimeout(APP_UP.timer);
  try {
    APP_UP.dl = await api('/engine/app-update/status');
    renderAppUpdate();
    if (APP_UP.dl.state === 'downloading' || APP_UP.dl.state === 'verifying') {
      APP_UP.timer = setTimeout(pollAppUpdate, 500);
    } else if (APP_UP.dl.state === 'ready') {
      toast(`Klipku v${APP_UP.dl.version} selesai diunduh! Klik "Pasang & restart" untuk memperbarui.`);
    } else if (APP_UP.dl.state === 'error') {
      toast(APP_UP.dl.error || 'Unduh update gagal.', true);
    }
  } catch { /* abaikan error sementara */ }
}

async function checkAppUpdate(silent = false) {
  const b = $('app-check-update');
  const st = $('app-ver-status');
  if (!silent) {
    b.disabled = true;
    b.textContent = 'Mengecek…';
    st.textContent = 'Menghubungi GitHub…';
    st.className = 'hint';
  }
  try {
    const r = await api('/engine/app-update');
    APP_UP.info = r;
    if (r.download) APP_UP.dl = r.download;
    renderAppUpdate();
    if (APP_UP.dl && (APP_UP.dl.state === 'downloading' || APP_UP.dl.state === 'verifying')) pollAppUpdate();
    if (!silent) {
      if (r.has_update && r.latest) toast(`Versi baru Klipku v${r.latest} tersedia!`);
      else if (!r.latest) toast(`Klipku v${r.current}. ${r.message || ''}`);
      else toast(`Klipku sudah versi terbaru (v${r.current}).`);
    }
  } catch (e) {
    if (!silent) {
      st.textContent = `Versi ${APP_VERSION || '?'} · gagal cek update`;
      st.className = 'bad hint';
      toast(e.message, true);
    }
  }
  if (!silent) {
    b.disabled = false;
    b.textContent = 'Cek versi terbaru';
  }
}

$('app-check-update').onclick = () => checkAppUpdate(false);
$('app-download-update').onclick = async () => {
  if (!APP_UP.info) return;
  if (!APP_UP.info.can_download) {
    if (APP_UP.info.url && K) K.openExternal(APP_UP.info.url);
    return;
  }
  try {
    APP_UP.dl = await api('/engine/app-update/download', { method: 'POST' });
    renderAppUpdate();
    pollAppUpdate();
  } catch (e) { toast(e.message, true); }
};
$('app-up-cancel').onclick = async () => {
  try {
    APP_UP.dl = await api('/engine/app-update/cancel', { method: 'POST' });
    clearTimeout(APP_UP.timer);
    renderAppUpdate();
    toast('Unduhan update dibatalkan.');
  } catch (e) { toast(e.message, true); }
};
$('app-install-update').onclick = async () => {
  const path = APP_UP.dl && APP_UP.dl.installer_path;
  if (!path || !K || !K.installUpdate) return;
  if (ACTIVE_ID && !confirm('Sedang ada proses video berjalan. Tutup Klipku dan pasang update sekarang?')) return;
  try {
    toast('Menutup Klipku dan menjalankan installer pembaruan…');
    await K.installUpdate(path);
  } catch (e) { toast(e.message, true); }
};
$('side-update').onclick = () => {
  show('settings');
  setTimeout(() => {
    const p = $('panel-engine');
    if (p) p.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, 120);
};

// ---------- Layar awal & Dukung Kreator (YouTube + Saweria) ----------

const WL = { gate: false, unlocked: false, timer: null };

function openSupportModal(gateMode = false) {
  WL.gate = gateMode;
  WL.unlocked = !gateMode;
  clearInterval(WL.timer);
  $('wl-yt-handle').textContent = CREATOR.youtubeHandle;
  $('wl-title').textContent = gateMode ? 'Selamat datang di Klipku' : 'Dukung Kreator Klipku';
  $('wl-desc').innerHTML = gateMode
    ? 'Aplikasi ini <b>100% gratis</b> untuk publik. Sebelum mulai bikin klip, yuk dukung pengembangannya dengan subscribe YouTube atau traktir seikhlasnya lewat Saweria.'
    : 'Terima kasih sudah memakai <b>Klipku</b>! Dukunganmu lewat subscribe YouTube atau gift Saweria seikhlasnya bikin app ini terus mendapat update fitur baru.';

  const enter = $('wl-enter');
  const skip = $('wl-skip');
  if (gateMode) {
    enter.disabled = true;
    enter.textContent = 'Klik YouTube atau Saweria dulu';
    skip.classList.remove('hidden');
    skip.disabled = true;
    let sec = 5;
    skip.textContent = `Sudah subscribe? Lanjut dalam ${sec}d…`;
    WL.timer = setInterval(() => {
      sec -= 1;
      if (sec <= 0) {
        clearInterval(WL.timer);
        skip.disabled = false;
        skip.textContent = 'Saya sudah subscribe, lanjut ke aplikasi';
      } else {
        skip.textContent = `Sudah subscribe? Lanjut dalam ${sec}d…`;
      }
    }, 1000);
  } else {
    enter.disabled = false;
    enter.textContent = 'Tutup';
    skip.classList.add('hidden');
  }
  $('welcome-modal').classList.remove('hidden');
}

function markSupported(cardId, pillId) {
  $(cardId).classList.add('done');
  $(pillId).textContent = '✓ Terima kasih!';
  if (WL.gate && !WL.unlocked) {
    WL.unlocked = true;
    clearInterval(WL.timer);
    const enter = $('wl-enter');
    enter.disabled = false;
    enter.textContent = 'Terima kasih! Mulai Pakai Klipku →';
    $('wl-skip').classList.add('hidden');
  }
}

async function finishWelcome() {
  clearInterval(WL.timer);
  $('welcome-modal').classList.add('hidden');
  if (WL.gate) {
    try { localStorage.setItem('klipku_welcome_seen', '1'); } catch { /* abaikan */ }
    try { await api('/settings', { method: 'PUT', body: { welcome_seen: true } }); } catch { /* abaikan */ }
  }
}

$('wl-yt').onclick = () => {
  if (K) K.openExternal(CREATOR.youtubeUrl);
  markSupported('wl-yt', 'wl-yt-pill');
};
$('wl-saweria').onclick = () => {
  if (K) K.openExternal(CREATOR.saweriaUrl);
  markSupported('wl-saweria', 'wl-saweria-pill');
};
$('wl-enter').onclick = finishWelcome;
$('wl-skip').onclick = finishWelcome;
$('open-support').onclick = () => openSupportModal(false);
$('welcome-modal').addEventListener('click', (ev) => {
  if (ev.target === $('welcome-modal') && !WL.gate) finishWelcome();
});
window.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape' && !$('welcome-modal').classList.contains('hidden') && !WL.gate) finishWelcome();
});

async function checkWelcome() {
  let seen = false;
  try { seen = localStorage.getItem('klipku_welcome_seen') === '1'; } catch { /* abaikan */ }
  try {
    const st = settingsCache || await api('/settings');
    settingsCache = st;
    if (st.settings && st.settings.welcome_seen) seen = true;
  } catch { /* abaikan */ }
  if (!seen) openSupportModal(true);
}

// ---------- start ----------

async function init() {
  if (!K) return;
  B = await K.backendInfo();
  if (!B) { toast('Mesin app gagal start. Tutup lalu buka lagi Klipku.', true); return; }
  for (let i = 0; i < 60; i++) {
    try {
      const h = await api('/health');
      APP_VERSION = h.app;
      $('ver').textContent = `v${h.app}`;
      await refreshRunNote();
      loadStyles();
      updateMoreSummary();
      pollActive();
      checkWelcome();
      setTimeout(() => checkAppUpdate(true), 2500);
      return;
    } catch { await new Promise((r) => setTimeout(r, 500)); }
  }
  toast('Mesin app tidak merespons. Buka Pengaturan > Tes mesin.', true);
}
init();
