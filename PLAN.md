# Plan: preloader ctime check and thumbnail shutdown race

Two gaps were found while reviewing PR #9. Delete this file once both are done.

Rules for this work:
- Python 3.8+, standard library only.
- Match the surrounding style. Comments only for non-obvious safety behavior.
- Never weaken, skip or delete existing tests.
- Every new test must fail against the code before the fix. Check this by temporarily reverting your `dedup.py` change, running the test, then restoring the change.
- Finish with `python3 -m unittest test_dedup -q` passing.

## 1. FullHashPreloader must also check ctime

**Problem.** `FullHashPreloader` in `dedup.py` (class at ~line 4264) caches `(size, mtime_ns, digest)` per path:
- `_compute_full_hash_entry` (~4290) builds the entry.
- `_entry_digest_if_current` (~4326) validates it by size and mtime only.

`_cached_full_hash` (~4545) now uses a `(size, mtime_ns, ctime_ns)` signature. But for sampled groups its reader is `FullHashPreloader.get`, so a file rewritten in place with its size and mtime restored still gets the preloader's stale digest. That defeats the ctime check for exactly the files it is meant to protect.

**Change.**
1. `_compute_full_hash_entry`:
   - Return `(size, mtime_ns, ctime_ns, digest)`, taken from the `before` stat.
   - Add `before.st_ctime_ns != after.st_ctime_ns` to the existing before/after comparison, so a write during the read discards the result.
2. `_entry_digest_if_current`: unpack four fields, and return `None` when the current `st_ctime_ns` differs as well.
3. `get()` ends with `return entry[2] if entry else None` (~4507). It must become the digest field, `entry[3]`. Search the whole file for every other place that reads a preloader cache entry, and update each one.
4. Do not touch `_cached_full_hash` or `revalidate_selected_file_exact`; they already use ctime.

**Tests.** Add to `TrashSafetyTests` in `test_dedup.py`, skipped on Windows (`os.name == "nt"`, where `st_ctime` is the creation time):
- Build a `FullHashPreloader([group])` for a two-file group, as the existing tests around line 1491 do, and prime it by calling `preloader.get(path)`.
- Rewrite that file in place with different bytes of the same length, then restore its mtime with `os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))`.
- Assert `preloader.get(path)` now returns the new content's digest, not the old one. The exact-match helpers in the file show how to compute the expected digest.
- A second test runs the end-to-end path: `revalidate_selected_file_exact(..., full_hash_reader=preloader.get)` on a sampled group. After a kept peer is rewritten that way, it must return `(False, ...)`, and both files must still exist.

## 2. Close the thumbnail shutdown race

**Problem.** In `render_thumbnail` (~line 792), `stop_event` is checked before `subprocess.Popen`, and the child is registered afterwards with `_track_thumbnail_child(process)`. Shutdown in `cleanup()` does `state.thumbnail_stop.set()`, then `terminate_thumbnail_subprocesses()`, which snapshots `_THUMBNAIL_CHILDREN` under `_THUMBNAIL_CHILDREN_LOCK`.

The race: a render passes the stop check, shutdown sets the event and snapshots, and only then does the render register its child. That ffmpeg is never terminated and can hold its source file open for up to the 20 s timeout. On Windows that blocks the trash move.

**Change.**
- Give `_track_thumbnail_child` a `stop_event` parameter.
- Inside `_THUMBNAIL_CHILDREN_LOCK`, add the process; if `stop_event` is set, also call `process.terminate()`, catching `OSError`.

This is correct because shutdown always sets the event *before* taking the lock to snapshot:
- If the render registers first, its child is in the snapshot.
- If it registers after, it sees the event already set and terminates its own child.

Pass `stop_event` from `render_thumbnail`. Leave `_untrack_thumbnail_child`, the semaphore and the 20 s timeout unchanged.

**Tests.** Add to `ThumbnailShutdownTests`, which already has a fake ffmpeg (`exec sleep 30`):
- Simulate the race deterministically. Patch `subprocess.Popen` in `dedup` with a wrapper that calls the real `Popen` and then sets the stop event, before `render_thumbnail` registers the child. Call `render_thumbnail` directly with that event.
- Assert it returns `None` within a few seconds (well under 30), and that the child process has exited (`poll()` is not `None`).
