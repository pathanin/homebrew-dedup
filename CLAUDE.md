# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

See `AGENTS.md` for commit style, PR guidelines, and general coding conventions.

## What this repo is

`dedup.py` is a self-contained Python script (~4 500 lines). It scans a folder for duplicate files, serves a local HTTP review UI, lets the user decide what to keep, then moves selections to the system Trash. No build step, no package manager, no framework.

Video thumbnails are generated server-side by ffmpeg and cached in `ThumbnailCache`. The preview modal uses native `<video>`. ffmpeg and ffprobe are optional but strongly recommended.

## Commands

Run the duplicate review UI:

```bash
python3 dedup.py /path/to/folder
python3 dedup.py /path/to/folder --dry-run       # selection flow without moving files
python3 dedup.py /path/to/folder --full-verify   # exact content verification after fast prefilter
```

Run all tests:

```bash
python3 -m unittest test_dedup -q
```

Run a single test class or method:

```bash
python3 -m unittest test_dedup.RootGuardTests -v
python3 -m unittest test_dedup.RootGuardTests.test_method_name -v
```

Manual browser UI verification: `python3 dedup.py testfile/ --dry-run`

## File layout (dedup.py)

| Lines | What's there |
|---|---|
| 1–90 | Imports, extension sets, tuning constants (`MAX_THUMBNAIL_CACHE_SIZE`, `CHUNK_*`, etc.) |
| ~91–500 | Utilities: media detection, ffmpeg thumbnails, text preview, hashing, thumbnail cache |
| ~500–900 | Duplicate scanning: size grouping, fast sampled hashing, full-content verify, `build_browser_payload` |
| **~1000–1050** | **`_SHARED_CSS`** — palette, dark mode, base button/modal rules |
| **~1051–1060** | **`_SHARED_JS`** — `esc()` HTML-escaping utility and popup window IIFE |
| **~1060–2900** | **`build_browser_html()`** — duplicate review UI (ffmpeg-only thumbnail path: `ffmpegThumbUrl`, `hydrateVideoFile`, `hydrateVideoMetadata`, `startGroupVideoThumbCycle`, `startPaneVideoCycle`, `renderPreview`) |
| ~2900–3050 | `sanitize_browser_trash_selection` + trash-selection helpers |
| ~3050–3300 | HTTP handler: `make_browser_handler`, `do_GET`, `do_POST`, `serve_file_with_range`, `serve_media`/`serve_pdf`/`serve_thumbnail`/`serve_meta`/`serve_text` |
| **~3300–3600** | **`build_empty_dirs_html()`** — empty folder cleanup UI |
| ~3600–4400 | Empty-dir handler, Trash logic, CLI argument parsing, `find_and_process_duplicates()` |

## Shared UI constants

`_SHARED_CSS` and `_SHARED_JS` are module-level Python string constants injected into **both** HTML-builder functions via string concatenation:

```python
return "...<style>" + _SHARED_CSS + "/* page-specific */" + ...
       "...<script>" + _SHARED_JS + "let allData..." + ...
```

- **Edit `_SHARED_CSS`** to change the color palette, dark mode tokens, or base `button`/modal rules.
- **Edit `_SHARED_JS`** to change the shared `esc()` escaping utility or popup window behavior.
- Page-specific CSS and JS follow the shared block in each builder. Cascade order is intentional: shared base rules first, page overrides after.

## Video thumbnails

Video thumbnails are served by the ffmpeg subprocess (`render_thumbnail`) cached in `ThumbnailCache`. The server computes how many frames to generate via `get_video_thumbnail_count(duration)` and returns `thumbnailCount` in the `/meta/` response. The browser fetches `/thumb/{id}?i=N` for each frame index; the server seeks to the appropriate timestamp and returns a JPEG.

- **ffmpeg/ffprobe optional:** when absent, video thumbnails fall back to `fallbackVideoThumb()` (a text span). Everything else still works.
- **CI gap — JS logic is not browser-tested in the test suite:** the Python `unittest` tests only assert that JS function signatures are present in the generated HTML. Any non-trivial browser-side change should be verified manually with `--dry-run` against a folder containing duplicate video files.

## Coding style

Constants in `UPPER_SNAKE_CASE`, classes in `PascalCase`, functions and variables in `snake_case`. Four-space indentation. Prefer `dataclass` for structured records. Comments only for non-obvious safety or server behavior.

## Non-obvious invariants

**`previewRenderToken`** — an integer incremented on every `renderPreview()` call and on `closePreview()`. Async modal work (`hydratePreviewText`) checks `token !== previewRenderToken` after awaits and bails if stale. This prevents rapid navigation or modal close from landing fetched text into the wrong card.

**Event delegation** — all JS interactions use a single `click`/`keydown` listener on the `#groups` container that reads `data-action`, `data-file-id`, `data-group-id`, and `data-group-action` attributes. There are no `onclick="..."` attributes with interpolated IDs. Keep it this way (XSS prevention).

**Video preview vs. hover cycling** — the preview modal renders a native `<video src="/media/{id}">` element. The card grid hover animation is separate: `hydrateVideoFile()` fetches `/meta/` to get `thumbnailCount`, then cycles `img[data-video-thumb]` elements whose frames come from `/thumb/{id}?i=N` (ffmpeg). These two paths are independent; do not merge them.

**`serve_media()` kind guard** — only `('image', 'video', 'audio')` kinds are served at `/media/`. PDFs go through `serve_pdf()` at `/pdf/`. Text goes through `serve_text()` at `/text/`. Do not relax this without considering the MIME surface.

**Hash algorithm is chosen at import, not hardcoded** — `select_hash_name()` micro-benchmarks blake2b against sha256 (~12 ms) and sets `FULL_HASH_NAME`/`FAST_HASH_NAME`. hashlib routes sha256 through OpenSSL, so it runs ~2x blake2b where the CPU has SHA extensions (ARMv8 crypto, x86 SHA-NI) and ~0.5x where it does not; platform strings do not reliably report those extensions. sha256 must beat blake2b by `HASH_PROBE_MARGIN` before it is picked, so timing noise cannot flip the choice. The probe must resolve before line ~137, where `DuplicateGroup.hash_name` uses `FULL_HASH_NAME` as a dataclass field default — that is why `make_hasher` sits with the constants instead of with the other hash helpers. Nothing persists a digest to disk, so the choice may differ between machines or runs without breaking anything.

**`FullHashPreloader.in_flight`** — `get()` blocks while a path sits in `in_flight`, waiting for the worker thread to publish the digest. Both the worker loop and `get()` therefore compute hashes inside `try/except Exception … finally`, so a crash can never leave a path in `in_flight` with no thread left to clear it. Removing either guard turns any exception into a silent, permanent hang.

**`restrict_to()` vs `begin_progress()` are split on purpose** — `restrict_to()` is pure bookkeeping, so `do_GET`/`do_POST` can call it on the HTTP thread the instant the user confirms (via `state.on_confirm`, wired in `select_files_in_browser`). That re-points the preloader at the confirmed selection while the server shuts down and `focus_terminal()` runs — about 750 ms of otherwise dead time on macOS — and makes the worker abandon an unrelated in-progress file within ~1 MB. `begin_progress()` is what arms the `[exact verify]` line, and it must stay in `trash_files()`: at confirm time the terminal still belongs to the browser session banner. It also measures the remaining bytes at that later moment, so the total excludes whatever finished in between. `trash_files()` must call `finish_progress()` after the validation loop to close the `\r` line.

**`state.on_confirm` runs before `state.done.set()`** — that ordering is the whole point; waking the main thread first would give the work away to teardown. It is wrapped in `try/except Exception` because a failure there must never strand the user in the UI with a recorded but unacknowledged selection.

**Trash safety** — `sanitize_browser_trash_selection()` and `revalidate_file()` must stay intact. Files are re-fingerprinted before any move. Root safety checks in `validate_scan_root()` must not be weakened.

The worst thing this program can do is trash a file that is not a duplicate. The guard is `revalidate_selected_file_exact()`: it full-hashes the selection and every unselected peer, and trashes only on an exact match, so a sparse-prefilter false positive is skipped rather than moved. Its tests live in `TrashSafetyTests` — `test_fast_mode_selection_requires_exact_kept_duplicate` (same size, same sampled bytes, one differing byte in an unsampled gap) and `test_selecting_every_file_in_a_group_leaves_no_keeper`. They assert on skip counters *and* on the files still existing, and they derive the attack offset from `iter_sparse_offsets()` rather than hardcoding it. Find these by running the class, not by grepping for message text: they deliberately do not assert on most strings.

The remaining guards reject input that never appears in normal use, so no ordinary change touches them and no diff-scoped test reaches them. Each needs a test that supplies the bad input it exists to catch, not one that confirms valid input passes.

| Guard | Bad input it must reject | Tests |
|---|---|---|
| `revalidate_file()` | File changed or replaced between scan and trash | `TrashSafetyTests` |
| `sanitize_browser_trash_selection()` | File IDs outside the scanned set | `TrashSafetyTests` |
| `validate_scan_root()` | Home directory, volume root, photo library | `RootGuardTests` |

`full_hash_reader` (normally `FullHashPreloader.get`) feeds `_cached_full_hash()`, whose result gates deletion in `revalidate_selected_file_exact()`. `None` there must always mean "do not trash", never "no objection". Re-check that whenever you touch error handling in the preloader: swallowing an exception turns a crash into a value this caller reads.

**ffmpeg/ffprobe are optional** — when absent, video thumbnails fall back to `fallbackVideoThumb()` (a text span/icon) and metadata fields are omitted. The UI remains functional without them.

**Python 3.8+** — no walrus operator in non-trivial contexts, no `match`, no 3.10+ syntax. Standard library only (plus `send2trash`).

**`sys.dont_write_bytecode = True`** is set at line 3 and `cleanup_own_bytecode()` runs at exit. Do not remove either.

## Color palette (CSS custom properties)

Defined in `_SHARED_CSS`. Zinc/slate neutral scale, system-preference dark mode only (no toggle).

| Variable | Light | Dark |
|---|---|---|
| `--bg` | `#fafafa` | `#09090b` |
| `--panel` | `#ffffff` | `#18181b` |
| `--text` | `#18181b` | `#fafafa` |
| `--muted` | `#71717a` | `#a1a1aa` |
| `--line` | `#e4e4e7` | `#27272a` |
| `--surface` | `#f4f4f5` | `#27272a` |
| `--keep` | `#16a34a` | `#22c55e` |
| `--danger` | `#dc2626` | `#ef4444` |

Semantic green/red only. No other accent color.
