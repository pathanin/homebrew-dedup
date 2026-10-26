# Plan: deferred review findings

Five findings from the 2026-10-26 review were confirmed but deferred as low value. None of them affects trash safety. This file plans the fixes in order of value per effort. Delete it once all five are done.

Every step ends with `python3 -m unittest test_dedup -q` passing. Each new test must fail against the code it guards before the fix lands.

## 1. Thumbnail caching and dead code (review #12, #14)

**Problem.** `send_bytes(..., cacheable=False)` is never called with `cacheable=True`, so thumbnails are sent with `no-store` and re-downloaded on every re-render. Several functions and parameters are unused.

**Change.**
- `serve_thumbnail` (`dedup.py` ~3666): pass `cacheable=True`. Before doing so, confirm the `/thumb/` URL carries the per-session token. Run IDs can repeat between runs on the fixed default port, so the token is what keeps the browser from showing a previous run's thumbnail for a different file.
- Delete `get_thumbnail()` (~782), which has no callers.
- Drop `revalidate_file`'s unused `expected_mtime_ns` parameter and update its callers. Do not start using it as a check: a `touch` without a content change would then block a legitimate trash.
- Drop `_is_cloud_placeholder`'s unused `path` parameter.
- `describe_original_reason` (~887): the original is chosen by minimum mtime, so only the "older file" branch is reachable for it. Collapse the other three.

**Tests.** A thumbnail response carries `Cache-Control: public, max-age=3600`. A `/media/` response stays `no-store`.

## 2. Restrict the local-trash fallback to macOS (review #13)

**Problem.** `--allow-slow-local-trash` copies to `~/.Trash`, which exists only on macOS. On Linux it fails with "not found". That failure is safe, but the flag looks supported there.

**Change.** Gate the fallback on macOS rather than "not Windows": the checks in `prompt_permanent_delete` (~4653, ~4668) and the trash-dir lookup (~4683). On other platforms, reject the flag at argument parsing with a clear message. Update the `--help` text and the README line. Implementing XDG trash (`~/.local/share/Trash/files` plus `.trashinfo`) is out of scope unless a Linux user asks.

**Tests.** With `CURRENT_OS` patched to Linux, the prompt offers no `[l]` option and parsing the flag errors out. On macOS, behavior is unchanged.

## 3. Stat-check the recheck hash cache (review #5)

**Problem.** `_cached_full_hash` stores `path -> digest` and never re-stats. Exposure is narrow:
- In production the reader is `FullHashPreloader.get`, which already checks size and mtime.
- The cache is cleared after every prompt.

What remains is a content change with the mtime preserved, inside a single `trash_files()` call.

**Change.**
- Store `(size, mtime_ns, digest)` and re-stat on read; a mismatch re-reads.
- `revalidate_selected_file_exact` writes `group.hash` or `None` into the same dict for full-hash peers, so both writers must move to the new shape.
- Keep the invariant that `None` means "do not trash".
- Reword the CLAUDE.md sentence "whose cache is reused when size and mtime are unchanged" so it clearly describes this cache.

**Tests.** Add to `TrashSafetyTests`:
- Hash a file, rewrite it with the same size, restore `st_mtime_ns` with `os.utime(ns=...)` and change the size of a peer. The recheck must skip, and both files must still exist.
- A stat failure on read returns "do not trash".

## 4. Stop in-flight thumbnail renders before trashing (review #3)

**Problem.** On shutdown, `_warm_thumbnails` keeps running and an ffmpeg child may still hold a source file open. On Windows that is a sharing violation, so the move of a just-confirmed file fails. It is reported and skipped, so this is a reliability problem, not a safety one. Do this step last, and only if Windows users hit it.

**Change.**
- Add a session-level `threading.Event`, checked between files in `_warm_thumbnails`.
- Replace `subprocess.run` in `render_thumbnail` with a tracked `Popen`, so `cleanup()` (~3810) can set the event, `terminate()` the live children and wait briefly (about 2 s, then `kill()`).
- Keep the 20 s timeout, and keep `THUMBNAIL_SUBPROCESS_SEMAPHORE` released on every path.

**Tests.**
- With ffmpeg replaced by a script that sleeps, `cleanup()` returns within the bound, and no child process survives it.
- After cleanup, the warm-up thread exits without starting a new render.

Mark the test skip-if-no-`sh` so CI on Windows still runs.
