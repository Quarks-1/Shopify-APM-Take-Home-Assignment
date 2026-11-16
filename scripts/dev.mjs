// Usage: node scripts/dev.mjs <prepare|frontend|backend>
import { spawn, spawnSync } from "node:child_process";
import { copyFileSync, existsSync } from "node:fs";
import path from "node:path";
import { getDevPorts, writeLaunchJson } from "./dev-ports.mjs";

const root = path.resolve(import.meta.dirname, "..");
const cmd = process.argv[2];
if (!["prepare", "frontend", "backend"].includes(cmd)) {
  console.error("Usage: node scripts/dev.mjs <prepare|frontend|backend>");
  process.exit(2);
}

const ports = getDevPorts({ cwd: root });
writeLaunchJson(ports);
console.log(
  `[dev] ${cmd}: frontend ${ports.frontendUrl}  backend ${ports.backendOrigin}  ` +
    (ports.isWorktree ? `(worktree slot ${ports.slot})` : "(main checkout)"),
);

function fail(msg) {
  console.error(`[dev] error: ${msg}`);
  process.exit(1);
}

function run(file, args, opts) {
  const r = spawnSync(file, args, { stdio: "inherit", ...opts });
  if (r.error || r.status !== 0) {
    fail(`${file} ${args.join(" ")} failed${r.error ? `: ${r.error.message}` : ` (exit ${r.status})`}`);
  }
}

const frontendDir = path.join(root, "frontend");
const backendDir = path.join(root, "backend");

function prepFrontend() {
  if (!existsSync(path.join(frontendDir, "node_modules"))) {
    console.log("[dev] Installing Node dependencies");
    run("npm", ["install"], { cwd: frontendDir });
  }
}

function findPython() {
  for (const base of [root, ports.mainRoot]) {
    const p = path.join(base, ".venv", "bin", "python");
    if (existsSync(p)) return p;
  }
  fail(
    `No Python virtualenv found. Create one: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt (looked in ${root} and ${ports.mainRoot}).`,
  );
}

function prepBackend() {
  const python = findPython();
  const db = path.join(backendDir, "db.sqlite3");
  const mainDb = path.join(ports.mainRoot, "backend", "db.sqlite3");
  if (ports.isWorktree && !existsSync(db) && existsSync(mainDb)) {
    run(python, [
      "-c",
      "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()",
      mainDb,
      db,
    ]);
    console.log("Seeded backend/db.sqlite3 from the main checkout");
  }
  run(python, ["manage.py", "migrate", "--noinput"], { cwd: backendDir });
  return python;
}

function serve(file, args, opts) {
  const child = spawn(file, args, { stdio: "inherit", detached: true, ...opts });
  let sent = false;
  let killTimer = null;
  const killGroup = (sig) => {
    try {
      process.kill(-child.pid, sig);
    } catch (err) {
      if (err.code !== "ESRCH") throw err;
    }
  };
  for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
    process.on(sig, () => {
      sent = true;
      killGroup(sig);
      killTimer ??= setTimeout(() => killGroup("SIGKILL"), 3000);
    });
  }
  child.on("error", (err) => fail(`failed to start ${file}: ${err.message}`));
  child.on("exit", (code, signal) => {
    // Sweep any stragglers left in the group (e.g. a reloader child).
    killGroup("SIGKILL");
    if (killTimer) clearTimeout(killTimer);
    process.exit(code ?? (sent ? 0 : 1));
  });
}

if (cmd === "prepare") {
  prepFrontend();
  prepBackend();
  process.exit(0);
} else if (cmd === "frontend") {
  prepFrontend();
  serve("npm", ["run", "dev"], { cwd: frontendDir });
} else {
  const python = prepBackend();
  serve(python, ["manage.py", "runserver", `127.0.0.1:${ports.backendPort}`], {
    cwd: backendDir,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: "1",
      CORS_ALLOWED_ORIGINS:
        process.env.CORS_ALLOWED_ORIGINS ??
        `http://localhost:${ports.frontendPort},http://127.0.0.1:${ports.frontendPort}`,
    },
  });
}
