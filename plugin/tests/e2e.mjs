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
import { httpRequest } from "../lib/http.js";
import { downloadFile } from "../lib/download.js";
import { downloadHls } from "../lib/hls.js";
import { probeFile } from "../lib/probe.js";
import { runCode } from "../lib/exec.js";
import { webSearch } from "../lib/search.js";

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
      out = await downloadFile(args);
      break;
    case "hls":
      out = await downloadHls(args);
      break;
    case "fetch":
      out = await httpRequest({ url: args.url, timeoutMs: args.timeoutMs ?? 30000, maxBytes: args.maxBytes ?? 5 * 1024 * 1024 }).then(
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
