// File probe: size / magic bytes / type guess / sha256. Zero runtime deps.
import { createHash } from "node:crypto";
import { open, stat } from "node:fs/promises";

const MAGIC = [
  { bytes: [0x25, 0x50, 0x44, 0x46], type: "application/pdf", ext: ".pdf" },
  { bytes: [0x89, 0x50, 0x4e, 0x47], type: "image/png", ext: ".png" },
  { bytes: [0xff, 0xd8, 0xff], type: "image/jpeg", ext: ".jpg" },
  { bytes: [0x47, 0x49, 0x46, 0x38], type: "image/gif", ext: ".gif" },
  { bytes: [0x52, 0x49, 0x46, 0x46], type: "image/webp", ext: ".webp" },
  { bytes: [0x50, 0x4b, 0x03, 0x04], type: "application/zip", ext: ".zip" },
  { bytes: [0x1f, 0x8b], type: "application/gzip", ext: ".gz" },
  { bytes: [0x37, 0x7a, 0xbc, 0xaf, 0x27, 0x1c], type: "application/x-7z-compressed", ext: ".7z" },
  { bytes: [0x49, 0x44, 0x33], type: "audio/mpeg", ext: ".mp3" },
  { bytes: [0x66, 0x4c, 0x61, 0x43], type: "audio/x-flac", ext: ".flac" },
  { bytes: [0x4f, 0x67, 0x67, 0x53], type: "application/ogg", ext: ".ogg" },
  { bytes: [0x00, 0x00, 0x00], type: "video/mp4", ext: ".mp4" },
  { bytes: [0x1a, 0x45, 0xdf, 0xa3], type: "video/x-matroska", ext: ".mkv" },
  { bytes: [0x4d, 0x5a], type: "application/x-msdownload", ext: ".exe" },
];

function hasPrefix(buf, arr) {
  if (buf.length < arr.length) return false;
  for (let i = 0; i < arr.length; i++) if (buf[i] !== arr[i]) return false;
  return true;
}

/** Guess content type from first bytes (mp4/video needs an ftyp check). */
function guessType(buf) {
  for (const m of MAGIC) if (hasPrefix(buf, m.bytes)) return m;
  // ISO-BMFF family: bytes 4..8 == "ftyp"
  if (buf.length >= 12 && buf.toString("latin1", 4, 8) === "ftyp") {
    const brand = buf.toString("latin1", 8, 12);
    return { type: brand === "qt  " ? "video/quicktime" : "video/mp4", ext: brand === "qt  " ? ".mov" : ".mp4" };
  }
  if (buf.length >= 2) {
    // crude text detection: no NUL bytes in the head
    if (!buf.subarray(0, Math.min(buf.length, 512)).includes(0)) {
      const head = buf.subarray(0, Math.min(buf.length, 64)).toString("utf8");
      if (/^<\?xml|<html|<svg|^[{[]|^#!/.test(head)) return { type: "text/plain", ext: null };
    }
  }
  return { type: "application/octet-stream", ext: null };
}

/**
 * Probe a file.
 * @param {string} path
 * @param {{sha256?: boolean}} [opts]
 */
export async function probeFile(path, opts = {}) {
  const info = await stat(path).catch(() => null);
  if (!info) return { ok: false, error: `file not found: ${path}` };
  let head = Buffer.alloc(0);
  let sha256;
  try {
    const fh = await open(path, "r");
    try {
      const read = Buffer.alloc(Math.min(info.size, 512));
      if (read.length > 0) {
        const { bytesRead } = await fh.read(read, 0, read.length, 0);
        head = read.subarray(0, bytesRead);
      }
    } finally {
      await fh.close();
    }
  } catch (e) {
    return { ok: false, error: `failed reading head: ${e.message}` };
  }
  if (opts.sha256) {
    const h = createHash("sha256");
    const fh = await open(path, "r");
    const buf = Buffer.alloc(1024 * 256);
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
    sha256 = h.digest("hex");
  }
  const m = guessType(head);
  return {
    ok: true,
    path,
    sizeBytes: info.size,
    mime: m.type,
    extension: m.ext,
    headHex: head.subarray(0, 16).toString("hex"),
    looksBinary: head.includes(0),
    ...(sha256 ? { sha256 } : {}),
  };
}
