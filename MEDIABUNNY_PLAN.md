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

### Phase 2 — Client thumbnail hydration (JS in `build_browser_html`)
- `async function mbCanvasThumb(fileId, timestamp, w, h)`: Input+UrlSource+CanvasSink+`getCanvas`,
  returns an object-URL (via `canvas.toBlob`); cache in `mbThumbCache` Map.
- `async function hydrateThumb(img)`: if `mbReady()` and kind is video → `canDecode()` gate →
  `mbCanvasThumb` → set `img.src` to object-URL. On *any* failure set
  `img.src = "/thumb/{id}?i=0"` (ffmpeg), whose existing `onerror="fallbackVideoThumb"` is rung 3.
- Change video branch of `mediaHtml`/`videoThumbHtml` (dedup.py:2222-2226) to emit the `<img>`
  **without** an immediate `/thumb/` src (data attributes only), so ffmpeg runs only on fallback —
  this is what actually reduces the ffmpeg dependency. Keep `onerror="fallbackVideoThumb(this)"`.
- Trigger `hydrateThumb` lazily from the existing `IntersectionObserver` (dedup.py:1403 / 2581)
  alongside `hydrateVideoFile`.

### Phase 3 — Hover-cycle frames via mediabunny
- Port `get_video_thumbnail_count` / `get_video_thumbnail_timestamp` (dedup.py:324-337, pure
  arithmetic) to JS.
- Replace the `/thumb/{id}?i=N` cycle source with mediabunny multi-frame
  (`canvasesAtTimestamps([...])` / repeated `getCanvas`) over computed timestamps; reuse object-URLs.
- Fallback: if mediabunny path unavailable, cycle `/thumb/?i=N` exactly as today.

### Phase 4 — Metadata via mediabunny (reduces ffprobe)
- In `hydrateVideoFile` (dedup.py:1826): when `mbReady()`, derive duration/width/height/codec from
  `input.computeDuration()` + `track.getDisplayWidth/Height()` + codec, compute thumbnail count
  client-side. Call `/meta/{id}` only when mediabunny is unavailable.
- Keep `serve_meta` (dedup.py:3009) intact — still the fallback, and still the source for image
  EXIF / audio info (those stay server-side).

### Phase 5 — Tests + docs
- Python (`test_dedup.py`): assert `build_browser_html` contains the mediabunny `<script>` when
  `MEDIABUNNY_SRC` is set and **omits it when blank** (boundary); assert the ffmpeg fallback wiring
  (`/thumb/`, `fallbackVideoThumb`) is still emitted (failure-mode coverage); regression-assert
  `serve_meta`/`serve_thumbnail` behavior is unchanged.
- JS isn't covered by the Python suite — document a manual browser matrix run via `--dry-run`:
  (a) mediabunny on (Chrome), (b) undecodable codec → ffmpeg fallback, (c) CDN blocked/offline →
  ffmpeg fallback, (d) ffmpeg absent → static icon.
- Update CLAUDE.md: file-layout table (mediabunny script + `hydrateThumb`), the "ffmpeg optional"
  invariant (now three-rung), and the video-preview-vs-hover note.

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
