const { app, BrowserWindow, WebContentsView, session } = require("electron");
const fs = require("node:fs");
const fsp = require("node:fs/promises");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");
const { spawn } = require("node:child_process");

const ROOT = __dirname;
const RUNTIME = path.join(ROOT, "runtime");
const DOWNLOAD_DIR = path.join(RUNTIME, "downloads");
const RESULT_PATH = path.join(RUNTIME, "spike_result.json");
const LOG_PATH = path.join(RUNTIME, "electron_spike.log");
const DEBUG_PORT_PATH = path.join(RUNTIME, "debug_port.txt");
const PYTHON_PATH = process.env.PKULAW_PYTHON || "D:\\Python\\python.exe";

let mainWindow;
let webView;
const childViews = [];
let downloadTask;
let downloadPath;
let localServer;
let localBaseUrl;
let debugPort;
let pythonChild;

function appendLog(message) {
  const line = `[${new Date().toISOString()}] ${message}\n`;
  fs.mkdirSync(RUNTIME, { recursive: true });
  fs.appendFileSync(LOG_PATH, line, "utf8");
  process.stdout.write(line);
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = address.port;
      server.close(() => resolve(port));
    });
  });
}

function startFixtureServer() {
  return new Promise((resolve, reject) => {
    localServer = http.createServer((req, res) => {
      if (req.url === "/download") {
        res.writeHead(200, {
          "Content-Type": "text/plain; charset=utf-8",
          "Content-Disposition": "attachment; filename=electron-drission-spike.txt"
        });
        res.end("ELECTRON_DRISSION_SPIKE_OK\n");
        return;
      }

      if (req.url === "/child") {
        res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        res.end("<!doctype html><html><body><h1>Child target</h1></body></html>");
        return;
      }

      res.setHeader("Set-Cookie", "pkulaw_spike_session=persisted; Path=/; Max-Age=86400; SameSite=Lax");
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(`<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>Electron DrissionPage Spike</title></head>
<body>
  <h1 id="heading">Electron WebContentsView + DrissionPage</h1>
  <p id="status">fixture-ready</p>
  <button id="download" onclick="location.href='/download'">下载测试文件</button>
  <button id="open-child" onclick="window.open('/child')">打开子窗口</button>
</body>
</html>`);
    });

    localServer.once("error", reject);
    localServer.listen(0, "127.0.0.1", () => {
      const address = localServer.address();
      localBaseUrl = `http://127.0.0.1:${address.port}/`;
      resolve(localBaseUrl);
    });
  });
}

function layoutWebView() {
  if (!mainWindow || !webView || mainWindow.isDestroyed()) {
    return;
  }
  const [width, height] = mainWindow.getContentSize();
  webView.setBounds({
    x: 0,
    y: 48,
    width,
    height: Math.max(1, height - 48)
  });
}

function createWindow(persistentSession) {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 820,
    minWidth: 900,
    minHeight: 620,
    show: true,
    title: "Electron + DrissionPage 技术探针",
    webPreferences: {
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  mainWindow.loadFile(path.join(ROOT, "shell.html"));
  webView = new WebContentsView({
    webPreferences: {
      session: persistentSession,
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  mainWindow.contentView.addChildView(webView);
  layoutWebView();
  mainWindow.on("resize", layoutWebView);

  webView.webContents.setWindowOpenHandler(({ url }) => {
    appendLog(`WINDOW_OPEN url=${url}`);
    const childView = new WebContentsView({
      webPreferences: {
        session: persistentSession,
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false
      }
    });
    childViews.push(childView);
    mainWindow.contentView.addChildView(childView);
    childView.setBounds({ x: 48, y: 88, width: 520, height: 360 });
    childView.webContents.loadURL(url);
    childView.webContents.on("will-destroy", () => {
      const index = childViews.indexOf(childView);
      if (index >= 0) {
        childViews.splice(index, 1);
      }
    });
    return { action: "deny" };
  });

  webView.webContents.on("did-finish-load", () => {
    appendLog(`VIEW_LOADED url=${webView.webContents.getURL()}`);
  });
  webView.webContents.on("render-process-gone", (_event, details) => {
    appendLog(`VIEW_GONE reason=${details.reason} exitCode=${details.exitCode}`);
  });

}

async function configureDownloads(persistentSession) {
  await fsp.mkdir(DOWNLOAD_DIR, { recursive: true });
  persistentSession.on("will-download", (_event, item) => {
    downloadTask = item;
    downloadPath = path.join(
      DOWNLOAD_DIR,
      `${Date.now()}-${item.getFilename() || "download.bin"}`
    );
    item.setSavePath(downloadPath);
    appendLog(`DOWNLOAD_STARTED filename=${item.getFilename()} url=${item.getURL()}`);

    item.on("updated", (_downloadEvent, state) => {
      appendLog(`DOWNLOAD_UPDATED state=${state} received=${item.getReceivedBytes()}`);
    });

    item.once("done", async (_downloadEvent, state) => {
      let content = "";
      try {
        if (state === "completed") {
          content = await fsp.readFile(downloadPath, "utf8");
        }
      } catch (error) {
        appendLog(`DOWNLOAD_READ_ERROR ${error.message}`);
      }
      appendLog(`DOWNLOAD_DONE state=${state} path=${downloadPath}`);
      downloadTask = null;
      await writeResult({
        downloadState: state,
        downloadPath,
        downloadContent: content
      });
    });
  });
}

async function writeResult(update) {
  let current = {};
  try {
    current = JSON.parse(await fsp.readFile(RESULT_PATH, "utf8"));
  } catch {
    current = {};
  }
  const next = {
    ...current,
    ...update,
    updatedAt: new Date().toISOString()
  };
  await fsp.writeFile(RESULT_PATH, JSON.stringify(next, null, 2), "utf8");
}

function runPythonProbe() {
  return new Promise((resolve) => {
    const script = path.join(ROOT, "probe_drission.py");
    const pythonResultPath = path.join(RUNTIME, "python_result.json");
    pythonChild = spawn(
      PYTHON_PATH,
      [script, String(debugPort), localBaseUrl, pythonResultPath],
      {
        cwd: ROOT,
        env: {
          ...process.env,
          PYTHONIOENCODING: "utf-8"
        },
        windowsHide: true
      }
    );

    pythonChild.stdout.on("data", (chunk) => appendLog(`PYTHON_STDOUT ${chunk.toString().trim()}`));
    pythonChild.stderr.on("data", (chunk) => appendLog(`PYTHON_STDERR ${chunk.toString().trim()}`));
    pythonChild.once("error", async (error) => {
      appendLog(`PYTHON_SPAWN_ERROR ${error.message}`);
      await writeResult({ pythonSpawnError: error.message });
      resolve(false);
    });
    pythonChild.once("exit", async (code) => {
      appendLog(`PYTHON_EXIT code=${code}`);
      let pythonResult = null;
      try {
        pythonResult = JSON.parse(await fsp.readFile(pythonResultPath, "utf8"));
      } catch (error) {
        appendLog(`PYTHON_RESULT_READ_ERROR ${error.message}`);
      }
      await writeResult({ pythonExitCode: code, pythonResult });
      resolve(code === 0 && Boolean(pythonResult?.success));
    });
  });
}

async function bootstrap() {
  await fsp.mkdir(RUNTIME, { recursive: true });
  await fsp.rm(RESULT_PATH, { force: true });
  await fsp.rm(LOG_PATH, { force: true });

  debugPort = Number(process.env.PKULAW_DEBUG_PORT || 0);
  if (!debugPort) {
    throw new Error("PKULAW_DEBUG_PORT was not supplied by the launcher");
  }

  await app.whenReady();
  await fsp.writeFile(DEBUG_PORT_PATH, String(debugPort), "utf8");
  appendLog(`DEBUG_PORT=${debugPort}`);

  const persistentSession = session.fromPartition("persist:pkulaw-spike");
  await configureDownloads(persistentSession);
  await startFixtureServer();
  appendLog(`FIXTURE_URL=${localBaseUrl}`);

  createWindow(persistentSession);
  await webView.webContents.loadURL(localBaseUrl);
  await writeResult({
    electronVersion: process.versions.electron,
    chromeVersion: process.versions.chrome,
    nodeVersion: process.versions.node,
    debugPort,
    fixtureUrl: localBaseUrl,
    webContentsId: webView.webContents.id
  });

  const pythonPassed = await runPythonProbe();
  appendLog(`SPIKE_PYTHON_PASSED=${pythonPassed}`);
  setTimeout(() => app.quit(), 750);
}

app.on("window-all-closed", () => {
  if (localServer) {
    localServer.close();
  }
  app.quit();
});

app.on("before-quit", () => {
  if (pythonChild && !pythonChild.killed) {
    pythonChild.kill();
  }
  if (localServer) {
    localServer.close();
  }
});

bootstrap().catch(async (error) => {
  appendLog(`BOOTSTRAP_ERROR ${error.stack || error.message}`);
  await writeResult({ bootstrapError: error.stack || error.message });
  app.quit();
});
