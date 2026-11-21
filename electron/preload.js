const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('klipku', {
  backendInfo: () => ipcRenderer.invoke('backend:info'),
  backendLog: () => ipcRenderer.invoke('backend:log'),
  openPath: (p) => ipcRenderer.invoke('shell:openPath', p),
});
