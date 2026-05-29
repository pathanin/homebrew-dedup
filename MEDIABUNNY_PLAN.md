# Implementation plan: mediabunny as primary thumbnail/metadata engine, ffmpeg as fallback

## Decisions captured (from interview)
- **Goal:** *Reduce the ffmpeg dependency.* Do video thumbnail + metadata work in the
  browser via [mediabunny](https://mediabunny.dev) (WebCodecs). ffmpeg/ffprobe become a
  pure fallback, used only when the browser can't decode the codec or mediabunny isn't loaded.
- **Player:** unchanged — native `<video src="/media/{id}">`. No custom mediabunny player.
  (Formats the browser's `<video>` can't play natively still won't *play*; that's an accepted
  limitation of this scope. Thumbnails for them can still come from ffmpeg fallback.)
- **Delivery:** mediabunny loads from CDN via a `<script>` tag, kept **optional**. When it
  can't load (offline, blocked) the code detects `window.Mediabunny` is absent and falls back
  to the existing ffmpeg `/thumb/` + `/meta/` paths. A single `MEDIABUNNY_SRC` constant makes
  vendoring a local `.cjs` (offline-capable) a one-line future swap.

## Why this fits the codebase
- `dedup.py` stays a self-contained Python file (stdlib + send2trash). mediabunny ships as one
  CDN `<script>`; **no build step, no npm**. The Python side gains one constant and a `<script>`
  injection — no new Python deps.
- `/media/{id}` (`serve_file_with_range`, dedup.py:2842) is fully **Range-aware** (206 +
  `Content-Range` + `Accept-Ranges`). mediabunny's `UrlSource('/media/{id}')` requires exactly
  this — confirmed working seam.
- The grid video thumb already renders `<img ... onerror="fallbackVideoThumb(this)">`
  (dedup.py:2226, `fallbackVideoThumb` at 1928). That `onerror` is the natural fallback hook.
- No CSP header is set (only `X-Content-Type-Options: nosniff`), so a CDN script tag needs no
  policy changes. WebCodecs requires a secure context — `localhost`/`127.0.0.1` qualifies; LAN-IP
  access would silently fall back to ffmpeg (acceptable).

## Graceful-degradation invariant (CLAUDE.md: ffmpeg must stay optional)
Three rungs, everywhere a video frame/metadata is needed:
1. **mediabunny** — CDN loaded AND `track.canDecode()` true → decode in browser.
2. **ffmpeg** — server `/thumb/{id}` (cached JPEG) and `/meta/{id}` (ffprobe) — today's behavior.
3. **static fallback** — `fallbackVideoThumb` text span / icon, when neither is available.

Decision between rung 1 and 2 is driven by a `canDecode` codec check where possible, not only a
post-hoc try/catch.

## Trade-off to keep visible
Moving thumbnails client-side **forfeits the persistent server `ThumbnailCache`** (dedup.py:449).
ffmpeg extracts one frame, caches a tiny JPEG, served instantly across sessions; mediabunny
re-fetches media bytes and decodes per browser session. Mitigations:
- In-page `Map` of object-URLs keyed by `(fileId, index)` so re-hover/re-render within a session
  doesn't re-decode.
- ffmpeg's cache still serves the fallback path unchanged.
This is an accepted cost of the stated goal (fewer users needing ffmpeg installed).

---

## Phased work (all in `dedup.py` unless noted)

### Phase 0 — Spike ✅ DONE (validated 2026-05-29)
Built a standalone harness in `/tmp/mb-spike/` (range_server.py mirroring `serve_file_with_range`,
spike.html, run_spike.mjs via headless Playwright Chromium) and decoded ffmpeg-minted clips.

**Result: the whole seam works.** `UrlSource('/clip') → Python 206 Range server → demux →
getPrimaryVideoTrack → canDecode → CanvasSink({width,height,fit:'contain'}).getCanvas(t) → real
canvas pixels` all succeeded:
- VP9 .webm: `canDecode` true, 160×120 frame, non-empty pixels, **48 ms** first frame.
- **H.264 .mp4** (the common case): codec `avc1.64000c`, `canDecode` true, frame drawn, **9 ms**.
- Metadata (`getCodec`/`getCodecParameterString`/`getDisplayWidth/Height`/`computeDuration`) all
  returned correctly — confirms Phase 4 can source metadata client-side.
- First-frame latency (9–48 ms) is low enough that losing the server `ThumbnailCache` is a mild,
  acceptable cost. `fit:'contain'` matches the current letterbox.

**Critical delivery finding (changes Phase 1):** loading the bundle via `<script src>` depends on
the served MIME. jsDelivr serves `mediabunny.cjs` as `application/node` → **browser refuses to
execute it**. Validated content-types:
| URL | Content-Type | Classic `<script src>` (global `window.Mediabunny`)? |
|---|---|---|
| `jsdelivr…/mediabunny.cjs` | `application/node` | ❌ refused |
| `unpkg.com/mediabunny/dist/bundles/mediabunny.cjs` | `text/javascript` | ✅ |
| `jsdelivr…/mediabunny.mjs` | `application/javascript` | ⚠️ ESM only (`type="module"`) |
| `unpkg…/mediabunny.mjs`, `esm.sh/mediabunny` | JS mime | ⚠️ ESM only |
| **vendored, served by dedup.py as `text/javascript`** | controlled by us | ✅ + offline |

The global namespace is `window.Mediabunny` (bundle is `var Mediabunny = (() => {…})()`).

### Phase 1 — Load + config (server) ✅ DONE (unpkg CDN, 2026-05-29)
Implemented: `MEDIABUNNY_SRC` + `mediabunny_script_tag()` + `MEDIABUNNY_SCRIPT_TAG` constants
(dedup.py ~95), a render-`defer`-ed `<script>` injected into the browser `<head>` between the
title and `<style>` (empty-dirs page intentionally untouched), and a lazy `mbReady()` JS helper.
Pinned `https://unpkg.com/mediabunny@1.45.4/dist/bundles/mediabunny.cjs` (`text/javascript`,
1-yr immutable cache). 4 new tests in `test_dedup.py` (tag boundary + empty/disabled case +
bundle-loaded integration + ffmpeg-fallback-still-wired guard); full `test_dedup` suite green
(95/95). No behavior change yet — thumbnails still use ffmpeg until Phase 2.

#### Original Phase 1 notes
- Add `MEDIABUNNY_SRC` constant near the tuning constants (~dedup.py:90). If using a CDN it **must
  be a JS-MIME URL** — use `https://unpkg.com/mediabunny/dist/bundles/mediabunny.cjs` (pin a
  version), **not** the jsDelivr `.cjs` (served as `application/node`, refused). Empty string =
  feature off.
- Inject `<script src="{MEDIABUNNY_SRC}"></script>` into the browser page head (dedup.py:1054).
  Empty-dirs page (head at 3133) has no media — skip it.
- Define a JS availability flag read from the confirmed global: `window.MB = window.Mediabunny ||
  null;` and a `mbReady()` helper. Script-load failure simply leaves it null → rung 2.
- **Recommended given the spike:** vendor the bundle and add a `/static/mediabunny.js` route that
  serves it as `text/javascript`. This is the only option that is both offline-capable and
  MIME-safe, and dedup fully controls the header. `MEDIABUNNY_SRC` then defaults to that local
  path, with the CDN URL as a documented alternative. (Final call is the user's — see report.)

### Phase 2 — Client thumbnail hydration (JS in `build_browser_html`) ✅ DONE (2026-05-29)
Implemented `waitForMediabunny`, `mbVideoTrack`, `mbCanvasThumb`, `hydrateThumb`, and
`hydrateVideoThumbs` in the review UI. Grid video thumbnails now start with data attributes and no
initial `/thumb/` `src`; visible cards hydrate through mediabunny first, using `track.canDecode()`
before `CanvasSink`, then cache object URLs in `mbThumbCache`. Any failure sets the image source to
the existing `/thumb/{id}?i=N` ffmpeg endpoint, whose `onerror="fallbackVideoThumb(this)"` remains
the static fallback. `stopGroupVideoThumbCycle()` now rehydrates the first frame through the same
primary path after hover cycling stops.

Important timing fix: because the CDN script is `defer` and the inline review UI can render before
it executes, `hydrateThumb` waits briefly on a shared `waitForMediabunny()` promise before falling
back. This keeps page render non-blocking while preventing a fast-but-unnecessary ffmpeg fallback
when unpkg finishes a moment later.

Validated with `rtk python3 -m unittest -v test_dedup.py` (97 OK) and a browser smoke test against
duplicate H.264 files from `/tmp/mb-spike/`; headless Chromium reported `thumbSource:
"mediabunny"` and a `blob:http://127.0.0.1:7981/...` thumbnail source.

### Phase 3 — Hover-cycle frames via mediabunny ✅ DONE (2026-05-29)
Ported `get_video_thumbnail_count` / `get_video_thumbnail_timestamp` to JS as
`videoThumbnailCount()` / `videoThumbnailTimestamp()`. `hydrateVideoFile()` still calls `/meta/{id}`
for duration in this phase, then computes the count client-side so timestamp selection matches the
server fallback.

Grid hover cycling now updates `data-thumb-index` and `data-thumb-timestamp`, clears
`data-thumb-loaded`, and calls `hydrateThumb()` instead of assigning `/thumb/{id}?i=N`. Preloading
warms `mbThumbCache` via `mbCanvasThumb()` when possible, and falls back to the old ffmpeg image
preload when mediabunny is unavailable. The side-pane video thumbnail cycle now uses the same
mediabunny-first path and no longer starts from an immediate `/thumb/` `src`.

Validated with `rtk python3 -m unittest -v test_dedup.py` (98 OK). Browser verification used the
Webwright contract in `/private/tmp/webwright-phase3/` against duplicate 22-second H.264 files in
`/private/tmp/dedup-mb-phase3/`: `final_script_log.txt` shows initial frame 0, hover frame 1, and
reset frame 0 all with `source: 'mediabunny'` and `blob:` URLs; `fallback-count: 0`.

### Phase 4 — Metadata via mediabunny (reduces ffprobe) ✅ DONE (2026-05-29)
Added `mbVideoMetadata(file)` to derive duration, dimensions, codec, and thumbnail count from
mediabunny (`input.computeDuration()`, `track.getDisplayWidth/Height()`,
`track.getCodecParameterString()` / `track.getCodec()`). `hydrateVideoFile()` is now
mediabunny-first and calls `/meta/{id}` only when mediabunny is unavailable or fails.

Video metadata is normalized and cached on each file via `applyVideoMetadata()` as
`videoDuration`, `thumbnailCount`, `videoMetadata`, and `videoMetadataSource`. `renderPaneMeta()`
uses that cached video payload; non-video metadata still uses `/meta/{id}`. `serve_meta()` remains
unchanged as the fallback and the image EXIF/audio metadata source.

Validated with `rtk python3 -m unittest -v test_dedup.py` (99 OK). Browser verification used the
Webwright contract in `/private/tmp/webwright-phase4/` against duplicate 22-second H.264 files:
`final_script_log.txt` shows both videos with `source: 'mediabunny'`, duration `22`, count `4`,
dimensions `320 × 240`, codec `avc1.64000c`, and no `/meta/` requests before or after side-pane
metadata render.

### Phase 5 — Tests + docs ✅ DONE (2026-05-29)
- Python (`test_dedup.py`): covered — `mediabunny_script_tag` emits a deferred tag when set and
  returns `""` when blank (boundary); `build_browser_html` loads `MEDIABUNNY_SRC`, keeps the ffmpeg
  fallback wiring (`/thumb/${...}?i=${...}`, `fallbackVideoThumb`), emits `hydrateThumb` /
  `waitForMediabunny` / `canDecode`, sets metadata `source = "mediabunny"` first, and the grid video
  `<img>` carries `onerror="fallbackVideoThumb(this)"` with no initial `src="/thumb/`. Full suite
  green (99 OK).
- CLAUDE.md updated: line count, file-layout table (mediabunny constants + client JS), a new
  "mediabunny (video thumbnails + metadata)" section (CDN delivery, unpkg-vs-jsDelivr, secure-context,
  trade-off), the three-rung ffmpeg-optional invariant, and the video-preview-vs-hover note.
- Manual browser matrix (headless Chromium, fixtures `/private/tmp/dedup-mb-phase3`,
  harness `/private/tmp/dedup-mb-phase5`):
  - **(a) mediabunny on** → `blob:` thumbnails + `source: mediabunny` metadata — verified phases 2–4.
  - **(b/c) CDN blocked** (`MEDIABUNNY_SRC` → unreachable URL) → `window.Mediabunny` undefined; grid
    `<img>` fell back to `/thumb/{id}` (`200 image/jpeg`, naturalWidth 360) and metadata to `/meta/`
    (2 ffprobe requests). Rung 1→2 confirmed.
  - **(d) ffmpeg absent** (`FFMPEG_PATH`/`FFPROBE_PATH` = None) → `/thumb/` returns 404, `onerror`
    swaps in `span.video-fallback` (filename text). 0 video `<img>` remain, 2 static spans. Rung 2→3
    confirmed.

### Phase 6 — Out of scope (noted, not built)
Custom mediabunny canvas+WebAudio player for browser-unplayable formats (MKV/AVI/HEVC/ProRes).
Excluded per the scope decision; revisit only if "play unsupported formats" becomes the goal.

## Files touched
- `dedup.py` — constant + script injection + client JS (Phases 1-4)
- `test_dedup.py` — Phase 5 assertions
- `CLAUDE.md` — invariant + layout updates

## Open items
- ~~Exact CDN URL + global namespace~~ → resolved: `window.Mediabunny`; unpkg `.cjs` for CDN, or
  vendor + serve as `text/javascript` (recommended). Pin the version at build time.
- ~~`fit` parity~~ → resolved: `fit:'contain'` letterboxes correctly (Phase 0).
- **Remaining decision for the user:** vendor the ~1.4 MB bundle (offline + MIME-safe, adds a JS
  blob to the repo) vs. unpkg CDN (repo stays one Python file, but UI needs internet → ffmpeg
  fallback offline). Spike works either way.
- HEVC/ProRes still depend on the browser's WebCodecs support (Safari strong, Chrome/Firefox
  partial); `canDecode` gate + ffmpeg fallback covers the gaps.
