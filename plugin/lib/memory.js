// Persistent site/service memory store (JSONL under <root>/memory/).
// The agent rates sites/services/datasets it encounters and reuses that
// knowledge later: look up a domain, its mirrors/similar services, known
// anti-bot behavior and methods that worked. Zero runtime deps.
import { promises as fsp } from "node:fs";
import { dirname, join } from "node:path";

const VERDICTS = new Set(["ok", "partial", "blocked", "needs_login", "unknown"]);

function normalizeDomain(d) {
  if (!d) return "";
  return String(d)
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, "")
    .replace(/\/.*$/, "")
    .replace(/^www\./, "");
}

class MemoryStore {
  constructor(dir) {
    this.file = join(dir, "sites.jsonl");
    this.lock = Promise.resolve();
    this.cache = null;
  }

  _run(task) {
    const next = this.lock.then(task, task);
    this.lock = next.catch(() => {});
    return next;
  }

  async _load() {
    if (this.cache) return this.cache;
    try {
      const raw = await fsp.readFile(this.file, "utf8");
      this.cache = raw
        .split("\n")
        .filter((l) => l.trim())
        .map((l) => JSON.parse(l))
        .filter((r) => r && r.domain);
    } catch {
      this.cache = [];
    }
    return this.cache;
  }

  async _save() {
    await fsp.mkdir(dirname(this.file), { recursive: true });
    const body = this.cache.map((r) => JSON.stringify(r)).join("\n") + "\n";
    const tmp = this.file + ".tmp";
    await fsp.writeFile(tmp, body);
    await fsp.rename(tmp, this.file);
  }

  /**
   * Create or merge a record for one normalized domain.
   * @param {object} rec {domain, score?, verdict?, resource_types?, tags?,
   *   aliases?, notes?, methods?}
   * @returns stored record
   */
  remember(rec) {
    return this._run(async () => {
      const domain = normalizeDomain(rec.domain);
      if (!domain) throw new Error("remember: domain is required");
      const rows = await this._load();
      const now = new Date().toISOString();
      const existing = rows.find((r) => r.domain === domain);
      const union = (a, b) => [...new Set([...(a || []), ...(b || [])])];
      const merged = {
        domain,
        score: rec.score ?? existing?.score ?? null,
        verdict: rec.verdict ?? existing?.verdict ?? "unknown",
        resource_types: union(existing?.resource_types, rec.resource_types),
        tags: union(existing?.tags, rec.tags),
        aliases: union(existing?.aliases, rec.aliases),
        checks: (existing?.checks ?? 0) + 1,
        last_seen: now,
        first_seen: existing?.first_seen ?? now,
        notes: appendLine(existing?.notes, rec.notes),
        methods: appendLine(existing?.methods, rec.methods),
      };
      if (!VERDICTS.has(merged.verdict)) throw new Error(`invalid verdict: ${merged.verdict}`);
      if (merged.score !== null && (typeof merged.score !== "number" || merged.score < 1 || merged.score > 5)) {
        throw new Error("score must be null or an integer 1..5");
      }
      if (existing) Object.assign(existing, merged);
      else rows.push(merged);
      await this._save();
      return { ...merged };
    });
  }

  /**
   * Query records. Any of domain/alias/tags/types/verdict/text may filter;
   * similar=true ranks results by shared tags/types (find similar sites).
   */
  query(q = {}) {
    return this._run(async () => {
      const rows = await this._load();
      const wantDomain = q.domain ? normalizeDomain(q.domain) : "";
      const wantTypes = (q.resource_types || []).filter(Boolean);
      const wantTags = (q.tags || []).filter(Boolean);
      const wantVerdict = q.verdict;
      const text = (q.text || "").toLowerCase();
      const similar = q.similar === true;
      const scored = rows
        .map((r) => {
          let rel = 0;
          let matched = true;
          if (wantDomain) {
            const hit = r.domain === wantDomain || (r.aliases || []).some((a) => normalizeDomain(a) === wantDomain);
            if (hit) rel += 5;
            else if (r.domain.includes(wantDomain) || wantDomain.includes(r.domain)) rel += 2;
            else if (!similar) matched = false;
          }
          if (wantTypes.length) {
            const sharedT = (r.resource_types || []).filter((t) => wantTypes.includes(t)).length;
            if (sharedT > 0) rel += sharedT * 2;
            else if (!similar) matched = false;
          }
          if (wantTags.length) {
            const sharedG = (r.tags || []).filter((t) => wantTags.includes(t)).length;
            if (sharedG > 0) rel += sharedG;
            else if (!similar) matched = false;
          }
          if (wantVerdict && r.verdict !== wantVerdict) matched = false;
          if (text) {
            const hay = `${r.domain} ${(r.tags || []).join(" ")} ${(r.resource_types || []).join(" ")} ${r.notes || ""} ${(r.aliases || []).join(" ")}`.toLowerCase();
            if (hay.includes(text)) rel += 3;
            else if (!similar) matched = false;
          }
          return { r, rel, matched };
        })
        .filter((s) => s.matched);
      // "similar" mode: if any selector is present, only entries with actual
      // overlap (relevance > 0) qualify; an empty selector set lists all.
      const hasSelector = Boolean(wantDomain || wantTypes.length || wantTags.length || text);
      const filtered = similar && hasSelector ? scored.filter((s) => s.rel > 0) : scored;
      filtered.sort((a, b) => b.rel - a.rel);
      return filtered.map((s) => ({ ...s.r, relevance: s.rel }));
    });
  }
}

function appendLine(prev, next) {
  if (!next) return prev ?? "";
  const line = String(next).replace(/\s*\n\s*/g, " | ");
  return prev ? `${prev}\n${line}` : line;
}

export { MemoryStore, normalizeDomain };
