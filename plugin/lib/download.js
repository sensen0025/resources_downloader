// Deterministic single-file downloader with Range-based resume (.part), sha256,
// and redirect following. Zero runtime deps; Node >= 18.17.
import { createWriteStream, promises as fsp } from "node:fs";
import { createHash } from "node:crypto";
import http from "node:http";
import https from "node:https";
import { basename, join } from "node:path";
import { DEFAULT_USER_AGENT } from "./http.js";

/** Open a streaming response, following redirects, resolving only on 2xx. */
function openStream(url, { method = "GET", headers = {}, timeoutMs = 30000, redirects = 8 } = {}) {
  return new Promise((resolve, reject) => {
    const go = (target, left) => {
      const mod = target.startsWith("https:") ? https : target.startsWith("http:") ? http : null;
      if (!mod) return reject(new Error(`unsupported URL protocol: ${target}`));
      const u = new URL(target);
      const req = mod.request(
        u,
        {
          method,
          headers: { "User-Agent": DEFAULT_USER_AGENT, Accept: "*/*", ...headers },
        },
        (res) => {
          const status = res.statusCode ?? 0;
          if (status >= 300 && status < 400 && res.headers.location && left > 0) {
            res.resume();
            return go(new URL(res.headers.location, u).toString(), left - 1);
          }
          if (status >= 200 && status < 300) return resolve({ res, status, finalUrl: target });
          res.resume();
          const err = new Error(`HTTP ${status} for ${method} ${target}`);
          err.status = status;
          reject(err);
        },
      );
      req.on("error", reject);
      req.setTimeout(timeoutMs, () => req.destroy(new Error(`idle timeout ${timeoutMs}ms`)));
      req.end();
    };
    go(url, redirects);
  });
}

/** Best-effort response metadata: status, length, type, disposition, ranges. */
async function headersOf(url, headers = {}, timeoutMs = 30000) {
  try {
    const { res } = await openStream(url, { method: "HEAD", headers, timeoutMs });
    const meta = { ...res.headers };
    res.resume();
    return meta;
  } catch (e) {
    if (e.status === 405 || e.status === 403 || e.status === 501) {
      // HEAD unsupported -> GET with a tiny Range and read headers only
      try {
        const { res } = await openStream(url, {
          method: "GET",
          headers: { ...headers, Range: "bytes=0-0" },
          timeoutMs,
        });
        const meta = { ...res.headers };
        res.resume();
        return meta;
      } catch {
        return {};
      }
    }
    return {};
  }
}

function fileNameFromDisposition(disposition) {
  if (!disposition) return null;
  const m = /filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i.exec(disposition);
  if (!m) return null;
  const name = decodeURIComponent(m[1] || m[2] || "");
  return name.replace(/[\\/:*?"<>|]/g, "_") || null;
}

/**
 * Download a direct URL to disk with .part resume and sha256.
 * @param {object} p
 * @param {string} p.url
 * @param {string} [p.outDir="downloads"]
 * @param {string} [p.outName]
 * @param {Record<string,string>} [p.headers]
 * @param {number} [p.timeoutMs=120000]
 */
export async function downloadFile(p) {
  const { url, outDir = "downloads", outName, headers = {}, timeoutMs = 120000 } = p;
  await fsp.mkdir(outDir, { recursive: true });
  const meta = await headersOf(url, headers, timeoutMs);
  const dispositionName = fileNameFromDisposition(meta["content-disposition"]);
  const name = outName || dispositionName || basename(new URL(url).pathname) || "download.bin";
  const finalPath = join(outDir, name);
  const partPath = `${finalPath}.part`;
  const supportsRange = /bytes/i.test(meta["accept-ranges"] || "");
  let start = 0;
  try {
    start = (await fsp.stat(partPath)).size;
  } catch {
    /* no partial yet */
  }
  if (!supportsRange || start === 0) start = 0;
  const reqHeaders = { ...headers };
  if (start > 0) reqHeaders.Range = `bytes=${start}-`;

  try {
    const { res, finalUrl } = await openStream(url, { headers: reqHeaders, timeoutMs });
    const status = res.statusCode ?? 0;
    // Server ignored Range and is resending from 0: restart clean.
    if (start > 0 && status === 200) {
      start = 0;
      await fsp.rm(partPath, { force: true });
    }
    const out = createWriteStream(partPath, { flags: start > 0 ? "a" : "w" });
    let written = start;
    res.on("data", (c) => {
      written += c.length;
    });
    await new Promise((resolvePromise, reject) => {
      res.pipe(out);
      res.on("error", reject);
      out.on("error", reject);
      out.on("finish", resolvePromise);
    });
    await fsp.rename(partPath, finalPath);
    const sizeBytes = (await fsp.stat(finalPath)).size;
    const sha256 = await sha256File(finalPath);
    return {
      ok: true,
      path: finalPath,
      fileName: name,
      sizeBytes,
      sha256,
      contentType: meta["content-type"] || "",
      fromResume: start > 0,
      finalUrl,
    };
  } catch (e) {
    return {
      ok: false,
      error: e.message,
      partialPath: partPath,
      resumeBytes: start,
    };
  }
}

async function sha256File(path) {
  const fh = await fsp.open(path, "r");
  const h = createHash("sha256");
  const buf = Buffer.alloc(256 * 1024);
  try {
    let pos = 0;
    for (;;) {
      const { bytesRead } = await fh.read(buf, 0, buf.length, pos);
      if (bytesRead === 0) break;
      h.update(buf.subarray(0, bytesRead));
      pos += bytesRead;
    }
  } finally {
    await fh.close();
  }
  return h.digest("hex");
}

export { sha256File };
