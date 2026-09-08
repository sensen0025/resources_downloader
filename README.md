# resources_downloader

A generic skill pack that lets a **DSH agent download anything itself** — with **zero site-specific code** to maintain.

```
User request (any site / any resource / dedicated downloader / captcha / crawler / multi-round retries)
        │
        ▼
DSH = the only brain (planning, multi-round strategy, failure retries, probe-verified delivery)   ← core
        │
   This repo does exactly two things ─────────────────────────────
   ① .dsh/skills/   methodology + resource-site skill cards that teach the agent how (21 cards)
   ② plugin/        cordis generic, stable, site-agnostic execution tooling
```

## Design principles

User requests are not enumerable: new sites, niche resources, login walls, anti-bot defenses,
"write your own crawler for this"…
Hard-coding an adapter per site means maintaining code that will rot.

This repo inverts that: **give the LLM all the capabilities to get the job done** — write programs,
execute them, search, fetch pages, download deterministically, verify.
- **Generic capability tools**: zero site-specific resident code;
- **Mature dedicated downloaders**: prefer community-maintained CLIs (e.g. BBDown for Bilibili,
  yt-dlp for YouTube) when they exist;
- **Evidence-driven retries**: the skill docs define multi-round strategy switching discipline
  (no blind guessing) **and** allow the agent to terminate gracefully when it judges further
  attempts hopeless (see `task-termination`);
- **Delivery must be verified**: a probe checks size / magic bytes / hash before a task counts as done.

---

## 🤖 DSH skill auto-mount prompt (copy & paste)

> Send the following prompt as a System Prompt or initial instruction to DSH to let it
> **discover, mount and orchestrate all skills in this repo by itself**:

````markdown
You are now an all-purpose resource download agent.
Your workspace contains a `.dsh/skills/` skill library and a `plugin/` generic execution base.

### Your execution discipline & workflow:

1. [Step 1: check the catalog & memory]
   - For any resource request (video / e-book / paper / dataset / cloud drive / wallpaper, etc.),
     first read `.dsh/skills/site-directory.md` via view_file and call `memory_query`.
   - Dedicated downloader first (table A): if Bilibili (BBDown), YouTube (yt-dlp), etc. match,
     load the corresponding card and run the CLI — do NOT bypass a mature tool to hand-write a crawler.
   - Site knowledge cards (table B): for Anna's Archive (DDoS-Guard + slow mirror), Gutenberg, HF,
     LittleSkin, etc., load the matching `site-<slug>.md` and follow it.
   - Niche/new sites: follow `resource-download` and `write-and-run-crawler`, use `run_code` to write
     one-off Python scripts on the spot to decrypt or extract direct links.
2. [Step 2: deterministic execution]
   - Direct links: call `download_file` (range resume);
   - Streams / segments: call `download_hls`;
   - Complex interaction / anti-bot / guard pages: call `browser` (Playwright persistent session
     and captcha solving).
3. [Step 3: mandatory probe verification]
   - After downloading, you MUST call `probe_file` to verify magic bytes (e.g. MP4 `ftyp`,
     EPUB `PK`, PDF `%PDF`), non-empty size and SHA-256.
   - Never deliver an empty file or an HTML error page.
4. [Step 4: clean delivery & memory]
   - Deliver full metadata: [file path] + [size] + [SHA-256] + [source URL/MD5].
   - Call `memory_remember` to persist domains, parameters and pitfalls that actually worked.
5. [Step 5: know when to stop]
   - If ~3+ distinct strategies made no real progress, tool calls keep spinning, or a decisive
     blocker is hit, wrap up per `task-termination`: deliver the closest verified variant of the
     same resource (marked near-miss) or report failure with evidence — do not burn forever.
6. [Step 6: summarize & designate result files]
   - Before finishing, write a short task summary and explicitly designate the result file(s)
     (one `primary` + optional `extra`) in the final FINAL_JSON, per the `task-summary` skill —
     the engine takes your designated primary as the task's result file.
````

---

## Repository contents

| Path | Contents |
|---|---|
| `.dsh/skills/*.md` | **22 DSH skill cards**: methodology (`resource-download`, `find-and-resolve-sources`, `write-and-run-crawler`, `download-and-verify`, `site-memory`, `captcha-handling`, `cookie-vault`, `task-termination`, `task-summary`) + `site-directory` (index) + site-specific cards (`site-bilibili-bbdown`, `site-videos-yt-dlp`, `site-gdgame`, `site-haowallpaper`, `site-annas-archive`, `site-project-gutenberg`, `site-open-access-papers`, `site-huggingface-datasets`, `site-quark-netdisk`, `site-littleskin`, …) |
| `plugin/` | cordis tool plugin `rd-tools`: `run_code`, `http_fetch`, `web_search`, `download_file` (range resume), `download_hls` (m3u8/AES-128), `probe_file` (magic/hash verification), `browser` (optional Python playwright automation: persistent session / login / anti-bot / click-to-download / cookie export / captcha solving), `memory_remember`/`memory_query` (site memory & scoring) |
| `plugin/lib/` | standalone implementations of every tool |
| `plugin/tests/run.mjs` | offline unit test suite (local HTTP fixtures) |
| `docs/` | skill-card & dedicated-downloader authoring specs |
| `.github/workflows/ci.yml` | automated CI: syntax checks + offline unit tests |

---

## Install & usage

### 1. Skills
Open a session with this repo as the DSH workspace and `.dsh/skills/` is auto-discovered;
alternatively symlink or copy `.dsh/skills/` into the same directory of any project.

### 2. Tool plugin (register into a profile such as `web` / `headless`)
```bash
dsh plugin --profile web add "link:/path/to/resources_downloader/plugin"
```

### 3. Runtime dependencies
- **Node** ≥ 18.17 (JS plugin: zero external deps)
- **ffmpeg**: required to mux audio/video
- **Dedicated downloaders** (on the host):
  - **BBDown** (`~/bin/BBDown`): preferred for Bilibili
  - **yt-dlp** (`~/bin/yt-dlp`): preferred for YouTube and 1000+ sites
- **Optional**: `playwright` (browser automation), `ddddocr` (captcha OCR)

---

## Development & testing

```bash
# Offline unit tests
node plugin/tests/run.mjs

# Syntax checks
for f in plugin/index.js plugin/lib/*.js; do node --check "$f"; done
```

## 🤝 Contributing

We welcome community skill cards for more sites!

- **Contribution guide & workflow**: see [CONTRIBUTING.md](./CONTRIBUTING.md)
- **Site knowledge-card spec**: see [docs/site-skills-spec.md](./docs/site-skills-spec.md)
- **Dedicated-downloader spec**: see [docs/targeted-downloader-spec.md](./docs/targeted-downloader-spec.md)

### Quick contribution checklist
1. Write the skill card at `.dsh/skills/site-<slug>.md` (follow the Frontmatter & section spec);
2. Register the new site/tool in `.dsh/skills/site-directory.md` (mark environment & date tested);
3. Keep `plugin/` generic (zero site-specific code) and run local tests: `node plugin/tests/run.mjs`;
4. Open a Pull Request!

---

## License

MIT © 2026 sensen0025
