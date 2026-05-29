# Mediabunny Removed — ffmpeg-only Thumbnails

## Summary

Mediabunny (WebCodecs client-side decoder) has been removed entirely from `dedup.py`.
Video thumbnails are served exclusively from the server-side ffmpeg path with the persistent
`ThumbnailCache`. The preview modal uses native `<video>` for all formats.

## Why removed

Mediabunny required downloading the full video file to the browser before it could extract a
frame. For large files this was significantly slower than serving a pre-cached ffmpeg JPEG
(~10–50 KB). Testing confirmed it was slower than server-side ffmpeg in all real-world cases.
The added complexity (WebCodecs secure-context requirement, in-page cache, CDN or local bundle
management, preview player fallback machinery) had no net benefit.

## What changed

- `MEDIABUNNY_*` constants, `get_mediabunny_src()`, `mediabunny_script_tag()` — removed from Python.
- `assets/mediabunny-1.45.4.cjs` and `assets/MEDIABUNNY_LICENSE.txt` — deleted.
- `import html`, `import urllib.request` — removed (were only used by mediabunny helpers).
- All mediabunny JS removed: `mbReady`, `mbEnabled`, `waitForMediabunny`, `mbVideoTrack`,
  `mbInputs`, `videoThumbnailCount`, `videoThumbnailTimestamp`, `setVideoThumbTimestamp`,
  `applyVideoMetadata`, `stopPreviewPlayer`, `previewUnavailableHtml`, `nativeVideoCannotPlay`,
  `hydratePreviewVideo`, `isPreviewPlayerCurrent`, `mountMediabunnyPreviewPlayer`.
- `hydrateVideoFile` reverted to pre-mediabunny: direct `/meta/` fetch, sets `thumbnailCount`
  from server payload.
- `hydrateVideoMetadata`, `startGroupVideoThumbCycle`, `stopGroupVideoThumbCycle`,
  `startPaneVideoCycle` simplified: no `data-thumb-timestamp`, no `setVideoThumbTimestamp`.
- `renderPaneMeta` simplified: uses `fetchServerMetadata` directly for all media kinds.
- `renderPreview` and `closePreview` simplified: no `stopPreviewPlayer` calls.
- `.mb-player` CSS removed.
- `/assets/mediabunny-*.cjs` HTTP route and `serve_mediabunny_asset()` handler removed.
- Formula: removed `libexec.install "assets"` and mediabunny asset assertion from tests.
- `ffmpeg` timeout raised from 5s to 20s in `generate_thumbnail()` to handle large videos.

## Thumbnail behaviour (current)

- Grid and side-pane video thumbnails: `src="/thumb/{id}?i=0"` — ffmpeg JPEG, immediate first paint.
- Hover cycling: server `/thumb/{id}?i=N` at 650 ms intervals; preload queue pre-fetches adjacent frames.
- Metadata (`thumbnailCount`) fetched from server `/meta/{id}`; count is computed server-side by
  `get_video_thumbnail_count(duration)`.
- Preview modal: native `<video src="/media/{id}">` only — no fallback player.
- `ffmpegThumbUrl`, `fetchServerMetadata`, `hydrateVideoFile`, `hydrateVideoMetadata` — all kept.

## Non-mediabunny improvements preserved

- Session token auth (`authUrl`, `authUrlAttr`, `DEDUP_SESSION_TOKEN`).
- Hardlink detection and `isHardlink` badge.
- iCloud placeholder skipping.
- Cloud-skipped and unreadable-path reporting in `ScanStats`.
- `.ts` MPEG-2 TS magic-byte detection.
- ffmpeg subprocess timeout increased to 20s with stderr logging.
- `st_dev`/`st_ino` on `FileInfo` for hardlink detection.
