// Lightweight multi-engine HTML search with graceful degradation. Backends are
// generic (Bing / Mojeek / DuckDuckGo Lite) and parsed defensively: if a site
// changes its HTML the tool reports the failure instead of crashing, and the
// model can fall back to the DSH base web_search tool.
import { httpRequest, DEFAULT_USER_AGENT } from "./http.js";

function decodeEntities(s) {
  return s
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#x27;|&#39;/g, "'")
    .replace(/&nbsp;/g, " ")
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(Number(d)));
}

function stripTags(s) {
  return decodeEntities(s)
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

const ENGINES = {
  bing: {
    build: (q) => `https://www.bing.com/search?q=${encodeURIComponent(q)}&setlang=zh-hans`,
    parse: (html) => {
      const out = [];
      const re = /<li class="b_algo"[\s\S]*?<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a><\/h2>[\s\S]*?(?:<p[^>]*>([\s\S]*?)<\/p>)?/g;
      let m;
      while ((m = re.exec(html)) && out.length < 12) {
        if (m[1].startsWith("http")) out.push({ title: stripTags(m[2]), url: m[1], snippet: stripTags(m[3] || "") });
      }
      return out;
    },
  },
  mojeek: {
    build: (q) => `https://www.mojeek.com/search?q=${encodeURIComponent(q)}`,
    parse: (html) => {
      const out = [];
      const re = /<li class="result"[\s\S]*?<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a><\/h2>[\s\S]*?(?:<p class="s"[^>]*>([\s\S]*?)<\/p>)?/g;
      let m;
      while ((m = re.exec(html)) && out.length < 12) {
        if (m[1].startsWith("http")) out.push({ title: stripTags(m[2]), url: m[1], snippet: stripTags(m[3] || "") });
      }
      return out;
    },
  },
  ddg: {
    build: (q) => `https://html.duckduckgo.com/html/?q=${encodeURIComponent(q)}`,
    parse: (html) => {
      const out = [];
      const re = /class="result__body"[\s\S]*?<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>[\s\S]*?(?:class="result__snippet"[^>]*>([\s\S]*?)<\/a>)?/g;
      let m;
      while ((m = re.exec(html)) && out.length < 12) {
        let u = m[1];
        const ud = /uddg=([^&]+)/.exec(u);
        if (ud) u = decodeURIComponent(ud[1]);
        if (u.startsWith("http")) out.push({ title: stripTags(m[2]), url: u, snippet: stripTags(m[3] || "") });
      }
      return out;
    },
  },
};

/**
 * Web search across several generic backends.
 * @param {object} p
 * @param {string} p.query
 * @param {string[]} [p.engines=["bing","mojeek","ddg"]]
 * @param {number} [p.limit=8]
 * @param {number} [p.timeoutMs=20000]
 */
export async function webSearch(p) {
  const { query, engines = ["bing", "mojeek", "ddg"], limit = 8, timeoutMs = 20000 } = p;
  const seen = new Set();
  const results = [];
  const failures = [];
  for (const name of engines) {
    const engine = ENGINES[name];
    if (!engine) {
      failures.push({ engine: name, error: "unknown engine" });
      continue;
    }
    try {
      const r = await httpRequest({
        url: engine.build(query),
        headers: { "User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8" },
        timeoutMs,
        maxBytes: 2_000_000,
      });
      if (r.status !== 200) {
        failures.push({ engine: name, error: `HTTP ${r.status}` });
        continue;
      }
      const hits = engine.parse(r.body.toString("utf8"));
      if (hits.length === 0) failures.push({ engine: name, error: "no results parsed" });
      for (const h of hits) {
        if (seen.has(h.url)) continue;
        seen.add(h.url);
        results.push(h);
        if (results.length >= limit) break;
      }
    } catch (e) {
      failures.push({ engine: name, error: e.message });
    }
    if (results.length >= limit) break;
  }
  return { ok: results.length > 0, results, failures };
}
