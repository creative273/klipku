// Klipku - proses utama Electron.
// Menjalankan backend Python (embeddable) di 127.0.0.1 dengan port acak + token acak,
// lalu membuka jendela UI. Tidak ada download executable saat runtime.
const { app, BrowserWindow, ipcMain, shell, dialog } = require('electron');
const { spawn } = require('child_process');
const crypto = require('crypto');
const net = require('net');
const path = require('path');
const fs = require('fs');

let backend = null;
let backendInfo = null;
let lastLog = [];

function engineDir() {
  // Saat terpasang: <install>\resources\engine. Saat dev: <repo>\engine.
  return app.isPackaged
    ? path.join(process.resourcesPath, 'engine')
    : path.join(__dirname, '..', 'engine');
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

function log(line) {
  lastLog.push(line);
  if (lastLog.length > 200) lastLog = lastLog.slice(-200);
}

async function startBackend() {
  const eng = engineDir();
  const py = path.join(eng, 'python', 'python.exe');
  const server = path.join(eng, 'backend', 'server.py');
  if (!fs.existsSync(py)) throw new Error(`Python tidak ditemukan: ${py}`);

  const port = await freePort();
  const token = crypto.randomBytes(24).toString('hex');
  const dataDir = path.join(app.getPath('appData'), 'Klipku');
  const outDir = path.join(app.getPath('videos'), 'Klipku');
  fs.mkdirSync(dataDir, { recursive: true });

  backend = spawn(py, [server, '--port', String(port)], {
    cwd: path.dirname(server),
    windowsHide: true,
    env: {
      ...process.env,
      KLIPKU_TOKEN: token,
      KLIPKU_ENGINE: eng,
      KLIPKU_DATA: dataDir,
      KLIPKU_OUTPUT: outDir,
      PYTHONUTF8: '1',
      PYTHONDONTWRITEBYTECODE: '1',
    },
  });
  backend.stdout.on('data', (d) => log(d.toString()));
  backend.stderr.on('data', (d) => log(d.toString()));
  backend.on('exit', (code) => log(`[backend keluar, kode ${code}]`));

  backendInfo = { url: `http://127.0.0.1:${port}`, token };
  return backendInfo;
}

function stopBackend() {
  if (backend && !backend.killed) {
    backend.kill();
    backend = null;
  }
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1100,
    height: 760,
    title: 'Klipku',
    backgroundColor: '#070b12',
    icon: path.join(__dirname, '..', 'ui', 'assets', 'icon-64.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.setMenuBarVisibility(false);
  win.loadFile(path.join(__dirname, '..', 'ui', 'index.html'));
}

ipcMain.handle('backend:info', () => backendInfo);
ipcMain.handle('backend:log', () => lastLog.join(''));
ipcMain.handle('shell:openPath', (_e, p) => shell.openPath(p));
ipcMain.handle('shell:showItem', (_e, p) => shell.showItemInFolder(p));
ipcMain.handle('shell:openExternal', (_e, url) => {
  if (/^https:\/\//.test(url)) return shell.openExternal(url);
});
ipcMain.handle('dialog:pickVideo', async () => {
  const r = await dialog.showOpenDialog({
    properties: ['openFile'],
    filters: [{ name: 'Video', extensions: ['mp4', 'mov', 'mkv', 'webm', 'm4v', 'avi'] }],
  });
  return r.canceled ? null : r.filePaths[0];
});
ipcMain.handle('dialog:pickImage', async () => {
  const r = await dialog.showOpenDialog({
    properties: ['openFile'],
    filters: [{ name: 'Gambar', extensions: ['png', 'jpg', 'jpeg', 'webp'] }],
  });
  return r.canceled ? null : r.filePaths[0];
});
ipcMain.handle('app:relaunch', () => { stopBackend(); app.relaunch(); app.exit(0); });
ipcMain.handle('dialog:pickFolder', async () => {
  const r = await dialog.showOpenDialog({ properties: ['openDirectory', 'createDirectory'] });
  return r.canceled ? null : r.filePaths[0];
});

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.whenReady().then(async () => {
    try {
      await startBackend();
    } catch (err) {
      log(`[gagal start backend] ${err.message}\n`);
    }
    createWindow();
  });
  app.on('window-all-closed', () => {
    stopBackend();
    app.quit();
  });
  app.on('before-quit', stopBackend);
}
