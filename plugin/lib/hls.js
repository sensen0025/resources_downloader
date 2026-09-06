// HLS/m3u8 downloader: master variant selection, AES-128 decryption, ordered
// segment merge. Zero runtime deps; ffmpeg is NOT required (segments merged
// byte-wise in order).
import { promises as fsp } from "node:fs";
import { createDecipheriv } from "node:crypto";
import { dirname, join } from "node:path";
import { httpRequest } from "./http.js";
import { sha256File } from "./download.js";

function resolveUri(base, uri) {
  if (/^https?:\/\//i.test(uri)) return uri;
  return new URL(uri, base).toString();
}

/**
 * Parse a playlist. Returns either `variants` (master) or `segments` (media),
 * with each segment carrying an optional `key {method, uri, ivHex}` inherited
 * from the running #EXT-X-KEY state (RFC 8216 default IV = media sequence).
 */
function parseM3u8(text) {
  const variants = [];
  const segments = [];
  let runningKey = null;
  let pendingVariant = null;

  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith("#EXT-X-STREAM-INF")) {
      pendingVariant = line;
      continue;
    }
    if (line.startsWith("#EXT-X-KEY")) {
      const method = /METHOD=([^,]+)/.exec(line)?.[1] || "NONE";
      const uri = /URI="([^"]+)"/.exec(line)?.[1];
      const iv = /IV=0x([0-9a-fA-F]+)/.exec(line)?.[1];
      runningKey =
        method === "NONE" || !uri
          ? null
          : { method, uri, iv: iv ? Buffer.from(iv, "hex") : null };
      continue;
    }
    if (line.startsWith("#")) continue; // other tags (EXTINF, MAP, ...) are not needed for byte-merge
    if (pendingVariant) {
      const bw = /BANDWIDTH=(\d+)/.exec(pendingVariant);
      const res = /RESOLUTION=(\d+x\d+)/.exec(pendingVariant);
      variants.push({
        uri: line,
        bandwidth: bw ? Number(bw[1]) : 0,
        resolution: res ? res[1] : "",
        name: /NAME="([^"]+)"/.exec(pendingVariant)?.[1] || "",
      });
      pendingVariant = null;
    } else {
      segments.push({ uri: line, key: runningKey });
    }
  }
  return { variants, segments };
}

async function getKeyData(key, headers, timeoutMs) {
  const r = await httpRequest({ url: key.uri, headers, timeoutMs });
  if (r.status !== 200) throw new Error(`key HTTP ${r.status}`);
  return r.body.subarray(0, 16);
}

async function fetchSegmentData(uri, headers, timeoutMs) {
  const r = await httpRequest({ url: uri, headers, timeoutMs });
  if (r.status !== 200) throw new Error(`segment HTTP ${r.status}: ${uri}`);
  return r.body;
}

/**
 * Download an HLS stream and merge segments in order.
 * @param {object} p
 * @param {string} p.m3u8Url
 * @param {string} p.outDir
 * @param {string} [p.outName="stream.mp4"]
 * @param {number} [p.maxConcurrency=4]
 * @param {Record<string,string>} [p.headers]
 * @param {number} [p.timeoutMs=60000]
 */
export async function downloadHls(p) {
  const {
    m3u8Url,
    outDir = "downloads",
    outName = "stream.mp4",
    maxConcurrency = 4,
    headers = {},
    timeoutMs = 60000,
  } = p;
  const dir = outDir;
  await fsp.mkdir(dir, { recursive: true });
  try {
    let playlistUrl = m3u8Url;
    let text = (await httpRequest({ url: m3u8Url, headers, timeoutMs })).body.toString("utf8");
    let parsed = parseM3u8(text);

    if (parsed.variants.length > 0 && parsed.segments.length === 0) {
      parsed.variants.sort((a, b) => b.bandwidth - a.bandwidth);
      playlistUrl = resolveUri(m3u8Url, parsed.variants[0].uri);
      const media = await httpRequest({ url: playlistUrl, headers, timeoutMs });
      if (media.status !== 200) return { ok: false, error: `variant HTTP ${media.status}` };
      parsed = parseM3u8(media.body.toString("utf8"));
    }

    if (parsed.segments.length === 0) return { ok: false, error: "no segments found in playlist" };

    const tmpDir = join(dir, `.hls_tmp_${Date.now()}_${Math.floor(Math.random() * 1e6)}`);
    await fsp.mkdir(tmpDir, { recursive: true });
    const base = dirname(playlistUrl) + "/";
    for (const s of parsed.segments) if (s.key?.uri) s.key.uri = resolveUri(base, s.key.uri);
    const keyCache = new Map();
    const segmentUris = parsed.segments.map((s) => resolveUri(base, s.uri));

    const segmentFiles = await mapLimit(segmentUris, maxConcurrency, async (uri, index) => {
      let data = await fetchSegmentData(uri, headers, timeoutMs);
      const key = parsed.segments[index].key;
      if (key && key.method === "AES-128") {
        if (!keyCache.has(key.uri)) keyCache.set(key.uri, await getKeyData(key, headers, timeoutMs));
        const iv = key.iv || Buffer.alloc(16);
        iv.writeUInt32BE(index, 12); // RFC 8216 default: 16-byte big-endian media sequence
        const decipher = createDecipheriv("aes-128-cbc", keyCache.get(key.uri), iv);
        data = Buffer.concat([decipher.update(data), decipher.final()]);
      }
      const segPath = join(tmpDir, `${String(index).padStart(6, "0")}.seg`);
      await fsp.writeFile(segPath, data);
      return segPath;
    });

    const finalPath = join(dir, outName);
    const out = await fsp.open(finalPath, "w");
    try {
      for (const segPath of segmentFiles) {
        const buf = await fsp.readFile(segPath);
        await out.write(buf);
      }
    } finally {
      await out.close();
    }
    await fsp.rm(tmpDir, { recursive: true, force: true });
    const sizeBytes = (await fsp.stat(finalPath)).size;
    return {
      ok: true,
      path: finalPath,
      fileName: outName,
      sizeBytes,
      sha256: await sha256File(finalPath),
      segments: parsed.segments.length,
    };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

async function mapLimit(items, limit, fn) {
  const results = new Array(items.length);
  let next = 0;
  const workerCount = Math.max(1, Math.min(limit, items.length));
  const worker = async () => {
    for (;;) {
      const i = next++;
      if (i >= items.length) return;
      results[i] = await fn(items[i], i);
    }
  };
  await Promise.all(Array.from({ length: workerCount }, worker));
  return results;
}
