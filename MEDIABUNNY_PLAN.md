# Implementation plan: mediabunny as primary thumbnail/metadata engine, ffmpeg as fallback

## Decisions captured (from interview)
- **Goal:** *Reduce the ffmpeg dependency.* Do video thumbnail + metadata work in the
  browser via [mediabunny](https://mediabunny.dev) (WebCodecs). ffmpeg/ffprobe become a
  pure fallback, used only when the browser can't decode the codec or mediabunny isn't loaded.
- **Player:** native `<video src="/media/{id}">` stays the **primary** player (rung A). Phases 1–5
  did not build a custom player. **Phase 6 (below) extends the original scope:** when native
  `<video>` can't play a file, the *preview modal* falls back to a mediabunny canvas + WebAudio
  player (rung B), then to the static "unavailable" notice (rung C). This is gated by
  `track.canDecode()` — it recovers *container*-blocked files, not codecs the platform's WebCodecs
  can't decode. See Phase 6 for the honest scope.
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

### Phase 6 — Fallback player for browser-unplayable formats (PLANNED — next work)
**Goal:** let the *preview modal* **play** files the native `<video>` can't, by decoding them
in-browser with mediabunny (WebCodecs video + the Web Audio API). Same engine as the
thumbnail/metadata work, so it stays browser-only — **no new Python dependency and no new server
route.**

**Honest scope — what this does and does NOT fix.** The win is files where the *container* is the
blocker but the *codec* is WebCodecs-decodable. `track.canDecode()` is the ceiling; this phase adds
no codec support the platform lacks.
- **MKV / AVI** wrapping H.264/H.265/VP9/AV1 + AAC/Opus → native `<video>` often refuses the
  container, mediabunny demuxes it and `canDecode()` passes → **player works.** Primary target.
- **HEVC/H.265 .mp4** → platform-dependent (Safari + some hardware Chrome decode, else not).
  `canDecode()` decides per-machine.
- **ProRes / uncompressed / exotic codecs** → almost always `canDecode() === false` (not a WebCodecs
  codec) → **no win; degrades to the static notice.** Do **not** promise "plays anything."

**Three rungs (preview player only — mirrors the thumbnail chain):**
- **A. native `<video src="/media/{id}">`** — unchanged, cheapest, keeps native controls, hardware
  audio, and accessibility. Always tried first.
- **B. mediabunny canvas + WebAudio player** — mounted only when native fails **and** the video
  track's `canDecode()` is true.
- **C. static** — existing "preview unavailable" notice, when B can't decode (or non-secure context).

**Detecting native failure (A → B)** — not the `error` event alone:
- `video.error` set after load → fail.
- **Stall/timeout:** `readyState` stuck below `HAVE_CURRENT_DATA` after a few seconds, no progress → fail.
- **Audio-plays-video-frozen:** `videoWidth === 0` while `currentTime` advances → fail.
Use an `error`-event + timeout combo, then swap the modal body from `<video>` to the canvas player.

**Player construction (B), reusing the Phase 0/2 seam:**
```js
const input = new Input({ formats: ALL_FORMATS, source: new UrlSource(`/media/${urlId(id)}`) });
const videoTrack = await input.getPrimaryVideoTrack();   // may be null
const audioTrack = await input.getPrimaryAudioTrack();   // may be null
// if (!videoTrack || !(await videoTrack.canDecode())) → rung C
const vSink = new CanvasSink(videoTrack, { width, height, fit: 'contain' });
const aSink = (audioTrack && await audioTrack.canDecode()) ? new AudioBufferSink(audioTrack) : null;
```
- **Video:** `vSink.canvases(startT)` async-generates `WrappedCanvas {canvas,timestamp,duration}`
  (pre-decodes a few ahead). `vSink.getCanvas(t)` paints an immediate poster frame on open/seek.
- **Audio = master clock:** `aSink.buffers(startT)` yields `WrappedAudioBuffer {buffer,timestamp,duration}`;
  schedule each on one `AudioContext` via `src.start(baseTime + (timestamp - startT))` (the docs'
  Web-Audio pattern). Media time = `startT + (audioContext.currentTime - baseTime)`.
- **Sync:** a `requestAnimationFrame` present-loop reads media time off the audio clock and draws the
  newest decoded `WrappedCanvas` with `timestamp <= mediaTime`. **No audio track** → use a
  `performance.now()` wall clock as master instead.
- **Window the audio:** do **not** for-await the whole file up front (the docs' 10-second snippet
  schedules eagerly — fine for a demo, a memory problem for a long clip). Schedule only ~3–5 s ahead
  of the master clock and top up; track scheduled `AudioBufferSourceNode`s so they can be stopped.
- **Controls:** play/pause via `audioContext.suspend()/resume()`; seek = stop scheduled sources,
  reset `baseTime`/`startT`, re-create the `canvases()`/`buffers()` iterators from the seek point,
  `getCanvas(t)` for the poster; volume via a `GainNode`.

**Lifecycle — MUST respect `previewRenderToken` and tear down the AudioContext (CLAUDE.md invariant).**
The player is heavily async (decode loop + rAF loop + audio scheduling) and lives in the preview
modal, which `previewRenderToken` governs (incremented on `renderPreview()`/`closePreview()`; stale
async work bails). Bake in:
- Capture the token when the player starts; re-check after every `await` and at the top of each rAF
  tick. If stale → stop the rAF loop, `.stop()` every scheduled source, and `audioContext.close()`.
- Browsers cap concurrent `AudioContext`s (~6), so teardown is **mandatory** — a leaked context keeps
  audio playing after the modal closes and exhausts the pool after a few previews.

**Seam discipline (do not regress existing invariants):**
- Integrate in the **preview modal path only** (the `<video src="/media/{id}">` element). The grid
  hover-cycle path stays separate — CLAUDE.md says the two are independent; **do not merge them.**
- Reuse the existing `/media/{id}` **206 Range** endpoint (`serve_file_with_range`). **No new Python
  route, no server change** — keeps the "ffmpeg/Python optional" and "browser-only mediabunny"
  invariants intact.
- **Secure-context only:** WebCodecs needs `localhost`/`127.0.0.1`. LAN-IP access → rung B skipped →
  native then static. Same accepted limitation as thumbnails.

**Reference implementation:** mediabunny ships an official from-scratch player with frame-accurate A/V
sync — read it before coding and lift its clock/scheduling structure rather than reinventing:
<https://mediabunny.dev/examples/media-player/> (source linked from <https://mediabunny.dev/examples>).

**Verification matrix (mirror Phase 5 — headless + manual):**
- **MKV/AVI wrapping H.264+AAC** (native can't play, WebCodecs can) → player mounts, video draws,
  audio plays in sync; seek + pause work.
- **ProRes / undecodable codec** → `canDecode()` false → graceful static notice, no console errors,
  AudioContext never created.
- **Long clip** → audio scheduling stays windowed (memory flat); closing the modal mid-playback stops
  audio and closes the AudioContext (no leak; `previewRenderToken` honored).
- **LAN-IP / non-secure context** → rung B skipped, falls to native then static.

**Still out of scope, even for Phase 6:** subtitle rendering (mediabunny can't read subtitle tracks
yet) and codecs the platform's WebCodecs cannot decode (the `canDecode()` gate is the honest ceiling).

## Files touched
- `dedup.py` — constant + script injection + client JS (Phases 1-4); Phase 6 adds the preview-modal
  fallback player JS (no server change).
- `test_dedup.py` — Phase 5 assertions (Phase 6 adds player-seam assertions).
- `CLAUDE.md` — invariant + layout updates.

## Open items
- ~~Exact CDN URL + global namespace~~ → resolved: `window.Mediabunny`; unpkg `.cjs` for CDN, or
  vendor + serve as `text/javascript` (recommended). Pin the version at build time.
- ~~`fit` parity~~ → resolved: `fit:'contain'` letterboxes correctly (Phase 0).
- **Remaining decision for the user:** vendor the ~1.4 MB bundle (offline + MIME-safe, adds a JS
  blob to the repo) vs. unpkg CDN (repo stays one Python file, but UI needs internet → ffmpeg
  fallback offline). Spike works either way.
- HEVC/ProRes still depend on the browser's WebCodecs support (Safari strong, Chrome/Firefox
  partial); `canDecode` gate + ffmpeg fallback covers the gaps.
