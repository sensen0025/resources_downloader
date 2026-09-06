// E2E tool bridge: exposes plugin/lib/* as CLI "tools" (one JSON result per call)
// so an agent/CI can exercise the exact code paths the DSH tools call.
//
//   node plugin/tests/e2e.mjs probe   '{"path":"downloads/x.bin","sha256":true}'
//   node plugin/tests/e2e.mjs download '{"url":"https://...","outDir":"/tmp/x","outName":"a.pdf"}'
//   node plugin/tests/e2e.mjs hls     '{"m3u8Url":"https://...","outDir":"/tmp/x","outName":"a.mp4"}'
//   node plugin/tests/e2e.mjs fetch   '{"url":"https://..."}'
//   node plugin/tests/e2e.mjs search  '{"query":"...","limit":5}'
//   node plugin/tests/e2e.mjs run     '{"language":"python","code":"...","workdir":"/tmp/x"}'
//   node plugin/tests/e2e.mjs runfile '{"language":"python","scriptPath":"/tmp/x/s.py","workdir":"/tmp/x"}'
//
// `"cookies"` controls the private vault login state:
//   - undefined / true  -> attach the user's vault cookies for the URL host (default)
//   - false             -> stay anonymous (no cookie header), even if host is in vault
// Values are kept in memory only (env RD_COOKIE_DIR -> vault.json).
import { readFileSync } from "node:fs";
import { httpRequest } from "../lib/http.js";
import { downloadFile } from "../lib/download.js";
import { downloadHls } from "../lib/hls.js";
import { probeFile } from "../lib/probe.js";
import { runCode } from "../lib/exec.js";
import { webSearch } from "../lib/search.js";

const COOKIE_DIR = process.env.RD_COOKIE_DIR || `${process.env.HOME || ""}/.rd-cookies`;

function domainMatches(cookieDomain, host) {
  const d = String(cookieDomain || "").trim().toLowerCase();
  const h = String(host || "").trim().toLowerCase();
  if (!d || !h) return false;
  if (d.startsWith(".")) return h === d.slice(1) || h.endsWith(d);
  return h === d;
}

/** Cookie header string for host from the vault ("" when none / no vault). */
export function vaultCookieHeader(url, cookiesEnabled = true) {
  if (cookiesEnabled === false || !url) return "";
  try {
    const host = new URL(url).hostname;
    const raw = readFileSync(`${COOKIE_DIR}/vault.json`, "utf8");
    const entries = JSON.parse(raw).entries || [];
    const now = Math.floor(Date.now() / 1000);
    const parts = entries
      .filter((c) => domainMatches(c.domain, host) && (c.session || (c.expires || 0) > now))
      .map((c) => `${c.name}=${c.value}`);
    return parts.join("; ");
  } catch {
    return "";
  }
}

function withCookies(args) {
  if (args?.cookies === false) return { ...args }; // explicit opt-out: stay anonymous
  const headers = { ...(args.headers || {}) };
  const url = args.url || args.m3u8Url || "";
  const cookie = vaultCookieHeader(url, true);
  if (cookie && !headers.Cookie) headers.Cookie = cookie;
  return { ...args, headers };
}

const [, , cmd, argJson] = process.argv;
if (!cmd || !argJson) {
  console.error("usage: node e2e.mjs <probe|download|hls|fetch|search|run|runfile> '<json args>'");
  process.exit(2);
}
let args;
try {
  args = JSON.parse(argJson);
} catch {
  console.error("invalid JSON args");
  process.exit(2);
}

async function main() {
  let out;
  switch (cmd) {
    case "probe":
      out = await probeFile(args.path, { sha256: args.sha256 ?? true });
      break;
    case "download":
      out = await downloadFile(withCookies(args));
      break;
    case "hls":
      out = await downloadHls(withCookies(args));
      break;
    case "fetch":
      args = withCookies(args);
      out = await httpRequest({ url: args.url, headers: args.headers ?? {}, timeoutMs: args.timeoutMs ?? 30000, maxBytes: args.maxBytes ?? 5 * 1024 * 1024 }).then(
        (r) => ({
          status: r.status,
          finalUrl: r.finalUrl,
          contentType: r.headers["content-type"] || "",
          bytes: r.body.length,
          text: r.body.toString("utf8").slice(0, args.slice ?? 2000),
        }),
        (e) => ({ error: e.message }),
      );
      break;
    case "search":
      out = await webSearch(args);
      break;
    case "run":
      out = await runCode(args);
      break;
    case "runfile":
      out = await runCode(args);
      break;
    default:
      console.error(`unknown cmd ${cmd}`);
      process.exit(2);
  }
  process.stdout.write(JSON.stringify(out) + "\n");
  process.exit(0);
}

main();
