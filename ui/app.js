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
  face916: 'Crop 9:16 dari tengah video. Ikut wajah otomatis menyusul di versi berikutnya.',
  gaming916: 'Atas: muka. Bawah: gameplay dari tengah layar, penuh tanpa bar hitam.',
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
  if (view !== 'session' && es) { es.close(); es = null; }
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
    if (!st.keys.groq) missing.push('Groq (untuk transkrip)');
    if (p !== 'groq' && !st.keys[p]) missing.push(st.providers[p].label);
    $('run-note').textContent = missing.length
      ? `Key belum diisi: ${missing.join(', ')}. Isi di Pengaturan.`
      : `AI: ${st.providers[p].label} · ${st.settings.model}`;
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
    : el('div', { class: 'ph' }, c.status === 'failed' ? `Gagal render: ${c.error || ''}` : c.status === 'rendering' ? 'Sedang render…' : 'Menunggu render');
  const fmtSel = el('select', { 'aria-label': 'Format klip' },
    ...Object.entries(FORMAT_LABEL).map(([k, lbl]) => el('option', { value: k, selected: k === c.format }, lbl)));
  const subs = el('label', { class: 'switch' }, el('input', { type: 'checkbox', checked: !!c.subtitle_on }), 'Subtitle');
  const rerender = el('button', {
    class: 'ghost sm', disabled: running,
    onclick: async () => {
      try {
        await api(`/clips/${c.id}`, { method: 'PATCH', body: { format: fmtSel.value, subtitle_on: subs.querySelector('input').checked } });
        await api(`/clips/${c.id}/render`, { method: 'POST' });
        toast(`Klip ${c.rank} masuk antrian render.`);
        scheduleRender();
      } catch (e) { toast(e.message, true); }
    },
  }, 'Render ulang');
  const openBtn = c.output_path ? el('button', { class: 'ghost sm', onclick: () => K.showItem(c.output_path) }, 'Tampilkan file') : null;
  return el('div', { class: 'clip' },
    v,
    el('div', { class: 'body' },
      el('div', { class: 'row between' },
        el('div', { class: 'score' }, el('b', {}, String(c.score_total)), el('span', {}, `Hook ${c.score_hook} · Flow ${c.score_flow} · Value ${c.score_value}`)),
        el('span', { class: `badge ${c.status}` }, c.status)),
      el('div', { class: 'title' }, c.hook_title),
      c.reason ? el('div', { class: 'hint' }, c.reason) : null,
      el('div', { class: 'hint' }, `${fmtDur(c.start_s)}–${fmtDur(c.end_s)} · ${Math.round(c.end_s - c.start_s)} dtk · ${FORMAT_LABEL[c.format]}`),
      el('div', { class: 'actions' }, fmtSel, subs, rerender, openBtn)));
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

// ---------- start ----------

async function init() {
  B = await K.backendInfo();
  if (!B) { toast('Mesin app gagal start. Tutup lalu buka lagi Klipku.', true); return; }
  for (let i = 0; i < 60; i++) {
    try {
      const h = await api('/health');
      $('ver').textContent = `v${h.app}`;
      refreshRunNote();
      return;
    } catch { await new Promise((r) => setTimeout(r, 500)); }
  }
  toast('Mesin app tidak merespons. Buka Pengaturan > Tes mesin.', true);
}
init();
