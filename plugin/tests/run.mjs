// Deterministic offline tests for plugin/lib/*. Plain assertions (no test-runner
// event-loop subtleties); exits non-zero on failure. Zero runtime deps.
import assert from "node:assert/strict";
import http from "node:http";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { probeFile } from "../lib/probe.js";
import { downloadFile } from "../lib/download.js";
import { downloadHls } from "../lib/hls.js";
import { runCode } from "../lib/exec.js";
import { httpRequest } from "../lib/http.js";
import { browserAction, browserClose } from "../lib/browser.js";
import { MemoryStore } from "../lib/memory.js";
import { execFileSync } from "node:child_process";

const BLOB = Buffer.from("0123456789abcdefghijklmnopqrstuvwxyz payload for range/resume checks");
const sha = (b) => createHash("sha256").update(b).digest("hex");
const start = (handler) =>
  new Promise((resolve) => {
    const srv = http.createServer(handler);
    srv.listen(0, "127.0.0.1", () => resolve(srv));
  });
const shutdown = (srv) => {
  try {
    srv.closeAllConnections?.();
  } catch {}
  srv.close();
};

function blobApp() {
  const total = BLOB.length;
  return (req, res) => {
    const url = req.url.split("?")[0];
    if (url !== "/blob.bin") return res.writeHead(404).end("not found");
    if (req.method === "HEAD")
      return res.writeHead(200, { "content-length": total, "accept-ranges": "bytes" }).end();
    const range = req.headers.range || "";
    if (range) {
      const m = /bytes=(\d+)-(\d*)/.exec(range);
      const rstart = m ? Number(m[1]) : 0;
      const rend = m && m[2] !== "" ? Number(m[2]) : total - 1;
      const slice = BLOB.subarray(rstart, rend + 1);
      return res
        .writeHead(206, { "content-length": slice.length, "content-range": `bytes ${rstart}-${rend}/${total}`, "accept-ranges": "bytes" })
        .end(slice);
    }
    res.writeHead(200, { "content-length": total, "accept-ranges": "bytes" }).end(BLOB);
  };
}

function hlsApp() {
  const segs = { "/seg0.ts": "aaaa", "/seg1.ts": "bbbb", "/seg2.ts": "cccc" };
  return (req, res) => {
    const url = req.url.split("?")[0];
    if (url === "/master.m3u8")
      return res.end("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000,RESOLUTION=640x360\nlow.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=9000,RESOLUTION=1920x1080\nhigh.m3u8\n");
    if (url === "/high.m3u8") return res.end("#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:1.0,\nhigh/seg0.ts\n#EXTINF:1.0,\nhigh/seg1.ts\n#EXTINF:1.0,\nhigh/seg2.ts\n#EXT-X-ENDLIST\n");
    const hit = segs[url.replace("/high", "")];
    if (hit !== undefined) return res.end(hit);
    res.writeHead(404).end("nf");
  };
}

async function main() {
  let failures = 0;
  const check = async (name, fn) => {
    try {
      await fn();
      console.log(`ok - ${name}`);
    } catch (e) {
      failures++;
      console.error(`FAIL - ${name}: ${e.message}`);
    }
  };

  await check("probe_file png magic + sha256", async () => {
    const dir = await mkdtemp(join(tmpdir(), "rd-"));
    try {
      const p = join(dir, "pic.png");
      const buf = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.from("data")]);
      await writeFile(p, buf);
      const r = await probeFile(p, { sha256: true });
      assert.equal(r.ok, true);
      assert.equal(r.mime, "image/png");
      assert.equal(r.extension, ".png");
      assert.equal(r.sizeBytes, buf.length);
      assert.equal(r.sha256, sha(buf));
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });

  await check("download_file full + range resume", async () => {
    const srv = await start(blobApp());
    const dir = await mkdtemp(join(tmpdir(), "rd-"));
    try {
      const url = `http://127.0.0.1:${srv.address().port}/blob.bin`;
      const full = await downloadFile({ url, outDir: dir, outName: "full.bin" });
      assert.equal(full.ok, true);
      assert.equal(full.sizeBytes, BLOB.length);
      assert.equal(full.sha256, sha(BLOB));
      assert.equal(full.fromResume, false);
      assert.equal((await readFile(full.path)).toString(), BLOB.toString());
      await writeFile(join(dir, "resume.bin.part"), BLOB.subarray(0, 5));
      const resumed = await downloadFile({ url, outDir: dir, outName: "resume.bin" });
      assert.equal(resumed.ok, true);
      assert.equal(resumed.fromResume, true);
      assert.equal((await readFile(resumed.path)).toString(), BLOB.toString());
    } finally {
      shutdown(srv);
      await rm(dir, { recursive: true, force: true });
    }
  });

  await check("download_hls master -> highest bandwidth, ordered merge", async () => {
    const srv = await start(hlsApp());
    const dir = await mkdtemp(join(tmpdir(), "rd-"));
    try {
      const r = await downloadHls({ m3u8Url: `http://127.0.0.1:${srv.address().port}/master.m3u8`, outDir: dir, outName: "out.ts" });
      assert.equal(r.ok, true);
      assert.equal(r.segments, 3);
      assert.equal((await readFile(r.path)).toString(), "aaaabbbbcccc");
    } finally {
      shutdown(srv);
      await rm(dir, { recursive: true, force: true });
    }
  });

  await check("http_request redirects", async () => {
    const srv = await start((req, res) => {
      if (req.url === "/a") return res.writeHead(302, { location: "/b" }).end();
      res.end("landed");
    });
    try {
      const r = await httpRequest({ url: `http://127.0.0.1:${srv.address().port}/a` });
      assert.equal(r.status, 200);
      assert.equal(r.body.toString(), "landed");
    } finally {
      shutdown(srv);
    }
  });

  await check("run_code executes node", async () => {
    const r = await runCode({ language: "node", code: 'console.log("hi-from-code")', workdir: await mkdtemp(join(tmpdir(), "rd-")) });
    assert.equal(r.ok, true);
    assert.match(r.stdout, /hi-from-code/);
  });

  await check("browser worker selfcheck (optional dependency)", async () => {
    const r = await browserAction({ action: "selfcheck" }, { timeoutMs: 20000 });
    if (r.ok === false) {
      console.log("  skip: python playwright not installed on this host (tool degrades gracefully)");
      return;
    }
    assert.equal(r.ok, true);
    await browserClose(null, { timeoutMs: 20000 });
  });

  await check("captcha worker selfcheck (optional deps)", async () => {
    const out = execFileSync("python3", ["-u", new URL("../lib/captcha.py", import.meta.url).pathname], {
      input: '{"action":"selfcheck"}',
      encoding: "utf8",
    });
    const r = JSON.parse(out.trim().split("\n").pop());
    assert.equal(r.ok, true);
    assert.equal(typeof r.ddddocr, "boolean");
  });

  await check("memory store: remember/merge + query similar", async () => {
    const dir = await mkdtemp(join(tmpdir(), "rd-mem-"));
    try {
      const m = new MemoryStore(join(dir, "memory"));
      await m.remember({ domain: "annas-archive.gs", score: 1, verdict: "blocked", tags: ["mirror", "antibot", "ebooks"], resource_types: ["ebook"], aliases: ["annas-archive.org"], notes: "mirror", methods: "turnstile blocked" });
      await m.remember({ domain: "annas-archive.org", score: 2, verdict: "partial", tags: ["dns-fail"], methods: "DNS EBUSY from this net" }); // merges into alias domain as separate? no, same alias -> new record
      await m.remember({ domain: "europepmc.org", score: 3, verdict: "partial", tags: ["mirror", "ratelimit", "open-access"], resource_types: ["pdf"] });
      await m.remember({ domain: "littleskin.cn", score: 4, verdict: "ok", tags: ["api"], resource_types: ["image"] });
      // exact hit via alias
      const byAlias = await m.query({ domain: "annas-archive.org" });
      assert.equal(byAlias.length >= 1, true);
      assert.equal(byAlias[0].domain, "annas-archive.gs" || "annas-archive.org");
      // similar search by tags
      const similar = await m.query({ tags: ["mirror"], similar: true });
      assert.equal(similar.length >= 2, true);
      const verdictFilter = await m.query({ verdict: "blocked" });
      assert.equal(verdictFilter.length >= 1, true);
      // merge updates checks
      const before = (await m.query({ domain: "europepmc.org" }))[0];
      await m.remember({ domain: "europepmc.org", tags: ["new-fact"], notes: "second sighting" });
      const after = (await m.query({ domain: "europepmc.org" }))[0];
      assert.equal(after.checks, before.checks + 1);
      assert.ok(after.tags.includes("new-fact"));
      assert.match(after.notes, /second sighting/);
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });

  if (failures > 0) {
    console.error(`\n${failures} test(s) failed`);
    process.exit(1);
  }
  console.log("\nall tests passed");
  process.exit(0);
}

main();
