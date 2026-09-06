// Node glue for the optional generic browser tool. Drives the Python
// playwright worker (browser_worker.py) over newline-delimited stdin/stdout
// JSON. One persistent worker process per profile_dir keeps the browser
// context (cookies AND the open page) alive across tool calls, enabling
// multi-step flows such as login -> captcha -> submit.
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const WORKER = join(dirname(fileURLToPath(import.meta.url)), "browser_worker.py");

/** @type {Map<string, {child: import('node:child_process').ChildProcess, queue: Array<{resolve:Function, timer:NodeJS.Timeout}>, buf: string}>} */
const workers = new Map();

function spawnWorker(python, headless) {
  const child = spawn(python, ["-u", WORKER], {
    stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env, RD_BROWSER_PERSIST: "1", RD_HEADLESS: headless ? "1" : "0" },
  });
  return child;
}

function cleanup(key, reason) {
  const w = workers.get(key);
  if (!w) return;
  workers.delete(key);
  try {
    w.child.kill("SIGKILL");
  } catch {}
  const msg = `browser session closed (${reason})`;
  for (const { resolve } of w.queue) resolve({ ok: false, error: msg });
}

/**
 * Run one browser action, keeping a per-profile persistent worker alive.
 * @param {object} req {action, profile_dir, ...}
 * @param {object} [opts]
 * @param {string} [opts.python="python3"]
 * @param {number} [opts.timeoutMs=240000]
 * @param {boolean} [opts.headless=true]
 */
export function browserAction(req, opts = {}) {
  const python = opts.python ?? "python3";
  const timeoutMs = opts.timeoutMs ?? 240000;
  const headless = opts.headless ?? true;
  const key = req.profile_dir || "_default";
  return new Promise((resolve) => {
    let w = workers.get(key);
    if (!w) {
      const child = spawnWorker(python, headless);
      w = { child, queue: [], buf: "" };
      workers.set(key, w);
      child.stdout.on("data", (c) => {
        w.buf += c.toString();
        let nl;
        while ((nl = w.buf.indexOf("\n")) >= 0) {
          const line = w.buf.slice(0, nl).trim();
          w.buf = w.buf.slice(nl + 1);
          if (!line) continue;
          const pending = w.queue.shift();
          if (!pending) continue;
          clearTimeout(pending.timer);
          try {
            pending.resolve(JSON.parse(line));
          } catch {
            pending.resolve({ ok: false, action: req.action, error: `bad worker JSON: ${line.slice(-300)}` });
          }
        }
      });
      child.stderr.on("data", (c) => {
        const w2 = workers.get(key);
        if (w2 && w2.queue.length > 0 && w2.queue[0].stderrTail === undefined) {
          w2.queue[0].stderrTail = (w2.queue[0].stderrTail || "") + c.toString().slice(-4000);
        }
      });
      child.on("error", (e) => cleanup(key, `spawn error: ${e.message}`));
      child.on("exit", (code) => cleanup(key, `worker exited ${code}`));
    }
    const timer = setTimeout(() => {
      const idx = w.queue.findIndex((p) => p.req === req);
      if (idx >= 0) w.queue.splice(idx, 1);
      cleanup(key, "timeout");
      resolve({ ok: false, action: req.action, error: `browser action timeout after ${timeoutMs}ms` });
    }, timeoutMs);
    w.queue.push({ resolve, timer, req });
    w.child.stdin.write(JSON.stringify(req) + "\n");
  });
}

/** Close the persistent session for a profile (call after a finished flow). */
export async function browserClose(profileDir, opts = {}) {
  const python = opts.python ?? "python3";
  const headless = opts.headless ?? true;
  const r = await browserAction({ action: "close", profile_dir: profileDir || "_default" }, { python, headless, timeoutMs: 20000 });
  // worker exits on close; drop any stale map entry
  const key = profileDir || "_default";
  const w = workers.get(key);
  if (w && !w.queue.length) workers.delete(key);
  return r;
}
