const $ = (id) => document.getElementById(id);
let info = null;
let outputPath = null;

async function api(path, method = 'GET') {
  const res = await fetch(info.url + path, { method, headers: { 'X-Klipku-Token': info.token } });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

function row(ok, label, detail) {
  const tr = document.createElement('tr');
  tr.innerHTML = `<td class="${ok ? 'ok' : 'bad'}">${ok ? '✓' : '✗'}</td><td><div></div><div class="muted"></div></td>`;
  tr.querySelector('div').textContent = label;
  tr.querySelector('.muted').textContent = detail;
  return tr;
}

async function waitHealth() {
  for (let i = 0; i < 60; i++) {
    try { return await api('/health'); } catch { await new Promise((r) => setTimeout(r, 500)); }
  }
  throw new Error('Backend tidak merespons dalam 30 detik');
}

async function refreshLog() { $('log').textContent = await window.klipku.backendLog(); }

async function init() {
  info = await window.klipku.backendInfo();
  if (!info) { $('health').append(row(false, 'Backend gagal start', 'Lihat log di bawah')); return refreshLog(); }
  try {
    const h = await waitHealth();
    outputPath = h.output_dir;
    const t = $('health');
    t.append(row(true, 'Python', h.python));
    t.append(row(!h.ffmpeg.startsWith('error'), 'ffmpeg', h.ffmpeg));
    t.append(row(!h.deno.startsWith('error'), 'deno', h.deno));
    t.append(row(!h.yt_dlp.startsWith('error'), 'yt-dlp', h.yt_dlp));
    t.append(row(!h.opencv.startsWith('error'), 'OpenCV', h.opencv));
    t.append(row(h.yunet_model, 'Model deteksi wajah', h.yunet_model ? 'ada' : 'tidak ada'));
    $('run').disabled = false;
  } catch (e) {
    $('health').append(row(false, 'Backend', e.message));
  }
  refreshLog();
}

$('run').onclick = async () => {
  $('run').disabled = true;
  $('run').textContent = 'Menguji…';
  $('results').innerHTML = '';
  try {
    const r = await api('/selftest', 'POST');
    r.results.forEach((x) => $('results').append(row(x.ok, x.test, x.detail)));
    $('results').append(row(r.ok, r.ok ? 'SEMUA LULUS' : 'ADA YANG GAGAL', r.output));
    $('open').disabled = false;
  } catch (e) {
    $('results').append(row(false, 'Tes gagal jalan', e.message));
  }
  $('run').textContent = 'Jalankan tes';
  $('run').disabled = false;
  refreshLog();
};

$('open').onclick = () => window.klipku.openPath(outputPath);

init();
