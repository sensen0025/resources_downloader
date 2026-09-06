// resources_downloader generic capability tools (DSH cordis plugin).
// Registers six stable, generic tools on ctx.tools:
//   run_code, http_fetch, web_search, download_file, download_hls, probe_file
// There is intentionally NO site-specific logic anywhere in this repository.
import { promises as fsp } from "node:fs";
import { isAbsolute, join, resolve } from "node:path";
import z from "@deepseek-ai/schemastery";
import { defineTool } from "@deepseek-ai/dsh-tools";
import { httpRequest } from "./lib/http.js";
import { downloadFile } from "./lib/download.js";
import { downloadHls } from "./lib/hls.js";
import { probeFile } from "./lib/probe.js";
import { runCode } from "./lib/exec.js";
import { webSearch } from "./lib/search.js";
import { browserAction } from "./lib/browser.js";
import { MemoryStore } from "./lib/memory.js";

const name = "rd-tools";
const inject = ["tools"];

const Config = z.object({
  root: z.string().optional(),
  downloadsDir: z.string().optional(),
  scratchDir: z.string().optional(),
  maxConcurrency: z.number().optional(),
  timeoutMs: z.number().optional(),
  python: z.string().optional(),
  headless: z.boolean().optional(),
  memoryDir: z.string().optional(),
});

const text = (value) => [{ type: "text", text: JSON.stringify(value, null, 2) }];

const OBJ = (properties) => ({
  type: "object",
  additionalProperties: false,
  properties: { ...properties },
});

const headersSchema = {
  type: "object",
  additionalProperties: true,
  properties: {},
  description: "extra HTTP headers as {name: value}",
};

function apply(ctx, config = {}) {
  const root = resolve(config.root ?? process.cwd());
  const downloadsDir = resolve(root, config.downloadsDir ?? "downloads");
  const scratchDir = resolve(root, config.scratchDir ?? ".rd_scratch");
  const maxConcurrency = config.maxConcurrency ?? 4;
  const timeoutMs = config.timeoutMs ?? 120000;
  const python = config.python ?? "python3";
  const headless = config.headless ?? true;
  const browserProfileDir = join(scratchDir, "browser_profile");
  const memoryDir = resolve(root, config.memoryDir ?? "memory");
  const memoryStore = new MemoryStore(memoryDir);

  void fsp.mkdir(downloadsDir, { recursive: true }).catch(() => {});
  void fsp.mkdir(scratchDir, { recursive: true }).catch(() => {});
  void fsp.mkdir(memoryDir, { recursive: true }).catch(() => {});

  ctx.tools.register(
    defineTool({
      name: "run_code",
      description:
        "Execute Python/Bash/Node code the model just wrote (ad-hoc crawlers/scripts). Pass inline `code` or an existing `script_path`. Runs in the workspace scratch dir with a hard timeout; stdout/stderr are tail-truncated. Use this to write-then-run one-off scrapers for arbitrary sites, then iterate from the error output.",
      parameters: {
        language: {
          type: "string",
          enum: ["python", "bash", "node"],
          description: "interpreter; python is the default for crawlers",
        },
        code: { type: "string", description: "inline source to write and run" },
        script_path: { type: "string", description: "existing script path (alternative to code)" },
        args: { type: "array", items: { type: "string" }, description: "script arguments" },
        timeout_ms: { type: "integer", description: "hard timeout in ms (default 60000)" },
      },
      output: {
        schema: OBJ({
          ok: { type: "boolean", required: true },
          exit_code: { type: "integer", required: true },
          timed_out: { type: "boolean", required: true },
          stdout: { type: "string", required: true },
          stderr: { type: "string", required: true },
          script_path: { type: "string", required: true },
        }),
        render: text,
      },
      async execute(args) {
        const r = await runCode({
          language: args.language ?? "python",
          code: args.code,
          scriptPath: args.script_path,
          args: args.args ?? [],
          workdir: scratchDir,
          timeoutMs: args.timeout_ms ?? 60000,
          interpreters: { python },
        });
        return {
          ok: r.ok,
          exit_code: r.exitCode ?? -1,
          timed_out: r.timedOut ?? false,
          stdout: r.stdout ?? "",
          stderr: r.stderr ?? "",
          script_path: r.scriptPath ?? "",
        };
      },
    }),
  );

  ctx.tools.register(
    defineTool({
      name: "http_fetch",
      description:
        "Fetch any HTTP(S) URL (redirects followed) and return status/headers and the body as text (truncated) or saved to a file. Use it to inspect pages, JSON APIs, and m3u8 playlists during reconnaissance.",
      parameters: {
        url: { type: "string", required: true, description: "absolute http(s) URL" },
        method: { type: "string", enum: ["GET", "HEAD"], description: "HTTP method (default GET)" },
        headers: headersSchema,
        max_bytes: { type: "integer", description: "body cap in bytes (default 10 MB)" },
        save_path: { type: "string", description: "optional absolute file path to save the raw body" },
        timeout_ms: { type: "integer", description: "timeout in ms (default 60000)" },
      },
      output: {
        schema: OBJ({
          ok: { type: "boolean", required: true },
          status: { type: "integer", required: true },
          final_url: { type: "string", required: true },
          content_type: { type: "string", required: true },
          bytes: { type: "integer", required: true },
          text: { type: "string", required: true },
          truncated: { type: "boolean", required: true },
          saved_path: { type: "string", required: true },
          error: { type: "string" },
        }),
        render: text,
      },
      async execute(args) {
        try {
          const maxBytes = args.max_bytes ?? 10 * 1024 * 1024;
          const r = await httpRequest({
            url: args.url,
            method: args.method ?? "GET",
            headers: args.headers ?? {},
            timeoutMs: args.timeout_ms ?? timeoutMs,
            maxBytes,
          });
          const textLimit = 20000;
          let saved = "";
          if (args.save_path) {
            const p = isAbsolute(args.save_path) ? args.save_path : join(downloadsDir, args.save_path);
            await fsp.mkdir(join(p, ".."), { recursive: true });
            await fsp.writeFile(p, r.body);
            saved = p;
          }
          const bodyText = r.body.toString("utf8");
          return {
            ok: r.status >= 200 && r.status < 300,
            status: r.status,
            final_url: r.finalUrl,
            content_type: r.headers["content-type"] || "",
            bytes: r.body.length,
            text: saved ? "" : bodyText.slice(0, textLimit),
            truncated: !saved && bodyText.length > textLimit,
            saved_path: saved,
          };
        } catch (e) {
          return { ok: false, status: 0, final_url: args.url, content_type: "", bytes: 0, text: "", truncated: false, saved_path: "", error: e.message };
        }
      },
    }),
  );

  ctx.tools.register(
    defineTool({
      name: "web_search",
      description:
        "Multi-engine web search (Bing/Mojeek/DuckDuckGo Lite) returning title/url/snippet hits. Parsing is defensive; per-engine failures are reported so you can retry with the DSH base web_search tool or different phrasing.",
      parameters: {
        query: { type: "string", required: true, description: "search query" },
        engines: { type: "array", items: { type: "string", enum: ["bing", "mojeek", "ddg"] }, description: "engines to try (default all)" },
        limit: { type: "integer", description: "max results (default 8)" },
        timeout_ms: { type: "integer", description: "per-engine timeout (default 20000)" },
      },
      output: {
        schema: OBJ({
          ok: { type: "boolean", required: true },
          results: {
            type: "array",
            required: true,
            items: {
              type: "object",
              additionalProperties: false,
              properties: {
                title: { type: "string", required: true },
                url: { type: "string", required: true },
                snippet: { type: "string", required: true },
              },
            },
          },
          failures: {
            type: "array",
            required: true,
            items: {
              type: "object",
              additionalProperties: false,
              properties: {
                engine: { type: "string", required: true },
                error: { type: "string", required: true },
              },
            },
          },
        }),
        render: text,
      },
      async execute(args) {
        return webSearch({
          query: args.query,
          engines: args.engines ?? ["bing", "mojeek", "ddg"],
          limit: args.limit ?? 8,
          timeoutMs: args.timeout_ms ?? 20000,
        });
      },
    }),
  );

  ctx.tools.register(
    defineTool({
      name: "download_file",
      description:
        "Deterministically download a direct URL to disk: Range .part resume, sha256, redirect following. Returns path/size/hash. Prefer this over hand-written download code whenever you have a real file URL.",
      parameters: {
        url: { type: "string", required: true, description: "direct file URL" },
        out_dir: { type: "string", description: "subdirectory under downloads (default downloads/)" },
        out_name: { type: "string", description: "file name override" },
        headers: headersSchema,
        timeout_ms: { type: "integer", description: "timeout (default 120000)" },
      },
      output: {
        schema: OBJ({
          ok: { type: "boolean", required: true },
          path: { type: "string", required: true },
          file_name: { type: "string", required: true },
          size_bytes: { type: "integer", required: true },
          sha256: { type: "string", required: true },
          content_type: { type: "string", required: true },
          from_resume: { type: "boolean", required: true },
          final_url: { type: "string", required: true },
          error: { type: "string" },
        }),
        render: text,
      },
      async execute(args) {
        const outDir = args.out_dir
          ? isAbsolute(args.out_dir)
            ? args.out_dir
            : join(downloadsDir, args.out_dir)
          : downloadsDir;
        const r = await downloadFile({
          url: args.url,
          outDir,
          outName: args.out_name,
          headers: args.headers ?? {},
          timeoutMs: args.timeout_ms ?? timeoutMs,
        });
        return r.ok
          ? { ok: true, path: r.path, file_name: r.fileName, size_bytes: r.sizeBytes, sha256: r.sha256, content_type: r.contentType, from_resume: r.fromResume, final_url: r.finalUrl }
          : { ok: false, path: "", file_name: "", size_bytes: 0, sha256: "", content_type: "", from_resume: false, final_url: args.url, error: r.error };
      },
    }),
  );

  ctx.tools.register(
    defineTool({
      name: "download_hls",
      description:
        "Download an HLS/m3u8 stream: resolves master playlists (highest bandwidth), fetches all segments with bounded concurrency, decrypts AES-128 keys, and merges segments in order into one file. Returns path/size/sha256/segment count.",
      parameters: {
        m3u8_url: { type: "string", required: true, description: "master or media m3u8 URL" },
        out_dir: { type: "string", description: "subdirectory under downloads" },
        out_name: { type: "string", description: "output file name (default stream.mp4)" },
        max_concurrency: { type: "integer", description: `segment concurrency (default ${maxConcurrency})` },
        headers: headersSchema,
        timeout_ms: { type: "integer", description: "per-request timeout (default 60000)" },
      },
      output: {
        schema: OBJ({
          ok: { type: "boolean", required: true },
          path: { type: "string", required: true },
          file_name: { type: "string", required: true },
          size_bytes: { type: "integer", required: true },
          sha256: { type: "string", required: true },
          segments: { type: "integer", required: true },
          error: { type: "string" },
        }),
        render: text,
      },
      async execute(args) {
        const outDir = args.out_dir
          ? isAbsolute(args.out_dir)
            ? args.out_dir
            : join(downloadsDir, args.out_dir)
          : downloadsDir;
        const r = await downloadHls({
          m3u8Url: args.m3u8_url,
          outDir,
          outName: args.out_name ?? "stream.mp4",
          maxConcurrency: args.max_concurrency ?? maxConcurrency,
          headers: args.headers ?? {},
          timeoutMs: args.timeout_ms ?? 60000,
        });
        return r.ok
          ? { ok: true, path: r.path, file_name: r.fileName, size_bytes: r.sizeBytes, sha256: r.sha256, segments: r.segments }
          : { ok: false, path: "", file_name: "", size_bytes: 0, sha256: "", segments: 0, error: r.error };
      },
    }),
  );

  ctx.tools.register(
    defineTool({
      name: "probe_file",
      description:
        "Verify a downloaded/generated file: size, magic-byte type guess, extension, head hex, binary/text hint, optional sha256. Always probe before telling the user a download succeeded — catch empty files and HTML error pages saved with media extensions.",
      parameters: {
        path: { type: "string", required: true, description: "absolute file path to inspect" },
        sha256: { type: "boolean", description: "also compute sha256 (default false)" },
      },
      output: {
        schema: OBJ({
          ok: { type: "boolean", required: true },
          path: { type: "string", required: true },
          size_bytes: { type: "integer", required: true },
          mime: { type: "string", required: true },
          extension: { type: "string", required: true },
          head_hex: { type: "string", required: true },
          looks_binary: { type: "boolean", required: true },
          sha256: { type: "string", required: true },
          error: { type: "string" },
        }),
        render: text,
      },
      async execute(args) {
        const r = await probeFile(args.path, { sha256: args.sha256 ?? false });
        return r.ok
          ? { ok: true, path: r.path, size_bytes: r.sizeBytes, mime: r.mime, extension: r.extension || "", head_hex: r.headHex, looks_binary: r.looksBinary, sha256: r.sha256 || "" }
          : { ok: false, path: args.path, size_bytes: 0, mime: "", extension: "", head_hex: "", looks_binary: false, sha256: "", error: r.error };
      },
    }),
  );

  ctx.tools.register(
    defineTool({
      name: "browser",
      description:
        "Generic browser automation (Python playwright, OPTIONAL dependency; headless by default). Use when http_fetch/run_code hit login walls, JS challenges or anti-bot (403 / Cloudflare / zhihu zse-ck). Actions: open (navigate+wait), act (click/fill/press/wait/wait_selector/screenshot/shot/drag/eval), solve (captcha: kind=ocr/slider/geetest, needs optional python libs ddddocr/geeked), eval (run JS), cookies (export Cookie header), download (capture file), screenshot, close. The session uses ONE persistent worker per profile: cookies AND the open page survive across calls — login once, then continue; export cookies for download_file when allowed.",
      parameters: {
        action: {
          type: "string",
          required: true,
          enum: ["open", "act", "eval", "cookies", "download", "screenshot", "solve", "close"],
          description: "what to do",
        },
        url: { type: "string", description: "target URL (open/download/screenshot)" },
        wait_ms: { type: "integer", description: "extra settle time after open (JS challenges)" },
        wait_until: { type: "string", description: "goto waitUntil: domcontentloaded|load|networkidle" },
        kind: { type: "string", enum: ["ocr", "slider", "geetest"], description: "solve kind" },
        img_selector: { type: "string", description: "captcha image element (solve/ocr)" },
        input_selector: { type: "string", description: "text input to fill the OCR answer (solve/ocr)" },
        bg_selector: { type: "string", description: "slider background element (solve/slider)" },
        target_selector: { type: "string", description: "slider puzzle-piece element (solve/slider)" },
        handle_selector: { type: "string", description: "draggable slider handle element (solve/slider)" },
        captcha_id: { type: "string", description: "Geetest v4 captcha_id (solve/geetest)" },
        risk_type: { type: "string", description: "geetest risk type: slide|icon|gobang|ai (solve/geetest)" },
        duration: { type: "integer", description: "drag duration ms" },
        wait_after: { type: "integer", description: "ms to wait after a drag before verifying" },
        steps: {
          type: "array",
          description: "ordered steps for act",
          items: {
            type: "object",
            additionalProperties: false,
            properties: {
              action: { type: "string", required: true, enum: ["click", "fill", "press", "wait", "wait_selector", "screenshot", "shot", "drag", "eval"] },
              selector: { type: "string", description: "CSS selector" },
              value: { type: "string", description: "fill text / key / wait ms / drag dx" },
              path: { type: "string", description: "file path (screenshot/shot steps)" },
              expression: { type: "string", description: "JS expression (eval step)" },
              dx: { type: "integer", description: "pixels to drag (drag step)" },
              wait_after: { type: "integer", description: "ms wait after drag" },
              timeout_ms: { type: "integer" },
            },
          },
        },
        expression: { type: "string", description: "JS expression (eval action)" },
        selector: { type: "string", description: "CSS selector for click-to-download" },
        out_dir: { type: "string", description: "download destination (download action)" },
        shot_path: { type: "string", description: "absolute screenshot path" },
        timeout_ms: { type: "integer", description: "per-step timeout (default 60000)" },
      },
      output: {
        schema: OBJ({
          ok: { type: "boolean", required: true },
          action: { type: "string", required: true },
          url: { type: "string", required: true },
          title: { type: "string", required: true },
          text: { type: "string", required: true },
          result: { type: "string", required: true },
          cookie_header: { type: "string", required: true },
          shot_path: { type: "string", required: true },
          path: { type: "string", required: true },
          file_name: { type: "string", required: true },
          size: { type: "integer", required: true },
          error: { type: "string" },
        }),
        render: text,
      },
      async execute(args) {
        const req = {
          action: args.action,
          profile_dir: browserProfileDir,
          headless,
          url: args.url,
          wait_ms: args.wait_ms,
          wait_until: args.wait_until,
          steps: args.steps,
          expression: args.expression,
          selector: args.selector,
          kind: args.kind,
          img_selector: args.img_selector,
          input_selector: args.input_selector,
          bg_selector: args.bg_selector,
          target_selector: args.target_selector,
          handle_selector: args.handle_selector,
          captcha_id: args.captcha_id,
          risk_type: args.risk_type,
          duration: args.duration,
          wait_after: args.wait_after,
          tmp_dir: scratchDir,
          out_dir: args.out_dir ? (isAbsolute(args.out_dir) ? args.out_dir : join(downloadsDir, args.out_dir)) : downloadsDir,
          shot_path: args.shot_path,
          timeout_ms: args.timeout_ms,
        };
        const r = await browserAction(req, { python, timeoutMs: timeoutMs + 60000 });
        return {
          ok: r.ok === true,
          action: r.action ?? args.action,
          url: r.url ?? "",
          title: r.title ?? "",
          text: r.text ?? "",
          result: r.result ?? "",
          cookie_header: r.cookie_header ?? "",
          shot_path: r.shot_path ?? "",
          path: r.path ?? "",
          file_name: r.file_name ?? "",
          size: r.size ?? 0,
          ...(r.error ? { error: r.error } : {}),
        };
      },
    }),
  );

  const recordShape = () => ({
    domain: { type: "string", required: true },
    score: { type: "integer", required: true },
    verdict: { type: "string", required: true },
    resource_types: { type: "array", required: true, items: { type: "string" } },
    tags: { type: "array", required: true, items: { type: "string" } },
    aliases: { type: "array", required: true, items: { type: "string" } },
    checks: { type: "integer", required: true },
    first_seen: { type: "string", required: true },
    last_seen: { type: "string", required: true },
    notes: { type: "string", required: true },
    methods: { type: "string", required: true },
  });
  const toRecord = (r) => ({
    domain: r.domain,
    score: r.score ?? 0,
    verdict: r.verdict,
    resource_types: r.resource_types ?? [],
    tags: r.tags ?? [],
    aliases: r.aliases ?? [],
    checks: r.checks,
    first_seen: r.first_seen,
    last_seen: r.last_seen,
    notes: r.notes ?? "",
    methods: r.methods ?? "",
  });

  ctx.tools.register(
    defineTool({
      name: "memory_remember",
      description:
        "Persist an assessment of a website/service/dataset the agent just dealt with (score it 1-5, verdict, tags, methods that worked) so later requests reuse the knowledge. Re-calling the same domain merges new evidence (checks+1, tags/types union, notes/methods appended). Call this at the end of every resource task per the site-memory skill.",
      parameters: {
        domain: { type: "string", required: true, description: "domain or dataset/service id, e.g. europepmc.org / huggingface.co/datasets/x" },
        score: { type: "integer", description: "reliability/success score 1..5 (5 = great)" },
        verdict: { type: "string", enum: ["ok", "partial", "blocked", "needs_login", "unknown"], description: "outcome of latest attempt" },
        resource_types: { type: "array", items: { type: "string" }, description: "e.g. ebook, dataset, pdf, video, pan, image" },
        tags: { type: "array", items: { type: "string" }, description: "e.g. mirror, antibot, akamai, turnstile, gated, open-access, ip-blocked, redirect" },
        aliases: { type: "array", items: { type: "string" }, description: "sibling/mirror domains of the same service" },
        notes: { type: "string", description: "what it is, why useful" },
        methods: { type: "string", description: "methods that worked/failed (one line each)" },
      },
      output: {
        schema: OBJ({ ok: { type: "boolean", required: true }, record: { type: "object", additionalProperties: false, properties: recordShape() }, error: { type: "string" } }),
        render: text,
      },
      async execute(args) {
        try {
          const r = await memoryStore.remember(args);
          return { ok: true, record: toRecord(r) };
        } catch (e) {
          return { ok: false, error: e.message };
        }
      },
    }),
  );

  ctx.tools.register(
    defineTool({
      name: "memory_query",
      description:
        "Query the persistent site/service memory before starting a resource request and whenever you need known-good info: exact domain lookups (incl. aliases/mirrors), filter by resource_types/tags/verdict, or similar=true to find similar websites/services ranked by shared tags+types (e.g. other mirrors of the same service).",
      parameters: {
        domain: { type: "string", description: "domain to look up" },
        similar: { type: "boolean", description: "true = also return similar sites (ranked), not only exact" },
        resource_types: { type: "array", items: { type: "string" }, description: "filter by resource type" },
        tags: { type: "array", items: { type: "string" }, description: "filter by tag" },
        verdict: { type: "string", enum: ["ok", "partial", "blocked", "needs_login", "unknown"], description: "filter by verdict" },
        text: { type: "string", description: "free-text search over domain/tags/types/notes" },
      },
      output: {
        schema: OBJ({
          ok: { type: "boolean", required: true },
          count: { type: "integer", required: true },
          results: {
            type: "array",
            required: true,
            items: { type: "object", additionalProperties: false, properties: { ...recordShape(), relevance: { type: "integer", required: true } } },
          },
          error: { type: "string" },
        }),
        render: text,
      },
      async execute(args) {
        try {
          const rows = await memoryStore.query(args);
          return { ok: true, count: rows.length, results: rows.map((r) => ({ ...toRecord(r), relevance: r.relevance })) };
        } catch (e) {
          return { ok: false, count: 0, results: [], error: e.message };
        }
      },
    }),
  );
}

export { Config, apply, inject, name };
