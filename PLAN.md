# Plan: drop dark mode, fix small interactions, fix the list-view Trash column

Follow-up to the flat hairline restyle on this branch. Three parts:
1. Drop dark mode (the owner does not use it).
2. Fix interaction problems found in a review of the review page and the empty-folder page.
3. Fix the list-view Trash button, which sits outside its row.

Delete this file in the implementation commit.

## Rules for this work

- Python 3.8+, standard library only. All UI code lives in `dedup.py` as Python strings (`_SHARED_CSS`, `_SHARED_JS`, `build_browser_html()`, `build_empty_dirs_html()`).
- These strings are normal (non-raw) Python strings. In JS/CSS you add, a backslash must be written as `\\`, and never write `"""`. Prefer code that needs no backslashes.
- Match the surrounding style. Comments only for non-obvious behavior.
- All review-page interactions go through the delegated listeners on `#groups` (`handleGroupsClick`, `handleGroupsKeydown`) or `addEventListener` in `init()`. Never add an `onclick="..."` attribute that interpolates an ID. That rule protects against XSS (see `CLAUDE.md`, "Event delegation").
- Never weaken, skip or delete existing tests. Add tests in the existing style (they assert that strings are present in the generated HTML) where they would catch a regression.
- Finish with `python3 -m unittest test_dedup -q` passing.
- Do not touch Python server logic, trash safety, hashing or the HTTP handler.

## Part A — review page (`build_browser_html`)

### A1. List-view Trash button sits outside its row
In list view each row's `.choice` cell holds three buttons (`.lv-preview-btn`, Keep, Trash), but `.choice` is a two-column grid (`grid-template-columns: 1fr 1fr`). Trash wraps onto a second line and is clipped by the 40px row.
- Change the existing `body.list-view .choice` rule so it lays out three columns: `grid-template-columns: 36px 1fr 1fr;`.
- Widen it if both labels do not fit: `.lv-col-actions` (header) and `body.list-view .choice` must keep the same `flex-basis`. Use 150px for both if 130px is too tight.
- `body.list-view .lv-preview-btn` already sets `width: 36px`. Keep it.

### A2. Cancel can be taken back for a few seconds
Today `#cancel` calls `submitSelection(true)` immediately, which ends the session and loses every manual choice.
- Add `const CANCEL_GRACE_MS = 5000;` next to the other top-level constants. Setting it to `0` restores the old instant behavior; keep that working.
- When Cancel is clicked and there is work to lose (`undoStack.length > 0`), do not submit yet:
  - Change the Cancel button text to `Keep reviewing · 5s`, and count down once per second (`· 4s`, …).
  - Add a class (e.g. `is-pending`) so CSS can show it as an active state: `background: var(--tint-selected); color: var(--text);`.
  - Clicking the button again, or pressing Escape, aborts. Restore the label `Cancel`.
  - When the countdown reaches zero, call `submitSelection(true)`.
- With nothing to lose, or with `CANCEL_GRACE_MS` at `0`, cancel immediately as today.
- Clicking "Move to Trash" during the countdown aborts the countdown first.

### A3. Grid view: clicking a thumbnail selects instead of opening a modal
Today a click on `.thumb[data-action="preview"]` opens the preview modal, even though the side pane already shows the group.
- Add `const THUMB_CLICK_SELECTS = true;` next to the other constants. `false` restores the old behavior.
- When `THUMB_CLICK_SELECTS && paneOpen && !listViewMode`:
  - a single click on `.thumb` behaves like a click on the card: it selects the file and updates the pane (reuse the existing card-click branch of `handleGroupsClick`);
  - a double-click on `.thumb` opens the preview modal. Add a `dblclick` listener on `#groups` in `init()` that finds `[data-action='preview']` and calls `openPreview`.
- When the pane is closed, a single click still opens the modal (no change).
- Enter or Space on a focused `.thumb` still opens the modal (`handleGroupsKeydown`, no change).
- Update the `.thumb` title so the double-click is discoverable: `title="Double-click to preview"` when the pane is open is fine; a static `title="Preview (double-click or Enter)"` is also fine.
- The `.lv-preview-btn` "(o)" button in list view keeps opening the modal on a single click.
- Watch for this: with the pane closed, a fast double-click must not open the modal and then close it again. `openPreview` toggles closed when the same file is already open; the `dblclick` handler must not call `openPreview` when the modal is already open for that file.

### A4. Undo: keyboard shortcut and specific labels
- Cmd+Z (macOS) or Ctrl+Z calls `undoLastAction()`:
  - only when `event.shiftKey` is false (there is no redo stack, so Cmd+Shift+Z must do nothing);
  - not when `isTextInputTarget(event.target)` is true, so the search box keeps native text undo;
  - not when any overlay (`#previewOverlay`, `#folderRulesOverlay`, `#confirmOverlay`) is open, except that inside the preview modal it is fine to undo.
  - Call `event.preventDefault()` only when it acts.
  - `markKey()` currently returns early on `metaKey`/`ctrlKey`; put the undo check in the `document` keydown handler in `init()`, before `markKey`.
- Labels say what will be undone. `#undo` shows `Undo ${label}`.
  - `mark(fileId, shouldTrash)`: label `trash ${file.name}` or `keep ${file.name}`.
  - `markGroup(group, mode, dirPath, global)`: label by mode. `trash` → `Trash copies`, `oldest` → `Keep oldest`, `none` → `Keep all`, `default` → `defaults`, `keepDir` → `Keep this folder`, `byDirKeep` → `Keep only this folder`, `byDirTrash` → `Trash this folder` (with `global`: `Trash this folder everywhere`).
  - Folder rules stay `folder rules`.
- `#undo` gets `max-width: 220px; overflow: hidden; text-overflow: ellipsis;` (it already has `white-space: nowrap` from the base button). Set `button.title` to the full text.

### A5. Closing Folder rules without a warning
`closeFolderRules()` calls `window.confirm(...)` when there are unapplied changes. Nothing is lost on close: the rules stay in the session and the header button already shows "(N unapplied)". Remove the confirm. Keep the `force` parameter if other code passes it, or drop it and update the callers.

### A6. "By folder" picker: keyboard and Escape
In `showDirPicker()`:
- After appending the picker, focus its first button.
- Escape closes the picker and returns focus to the "By folder" button that opened it. Guard that: the button may no longer be in the document (`document.contains(button)`).
- Use one `close()` function for every way the picker closes: an action button, an outside click, Escape. It removes the picker and both document listeners (click and keydown). Also call `close()`-equivalent cleanup if a later `showDirPicker` call removes an existing picker.
- The global Escape handler in `init()` must not also act on the same keypress: stop propagation in the picker's handler, or check for `.dir-picker` in the global handler first.

### A7. Enter opens the selected file
Arrow Up/Down select files (`navigateMainView`), but Enter does nothing. In the `document` keydown handler in `init()`, Enter calls `openPreview(activeFileId, activeGroupId)` when all of these hold:
- `activeFileId` and `activeGroupId` are set;
- no overlay is open;
- `!event.defaultPrevented` (the focused-thumb path in `handleGroupsKeydown` already handled it);
- the target is not interactive: not inside `button, a, input, textarea, select, [role="button"], video, audio`;
- no modifier keys.
Without these guards Enter on a focused thumbnail opens the modal and then immediately closes it, because `openPreview` toggles closed when the same file is open.

### A8. "Move to Trash" names the count
- In `updateStats()`, set `#finish` text to `Move ${count} to Trash` when `count > 0`, and `Finish` when `count === 0`. Do not overwrite it while `isSubmitting` is true (`submitSelection` sets "Submitting...").
- In `showMoveConfirmation()`, set `#confirmMove` text to `Move ${count} to Trash`.
- `submitSelection` restores button text on failure from saved values. Make sure the restore still matches what `updateStats()` would show.

### A9. Submit errors shown inline, not with `alert()`
In `submitSelection()`, replace `alert("Failed to submit selection: " + err.message)`:
- Add `<span id="submitError" class="submit-error" role="alert"></span>` inside `.header-actions`, before `#cancel`. CSS: `color: var(--danger); font-size: 13px;`, hidden when empty (`.submit-error:empty { display: none; }`).
- Also add `<p id="confirmError" class="submit-error" role="alert"></p>` inside the confirm modal, above `.modal-actions`. The modal covers the header, so an error shown only in the header is invisible while the modal is open.
- On failure, set both texts to `Could not submit: ${err.message}`. Clear both at the start of every `submitSelection` call.

### A10. "Open" on a card is really "Reveal"
In `renderGroupContent`, the `.reveal-btn` label `Open` becomes `Reveal`. Keep its title and aria-label.

### A11. Escape cancels a pane-resize drag
In `initPaneResizer()`, during a pointer drag, Escape restores `startWidth`, then ends the drag the same way `onUp` does. Skip the localStorage write in that case. Remove the keydown listener in every end path.

### A12. CSS added for this part
Add new rules right after the existing `button.danger-action { … }` rule, in this order: `#cancel.is-pending`, `#undo` max-width, `.submit-error`, `.submit-error:empty`.

### A tests
Add to `BrowserHelperTests`, directly after `test_undo_keeps_a_bounded_stack`:
- the list-view `.choice` has three columns (assert the new `grid-template-columns` string);
- `CANCEL_GRACE_MS` and `THUMB_CLICK_SELECTS` constants exist;
- Cmd/Ctrl+Z is wired and ignores Shift (assert the key condition string you write);
- `window.confirm` no longer appears in the review page, and `alert(` no longer appears in it;
- `id="submitError"` and `id="confirmError"` exist.

## Part B — shared CSS and empty-folder page

### B1. Drop dark mode
In `_SHARED_CSS`:
- Delete the whole `@media (prefers-color-scheme: dark) { :root { … } }` block.
- Change `color-scheme: light dark;` to `color-scheme: light;`, so native controls (selects, checkboxes, scrollbars) stay light when the OS is in dark mode.
- Keep every other token. Light values stay exactly as they are.

### B2. Empty-folder page (`build_empty_dirs_html`)
- **Labels say "Trash", not "Delete".**
  - The header button `Delete Selected` (`#confirmBtn`) shows `Move ${n} to Trash` when folders are selected and `Move to Trash` when none are (still disabled at 0). Use the effective count (`effectiveSelection().length`). Update it in `render()`.
  - The modal heading `Confirm Deletion` becomes `Move ${n} folder(s) to Trash?` (singular/plural correct), set in `openConfirm()`. Give the `<h2>` an id.
  - The modal's confirm button `Move to Trash` shows `Move ${n} to Trash`. Give it an id.
- **The modal closes with Escape and a backdrop click.** Add a `keydown` listener on `document` (Escape → `closeConfirm()` when the modal is open) and a click listener on `#confirmModal` that closes when `event.target` is the backdrop itself. When the modal opens, focus its "Back" button. When it closes, return focus to `#confirmBtn`.
- **Errors shown inline, not with `alert()`.** In `postSelection`, replace `alert(...)` with text in a `<p id="postError" class="submit-error" role="alert"></p>`. Put it in the header toolbar, and add a second copy (`id="confirmPostError"`) inside the modal above `.modal-actions`. Clear both at the start of each post. CSS for `.submit-error` and `.submit-error:empty` goes in the empty-folder page CSS (same values as A9).

### B tests
Add to `BrowserHelperTests`, directly after `test_empty_dirs_html_posts_effective_selection`:
- `prefers-color-scheme: dark` appears in neither page, and `color-scheme: light;` appears in both;
- the empty-folder page has no `alert(` and no `Delete Selected` / `Confirm Deletion` text;
- its Escape handler string is present.

## Verification (done by the integrator, not the agents)
- `python3 -m unittest test_dedup -q` passes.
- `--dry-run` in a browser on a throwaway duplicate fixture, checking every item above. Stub `window.confirm`, `window.alert` and `fetch` with `javascript_tool` before exercising failure paths, so a native dialog cannot block the browser session.
