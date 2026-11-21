const { contextBridge, ipcRenderer, webUtils } = require('electron');

contextBridge.exposeInMainWorld('klipku', {
  backendInfo: () => ipcRenderer.invoke('backend:info'),
  backendLog: () => ipcRenderer.invoke('backend:log'),
  openPath: (p) => ipcRenderer.invoke('shell:openPath', p),
  showItem: (p) => ipcRenderer.invoke('shell:showItem', p),
  openExternal: (url) => ipcRenderer.invoke('shell:openExternal', url),
  pickVideo: () => ipcRenderer.invoke('dialog:pickVideo'),
  pickFolder: () => ipcRenderer.invoke('dialog:pickFolder'),
  // path asli file yang di-drag ke jendela (Electron 32+)
  pathForFile: (file) => webUtils.getPathForFile(file),
});
