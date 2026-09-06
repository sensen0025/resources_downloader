// Generic HTTP client (zero runtime deps). Handles redirects, timeout, size cap,
// and gzip/deflate decoding for buffered responses. Used by all tools.
import http from "node:http";
import https from "node:https";
import { gunzipSync, inflateSync } from "node:zlib";

const DEFAULT_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36";

/**
 * Perform one buffered HTTP(S) request with redirect following.
 * @param {object} opts
 * @param {string} opts.url
 * @param {string} [opts.method="GET"]
 * @param {Record<string,string>} [opts.headers]
 * @param {number} [opts.timeoutMs=30000] socket idle / total wall budget
 * @param {number} [opts.maxBytes=0] cap on response body bytes (0 = unlimited)
 * @param {number} [opts.redirects=8]
 * @returns {Promise<{status:number, statusText:string, headers:Record<string,string>,
 *   finalUrl:string, body:Buffer}>}
 */
export function httpRequest(opts = {}) {
  const {
    url,
    method = "GET",
    headers = {},
    timeoutMs = 30000,
    maxBytes = 0,
    redirects = 8,
  } = opts;
  return new Promise((resolve, reject) => {
    const mod = url.startsWith("https:") ? https : url.startsWith("http:") ? http : null;
    if (!mod) return reject(new Error(`unsupported URL protocol: ${url}`));
    const u = new URL(url);
    const req = mod.request(
      u,
      {
        method,
        headers: {
          "User-Agent": DEFAULT_UA,
          Accept: "*/*",
          "Accept-Encoding": "gzip, deflate",
          ...headers,
        },
      },
      (res) => {
        const status = res.statusCode ?? 0;
        const flat = {};
        for (let i = 0; i < res.rawHeaders.length; i += 2) {
          const k = res.rawHeaders[i].toLowerCase();
          flat[k] = res.rawHeaders[i + 1];
        }
        const loc = flat.location;
        if (status >= 300 && status < 400 && loc && redirects > 0) {
          res.resume();
          return resolve(
            httpRequest({
              url: new URL(loc, u).toString(),
              method,
              headers,
              timeoutMs,
              maxBytes,
              redirects: redirects - 1,
            }),
          );
        }
        const chunks = [];
        let total = 0;
        res.on("data", (c) => {
          total += c.length;
          if (maxBytes > 0 && total > maxBytes) {
            res.destroy(new Error(`response exceeds maxBytes=${maxBytes}`));
            return;
          }
          chunks.push(c);
        });
        res.on("end", () => {
          let body = Buffer.concat(chunks);
          const enc = (flat["content-encoding"] || "").toLowerCase();
          try {
            if (enc === "gzip") body = gunzipSync(body);
            else if (enc === "deflate") body = inflateSync(body);
          } catch {
            /* keep raw body if decode fails */
          }
          resolve({ status, statusText: res.statusMessage || "", headers: flat, finalUrl: url, body });
        });
        res.on("error", (err) => {
          clear();
          reject(err);
        });
        function clear() {}
      },
    );
    req.on("error", (err) => {
      clearTimeout(timer);
      reject(err);
    });
    const timer = setTimeout(() => {
      req.destroy(new Error(`request timeout after ${timeoutMs}ms: ${method} ${url}`));
    }, timeoutMs);
    req.end();
  });
}

export const DEFAULT_USER_AGENT = DEFAULT_UA;
