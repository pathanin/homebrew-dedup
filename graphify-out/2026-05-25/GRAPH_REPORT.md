# Graph Report - .  (2026-05-24)

## Corpus Check
- Corpus is ~16,254 words - fits in a single context window. You may not need a graph.

## Summary
- 187 nodes · 310 edges · 19 communities (12 shown, 7 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 8 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

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

## God Nodes (most connected - your core abstractions)
1. `trash_files()` - 12 edges
2. `trash_empty_dirs()` - 12 edges
3. `move_to_trash_safely()` - 11 edges
4. `BrowserSelectionHandler` - 11 edges
5. `find_duplicates()` - 10 edges
6. `_ensure_no_symlink_replacement()` - 9 edges
7. `render_thumbnail()` - 8 edges
8. `find_and_process_duplicates()` - 8 edges
9. `ThumbnailCache` - 7 edges
10. `_BaseHandler` - 7 edges

## Surprising Connections (you probably didn't know these)
- `serve_media Kind Guard` --rationale_for--> `BrowserSelectionHandler`  [EXTRACTED]
  CLAUDE.md → dedup.py
- `previewRenderToken` --rationale_for--> `build_browser_html`  [EXTRACTED]
  CLAUDE.md → dedup.py
- `Event Delegation Pattern` --rationale_for--> `build_browser_html`  [EXTRACTED]
  CLAUDE.md → dedup.py
- `Video Preview vs Hover Cycling Invariant` --rationale_for--> `build_browser_html`  [EXTRACTED]
  CLAUDE.md → dedup.py
- `Trash Safety Model` --rationale_for--> `revalidate_file`  [EXTRACTED]
  README.md → dedup.py

## Hyperedges (group relationships)
- **Duplicate Detection Pipeline** — dedup_scan_by_size, dedup_get_fast_multichunk_hash, dedup_get_full_content_hash, dedup_find_duplicates [EXTRACTED 1.00]
- **Browser Review Session** — dedup_browserselectionstate, dedup_make_browser_handler, dedup_run_browser_session, dedup_select_files_in_browser [EXTRACTED 1.00]
- **Shared UI Constants Injection** — dedup_shared_css, dedup_shared_js, dedup_build_browser_html, dedup_build_empty_dirs_html [EXTRACTED 1.00]

## Communities (19 total, 7 thin omitted)

### Community 0 - "Trash & Volume Management"
Cohesion: 0.10
Nodes (30): build_expected_hashes(), _decide_no_trash_strategy(), _deduplicate_by_ancestry(), _ensure_no_symlink_replacement(), find_nas_recycle_root(), get_macos_volume_root(), is_macos_external_volume_path(), load_send_to_trash() (+22 more)

### Community 1 - "Duplicate Review Data Model"
Cohesion: 0.10
Nodes (24): BrowserSelectionState, build_browser_payload, build_options, describe_original_reason, DuplicateGroup, EmptyDirSelectionState, FileInfo, find_and_process_duplicates (+16 more)

### Community 2 - "HTTP Server Infrastructure"
Cohesion: 0.12
Nodes (12): BaseHTTPRequestHandler, _BaseHandler, _bind_server(), BrowserSelectionState, focus_terminal(), get_media_info(), get_thumbnail(), get_thumbnail_cache_key() (+4 more)

### Community 3 - "Application Core & HTML Builders"
Cohesion: 0.15
Nodes (11): build_reveal_command(), build_thumbnail_command(), build_windows_reveal_command(), get_media_kind(), get_video_duration(), get_video_thumbnail_count(), get_video_thumbnail_timestamp(), is_probably_text_bytes() (+3 more)

### Community 4 - "Media Metadata & Thumbnails"
Cohesion: 0.17
Nodes (15): BrowserSelectionHandler, build_thumbnail_command, get_exif_info, get_media_info, get_media_kind, get_thumbnail, get_thumbnail_threadsafe, get_video_duration (+7 more)

### Community 5 - "Trash Safety Operations"
Cohesion: 0.21
Nodes (13): _ensure_no_symlink_replacement, find_nas_recycle_root, load_send_to_trash, move_to_local_trash, move_to_nas_recycle, move_to_trash_safely, SymlinkReplacementError, trash_empty_dirs (+5 more)

### Community 6 - "Empty Directory Cleanup"
Cohesion: 0.17
Nodes (10): Exception, EmptyDirSelectionState, find_empty_dirs(), is_effectively_empty_dir(), Raised when send2trash, /usr/bin/trash, and on-volume NAS recycle     folders ar, Return sorted list of paths that are effectively empty.      A directory is empt, _run_empty_dir_phase(), select_empty_dirs_in_browser() (+2 more)

### Community 7 - "Duplicate Scan Pipeline"
Cohesion: 0.27
Nodes (9): DuplicateGroup, FileInfo, find_and_process_duplicates(), find_duplicates(), finish_progress(), parse_args(), print_progress(), scan_by_size() (+1 more)

### Community 8 - "Shared UI Constants & Patterns"
Cohesion: 0.28
Nodes (9): build_browser_html, build_empty_dirs_html, EmptyDirHandler, Event Delegation Pattern, make_empty_dir_handler, previewRenderToken, _SHARED_CSS, _SHARED_JS (+1 more)

### Community 9 - "Content Hashing & Verification"
Cohesion: 0.32
Nodes (8): get_candidate_hash(), get_fast_multichunk_hash(), get_full_content_hash(), get_sparse_sample_count(), iter_sparse_offsets(), make_fast_hasher(), make_hasher(), revalidate_file()

### Community 10 - "Scan Configuration & Validation"
Cohesion: 0.33
Nodes (5): build_options(), is_drive_root(), is_photo_library_path(), ScanOptions, validate_scan_root()

### Community 11 - "File Metadata & Naming"
Cohesion: 0.50
Nodes (5): build_browser_payload(), copy_name_rank(), describe_original_reason(), format_size(), guess_original_filename()

## Knowledge Gaps
- **24 isolated node(s):** `allow`, `FileInfo`, `ScanOptions`, `ScanStats`, `VolumeHasNoTrashError` (+19 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `BrowserSelectionHandler` connect `Media Metadata & Thumbnails` to `Shared UI Constants & Patterns`, `Duplicate Review Data Model`?**
  _High betweenness centrality (0.049) - this node is a cross-community bridge._
- **Why does `_BaseHandler` connect `HTTP Server Infrastructure` to `Application Core & HTML Builders`?**
  _High betweenness centrality (0.034) - this node is a cross-community bridge._
- **Why does `build_browser_html` connect `Shared UI Constants & Patterns` to `Media Metadata & Thumbnails`?**
  _High betweenness centrality (0.025) - this node is a cross-community bridge._
- **What connects `Raised when send2trash, /usr/bin/trash, and on-volume NAS recycle     folders ar`, `Raised when a validated path is replaced with a symlink before removal.`, `Return sorted list of paths that are effectively empty.      A directory is empt` to the rest of the system?**
  _41 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Trash & Volume Management` be split into smaller, more focused modules?**
  _Cohesion score 0.09655172413793103 - nodes in this community are weakly interconnected._
- **Should `Duplicate Review Data Model` be split into smaller, more focused modules?**
  _Cohesion score 0.10144927536231885 - nodes in this community are weakly interconnected._
- **Should `HTTP Server Infrastructure` be split into smaller, more focused modules?**
  _Cohesion score 0.11857707509881422 - nodes in this community are weakly interconnected._