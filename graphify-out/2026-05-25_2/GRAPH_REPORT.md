# Graph Report - homebrew-dedup  (2026-05-25)

## Corpus Check
- 10 files · ~17,010 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 250 nodes · 366 edges · 21 communities (12 shown, 9 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 8 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `0e59c8d7`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Trash & Volume Management|Trash & Volume Management]]
- [[_COMMUNITY_Duplicate Review Data Model|Duplicate Review Data Model]]
- [[_COMMUNITY_HTTP Server Infrastructure|HTTP Server Infrastructure]]
- [[_COMMUNITY_Application Core & HTML Builders|Application Core & HTML Builders]]
- [[_COMMUNITY_Media Metadata & Thumbnails|Media Metadata & Thumbnails]]
- [[_COMMUNITY_Trash Safety Operations|Trash Safety Operations]]
- [[_COMMUNITY_Empty Directory Cleanup|Empty Directory Cleanup]]
- [[_COMMUNITY_Duplicate Scan Pipeline|Duplicate Scan Pipeline]]
- [[_COMMUNITY_Shared UI Constants & Patterns|Shared UI Constants & Patterns]]
- [[_COMMUNITY_Content Hashing & Verification|Content Hashing & Verification]]
- [[_COMMUNITY_Scan Configuration & Validation|Scan Configuration & Validation]]
- [[_COMMUNITY_File Metadata & Naming|File Metadata & Naming]]
- [[_COMMUNITY_Claude Code Settings|Claude Code Settings]]
- [[_COMMUNITY_Homebrew Formula|Homebrew Formula]]
- [[_COMMUNITY_Project Documentation|Project Documentation]]
- [[_COMMUNITY_Scan Options|Scan Options]]
- [[_COMMUNITY_HTTP Base Handler|HTTP Base Handler]]
- [[_COMMUNITY_Bytecode Cleanup|Bytecode Cleanup]]
- [[_COMMUNITY_OS Detection|OS Detection]]
- [[_COMMUNITY_Community 19|Community 19]]

## God Nodes (most connected - your core abstractions)
1. `trash_files()` - 13 edges
2. `move_to_trash_safely()` - 12 edges
3. `trash_empty_dirs()` - 12 edges
4. `BrowserSelectionHandler` - 11 edges
5. `find_duplicates()` - 10 edges
6. `_ensure_no_symlink_replacement()` - 10 edges
7. `dedup` - 9 edges
8. `render_thumbnail()` - 8 edges
9. `find_and_process_duplicates()` - 8 edges
10. `Repository Guidelines` - 8 edges

## Surprising Connections (you probably didn't know these)
- `serve_media Kind Guard` --rationale_for--> `BrowserSelectionHandler`  [EXTRACTED]
  CLAUDE.md → dedup.py
- `previewRenderToken` --rationale_for--> `build_browser_html`  [EXTRACTED]
  CLAUDE.md → dedup.py
- `Event Delegation Pattern` --rationale_for--> `build_browser_html`  [EXTRACTED]
  CLAUDE.md → dedup.py
- `Video Preview vs Hover Cycling Invariant` --rationale_for--> `build_browser_html`  [EXTRACTED]
  CLAUDE.md → dedup.py
- `Trash Safety Model` --rationale_for--> `sanitize_browser_trash_selection`  [EXTRACTED]
  README.md → dedup.py

## Hyperedges (group relationships)
- **Duplicate Detection Pipeline** — dedup_scan_by_size, dedup_get_fast_multichunk_hash, dedup_get_full_content_hash, dedup_find_duplicates [EXTRACTED 1.00]
- **Browser Review Session** — dedup_browserselectionstate, dedup_make_browser_handler, dedup_run_browser_session, dedup_select_files_in_browser [EXTRACTED 1.00]
- **Shared UI Constants Injection** — dedup_shared_css, dedup_shared_js, dedup_build_browser_html, dedup_build_empty_dirs_html [EXTRACTED 1.00]

## Communities (21 total, 9 thin omitted)

### Community 0 - "Trash & Volume Management"
Cohesion: 0.07
Nodes (40): build_expected_hashes(), _decide_no_trash_strategy(), _deduplicate_by_ancestry(), _ensure_no_symlink_replacement(), find_nas_recycle_root(), get_macos_volume_root(), is_macos_external_volume_path(), load_send_to_trash() (+32 more)

### Community 1 - "Duplicate Review Data Model"
Cohesion: 0.09
Nodes (29): build_options, _ensure_no_symlink_replacement, FileInfo, find_and_process_duplicates, find_duplicates, find_empty_dirs, find_nas_recycle_root, get_fast_multichunk_hash (+21 more)

### Community 2 - "HTTP Server Infrastructure"
Cohesion: 0.17
Nodes (7): BaseHTTPRequestHandler, _BaseHandler, get_media_info(), get_thumbnail(), get_thumbnail_cache_key(), get_thumbnail_threadsafe(), ThumbnailCache

### Community 3 - "Application Core & HTML Builders"
Cohesion: 0.06
Nodes (49): _bind_server(), BrowserSelectionState, build_browser_payload(), build_options(), build_reveal_command(), build_thumbnail_command(), build_windows_reveal_command(), copy_name_rank() (+41 more)

### Community 4 - "Media Metadata & Thumbnails"
Cohesion: 0.11
Nodes (23): BrowserSelectionHandler, BrowserSelectionState, build_browser_payload, build_thumbnail_command, describe_original_reason, DuplicateGroup, EmptyDirSelectionState, get_exif_info (+15 more)

### Community 5 - "Trash Safety Operations"
Cohesion: 0.09
Nodes (22): code:sh (brew tap pathanin/dedup), code:sh (python3 dedup.py /path/to/folder), code:sh (python3 -m pip install send2trash), code:sh (brew update), code:sh (brew reinstall pathanin/dedup/dedup), code:sh (dedup --help), code:sh (brew install ffmpeg        # video thumbnails and media dura), code:sh (dedup /path/to/folder) (+14 more)

### Community 6 - "Empty Directory Cleanup"
Cohesion: 0.33
Nodes (4): Exception, EmptyDirSelectionState, Raised when send2trash, /usr/bin/trash, and on-volume NAS recycle     folders ar, VolumeHasNoTrashError

### Community 7 - "Duplicate Scan Pipeline"
Cohesion: 0.20
Nodes (9): CLAUDE.md — Browser Dedup, code:bash (python3 -m unittest test_dedup -q), code:python (return "...<style>" + _SHARED_CSS + "/* page-specific */" + ), Color palette (CSS custom properties), File layout (dedup.py), graphify, Non-obvious invariants, Shared UI constants (+1 more)

### Community 8 - "Shared UI Constants & Patterns"
Cohesion: 0.28
Nodes (9): build_browser_html, build_empty_dirs_html, EmptyDirHandler, Event Delegation Pattern, make_empty_dir_handler, previewRenderToken, _SHARED_CSS, _SHARED_JS (+1 more)

### Community 9 - "Content Hashing & Verification"
Cohesion: 0.22
Nodes (8): Build, Test, and Development Commands, Coding Style & Naming Conventions, Commit & Pull Request Guidelines, graphify, Project Structure & Module Organization, Repository Guidelines, Security & Configuration Tips, Testing Guidelines

### Community 10 - "Scan Configuration & Validation"
Cohesion: 0.50
Nodes (3): hooks, PostToolUse, PreToolUse

## Knowledge Gaps
- **56 isolated node(s):** `plugin`, `PostToolUse`, `PreToolUse`, `allow`, `PreToolUse` (+51 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **9 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `BrowserSelectionHandler` connect `Media Metadata & Thumbnails` to `Shared UI Constants & Patterns`, `Duplicate Review Data Model`?**
  _High betweenness centrality (0.027) - this node is a cross-community bridge._
- **Why does `_BaseHandler` connect `HTTP Server Infrastructure` to `Application Core & HTML Builders`?**
  _High betweenness centrality (0.021) - this node is a cross-community bridge._
- **Why does `ThumbnailCache` connect `HTTP Server Infrastructure` to `Application Core & HTML Builders`?**
  _High betweenness centrality (0.014) - this node is a cross-community bridge._
- **What connects `Raised when send2trash, /usr/bin/trash, and on-volume NAS recycle     folders ar`, `Raised when a validated path is replaced with a symlink before removal.`, `Return sorted list of paths that are effectively empty.      A directory is empt` to the rest of the system?**
  _83 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Trash & Volume Management` be split into smaller, more focused modules?**
  _Cohesion score 0.06666666666666667 - nodes in this community are weakly interconnected._
- **Should `Duplicate Review Data Model` be split into smaller, more focused modules?**
  _Cohesion score 0.09113300492610837 - nodes in this community are weakly interconnected._
- **Should `Application Core & HTML Builders` be split into smaller, more focused modules?**
  _Cohesion score 0.0633879781420765 - nodes in this community are weakly interconnected._