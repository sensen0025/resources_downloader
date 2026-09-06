// run_code: execute python/bash/node snippets or script files written by the
// model, capturing tail-limited stdout/stderr with a hard timeout.
import { spawn } from "node:child_process";
import { promises as fsp } from "node:fs";
import { isAbsolute, join } from "node:path";

const TAIL = 24000; // bytes kept per stream
const EXT = { python: ".py", bash: ".sh", node: ".js" };

function pickCommand(language, interpreters = {}) {
  switch (language) {
    case "bash":
      return [interpreters.bash || "bash"];
    case "node":
      return [interpreters.node || "node"];
    case "python":
    default:
      return [interpreters.python || "python3"];
  }
}

/**
 * Run code or a script.
 * @param {object} p
 * @param {"python"|"bash"|"node"} p.language
 * @param {string} [p.code] inline source (written to scratch before run)
 * @param {string} [p.scriptPath] existing script (alternative to code)
 * @param {string[]} [p.args]
 * @param {string} [p.workdir=".rd_scratch"] working dir relative to cwd
 * @param {Record<string,string>} [p.env]
 * @param {number} [p.timeoutMs=60000]
 * @param {Record<string,string>} [p.interpreters]
 */
export async function runCode(p) {
  const {
    language = "python",
    code,
    scriptPath,
    args = [],
    workdir = ".rd_scratch",
    env = {},
    timeoutMs = 60000,
    interpreters = {},
  } = p;
  try {
    const dir = isAbsolute(workdir) ? workdir : join(process.cwd(), workdir);
    await fsp.mkdir(dir, { recursive: true });
    let file = scriptPath || null;
    if (!file && typeof code === "string") {
      file = join(dir, `_s_${Date.now()}_${Math.random().toString(36).slice(2, 8)}${EXT[language] || ".py"}`);
      await fsp.writeFile(file, code);
    }
    if (!file) throw new Error("provide either code or scriptPath");
    const [cmd, ...fixed] = pickCommand(language, interpreters);
    const child = spawn(cmd, [...fixed, file, ...args], {
      cwd: dir,
      env: { ...process.env, ...env },
      shell: false,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (c) => {
      stdout = (stdout + c.toString()).slice(-TAIL);
    });
    child.stderr.on("data", (c) => {
      stderr = (stderr + c.toString()).slice(-TAIL);
    });
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGKILL");
    }, timeoutMs);
    const exitCode = await new Promise((resolvePromise) => {
      child.on("error", (e) => {
        clearTimeout(timer);
        stderr = (stderr + `\nspawn error: ${e.message}`).slice(-TAIL);
        resolvePromise(null);
      });
      child.on("close", (code, signal) => {
        clearTimeout(timer);
        resolvePromise(signal ? (timedOut ? 124 : 1) : code);
      });
    });
    return {
      ok: exitCode === 0,
      exitCode,
      timedOut,
      stdout,
      stderr,
      scriptPath: file,
    };
  } catch (e) {
    return { ok: false, exitCode: null, stdout: "", stderr: e.message, scriptPath: null };
  }
}
