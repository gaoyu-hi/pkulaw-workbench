const net = require("node:net");
const path = require("node:path");
const { spawn } = require("node:child_process");

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

async function main() {
  const debugPort = await getFreePort();
  const electronCli = path.join(
    __dirname,
    "node_modules",
    "electron",
    "cli.js"
  );
  const child = spawn(
    process.execPath,
    [
      electronCli,
      ".",
      `--remote-debugging-port=${debugPort}`,
      "--remote-debugging-address=127.0.0.1"
    ],
    {
      cwd: __dirname,
      env: {
        ...process.env,
        PKULAW_DEBUG_PORT: String(debugPort)
      },
      stdio: "inherit"
    }
  );

  child.once("exit", (code, signal) => {
    if (signal) {
      process.kill(process.pid, signal);
      return;
    }
    process.exit(code ?? 1);
  });
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
