# Handoff — mediabunny thumbnail/metadata migration

**Branch:** `mediabunny-thumbnails` (off `main`)
**Last completed commit before this handoff:** `ea8a0de Load mediabunny bundle in review UI` (Phase 1)
**Detailed plan & spike log:** `MEDIABUNNY_PLAN.md` (read it — this is just orientation + next action)
**Date:** 2026-05-29

## Goal (locked with the user)
Make **mediabunny** (browser/WebCodecs) the *primary* engine for video **thumbnails + metadata**,
with server-side **ffmpeg/ffprobe as fallback**. Motivation: *reduce the ffmpeg dependency* so most
users never install it. The native `<video>` tag stays the player — **no custom player is being
built** (formats the browser can't play natively still won't play; that's accepted).

## Delivery decision (locked)
**unpkg CDN**, not vendored. `MEDIABUNNY_SRC = https://unpkg.com/mediabunny@1.45.4/dist/bundles/mediabunny.cjs`.
- Why not jsDelivr: it serves `.cjs` as `application/node` → **browser refuses the `<script>`**.
  unpkg serves `text/javascript` (verified). Global namespace is `window.Mediabunny`.
- Why not vendor: the Homebrew formula installs **only `dedup.py`** (`Formula/dedup.rb`), so a
  vendored blob would need a formula `install` line + runtime path resolution + a ~1.4 MB blob in
  the one-file repo. CDN keeps the install one file; offline simply falls back to ffmpeg.

## Fallback chain (do NOT weaken — CLAUDE.md says ffmpeg must stay optional)
Three rungs, everywhere a video frame/metadata is needed:
1. **mediabunny** — `mbReady()` true AND `track.canDecode()` true → decode in browser.
2. **ffmpeg** — server `/thumb/{id}` (cached JPEG) + `/meta/{id}` (ffprobe). Today's behavior.
3. **static** — `fallbackVideoThumb()` text span/icon, when neither is available.
Prefer driving rung 1↔2 off a `canDecode` check, not only a post-hoc try/catch.

## Phase 0 — spike (DONE, validated)
Proven in a headless harness at `/tmp/mb-spike/` (disposable; may be gone — recreate from
`MEDIABUNNY_PLAN.md` if needed): `new Input({formats: ALL_FORMATS, source: new UrlSource('/media/{id}')})`
→ `getPrimaryVideoTrack()` → `track.canDecode()` → `new CanvasSink(track,{width,height,fit:'contain'})`
→ `getCanvas(t)` returns a real canvas frame, reading from dedup's **206 Range** server.
- H.264 .mp4 decoded in **9 ms**, VP9 .webm in **48 ms** → losing the server ThumbnailCache is a
  mild, acceptable cost.
- Metadata APIs confirmed: `input.computeDuration()`, `track.getDisplayWidth/Height()`,
  `track.getCodec()`, `track.getCodecParameterString()`.

## Phase 1 — load + config (DONE this session, committed)
In `dedup.py` (~line 95): `MEDIABUNNY_SRC`, `mediabunny_script_tag(src)`, `MEDIABUNNY_SCRIPT_TAG`.
Deferred `<script>` injected into the **review-UI** `<head>` (between `<title>` and `<style>`;
empty-dirs page intentionally NOT touched). Lazy JS helper added right after the page `<script>`
open:
```js
function mbReady() { return typeof window.Mediabunny !== "undefined" && window.Mediabunny !== null; }
```
`defer` + lazy check = CDN fetch never blocks render. **No behavior change yet.**
Tests added to `test_dedup.py` (gitignored, so not in git — still runs): tag boundary, empty/disabled
case, bundle-loaded integration, ffmpeg-fallback-still-wired guard.

## Phase 2 — client thumbnail hydration (DONE this session)
Wired mediabunny into the grid thumbnail fallback seam.
- Added `waitForMediabunny()` so the deferred CDN bundle can finish loading before thumbnail
  hydration falls back to ffmpeg. Page render still does not block on the CDN.
- Added `mbVideoTrack()`, `mbCanvasThumb()`, `hydrateThumb()`, and `hydrateVideoThumbs()` in the
  review UI JS. `track.canDecode()` is checked before `CanvasSink` decode, and successful frames
  become session-cached `blob:` object URLs.
- Changed the grid video branch of `mediaHtml()` to emit a data-only `<img>` with no initial
  `/thumb/` `src`. The existing `onerror="fallbackVideoThumb(this)"` static rung is preserved.
- Triggered `hydrateVideoThumbs(el)` from `renderGroupContent()`, the existing lazy render path
  reached by the `IntersectionObserver`.
- Adjusted `stopGroupVideoThumbCycle()` so leaving hover rehydrates the first frame through the
  same mediabunny-first path instead of forcing `/thumb/?i=0`.

Key code seams (verified this session):
- `/media/{id}` Range server: `serve_file_with_range` dedup.py:2952 (206/Content-Range/Accept-Ranges ✓).
- Grid video thumb `<img ... onerror="fallbackVideoThumb(this)">`: `mediaHtml` dedup.py:2331;
  `fallbackVideoThumb` def at dedup.py:2037 (currently just swaps in a text span).
- ffmpeg thumbnail path (the fallback, leave intact): `serve_thumbnail` 3102 → `render_thumbnail`
  496 → `build_thumbnail_command` 427.
- Hover cycling + meta fetch: `hydrateVideoFile` dedup.py:1934, `/meta/{id}` → `serve_meta` 3119.

## Phase 3 — hover-cycle frames via mediabunny (DONE this session)
Ported `get_video_thumbnail_count` / `get_video_thumbnail_timestamp` to JS as
`videoThumbnailCount()` / `videoThumbnailTimestamp()`.
- `hydrateVideoFile()` still uses `/meta/{id}` for duration in this phase, then computes the count
  client-side so the JS timestamp math matches the server fallback.
- Grid hover cycling now updates `data-thumb-index` + `data-thumb-timestamp`, clears
  `data-thumb-loaded`, and calls `hydrateThumb()` instead of assigning `/thumb/{id}?i=N`.
- Hover preloading now warms the mediabunny object URL cache when possible and falls back to the
  old ffmpeg image preload when mediabunny is unavailable.
- The side-pane video thumbnail cycle now uses the same mediabunny-first `hydrateThumb()` path and
  no longer starts from an immediate `/thumb/` `src`.

## NEXT: Phase 4 — metadata via mediabunny
Move video duration/dimensions/codec collection from `/meta/{id}` to mediabunny where possible:
`input.computeDuration()`, `track.getDisplayWidth/Height()`, `track.getCodec()`, and
`track.getCodecParameterString()`. Keep `/meta/{id}` as fallback and for image EXIF/audio.

## How to run / verify
```bash
rtk python3 -m unittest -v test_dedup.py   # 98 OK after Phase 3 local tests
python3 dedup.py testfile/ --dry-run       # manual browser check (testfile/ has no video though)
```
Phase 2 smoke used duplicate H.264 files copied from `/tmp/mb-spike/` into
`/private/tmp/dedup-mb-smoke/`, with `webbrowser.open` suppressed for controlled testing:
headless Chromium loaded `http://127.0.0.1:7981/` and reported `thumbSource: "mediabunny"` with a
`blob:http://127.0.0.1:7981/...` grid thumbnail. The ffmpeg fallback path remains unit-covered.
Phase 3 smoke used the Webwright contract in `/private/tmp/webwright-phase3/` and duplicate
22-second H.264 files in `/private/tmp/dedup-mb-phase3/`; `final_script_log.txt` shows initial
frame 0, hover frame 1, and reset frame 0 all using `source: 'mediabunny'` with `blob:` URLs, plus
`fallback-count: 0`.

## Watch out for
- **`test_popup_port` has 8 PRE-EXISTING failures on `main`** (assert popup-window code in
  `_SHARED_JS` that isn't present). NOT caused by this work — confirmed by stashing edits. Don't
  chase them as if Phase 1 broke something. (User aware; left as a separate question.)
- `test_dedup.py` / `test_popup_port.py` are **gitignored** — edits won't show in `git diff` or
  ship in the tarball, but they still run locally.
- `graphify-out/` deletion is already committed separately as `05fa359 Remove generated graphify output`.
- WebCodecs needs a secure context: `localhost`/`127.0.0.1` OK; LAN-IP access silently falls back
  to ffmpeg. Acceptable.
- Python 3.8+, stdlib-only (+send2trash) on the server side; `sys.dont_write_bytecode` invariant
  intact. mediabunny is browser-only — adds no Python dep.

## Open decision left for the user
Pre-existing `test_popup_port` breakage: investigate/fix separately, or leave?
