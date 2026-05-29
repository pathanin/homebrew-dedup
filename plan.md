# Bundle Mediabunny Locally and Restore Fast Thumbnail First Paint

## Summary

Replace runtime CDN loading of Mediabunny with a vendored local bundle served by the local review server. Preserve fast first paint by using server-side `ffmpeg` thumbnail URLs as the initial video thumbnail source, then use Mediabunny only as an opportunistic browser-side enhancement when it is already loaded.

## Implementation

- Vendor `assets/mediabunny-1.45.4.cjs` with its MPL-2.0 notice and include `assets/MEDIABUNNY_LICENSE.txt`.
- Resolve the default Mediabunny script to `/assets/mediabunny-1.45.4.cjs` only when the local asset exists.
- Preserve `DEDUP_MEDIABUNNY_SRC` behavior: non-empty values override the script URL, and an empty value disables Mediabunny.
- Serve the local Mediabunny asset before session-token authorization because it contains no user data.
- Send the asset as `text/javascript; charset=utf-8` with `X-Content-Type-Options: nosniff`.
- Do not fall back to a public CDN if the local asset is missing.

## Thumbnail Behavior

- Grid video thumbnails start with `/thumb/<id>?i=0` so the browser can fetch a local `ffmpeg` JPEG immediately.
- `hydrateThumb()` does not wait for Mediabunny; if `mbReady()` is false, it leaves the current `ffmpeg` thumbnail intact.
- Hover cycling updates the `/thumb/<id>?i=<index>` source before any Mediabunny work.
- Side-pane video thumbnails keep the same immediate `/thumb` first-paint behavior.
- Thumbnail preloading uses a small capped queue and starts the `ffmpeg` preload before optional Mediabunny cache fill.
- Metadata hydration uses Mediabunny only when ready; otherwise it fetches server metadata immediately.

## Packaging and Docs

- Install `assets/` beside `dedup.py` in the Homebrew formula.
- Extend formula tests to assert that the local Mediabunny asset is installed.
- Update README preview-tool docs to state that Mediabunny is bundled locally and no public CDN is loaded by default.
- Keep `ffmpeg` and `ffprobe` documented as useful optional local tools.

## Test Cases

- Default HTML references `/assets/mediabunny-1.45.4.cjs` and does not reference `unpkg.com` or `jsdelivr`.
- `DEDUP_MEDIABUNNY_SRC=""` omits the Mediabunny script while grid video thumbnails still use `/thumb`.
- Missing local asset omits the script without CDN fallback and leaves `/thumb` thumbnails intact.
- The local asset endpoint returns `200`, JavaScript content type, `nosniff`, and bundle bytes without requiring a session token.
- Grid video HTML includes both `src` for `/thumb/<id>?i=0` and `data-video-thumb="1"`.
- `hydrateThumb()` does not await `waitForMediabunny()` and does not overwrite `/thumb` when Mediabunny is not ready.
- Hover cycling sets `/thumb/<id>?i=<index>` before `hydrateThumb()`.
- Side-pane video preview keeps `authUrlAttr(\`/thumb/${urlId(file.id)}?i=0\`)`.
- Metadata hydration uses server `/meta` immediately when Mediabunny is not ready.
- Validate with `python3 -m unittest -v test_dedup.py`, `brew test pathanin/dedup/dedup`, and `brew audit pathanin/dedup/dedup`.
