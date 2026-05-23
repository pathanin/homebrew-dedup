# dedup

`dedup` is a local duplicate-file review tool. It scans a folder, groups likely duplicate files, opens a browser-based review UI on your machine, and moves the selected duplicates to Trash.

The tool is designed for cautious cleanup:

- Uses fast sampled hashing by default for quick scans of large folders
- Offers `--full-verify` for exact content verification
- Opens a local browser UI for visual review before trashing files
- Supports previews for images, video, PDFs, and text files
- Keeps `ffmpeg` and `ffprobe` optional
- Preserves Trash/Recycling behavior instead of permanently deleting by default
- Includes safeguards for risky roots such as the home folder and Photos libraries

## Install

Install from this Homebrew tap:

```sh
brew tap pathanin/dedup
brew install pathanin/dedup/dedup
```

Verify the command:

```sh
dedup --help
```

## Optional Media Preview Support

`dedup` works without `ffmpeg`, but image and video thumbnails are better when it is installed:

```sh
brew install ffmpeg
```

## Usage

Scan a folder and open the local review UI:

```sh
dedup /path/to/folder
```

Dry-run mode lets you exercise the selection flow without moving files:

```sh
dedup /path/to/folder --dry-run
```

Use exact content verification after the fast prefilter:

```sh
dedup /path/to/folder --full-verify
```

Clean empty folders after the duplicate-file review:

```sh
dedup /path/to/folder --clean-empty-dirs
```

## Common Options

```text
--fast-only              Use sampled chunks only. This is the default.
--full-verify           Fully hash matching candidates for exact verification.
--dry-run               Review selections without moving files.
--include-hidden        Include hidden files and directories.
--ignore-dir NAME       Ignore an additional directory name.
--ignore-file NAME      Ignore an additional file name or suffix.
--clean-empty-dirs      Review and trash empty directories after file cleanup.
```

Run `dedup --help` for the full option list.

## Safety Notes

By default, selected files are moved to Trash or a recoverable same-volume recycle area where supported. Permanent deletion is not the default.

For NAS or external volumes with no recycle bin, `dedup` avoids slow network copies to local Trash unless explicitly allowed:

```sh
dedup /path/to/folder --allow-slow-local-trash
```

Use `--permanent-on-no-trash` only when you understand that selected files may be irreversibly deleted.

## Update

```sh
brew update
brew upgrade pathanin/dedup/dedup
```

If you already installed it and want to reinstall the current formula:

```sh
brew reinstall pathanin/dedup/dedup
```
