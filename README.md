# dedup

`dedup` is a local duplicate-file review tool. It scans a folder, groups likely duplicates, opens a browser UI on your machine, and moves only the files you select to Trash or a recoverable recycle location.

It is built for large, messy folders where you want fast duplicate detection, visual review, and conservative delete-time safety.

## Highlights

- Fast default scan: full hashing for small files and sampled hashing for large files
- Exact destructive safety: sampled large-file matches are full-hashed before anything is moved
- Background full hashing starts while you review, then narrows to the files you selected and their kept peers
- Optional exact scan-time verification with `--full-verify`
- Local browser review UI with filters, sortable list and grid views, and a resizable side preview pane
- Decision summary bar shows groups, files, marked count, and reclaimable bytes at a glance
- Per-group marked count and reclaimable size alongside the group actions
- Folder impact strip highlights which folders hold the most marked files; click to filter, click again to clear
- Reason column in list view co-locates the original/copy rationale with Keep and Trash controls
- Previews for images, videos, audio, PDFs, and text files
- Media metadata when optional tools are installed: duration, dimensions, codec, bitrate, and EXIF
- Recoverable cleanup by default: Trash (Recycle Bin on Windows), `/usr/bin/trash` on macOS external volumes, or same-volume NAS recycle folders when available
- Safety checks for filesystem roots, home root scans, macOS Photos libraries, stale selections, and symlink replacement
- Optional empty-folder cleanup after duplicate review

## Install

### macOS / Linux

Install from this Homebrew tap:

```sh
brew tap pathanin/dedup
brew install pathanin/dedup/dedup
```

### Windows

Requires **Python 3.8+** (install from [python.org](https://python.org) or the Microsoft Store).

Install with the PowerShell installer from the [latest release](https://github.com/pathanin/homebrew-dedup/releases/latest):

```powershell
# Download the installer
Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/pathanin/homebrew-dedup/releases/latest/download/install.ps1" -OutFile install.ps1

# Run it
powershell -ExecutionPolicy Bypass -File install.ps1
```

By default, the installer:

1. Resolves Python (tries `py -3` first, then `python`).
2. Bootstraps pip if needed, then installs `send2trash`.
3. Downloads the latest release from GitHub and extracts `dedup.py` + `dedup.cmd` to `%LOCALAPPDATA%\Programs\dedup`.
4. Adds that directory to your user `PATH` (unless `-NoPathUpdate`).

**No administrator privileges are required.** Every step runs in per-user scope:
install directory under `%LOCALAPPDATA%`, `pip install --user`, and user-level `PATH` via `HKCU\Environment`.

After installation, reopen your terminal and verify:

```powershell
dedup --help
```

**Installer options:**

```powershell
.\install.ps1 -Version v0.4.1                    # specific version
.\install.ps1 -InstallDir "D:\tools\dedup"       # custom location
.\install.ps1 -Force                              # overwrite without prompt
.\install.ps1 -NoPathUpdate                       # skip PATH modification
```

### Verify (all platforms)

```sh
dedup --help
```

## Optional Preview Tools

`dedup` works without these tools, but they improve previews and metadata:

**macOS / Linux:**

```sh
brew install ffmpeg        # video thumbnails and media duration, codec, and bitrate
brew install exiftool      # image EXIF such as camera, lens, aperture, and GPS
```

**Windows:**

```powershell
winget install Gyan.FFmpeg        # video thumbnails and media duration, codec, and bitrate
```
Or download FFmpeg manually from [ffmpeg.org](https://ffmpeg.org) and add `bin\` to your `PATH`.

`exiftool` on Windows requires a manual install from [exiftool.org](https://exiftool.org).

`ffmpeg` also provides `ffprobe`, which `dedup` uses for media metadata.


## Usage

Scan a folder and open the local review UI:

```sh
dedup /path/to/folder
```

Exercise the selection flow without moving files:

```sh
dedup /path/to/folder --dry-run
```

Fully hash candidate matches after the fast prefilter:

```sh
dedup /path/to/folder --full-verify
```

Review empty folders after duplicate-file cleanup:

```sh
dedup /path/to/folder --clean-empty-dirs
```

Use a fixed local UI port:

```sh
dedup /path/to/folder --port 7979
```

## Browser UI

The review UI opens automatically at a local `http://127.0.0.1:7979` URL with a per-session token.

**Header** — search, type filter, sort order, trashed-only toggle, list/grid toggle, preview pane toggle, and undo.

**Summary bar** — live counts of visible groups, files, marked files, and reclaimable bytes. Finish and Cancel buttons live here so the action cost is always visible.

**Folder strip** — appears below the summary bar when files are marked. Shows up to eight folders ordered by reclaimable bytes. Click a folder to filter the list to that folder; click the same button again to clear the filter.

**Group headers** — each duplicate group shows its file count, hash identifier, and how many files in that group are currently marked and how many bytes would be reclaimed.

**List view** — the default high-density surface. Columns: type, name, directory, size, reason (original or copy rationale), and Keep/Trash actions. Click any column header to sort; click again to reverse. Click a row to load it in the side preview pane.

**Grid view** — toggle with the List button. Shows thumbnail previews for images and videos with hovering frame cycling.

**Side preview pane** — resizable panel on the right. Shows a preview of the selected file and its metadata. Drag the divider to resize; the width is remembered across sessions.

**Preview modal** — click a thumbnail (grid view) or the `(o)` button (list view) for a full-size preview with Keep/Trash controls and prev/next navigation.

**Keyboard shortcuts** — `↑`/`↓` to move between files, `←`/`→` inside the preview modal, `Escape` to close modals.

## Common Options

```text
--fast-only                 Use sampled chunks only. This is the default.
--full-verify               Fully hash matching candidates for exact verification.
-d, --dry-run               Review selections without moving files.
--yes                       Skip the final browser confirmation when trashing.
--include-hidden            Include hidden files and directories.
--ignore-dir NAME           Ignore an additional directory name.
--ignore-file NAME          Ignore an additional file name or suffix.
--allow-home-root           Allow scanning your home directory root.
--allow-photo-library       Allow scanning inside macOS .photoslibrary packages.
-e, --clean-empty-dirs      Review and trash empty directories after file cleanup.
--allow-slow-local-trash    Allow last-resort copies to a local trash fallback (macOS ~/.Trash)
--permanent-on-no-trash     Permanently delete when no recoverable trash route works.
--port PORT                 Port for the local browser UI.
```

Run `dedup --help` for the full option list.

## Safety Model

`dedup` does not permanently delete by default. Selected files are moved to Trash or to an existing same-volume recycle folder where supported.

Before destructive actions, `dedup` revalidates selected files against the scanned duplicate groups, checks that size and modified time still match, and fully hashes sampled large-file matches against a kept peer. Background full hashing starts during browser review to reduce the final wait, and pending work outside the submitted selection is abandoned after you click Move. Empty-folder cleanup also rechecks that folders are still empty before removal.

Risky scan roots are guarded. Filesystem roots are refused, home-directory root scans require `--allow-home-root`, and macOS Photos libraries require `--allow-photo-library`.

When trashing fails, `dedup` tries recoverable paths first:

1. `send2trash` (system Trash / Recycle Bin)
2. `/usr/bin/trash` on macOS external volumes
3. Existing same-volume NAS recycle folders such as `#recycle`, `@Recycle`, or `.recycle`

If no recoverable trash path exists, `dedup` skips the file in non-interactive mode. Interactive runs ask what to do. Use `--allow-slow-local-trash` to allow copying into a local trash fallback (`~/.Trash` on macOS); use `--permanent-on-no-trash` only when irreversible deletion is acceptable.

## Direct Python Use

You can also run the script directly:

```sh
python3 dedup.py /path/to/folder
```

Install `send2trash` for actual Trash moves when running directly from Python:

```sh
python3 -m pip install send2trash
```

Optional preview tools are still discovered from your `PATH`.

## Update

**macOS / Linux (Homebrew):**

```sh
brew update
brew upgrade pathanin/dedup/dedup
```

If you already installed it and want to reinstall the current formula:

```sh
brew reinstall pathanin/dedup/dedup
```

**Windows:**

Download and rerun the installer — it upgrades in place:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -Force
```
