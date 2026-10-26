# Plan: flat / hairline restyle of both web UIs

Restyle the duplicate review page (`build_browser_html`) and the empty-folder page (`build_empty_dirs_html`) to a flat, neutral-gray, low-elevation style. Layers are separated by 1px translucent hairlines and 4–10% tints, not shadows. Delete this file once the work is merged.

This is CSS-only. No HTML, JS, or Python logic changes. One exception: the pane text preview's inline `style` in a JS template moves from `background:var(--surface)` to `background:transparent` (see "Review fixes" below).

## Rules for this work

1. Neutral grays only, no warm or blue tint. Express grays as black (light) or white (dark) at an alpha, so they work on any surface.
2. Borders are 1px at 8–12% alpha. Shadows only on floating things (modals, the dir-picker popover), always paired with a 1px ring.
3. Hover = `var(--tint-hover)` (+5%). Selected / pressed toggle = `var(--tint-selected)` (+10%). No lift and no scale on hover. The existing press scale (`button:active … scale(0.97)`) stays; it is test-pinned.
4. Type: 14px for header/modal/toolbar controls, 14px body. Dense in-card controls may stay at 12–13px. Nothing below 11px.
5. Radius ladder: 6px small controls, 8px inputs and file tiles, 12px cards and popovers, 14px modals.
6. Motion: 60ms (`--dur-fast`) for press, 200ms (`--dur`) for color/shadow, `--ease`.
7. Semantic green/red stay the only accent colors.

## Constraints on the edit

- Change values only. Keep every selector, and keep rule order. Several rules are intentionally redefined later in the cascade (for example `.modal.wide` under "Compact modal"). Do not merge or deduplicate them.
- Keep the legacy variable names (`--bg`, `--panel`, `--surface`, `--line`, `--text`, `--muted`, `--hover-tint`, `--keep*`, `--danger*`). Inline `style="…"` attributes in JS templates depend on them.
- No backslashes in new CSS, and no triple quotes. The CSS lives inside non-raw Python strings. Lines that already end in `\` must keep it.
- Do not introduce any of these colors: `#fef9c3 #854d0e #dbeafe #1d4ed8 #422006 #1e3a5f`. Do not use `"Inter"` or `opacity: .8;`.

### Test-pinned strings (must stay byte-for-byte)

```
#previewOverlay, #previewOverlay .modal { transition: none; }
.choice button.keep:active:not(:disabled) { background: var(--keep-bg); color: var(--keep); }
.choice button.trash:active:not(:disabled) { background: var(--danger-bg); color: var(--danger); }
.choice button:active, .folder-row:active { transform: none; }
.folder-rules-modal { gap: 12px; width: min(920px, 100%);
.pane-preview-area pre
@media (prefers-reduced-motion: reduce) { .modal-backdrop .modal, .dir-picker { transform: none !important; } button:active:not(:disabled) { transform: none; } }
@starting-style { .dir-picker { opacity: 0; transform: scale(0.96); } }
button:active:not(:disabled) { transform: scale(0.97); }
button:hover:not(:disabled) { box-shadow: inset 0 0 0 100vmax var(--hover-tint); }
display 150ms allow-discrete
overflow-wrap: anywhere
pointer-events: none
transform-origin: top right
```

One pinned string changes on purpose: the body font moves from `font: 13px/1.5 system-ui,` to `font: 14px/20px system-ui,` on both pages. `test_pages_use_system_font_and_palette_only_badges` is updated to match; it still checks system-ui and no Inter.

## 1. `_SHARED_CSS` — tokens and base rules

Replace the `:root` block and its dark-mode block with exactly this. Legacy names become aliases.

```css
:root {
  color-scheme: light dark;
  --bg-page: #ffffff;
  --bg-sidebar: #fafafa;
  --bg-raised: #ffffff;
  --tint-hover: rgba(0,0,0,.05);
  --tint-selected: rgba(0,0,0,.10);
  --tint-track: rgba(11,11,11,.05);
  --text-primary: rgba(0,0,0,.90);
  --text-secondary: rgba(0,0,0,.60);
  --text-muted: rgba(0,0,0,.47);
  --border: rgba(0,0,0,.08);
  --border-strong: rgba(0,0,0,.10);
  --ring-composer: rgba(0,0,0,.12);
  --radius-sm: 4px; --radius-md: 6px; --radius: 8px; --radius-xl: 12px; --radius-composer: 14px;
  --shadow-ring: 0 0 0 1px var(--border);
  --shadow-thumb: inset 0 0 0 1px var(--border), 0 1px 2px rgba(0,0,0,.05);
  --shadow-sm: 0 0 0 1px var(--border), 0 1px 2px rgba(11,11,11,.06), 0 2px 8px rgba(11,11,11,.08);
  --shadow-panel: 0 0 0 1px var(--border), 0 8px 24px rgba(0,0,0,.12), 0 2px 6px rgba(0,0,0,.08);
  --weight-body: 400; --weight-control: 435; --weight-header: 520; --weight-semibold: 580;
  --ease: cubic-bezier(0.4, 0, 0.2, 1);
  --dur-fast: 60ms; --dur: 200ms;
  --bg: var(--bg-sidebar);
  --panel: var(--bg-page);
  --surface: rgba(0,0,0,.04);
  --line: var(--border);
  --text: var(--text-primary);
  --muted: var(--text-secondary);
  --hover-tint: var(--tint-hover);
  --keep: #16a34a;
  --keep-bg: rgba(22,163,74,.08);
  --keep-border: rgba(22,163,74,.35);
  --danger: #dc2626;
  --danger-bg: rgba(220,38,38,.07);
  --danger-border: rgba(220,38,38,.35);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg-page: #161616;
    --bg-sidebar: #111111;
    --bg-raised: #1f1f1f;
    --tint-hover: rgba(255,255,255,.06);
    --tint-selected: rgba(255,255,255,.12);
    --tint-track: rgba(255,255,255,.06);
    --text-primary: rgba(255,255,255,.92);
    --text-secondary: rgba(255,255,255,.64);
    --text-muted: rgba(255,255,255,.48);
    --border: rgba(255,255,255,.10);
    --border-strong: rgba(255,255,255,.12);
    --ring-composer: rgba(255,255,255,.14);
    --shadow-thumb: inset 0 0 0 1px var(--border), 0 1px 2px rgba(0,0,0,.3);
    --shadow-sm: 0 0 0 1px var(--border), 0 1px 2px rgba(0,0,0,.3), 0 2px 8px rgba(0,0,0,.4);
    --shadow-panel: 0 0 0 1px var(--border), 0 8px 24px rgba(0,0,0,.5), 0 2px 6px rgba(0,0,0,.35);
    --surface: rgba(255,255,255,.05);
    --keep: #22c55e;
    --keep-bg: rgba(34,197,94,.12);
    --keep-border: rgba(34,197,94,.40);
    --danger: #ef4444;
    --danger-bg: rgba(239,68,68,.12);
    --danger-border: rgba(239,68,68,.45);
  }
}
```

Base rules in the same block:

- `button`: `border: 1px solid var(--border-strong); border-radius: var(--radius-md); background: var(--bg-raised); color: var(--text); padding: 5px 10px; font: inherit; font-weight: var(--weight-control);` Keep `cursor`, `white-space`.
- `button.primary`: unchanged (`var(--text)` background, `var(--panel)` text).
- `button:disabled` and the pinned hover line: unchanged.
- The later `button { transition: … }` line becomes `button { transition: transform var(--dur-fast) var(--ease), box-shadow var(--dur) var(--ease), background-color var(--dur) var(--ease), border-color var(--dur) var(--ease); }`.
- `.modal-backdrop` background: `rgba(0,0,0,.32)`. Keep everything else on that line.
- `.modal h2`: `font-size: 16px; font-weight: var(--weight-header);`. `.modal p`: `font-size: 13px;`.
- Leave the `@starting-style`, `is-open` transitions and reduced-motion lines exactly as they are.

## 2. Review page CSS (`build_browser_html`)

Only the rules listed here change. Everything else stays as is.

- `body`: `font: 14px/20px system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;` (rest unchanged).
- `header`: padding `8px 16px`.
- `input[type="search"]` and `select`: `height: 32px; padding: 0 10px; border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--bg-raised);` plus `transition: border-color var(--dur) var(--ease), box-shadow var(--dur) var(--ease);`. Focus: `outline: 2px solid var(--text-secondary); outline-offset: 1px;`.
- `input::placeholder` (new rule allowed here, right after the search rule): `color: var(--text-muted);`.
- `button#cancel`: font-size `14px`.
- `button[aria-pressed="true"], button.active-toggle`: `background: var(--tint-selected); border-color: transparent;`.
- `.stats-bar`: `background: var(--bg); font-size: 13px;`.
- `.stats-inline b`: `font-weight: var(--weight-semibold);`.
- `.group`: `border-radius: var(--radius-xl);`.
- `.group-head`: `font-size: 13px;`.
- `.group-actions > button`: `font-size: 12px; padding: 3px 8px; border-radius: var(--radius-md);`.
- `.file`: `border-radius: var(--radius);` and `transition: border-color var(--dur) var(--ease);`.
- `.name`: `font-weight: var(--weight-semibold); font-size: 13px;`.
- `.choice button`: `font-size: 13px; font-weight: var(--weight-control);`. The `.active` variants: `font-weight: var(--weight-semibold);`.
- `.reveal-btn`: `border: 1px solid var(--border-strong); border-radius: var(--radius-md); background: var(--bg-raised);` and opacity transition `var(--dur) var(--ease)`.
- `.folder-row`: `font-size: 11px; border-radius: var(--radius-md); border: 1px solid var(--border);`. `:hover` → `background: var(--tint-hover);`. `.is-active` → `background: var(--tint-selected); border-color: transparent;`.
- `.modal` (first definition): `border: 0; border-radius: var(--radius-composer); box-shadow: var(--shadow-panel);`.
- `.confirm-list, .preview-meta`: `border-radius: var(--radius);`.
- `.preview-keep-btn`, `.preview-trash-btn`: `font-size: 13px; padding: 4px 10px;` (border/background unchanged).
- `.preview-body`: `border-radius: var(--radius);`.
- `.dir-picker`: `border: 0; border-radius: var(--radius-xl); box-shadow: var(--shadow-panel);`. Keep `transform-origin` and its transition (make the durations `150ms` stay; only the shadow/border/radius change).
- `.dir-picker-path`: `font-size: 12px;`. `.dir-picker-impact`: `font-size: 11px;`. `.dir-picker button`: `font-size: 12px; font-weight: var(--weight-control);`.
- `.folder-rule-chip`: `border-radius: var(--radius);`. `.folder-rules-preview`, `.folder-rules-list`: `border-radius: var(--radius-xl);`.
- `.folder-rule-chip button`, `.folder-rule-actions button`: `font-size: 12px;`.
- `.rule-pill`: `font-size: 11px; font-weight: var(--weight-semibold);`.
- `.pane-resizer`: `flex: 0 0 1px; background: var(--border-strong);` (keep the `::before` hit area). `:hover, .is-dragging` → `background: var(--text-muted);`. `:focus-visible` → `background: var(--text); box-shadow: 0 0 0 1px var(--text);`.
- `.pane-meta-section`: `font-size: 11px; font-weight: var(--weight-semibold);`.
- `.group.is-active > .group-head`: `background: var(--tint-selected);`.
- `.file.is-pane-active`: `background: var(--tint-selected);`.
- `#listViewHeader`: must stay opaque because it is sticky over scrolling rows. `background: linear-gradient(var(--surface), var(--surface)), var(--panel); border-bottom: 1px solid var(--border-strong); font-size: 12px; font-weight: var(--weight-semibold);`.
- `body.list-view .file-type-badge`: `font-size: 11px;`. `body.list-view .file-reason`: `font-size: 11px;`.
- `body.list-view .lv-preview-btn:hover`: `background: var(--tint-hover);`.

## 3. Empty-folder page CSS (`build_empty_dirs_html`)

- `body`: `font: 14px/20px system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;`.
- `header`: padding `8px 16px`.
- `h1`: `font-size: 16px; font-weight: var(--weight-header);`.
- `.summary`: `font-size: 13px;`.
- `.tree-group`: `border-radius: var(--radius-xl);`.
- `.root-row:hover`, `.child-row:hover`: `background: var(--tint-hover);`.
- `.node-name`: `font-size: 14px; font-weight: var(--weight-semibold);`.
- `.node-badge`: `font-size: 11px;`.
- `.child-name`: `font-size: 13px;`.
- `.child-indent`: `color: var(--text-muted);`.
- `.modal`: `border: 0; border-radius: var(--radius-composer); box-shadow: var(--shadow-panel);`.
- `.confirm-list`: `border-radius: var(--radius);`.

## Review fixes (applied after the agents' pass)

- `--surface` is now translucent, so a surface nested inside another surface shows as a darker box. `.text-preview`, `.preview-body pre`, and the pane `<pre>` inline style get `background: transparent`; their parents already carry the tint.
- `.folder-rule-chip button` and `.folder-rule-actions button` stay at `11px`. At 12px they squeezed the folder-name column of the folder-rules list from 67px to 45px.

## 4. Docs and tests

- `CLAUDE.md`: replace the palette table and the "Zinc/slate" sentence with the new tokens.
- `test_dedup.py`: update the body font literal as noted above, and add `test_sticky_list_header_stays_opaque_over_translucent_surface` so the sticky header cannot regress to a see-through `var(--surface)`.

## Verification

- `python3 -m unittest test_dedup -q` passes.
- `git diff --stat` touches only `dedup.py`, `test_dedup.py`, `CLAUDE.md`, `PLAN.md`.
- `--dry-run` against a throwaway duplicate fixture, in light and dark mode. Check grid and list views, the sticky list header while scrolling, the preview pane and resizer, every modal, the dir-picker, and the empty-folder page.
