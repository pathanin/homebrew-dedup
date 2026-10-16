# dedup

[![CI](https://github.com/pathanin/homebrew-dedup/actions/workflows/ci.yml/badge.svg)](https://github.com/pathanin/homebrew-dedup/actions/workflows/ci.yml)

Find duplicate files in a folder, review them in your browser, and move the ones you pick to Trash.

```sh
dedup ~/Downloads
```

- **Fast:** small files are fully hashed; large files are sampled, then fully verified before anything moves.
- **Visual review:** list and grid views, previews for images, video, audio, PDFs and text.
- **Recoverable:** files go to Trash (Recycle Bin on Windows), never permanently deleted by default.
- **Careful:** a file is only trashed if an identical copy you're keeping still exists.
- **Tidy:** offers to remove empty folders afterward.

## Install

### macOS / Linux

```sh
brew tap pathanin/dedup
brew trust --tap pathanin/dedup
brew install pathanin/dedup/dedup
```

The `brew trust` step is required: Homebrew only loads formulae from taps you have trusted.

Update with `brew update && brew upgrade pathanin/dedup/dedup`.

### Windows

Needs **Python 3.8+**. In PowerShell:

```powershell
irm https://raw.githubusercontent.com/pathanin/homebrew-dedup/main/install.ps1 | iex
```

Reopen your terminal, then run `dedup --help`. Rerun the same command to update.

<details>
<summary>Installer details and options</summary>

The installer needs no admin rights. It:

1. Finds Python (`py -3`, then `python`).
2. Installs `send2trash` with `pip install --user`.
3. Downloads the latest release into `%LOCALAPPDATA%\Programs\dedup`.
4. Adds that folder to your user `PATH`.

To pass options, run it as a scriptblock:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/pathanin/homebrew-dedup/main/install.ps1))) -Force
```

```powershell
-Version v0.4.1                 # specific version
-InstallDir "D:\tools\dedup"    # custom location
-Force                          # overwrite without prompt
-NoPathUpdate                   # skip PATH modification
```

To read the script before running it:

```powershell
irm https://raw.githubusercontent.com/pathanin/homebrew-dedup/main/install.ps1 -OutFile install.ps1
powershell -ExecutionPolicy Bypass -File install.ps1
```

</details>

### Optional: better previews

Not required, but they add video thumbnails and media/photo details.

```sh
brew install ffmpeg exiftool      # macOS / Linux
winget install Gyan.FFmpeg        # Windows (exiftool: manual install from exiftool.org)
```

## Usage

```sh
dedup /path/to/folder                   # scan and open the review UI
dedup /path/to/folder --dry-run         # try it out without moving anything
dedup /path/to/folder --full-verify     # fully hash every match during the scan
dedup /path/to/folder --skip-empty-dirs # skip the empty-folder step
```

The review UI opens at `http://127.0.0.1:7979`. Mark files Keep or Trash, then click Finish. Use `↑`/`↓` to move between files and `Escape` to close previews.

<details>
<summary>More options</summary>

```text
--full-verify               Fully hash matching candidates for exact verification.
-d, --dry-run               Review selections without moving files.
--yes                       Skip the final browser confirmation when trashing.
--include-hidden            Include hidden files and directories.
--ignore-dir NAME           Ignore an additional directory name.
--ignore-file NAME          Ignore an additional file name or suffix.
--allow-home-root           Allow scanning your home directory root.
--allow-photo-library       Allow scanning inside macOS .photoslibrary packages.
-E, --skip-empty-dirs       Skip the empty-folder review that runs after file cleanup.
--allow-slow-local-trash    Allow last-resort copies to a local trash fallback (macOS ~/.Trash).
--permanent-on-no-trash     Permanently delete when no recoverable trash route works.
--port PORT                 Port for the local browser UI.
--fast-only                 Sampled hashing only. This is the default; the flag has no effect.
```

Run `dedup --help` for the full list.

</details>

## Safety

- **Nothing is permanently deleted by default.** Files go to Trash, `/usr/bin/trash` on macOS external volumes, or a same-volume NAS recycle folder (`#recycle`, `@Recycle`, `.recycle`).
- **Every file is rechecked before it moves.** Its size and full content must still match, and at least one copy you're keeping must still exist with identical content. Otherwise it's skipped.
- **Risky folders are refused.** Filesystem roots are blocked; your home folder needs `--allow-home-root`; macOS Photos libraries need `--allow-photo-library`.
- **No Trash available?** Non-interactive runs skip the file; interactive runs ask. `--permanent-on-no-trash` allows real deletion, with the same checks repeated right before each removal.
- **Empty folders** are rechecked before removal. Only system files like `.DS_Store` count as empty, so a folder holding `.env` is kept.

## Run with Python directly

```sh
python3 -m pip install send2trash
python3 dedup.py /path/to/folder
```
