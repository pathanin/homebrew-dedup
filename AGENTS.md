# Repository Guidelines

## Project Structure & Module Organization

- `dedup.py` is the active standalone application. It scans a target folder, serves a local browser review UI, previews media, and moves selected duplicates to Trash.
- `test_dedup.py` contains the `unittest` suite for duplicate detection, root safety checks, CLI parsing, browser helpers, and trash safety.
- `testfile/` stores sample media and PDF fixtures for manual duplicate-review checks. Treat these as test assets, not source.
- `not use/dedup.py` is a legacy script kept outside the active source path.

## Build, Test, and Development Commands

- `python3 dedup.py /path/to/folder` runs the duplicate review UI against a folder.
- `python3 dedup.py /path/to/folder --dry-run` exercises selection flow without moving files.
- `python3 dedup.py /path/to/folder --full-verify` performs exact content verification after fast prefiltering.
- `python3 -m unittest -v test_dedup.py` runs the current test file.

## Coding Style & Naming Conventions

Use Python 3.8+ compatible code. Follow the standard-library-first style in `dedup.py`: constants in `UPPER_SNAKE_CASE`, classes in `PascalCase`, and functions/variables in `snake_case`. Use four-space indentation. Prefer dataclasses for structured records. Keep comments short and limited to non-obvious safety or server behavior.

## Testing Guidelines

Tests use `unittest`. Name test classes by behavior area, such as `RootGuardTests`, and test methods with `test_` plus the expected behavior. Prefer temporary directories and small fixtures created inside tests. For browser UI changes, cover handler behavior and safety validation where practical, then manually verify with `--dry-run` on `testfile/`.

## Commit & Pull Request Guidelines

Recent commits use short imperative subjects, for example `Add browser PDF previews` and `Fix directory sort key in browser UI`. Keep commit messages concise and focused on one behavior change.

Pull requests should include a summary, commands run, known test status, and screenshots or recordings for visible browser UI changes. Call out changes affecting trashing behavior, root safety checks, hashing, or media previews.

## Security & Configuration Tips

Do not replace Trash/Recycling behavior with permanent deletion. Preserve root-folder and Photos Library safeguards unless the PR explicitly changes that policy. `ffmpeg` and `ffprobe` improve previews but must remain optional.