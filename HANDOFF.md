# Handoff — mediabunny thumbnail/metadata migration

**Branch:** `mediabunny-thumbnails` (off `main`)
**Last commit:** `ea8a0de Load mediabunny bundle in review UI` (Phase 1)
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

## NEXT: Phase 2 — client thumbnail hydration (the actual win)
Wire mediabunny into the existing fallback seam. Concrete steps:
1. JS `async function mbCanvasThumb(fileId, timestamp, w, h)`: build Input+UrlSource('/media/'+id)
   +CanvasSink, `getCanvas`, return an object-URL via `canvas.toBlob`. Cache in a session
   `Map` keyed `(fileId,index)` to offset the lost server cache.
2. JS `async function hydrateThumb(img)`: if `mbReady()` && video && `await track.canDecode()` →
   set `img.src` to the object-URL. On ANY failure → `img.src = "/thumb/{id}?i=0"` (ffmpeg), whose
   existing `onerror="fallbackVideoThumb(this)"` is rung 3.
3. Change the video branch of `mediaHtml`/`videoThumbHtml` (dedup.py ~2222-2226) to emit the
   video `<img>` **without** an immediate `/thumb/` src (data attributes only) so ffmpeg runs only
   on fallback — this is what actually reduces the ffmpeg dependency. **Keep** the
   `onerror="fallbackVideoThumb(this)"` attribute.
4. Trigger `hydrateThumb` lazily from the existing `IntersectionObserver` (dedup.py:1403, observed
   at 2581), alongside `hydrateVideoFile`.

Key code seams (verified this session):
- `/media/{id}` Range server: `serve_file_with_range` dedup.py:2842 (206/Content-Range/Accept-Ranges ✓).
- Grid video thumb `<img ... onerror="fallbackVideoThumb(this)">`: dedup.py ~2226;
  `fallbackVideoThumb` def at dedup.py:1928 (currently just swaps in a text span).
- ffmpeg thumbnail path (the fallback, leave intact): `serve_thumbnail` 2992 → `render_thumbnail`
  479 → `build_thumbnail_command` 410.
- Hover cycling + meta fetch: `hydrateVideoFile` dedup.py:1826, `/meta/{id}` → `serve_meta` 3009.

Then Phase 3 (hover-cycle frames via mediabunny — port `get_video_thumbnail_count`/`_timestamp`
dedup.py:324-337 to JS), Phase 4 (metadata via mediabunny, keep `serve_meta` as fallback + for
image EXIF/audio), Phase 5 (tests + docs). See MEDIABUNNY_PLAN.md.

## How to run / verify
```bash
python3 -m unittest test_dedup -q          # should be 95 OK (incl. 4 new mediabunny tests)
python3 dedup.py testfile/ --dry-run       # manual browser check (testfile/ has no video though)
```
Manual matrix for Phase 2: (a) mediabunny on, (b) undecodable codec → ffmpeg, (c) CDN blocked/offline
→ ffmpeg, (d) ffmpeg absent → icon. Need a real video folder; testfile/ only has PNG/PDF.

## Watch out for
- **`test_popup_port` has 8 PRE-EXISTING failures on `main`** (assert popup-window code in
  `_SHARED_JS` that isn't present). NOT caused by this work — confirmed by stashing edits. Don't
  chase them as if Phase 1 broke something. (User aware; left as a separate question.)
- `test_dedup.py` / `test_popup_port.py` are **gitignored** — edits won't show in `git diff` or
  ship in the tarball, but they still run locally.
- Working tree also has a large unrelated `graphify-out/` deletion — keep commits scoped to the
  mediabunny work (`git add dedup.py <plan/handoff>`), don't `git add -A`.
- WebCodecs needs a secure context: `localhost`/`127.0.0.1` OK; LAN-IP access silently falls back
  to ffmpeg. Acceptable.
- Python 3.8+, stdlib-only (+send2trash) on the server side; `sys.dont_write_bytecode` invariant
  intact. mediabunny is browser-only — adds no Python dep.

## Open decision left for the user
Pre-existing `test_popup_port` breakage: investigate/fix separately, or leave?
