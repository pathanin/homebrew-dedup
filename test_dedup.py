import contextlib
import http.client
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.dont_write_bytecode = True

import dedup


class RootGuardTests(unittest.TestCase):
    def test_rejects_filesystem_root(self):
        with self.assertRaises(ValueError):
            dedup.validate_scan_root(os.path.abspath(os.sep))

    def test_rejects_home_root_without_opt_in(self):
        with self.assertRaises(ValueError):
            dedup.validate_scan_root(os.path.expanduser("~"))

    def test_allows_safe_temp_subdir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(
                dedup.validate_scan_root(temp_dir),
                (os.path.abspath(temp_dir), os.path.realpath(temp_dir)),
            )

    def test_rejects_photo_library_package_without_opt_in(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            library_dir = os.path.join(temp_dir, "Photos Library.photoslibrary")
            os.mkdir(library_dir)
            with self.assertRaises(ValueError):
                dedup.validate_scan_root(library_dir)


class CliTests(unittest.TestCase):
    def test_default_options_use_fast_verification(self):
        args = dedup.parse_args(["."])
        options = dedup.build_options(args)

        self.assertFalse(args.dry_run)
        self.assertEqual(args.progress_every, dedup.DEFAULT_PROGRESS_EVERY)
        self.assertEqual(options.verify_mode, dedup.VERIFY_FAST)

    def test_full_verify_sets_full_verification(self):
        args = dedup.parse_args(["--full-verify", "."])
        options = dedup.build_options(args)

        self.assertEqual(options.verify_mode, dedup.VERIFY_FULL)

    def test_ignore_flags_are_added_to_options(self):
        args = dedup.parse_args(["--ignore-dir", "cache", "--ignore-file", ".tmp", "."])
        options = dedup.build_options(args)

        self.assertIn("cache", options.ignore_dirs)
        self.assertIn(".tmp", options.ignore_files)

    def test_allow_slow_local_trash_defaults_to_false(self):
        args = dedup.parse_args(["."])

        self.assertFalse(args.allow_slow_local_trash)

    def test_allow_slow_local_trash_flag_enables_last_resort(self):
        args = dedup.parse_args(["--allow-slow-local-trash", "."])

        self.assertTrue(args.allow_slow_local_trash)

    def test_permanent_on_no_trash_defaults_to_false(self):
        args = dedup.parse_args(["."])

        self.assertFalse(args.permanent_on_no_trash)

    def test_permanent_on_no_trash_flag_enables_permanent_delete(self):
        args = dedup.parse_args(["--permanent-on-no-trash", "."])

        self.assertTrue(args.permanent_on_no_trash)

    def test_default_port_is_7979(self):
        args = dedup.parse_args(["."])

        self.assertEqual(args.port, 7979)

    def test_port_flag_overrides_default(self):
        args = dedup.parse_args(["--port", "8080", "."])

        self.assertEqual(args.port, 8080)


class DuplicateGroupingTests(unittest.TestCase):
    def make_options(self, path, verify_mode=dedup.VERIFY_FAST):
        return dedup.ScanOptions(
            path=os.path.abspath(path),
            real_path=os.path.realpath(path),
            ignore_dirs=frozenset(),
            ignore_files=frozenset(),
            progress_every=1000000,
            verify_mode=verify_mode,
        )

    def find_quietly(self, options):
        with contextlib.redirect_stdout(io.StringIO()):
            return dedup.find_duplicates(options)

    def test_finds_duplicate_files_in_fast_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path_a = os.path.join(temp_dir, "a.txt")
            path_b = os.path.join(temp_dir, "copy a.txt")
            unique = os.path.join(temp_dir, "unique.txt")
            for path, body in ((path_a, b"same"), (path_b, b"same"), (unique, b"diff")):
                with open(path, "wb") as file_obj:
                    file_obj.write(body)

            groups, stats = self.find_quietly(self.make_options(temp_dir))

            self.assertEqual(stats.duplicate_groups, 1)
            self.assertEqual({info.path for info in groups[0].files}, {path_a, path_b})
            self.assertEqual(groups[0].hash_name, dedup.FULL_HASH_NAME)

    def test_full_verify_rejects_same_sparse_sample_with_different_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path_a = os.path.join(temp_dir, "a.bin")
            path_b = os.path.join(temp_dir, "b.bin")
            chunk = dedup.FAST_SAMPLE_BYTES
            payload_a = (b"s" * chunk) + b"a" + (b"e" * (chunk - 1))
            payload_b = (b"s" * chunk) + b"b" + (b"e" * (chunk - 1))
            for path, body in ((path_a, payload_a), (path_b, payload_b)):
                with open(path, "wb") as file_obj:
                    file_obj.write(body)

            groups, stats = self.find_quietly(self.make_options(temp_dir, dedup.VERIFY_FULL))

            self.assertEqual(groups, [])
            self.assertEqual(stats.duplicate_groups, 0)

    def test_ignored_hidden_files_are_not_scanned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(os.path.join(temp_dir, ".hidden"), "wb") as file_obj:
                file_obj.write(b"ignored")
            stats = dedup.ScanStats()

            with contextlib.redirect_stdout(io.StringIO()):
                candidates = dedup.scan_by_size(self.make_options(temp_dir), stats)

            self.assertEqual(candidates, {})
            self.assertEqual(stats.scanned, 0)
            self.assertEqual(stats.ignored, 1)


class BrowserHelperTests(unittest.TestCase):
    def test_media_kind_detects_supported_preview_types(self):
        self.assertEqual(dedup.get_media_kind("/tmp/photo.JPG"), "image")
        self.assertEqual(dedup.get_media_kind("/tmp/clip.MOV"), "video")
        self.assertIsNone(dedup.get_media_kind("/tmp/readme.txt"))

    def test_media_kind_detects_audio(self):
        self.assertEqual(dedup.get_media_kind("/tmp/track.mp3"), "audio")
        self.assertEqual(dedup.get_media_kind("/tmp/track.MP3"), "audio")
        self.assertEqual(dedup.get_media_kind("/tmp/track.flac"), "audio")
        self.assertEqual(dedup.get_media_kind("/tmp/track.wav"), "audio")
        self.assertEqual(dedup.get_media_kind("/tmp/track.ogg"), "audio")
        self.assertEqual(dedup.get_media_kind("/tmp/track.m4a"), "audio")
        self.assertEqual(dedup.get_media_kind("/tmp/track.aiff"), "audio")
        self.assertEqual(dedup.get_media_kind("/tmp/track.wma"), "audio")
        self.assertEqual(dedup.get_media_kind("/tmp/track.opus"), "audio")

    def test_audio_extensions_set_is_comprehensive(self):
        expected = {
            ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wav", ".opus",
            ".wma", ".aiff", ".aif", ".alac", ".ape", ".wv", ".ra",
            ".mid", ".midi", ".caf", ".amr", ".3ga",
        }
        self.assertTrue(expected.issubset(dedup.AUDIO_EXTENSIONS))

    def test_browser_payload_sets_audio_preview_kind(self):
        group = dedup.DuplicateGroup(
            "abc",
            (
                dedup.FileInfo("/tmp/song.mp3", 4, 1),
                dedup.FileInfo("/tmp/song copy.mp3", 4, 2),
            ),
        )

        payload = dedup.build_browser_payload([group])
        files = payload["groups"][0]["files"]

        self.assertEqual(files[0]["mediaKind"], "audio")
        self.assertEqual(files[0]["previewKind"], "audio")
        self.assertEqual(files[1]["mediaKind"], "audio")
        self.assertEqual(files[1]["previewKind"], "audio")

    def test_browser_html_includes_audio_filter_option(self):
        html = dedup.build_browser_html()

        self.assertIn('value="audio"', html)
        self.assertIn(">Audio<", html)

    def test_mediabunny_script_tag_emits_deferred_tag(self):
        tag = dedup.mediabunny_script_tag("https://example.test/mediabunny.cjs")
        self.assertIn("<script", tag)
        self.assertIn("defer", tag)
        self.assertIn("https://example.test/mediabunny.cjs", tag)

    def test_mediabunny_script_tag_blank_disables(self):
        self.assertEqual(dedup.mediabunny_script_tag(""), "")

    def test_browser_html_loads_local_mediabunny_bundle_by_default(self):
        html = dedup.build_browser_html()

        self.assertIn(dedup.MEDIABUNNY_ASSET_ROUTE, html)
        self.assertNotIn("unpkg.com", html)
        self.assertNotIn("jsdelivr", html)
        self.assertIn("mbReady", html)

    def test_blank_mediabunny_env_disables_script_but_keeps_ffmpeg_thumbs(self):
        with mock.patch.dict(os.environ, {"DEDUP_MEDIABUNNY_SRC": ""}):
            html = dedup.build_browser_html()

        self.assertNotIn(dedup.MEDIABUNNY_ASSET_ROUTE, html)
        self.assertIn('src="${authUrlAttr(`/thumb/${urlId(file.id)}?i=0`)}"', html)

    def test_missing_local_mediabunny_asset_disables_script_without_cdn_fallback(self):
        missing = os.path.join(tempfile.gettempdir(), "missing-mediabunny.cjs")
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(dedup, "MEDIABUNNY_ASSET_PATH", missing):
                html = dedup.build_browser_html()

        self.assertNotIn(dedup.MEDIABUNNY_ASSET_ROUTE, html)
        self.assertNotIn("unpkg.com", html)
        self.assertNotIn("jsdelivr", html)
        self.assertIn('src="${authUrlAttr(`/thumb/${urlId(file.id)}?i=0`)}"', html)

    def test_browser_html_keeps_ffmpeg_thumbnail_fallback(self):
        # ffmpeg remains the immediate first-paint path for thumbnails.
        html = dedup.build_browser_html()
        self.assertIn("function ffmpegThumbUrl(fileId, thumbIndex)", html)
        self.assertIn("/thumb/${urlId(fileId)}?i=${thumbIndex}", html)
        self.assertIn("fallbackVideoThumb", html)

    def test_browser_html_hydrates_grid_video_thumbs_with_mediabunny(self):
        html = dedup.build_browser_html()

        self.assertIn("async function mbCanvasThumb(fileId, timestamp, w, h, index = 0)", html)
        self.assertIn("async function hydrateThumb(img)", html)
        self.assertIn("async function waitForMediabunny(timeoutMs = 2500)", html)
        self.assertIn("if (!fileId || !mbReady()) return;", html)
        self.assertNotIn("if (!fileId || !(await waitForMediabunny()))", html)
        self.assertIn("hydrateVideoThumbs(el);", html)
        self.assertIn("new UrlSource(authUrl(`/media/${urlId(fileId)}`))", html)
        self.assertIn("await track.canDecode()", html)
        self.assertIn("function videoThumbnailTimestamp(duration, index, count)", html)
        self.assertIn("setVideoThumbTimestamp(img, thumbIndex);", html)

    def test_browser_html_loads_video_metadata_with_mediabunny_first(self):
        html = dedup.build_browser_html()
        meta_start = html.index("async function hydrateVideoFile(file)")
        meta_end = html.index("async function hydrateVideoMetadata(root)", meta_start)
        meta_html = html[meta_start:meta_end]

        self.assertIn("async function mbVideoMetadata(file)", html)
        self.assertIn("await input.computeDuration()", html)
        self.assertIn("await track.getDisplayWidth()", html)
        self.assertIn("await track.getDisplayHeight()", html)
        self.assertIn("await track.getCodecParameterString()", html)
        self.assertIn('source = "mediabunny"', meta_html)
        self.assertIn('if (!mbReady()) throw new Error("mediabunny unavailable");', meta_html)
        self.assertNotIn("waitForMediabunny", meta_html)
        self.assertIn('source = "server"', meta_html)
        self.assertIn("payload = await fetchServerMetadata(file);", meta_html)

    def test_browser_html_adds_mediabunny_preview_player_fallback(self):
        html = dedup.build_browser_html()

        self.assertIn("async function hydratePreviewVideo(file, token)", html)
        self.assertIn("function nativeVideoCannotPlay(video, token, file)", html)
        self.assertIn("async function mountMediabunnyPreviewPlayer(file, token)", html)
        self.assertIn("previewPlayer = await mountMediabunnyPreviewPlayer(file, token);", html)
        self.assertIn("const { CanvasSink, AudioBufferSink } = window.Mediabunny;", html)
        self.assertIn("if (!canDecode) throw new Error(\"codec unavailable\");", html)
        self.assertIn("new UrlSource(authUrl(`/media/${urlId(fileId)}`))", html)
        self.assertIn('className = "mb-player"', html)
        self.assertIn("previewUnavailableHtml(file)", html)

    def test_preview_player_teardown_closes_audio_context(self):
        html = dedup.build_browser_html()

        self.assertIn("let previewPlayer = null;", html)
        self.assertIn("function stopPreviewPlayer()", html)
        self.assertIn("audioContext.close().catch(() => {});", html)
        self.assertIn("stopPreviewPlayer();\n  const oldVideo", html)
        self.assertIn("stopPreviewPlayer();\n  const video", html)
        self.assertIn("if (!isCurrent()) {\n      destroy();", html)

    def test_preview_player_checks_render_token_after_async_work(self):
        html = dedup.build_browser_html()

        self.assertIn("function isPreviewPlayerCurrent(file, token)", html)
        self.assertIn("token === previewRenderToken", html)
        self.assertIn("if (!previewContext || previewContext.fileId !== file.id || token !== previewRenderToken) return;", html)
        self.assertIn("if (!body || !isPreviewPlayerCurrent(file, token)) throw new Error(\"stale preview\");", html)
        self.assertIn("if (!isPreviewPlayerCurrent(file, token)) throw new Error(\"stale preview\");", html)

    def test_grid_video_thumb_uses_ffmpeg_first_paint_with_mediabunny_data(self):
        html = dedup.build_browser_html()
        media_start = html.index("function mediaHtml(file)")
        video_start = html.index('if (file.mediaKind === "video")', media_start)
        video_return = html.index('return `<img loading="lazy"', video_start)
        video_end = html.index('if (file.previewKind === "pdf")', video_start)
        video_html = html[video_return:video_end]

        self.assertIn('src="${authUrlAttr(`/thumb/${urlId(file.id)}?i=0`)}"', video_html)
        self.assertIn('onerror="fallbackVideoThumb(this)"', video_html)
        self.assertIn('data-video-thumb="1"', video_html)
        self.assertIn('data-thumb-timestamp="1"', video_html)

    def test_group_video_hover_cycle_sets_ffmpeg_frame_before_mediabunny(self):
        html = dedup.build_browser_html()
        cycle_start = html.index("function startGroupVideoThumbCycle(groupEl)")
        cycle_end = html.index("function stopGroupVideoThumbCycle(groupEl)", cycle_start)
        cycle_html = html[cycle_start:cycle_end]

        self.assertIn("setVideoThumbTimestamp(img, thumbIndex);", cycle_html)
        self.assertIn('img.dataset.thumbSource = "ffmpeg";', cycle_html)
        self.assertIn("img.src = ffmpegThumbUrl(img.dataset.fileId, thumbIndex);", cycle_html)
        self.assertIn("delete img.dataset.thumbLoaded;", cycle_html)
        self.assertIn("hydrateThumb(img);", cycle_html)
        self.assertLess(
            cycle_html.index("img.src = ffmpegThumbUrl(img.dataset.fileId, thumbIndex);"),
            cycle_html.index("hydrateThumb(img);"),
        )

    def test_stop_group_video_thumb_cycle_resets_and_rehydrates(self):
        html = dedup.build_browser_html()
        stop_start = html.index("function stopGroupVideoThumbCycle(groupEl)")
        stop_end = html.index("function fallbackVideoThumb(img)", stop_start)
        stop_html = html[stop_start:stop_end]

        self.assertIn("clearInterval(timer);", stop_html)
        self.assertIn("thumbTimers.delete(groupEl);", stop_html)
        self.assertIn('groupEl.dataset.thumbIndex = "0";', stop_html)
        self.assertIn('img.dataset.thumbIndex = "0";', stop_html)
        self.assertIn("setVideoThumbTimestamp(img, 0);", stop_html)
        self.assertIn("img.src = ffmpegThumbUrl(img.dataset.fileId, 0);", stop_html)
        self.assertIn("delete img.dataset.prefetched;", stop_html)
        self.assertIn("delete img.dataset.thumbLoaded;", stop_html)
        self.assertIn("hydrateThumb(img);", stop_html)

    def test_local_mediabunny_asset_endpoint_is_public_javascript(self):
        if not os.path.isfile(dedup.MEDIABUNNY_ASSET_PATH):
            self.skipTest("local mediabunny asset is not present")
        group = dedup.DuplicateGroup(
            "abc",
            (dedup.FileInfo("/tmp/a.txt", 4, 1),),
        )
        state = dedup.BrowserSelectionState([group])
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), dedup.make_browser_handler(state))
        except PermissionError:
            self.skipTest("loopback bind not permitted")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            with urllib.request.urlopen(f"{base_url}{dedup.MEDIABUNNY_ASSET_ROUTE}") as response:
                status = response.status
                body = response.read(64).decode("utf-8", errors="replace")
                content_type = response.headers.get("Content-Type", "")
                nosniff = response.headers.get("X-Content-Type-Options", "")

            self.assertEqual(status, 200)
            self.assertIn("text/javascript", content_type)
            self.assertEqual(nosniff, "nosniff")
            self.assertIn("Copyright", body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_meta_endpoint_returns_audio_duration(self):
        group = dedup.DuplicateGroup(
            "abc",
            (
                dedup.FileInfo("/tmp/song.mp3", 4, 1),
                dedup.FileInfo("/tmp/song copy.mp3", 4, 2),
            ),
        )
        state = dedup.BrowserSelectionState([group])
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), dedup.make_browser_handler(state))
        except PermissionError:
            self.skipTest("loopback bind not permitted")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            token = state.session_id
            with urllib.request.urlopen(f"{base_url}/api/groups?token={token}") as response:
                file_info = json.loads(response.read().decode("utf-8"))["groups"][0]["files"][0]

            self.assertEqual(file_info["mediaKind"], "audio")

            with mock.patch.object(dedup, "get_video_duration", return_value=183.5):
                with urllib.request.urlopen(f"{base_url}/meta/{file_info['id']}?token={token}") as response:
                    metadata = json.loads(response.read().decode("utf-8"))

            self.assertEqual(metadata["mediaKind"], "audio")
            self.assertAlmostEqual(metadata["duration"], 183.5)
            self.assertNotIn("thumbnailCount", metadata)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_meta_endpoint_omits_duration_when_ffprobe_absent(self):
        group = dedup.DuplicateGroup(
            "abc",
            (dedup.FileInfo("/tmp/song.mp3", 4, 1),),
        )
        state = dedup.BrowserSelectionState([group])
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), dedup.make_browser_handler(state))
        except PermissionError:
            self.skipTest("loopback bind not permitted")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            token = state.session_id
            with urllib.request.urlopen(f"{base_url}/api/groups?token={token}") as response:
                file_info = json.loads(response.read().decode("utf-8"))["groups"][0]["files"][0]

            with mock.patch.object(dedup, "get_video_duration", return_value=None):
                with urllib.request.urlopen(f"{base_url}/meta/{file_info['id']}?token={token}") as response:
                    metadata = json.loads(response.read().decode("utf-8"))

            self.assertNotIn("duration", metadata)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_readable_text_preview(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "notes.txt")
            with open(path, "w", encoding="utf-8") as file_obj:
                file_obj.write("alpha\nbeta\n")

            self.assertTrue(dedup.is_readable_text_file(path, os.path.getsize(path)))
            self.assertEqual(dedup.read_text_preview(path), "alpha\nbeta\n")

    def test_thumbnail_command_uses_list_arguments(self):
        command = dedup.build_thumbnail_command(
            "/tmp/a file.jpg",
            "image",
            24,
            8,
            "/usr/bin/ffmpeg",
        )

        self.assertEqual(command[0], "/usr/bin/ffmpeg")
        self.assertIn("/tmp/a file.jpg", command)
        self.assertEqual(command[-5:], ["-f", "image2pipe", "-vcodec", dedup.THUMBNAIL_FORMAT, "-"])

    def test_browser_payload_marks_likely_copy_for_trash(self):
        group = dedup.DuplicateGroup(
            "abc",
            (
                dedup.FileInfo("/tmp/photo.jpg", 4, 1),
                dedup.FileInfo("/tmp/photo copy.jpg", 4, 2),
            ),
        )

        payload = dedup.build_browser_payload([group])
        files = payload["groups"][0]["files"]

        self.assertFalse(files[0]["defaultTrash"])
        self.assertTrue(files[1]["defaultTrash"])
        self.assertEqual(files[0]["mediaKind"], "image")

    def test_browser_payload_defers_preview_probes(self):
        group = dedup.DuplicateGroup(
            "abc",
            (
                dedup.FileInfo("/tmp/clip.mp4", 4, 1),
                dedup.FileInfo("/tmp/blob.bin", 4, 2),
            ),
        )

        with mock.patch.object(dedup, "get_video_duration", side_effect=AssertionError("eager video probe")):
            with mock.patch.object(dedup, "is_readable_text_file", side_effect=AssertionError("eager text probe")):
                payload = dedup.build_browser_payload([group])

        files = payload["groups"][0]["files"]
        self.assertEqual(files[0]["mediaKind"], "video")
        self.assertEqual(files[0]["thumbnailCount"], 1)
        self.assertIsNone(files[1]["previewKind"])

    def test_browser_html_uses_delegated_card_actions(self):
        html = dedup.build_browser_html()

        self.assertIn('data-action="preview"', html)
        self.assertIn('data-mark="trash"', html)
        self.assertIn('addEventListener("click", handleGroupsClick)', html)
        self.assertNotIn("onclick=\"openPreview('", html)
        self.assertNotIn("onclick=\"mark('", html)
        self.assertNotIn("onclick=\"revealFile('", html)
        self.assertNotIn("onkeydown=\"if(event.key", html)

    def test_browser_html_opens_preview_before_async_text_fetch(self):
        html = dedup.build_browser_html()

        # classList.add("is-open") must appear before renderPreview() in openPreview()
        open_overlay = html.index('overlay.classList.add("is-open");')
        render_preview = html.index("renderPreview(fileId);", open_overlay)
        self.assertLess(open_overlay, render_preview)
        self.assertIn("const token = ++previewRenderToken;", html)
        self.assertIn("hydratePreviewText(file.id, token, textNode);", html)
        self.assertNotIn("await renderPreview(fileId);", html)
        self.assertIn("if (!response.ok) throw new Error(\"Preview unavailable\");", html)

    def test_browser_html_preserves_video_arrow_keys(self):
        html = dedup.build_browser_html()

        self.assertIn('target.closest("input, textarea, select, video, audio")', html)
        self.assertIn('if (isTextInputTarget(event.target)) return;', html)

    def test_browser_html_numeric_sort_arrows_match_direction(self):
        html = dedup.build_browser_html()

        self.assertIn('const dir = sortDescending ? -1 : 1;', html)
        self.assertIn(
            'if (sort === "size") renderGroups.sort((a, b) => dir * (repr(a).size - repr(b).size));',
            html,
        )
        self.assertIn(
            'else if (sort === "count") renderGroups.sort((a, b) => dir * (a.files.length - b.files.length));',
            html,
        )
        self.assertIn('if (active) col.textContent += sortDescending ? " ↓" : " ↑";', html)
        self.assertIn('sortDescending = !sortDescending;', html)
        self.assertIn('sortDescending = false; render(); updateListViewHeader();', html)

    def test_browser_html_has_folder_summary_element(self):
        html = dedup.build_browser_html()

        self.assertIn('id="folderSummary"', html)
        self.assertIn('renderFolderSummary(', html)
        self.assertIn('getFolderSummary(', html)

    def test_browser_html_has_folder_rules_panel_hooks(self):
        html = dedup.build_browser_html()

        self.assertIn('id="folderRules"', html)
        self.assertIn('id="folderRulesOverlay"', html)
        self.assertIn('function computeFolderRulePreview()', html)
        self.assertIn('manualChoices.has(file.id)', html)
        self.assertIn('skippedGroups.push(group)', html)
        self.assertIn('addEventListener("click", openFolderRules)', html)
        self.assertIn('const folderRuleReasons = new Map();', html)
        self.assertIn('function selectionReasonForFile(file, isTrash)', html)
        self.assertIn('folderRules = folderRules.filter(rule => !(rule.directory === directory && rule.subtree === subtree));', html)
        self.assertIn('exactRule: rule.subtree ? 0 : 1', html)
        self.assertIn('folderRuleRowsCache', html)
        self.assertIn('Folder rules (${folderRules.length}${folderRulesDirty ? " unapplied" : ""})', html)
        self.assertIn('folderRulesDirty', html)
        self.assertIn('Keep duplicates here', html)
        self.assertIn('Mark duplicates here for Trash', html)
        self.assertIn('Apply: update ${preview.changedIds.length} file', html)
        self.assertIn('skipped because every copy matched trash rules', html)
        self.assertIn('class="rule-pill', html)
        self.assertIn('function basenameForDirectory(directory)', html)
        self.assertIn('.folder-rules-modal { gap: 12px; width: min(920px, 100%);', html)

    def test_browser_html_has_group_impact_in_header(self):
        html = dedup.build_browser_html()

        self.assertIn('groupTrashCount', html)
        self.assertIn('groupTrashBytes', html)
        self.assertIn('class="group-impact"', html)

    def test_browser_html_collocates_reason_with_decision(self):
        html = dedup.build_browser_html()

        self.assertIn('class="file-reason"', html)
        self.assertIn('file.selectionReason', html)
        self.assertIn('file.originalReason', html)

    def test_browser_html_has_preview_pane_and_modal_hooks(self):
        html = dedup.build_browser_html()

        self.assertIn('id="previewPane"', html)
        self.assertIn('id="panePlaceholder"', html)
        self.assertIn('id="paneContent"', html)
        self.assertIn('id="previewOverlay"', html)
        self.assertIn('id="previewBody"', html)
        self.assertIn('id="previewPrev"', html)
        self.assertIn('id="previewNext"', html)
        self.assertIn('class="modal wide preview-modal"', html)
        self.assertIn('overflow-wrap: anywhere', html)
        self.assertIn('.pane-preview-area pre', html)

    def test_browser_html_cycles_pane_video_thumbnails_until_play(self):
        html = dedup.build_browser_html()

        self.assertIn("let paneVideoTimer = null;", html)
        self.assertIn("function stopPaneVideoCycle()", html)
        self.assertIn("async function startPaneVideoCycle(file)", html)
        self.assertIn("img[data-pane-video-thumb='1']", html)
        self.assertIn('data-pane-video-thumb="1"', html)
        self.assertIn('data-video-thumb="1" data-pane-video-thumb="1"', html)
        self.assertIn('onerror="fallbackVideoThumb(this)" data-video-thumb="1"', html)
        self.assertIn("startPaneVideoCycle(file);", html)
        self.assertIn("setVideoThumbTimestamp(img, index);", html)
        self.assertIn("hydrateThumb(img);", html)
        self.assertIn("await hydrateVideoFile(file);", html)
        self.assertIn('payload = { ...(file.videoMetadata || { mediaKind: "video" }) };', html)
        self.assertIn('for (const k of ["audioCodec", "sampleRate", "channels", "bitrate"])', html)

        play_start = html.index("function startPaneVideo(file)")
        play_stop = html.index("stopPaneVideoCycle();", play_start)
        play_replace = html.index('area.innerHTML = "";', play_start)
        self.assertLess(play_stop, play_replace)

        render_start = html.index("function renderPanePreview(file)")
        render_stop = html.index("stopPaneVideoCycle();", render_start)
        render_video = html.index('file.mediaKind === "video"', render_start)
        self.assertLess(render_stop, render_video)
        render_end = html.index('file.mediaKind === "audio"', render_start)
        pane_video_html = html[render_video:render_end]
        self.assertIn('authUrlAttr(`/thumb/${urlId(file.id)}?i=0`)', pane_video_html)

    def test_empty_dirs_html_posts_effective_selection(self):
        html = dedup.build_empty_dirs_html()

        self.assertIn("body: JSON.stringify({selected: effectiveSelection()})", html)
        self.assertNotIn("body: JSON.stringify({selected: [...selected]})", html)

    def test_browser_selection_keeps_one_file_per_group(self):
        group = dedup.DuplicateGroup(
            "abc",
            (
                dedup.FileInfo("/tmp/photo.jpg", 4, 1),
                dedup.FileInfo("/tmp/photo copy.jpg", 4, 2),
            ),
        )
        payload = dedup.build_browser_payload([group])
        file_ids = [file_info["id"] for file_info in payload["groups"][0]["files"]]

        selected = dedup.sanitize_browser_trash_selection(payload["groups"], file_ids)

        self.assertEqual(selected, ["/tmp/photo copy.jpg"])

    def test_browser_handler_serves_groups_and_accepts_selection(self):
        group = dedup.DuplicateGroup(
            "abc",
            (
                dedup.FileInfo("/tmp/photo.jpg", 4, 1),
                dedup.FileInfo("/tmp/photo copy.jpg", 4, 2),
            ),
        )
        state = dedup.BrowserSelectionState([group])
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), dedup.make_browser_handler(state))
        except PermissionError:
            self.skipTest("loopback bind not permitted")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            token = state.session_id
            with urllib.request.urlopen(f"{base_url}/api/groups?token={token}") as response:
                groups = json.loads(response.read().decode("utf-8"))["groups"]
            file_id = groups[0]["files"][1]["id"]
            request = urllib.request.Request(
                f"{base_url}/api/selection?token={token}",
                data=json.dumps({"trashIds": [file_id]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request) as response:
                result = json.loads(response.read().decode("utf-8"))

            self.assertTrue(result["ok"])
            self.assertTrue(state.done.is_set())
            self.assertEqual(state.selected_paths, ["/tmp/photo copy.jpg"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_browser_handler_serves_lazy_video_metadata(self):
        group = dedup.DuplicateGroup(
            "abc",
            (
                dedup.FileInfo("/tmp/clip.mp4", 4, 1),
                dedup.FileInfo("/tmp/clip copy.mp4", 4, 2),
            ),
        )
        state = dedup.BrowserSelectionState([group])
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), dedup.make_browser_handler(state))
        except PermissionError:
            self.skipTest("loopback bind not permitted")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            token = state.session_id
            with urllib.request.urlopen(f"{base_url}/api/groups?token={token}") as response:
                file_info = json.loads(response.read().decode("utf-8"))["groups"][0]["files"][0]

            self.assertEqual(file_info["thumbnailCount"], 1)

            with mock.patch.object(dedup, "get_video_duration", return_value=120):
                with urllib.request.urlopen(f"{base_url}/meta/{file_info['id']}?token={token}") as response:
                    metadata = json.loads(response.read().decode("utf-8"))

            self.assertEqual(metadata["mediaKind"], "video")
            self.assertEqual(metadata["thumbnailCount"], 4)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_same_thumbnail_requests_share_inflight_render(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "photo.jpg")
            with open(path, "wb") as file_obj:
                file_obj.write(b"image")

            cache = dedup.ThumbnailCache()
            cache_lock = threading.Lock()
            in_flight = {}
            render_started = threading.Event()
            release_render = threading.Event()
            results = []

            def render_once(*args, **kwargs):
                render_started.set()
                self.assertTrue(release_render.wait(timeout=2))
                return b"thumb"

            def request_thumbnail():
                results.append(
                    dedup.get_thumbnail_threadsafe(path, cache, cache_lock, in_flight)
                )

            with mock.patch.object(dedup, "render_thumbnail", side_effect=render_once) as render:
                first = threading.Thread(target=request_thumbnail)
                first.start()
                self.assertTrue(render_started.wait(timeout=2))
                second = threading.Thread(target=request_thumbnail)
                second.start()
                release_render.set()
                first.join(timeout=2)
                second.join(timeout=2)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(results, [b"thumb", b"thumb"])
            self.assertEqual(render.call_count, 1)

    def test_different_thumbnail_requests_render_concurrently(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = []
            for name in ("a.jpg", "b.jpg"):
                path = os.path.join(temp_dir, name)
                with open(path, "wb") as file_obj:
                    file_obj.write(name.encode("ascii"))
                paths.append(path)

            cache = dedup.ThumbnailCache()
            cache_lock = threading.Lock()
            in_flight = {}
            started = []
            started_lock = threading.Lock()
            both_started = threading.Event()
            release_render = threading.Event()

            def render_blocking(path, *args, **kwargs):
                with started_lock:
                    started.append(path)
                    if len(started) == 2:
                        both_started.set()
                self.assertTrue(release_render.wait(timeout=2))
                return path.encode("utf-8")

            def request_thumbnail(path):
                dedup.get_thumbnail_threadsafe(path, cache, cache_lock, in_flight)

            with mock.patch.object(dedup, "render_thumbnail", side_effect=render_blocking):
                threads = [threading.Thread(target=request_thumbnail, args=(path,)) for path in paths]
                for thread in threads:
                    thread.start()
                self.assertTrue(both_started.wait(timeout=2))
                release_render.set()
                for thread in threads:
                    thread.join(timeout=2)

            self.assertEqual(set(started), set(paths))


class TrashSafetyTests(unittest.TestCase):
    def test_trash_files_skips_paths_outside_duplicate_groups(self):
        group = dedup.DuplicateGroup(
            "hash",
            (dedup.FileInfo("/tmp/original.txt", 4, 1),),
        )

        with contextlib.redirect_stderr(io.StringIO()):
            result = dedup.trash_files(["/tmp/not-in-group.txt"], [group], dry_run=True)

        self.assertEqual(result.selected, 1)
        self.assertEqual(result.skipped, 1)

    def test_trash_files_revalidates_full_hash_before_dry_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path_a = os.path.join(temp_dir, "a.txt")
            path_b = os.path.join(temp_dir, "copy a.txt")
            for path in (path_a, path_b):
                with open(path, "wb") as file_obj:
                    file_obj.write(b"same")
            digest = dedup.get_full_content_hash(path_a)
            info_a = dedup.FileInfo(path_a, os.path.getsize(path_a), os.stat(path_a).st_mtime_ns)
            info_b = dedup.FileInfo(path_b, os.path.getsize(path_b), os.stat(path_b).st_mtime_ns)
            group = dedup.DuplicateGroup(digest, (info_a, info_b), dedup.FULL_HASH_NAME)

            with mock.patch.object(
                dedup,
                "get_full_content_hash",
                wraps=dedup.get_full_content_hash,
            ) as mocked_hash:
                with contextlib.redirect_stdout(io.StringIO()):
                    result = dedup.trash_files([path_b], [group], dry_run=True)

            self.assertEqual(result.skipped, 0)
            self.assertTrue(mocked_hash.called)

    def test_revalidate_file_rejects_changed_content_with_restored_mtime(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "copy.txt")
            with open(path, "wb") as file_obj:
                file_obj.write(b"same")
            file_stat = os.stat(path)
            expected_hash = dedup.get_full_content_hash(path)

            with open(path, "wb") as file_obj:
                file_obj.write(b"diff")
            os.utime(path, ns=(file_stat.st_atime_ns, file_stat.st_mtime_ns))

            valid, reason = dedup.revalidate_file(
                path,
                file_stat.st_size,
                file_stat.st_mtime_ns,
                expected_hash,
                dedup.FULL_HASH_NAME,
            )

        self.assertFalse(valid)
        self.assertEqual(reason, "hash changed")

    def test_revalidate_file_passes_when_mtime_changes_but_content_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "copy.txt")
            with open(path, "wb") as file_obj:
                file_obj.write(b"same")
            file_stat = os.stat(path)
            expected_hash = dedup.get_full_content_hash(path)
            os.utime(path, ns=(file_stat.st_atime_ns, file_stat.st_mtime_ns + 1_000_000_000))

            valid, reason = dedup.revalidate_file(
                path,
                file_stat.st_size,
                file_stat.st_mtime_ns,
                expected_hash,
                dedup.FULL_HASH_NAME,
            )

        self.assertTrue(valid)
        self.assertEqual(reason, "")

    def test_fast_mode_selection_requires_exact_kept_duplicate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path_a = os.path.join(temp_dir, "a.bin")
            path_b = os.path.join(temp_dir, "b.bin")
            size = dedup.SMALL_FILE_FULL_HASH_BYTES + dedup.FAST_SAMPLE_BYTES * 3
            body_a = bytearray(b"x" * size)
            body_b = bytearray(body_a)
            body_b[dedup.FAST_SAMPLE_BYTES + 123] = ord("y")
            for path, body in ((path_a, body_a), (path_b, body_b)):
                with open(path, "wb") as file_obj:
                    file_obj.write(body)
            stat_a = os.stat(path_a)
            stat_b = os.stat(path_b)
            sparse_hash = dedup.get_fast_multichunk_hash(path_a, stat_a.st_size)
            self.assertEqual(sparse_hash, dedup.get_fast_multichunk_hash(path_b, stat_b.st_size))
            group = dedup.DuplicateGroup(
                sparse_hash,
                (
                    dedup.FileInfo(path_a, stat_a.st_size, stat_a.st_mtime_ns),
                    dedup.FileInfo(path_b, stat_b.st_size, stat_b.st_mtime_ns),
                ),
                "fast-test",
            )

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = dedup.trash_files([path_b], [group], dry_run=True)

        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.trashed, 0)

    def test_full_hash_preloader_restricts_to_selected_paths_and_peers(self):
        paths = [f"/tmp/f{i}.bin" for i in range(3)]
        group = dedup.DuplicateGroup(
            "sparse",
            tuple(dedup.FileInfo(path, 10, idx) for idx, path in enumerate(paths)),
            "fast-test",
        )

        self.assertEqual(
            dedup.exact_hash_paths_for_selection([paths[1]], [group]),
            {paths[0], paths[1], paths[2]},
        )

        preloader = dedup.FullHashPreloader([group])
        preloader.restrict_to({paths[1]})
        self.assertEqual(list(preloader.pending.keys()), [paths[1]])

    def test_full_hash_preloader_recomputes_stale_cached_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "file.bin")
            with open(path, "wb") as file_obj:
                file_obj.write(b"old")
            preloader = dedup.FullHashPreloader([])
            old_stat = os.stat(path)
            preloader.cache[path] = (old_stat.st_size, old_stat.st_mtime_ns, "stale")

            with open(path, "wb") as file_obj:
                file_obj.write(b"new content")

            expected = dedup.get_full_content_hash(path)
            digest = preloader.get(path)

        self.assertNotEqual(digest, "stale")
        self.assertEqual(digest, expected)

    def test_trash_files_uses_trash_cmd_fallback_for_macos_volume(self):
        path = "/Volumes/Storage/Movies/test.mp4"
        group = dedup.DuplicateGroup(
            "hash",
            (dedup.FileInfo(path, 4, 1),),
        )
        send_to_trash = mock.Mock(side_effect=OSError("Directory not found"))

        with mock.patch.object(dedup, "CURRENT_OS", dedup.OS_MACOS):
            with mock.patch.object(dedup, "MACOS_TRASH_CMD", "/usr/bin/trash"):
                with mock.patch.object(dedup, "load_send_to_trash", return_value=send_to_trash):
                    with mock.patch.object(dedup, "revalidate_file", return_value=(True, "")):
                        with mock.patch.object(dedup, "move_to_trash_with_cmd") as trash_cmd:
                            with contextlib.redirect_stdout(io.StringIO()):
                                result = dedup.trash_files([path], [group], dry_run=False)

        self.assertEqual(result.trashed, 1)
        self.assertEqual(result.errors, 0)
        send_to_trash.assert_called_once_with(path)
        trash_cmd.assert_called_once_with(path)

    def test_trash_cmd_checks_symlink_before_subprocess(self):
        path = "/Volumes/Storage/Movies/a file.mp4"
        calls = []

        def guard(candidate):
            calls.append(("guard", candidate))

        def run_command(*args, **kwargs):
            calls.append(("run", args[0]))
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(dedup, "MACOS_TRASH_CMD", "/usr/bin/trash"):
            with mock.patch.object(dedup, "_ensure_no_symlink_replacement", side_effect=guard) as guard_mock:
                with mock.patch.object(dedup.subprocess, "run", side_effect=run_command):
                    dedup.move_to_trash_with_cmd(path)

        guard_mock.assert_called_once_with(path)
        self.assertEqual(calls, [("guard", path), ("run", ["/usr/bin/trash", path])])

    def test_move_to_trash_safely_aborts_symlink_before_fallbacks(self):
        path = "/Volumes/Storage/Movies/test.mp4"
        send_to_trash = mock.Mock()

        with mock.patch.object(
            dedup,
            "_ensure_no_symlink_replacement",
            side_effect=dedup.SymlinkReplacementError("symlink"),
        ) as guard:
            with mock.patch.object(dedup, "move_to_trash_with_cmd") as trash_cmd:
                with mock.patch.object(dedup, "find_nas_recycle_root") as recycle:
                    with self.assertRaises(dedup.SymlinkReplacementError):
                        dedup.move_to_trash_safely(path, send_to_trash)

        guard.assert_called_once_with(path)
        send_to_trash.assert_not_called()
        trash_cmd.assert_not_called()
        recycle.assert_not_called()

    def test_move_to_trash_safely_reports_no_trash_on_any_platform(self):
        cases = (
            (dedup.OS_LINUX, "/Volumes/Storage/Movies/test.mp4"),
            (dedup.OS_MACOS, "/tmp/test.mp4"),
        )

        for current_os, path in cases:
            with self.subTest(current_os=current_os, path=path):
                send_to_trash = mock.Mock(side_effect=OSError("Directory not found"))
                with mock.patch.object(dedup, "CURRENT_OS", current_os):
                    with mock.patch.object(dedup, "move_to_trash_with_cmd") as trash_cmd:
                        with self.assertRaises(dedup.VolumeHasNoTrashError):
                            dedup.move_to_trash_safely(path, send_to_trash)

                send_to_trash.assert_called_once_with(path)
                trash_cmd.assert_not_called()

    def test_move_to_trash_safely_uses_existing_nas_recycle_folder(self):
        send_to_trash = mock.Mock(side_effect=OSError("Directory not found"))
        with tempfile.TemporaryDirectory() as volume:
            os.mkdir(os.path.join(volume, "#recycle"))
            file_path = os.path.join(volume, "driver", "f.zip")
            os.makedirs(os.path.dirname(file_path))
            with open(file_path, "wb") as fh:
                fh.write(b"x")

            with mock.patch.object(dedup, "CURRENT_OS", dedup.OS_MACOS):
                with mock.patch.object(dedup, "is_macos_external_volume_path", return_value=True):
                    with mock.patch.object(dedup, "get_macos_volume_root", return_value=volume):
                        with mock.patch.object(
                            dedup, "move_to_trash_with_cmd", side_effect=OSError("volume has no trash")
                        ):
                            method = dedup.move_to_trash_safely(file_path, send_to_trash)

            self.assertEqual(method, "nas-recycle")
            self.assertTrue(os.path.isfile(os.path.join(volume, "#recycle", "driver", "f.zip")))
            self.assertFalse(os.path.exists(file_path))

    def test_move_to_trash_safely_raises_volume_has_no_trash_when_no_recycle(self):
        send_to_trash = mock.Mock(side_effect=OSError("Directory not found"))
        with tempfile.TemporaryDirectory() as volume:
            file_path = os.path.join(volume, "f.zip")
            with open(file_path, "wb") as fh:
                fh.write(b"x")

            with mock.patch.object(dedup, "CURRENT_OS", dedup.OS_MACOS):
                with mock.patch.object(dedup, "is_macos_external_volume_path", return_value=True):
                    with mock.patch.object(dedup, "get_macos_volume_root", return_value=volume):
                        with mock.patch.object(
                            dedup, "move_to_trash_with_cmd", side_effect=OSError("volume has no trash")
                        ):
                            with self.assertRaises(dedup.VolumeHasNoTrashError) as ctx:
                                dedup.move_to_trash_safely(file_path, send_to_trash)

            self.assertEqual(ctx.exception.volume_root, volume)
            self.assertIn("Directory not found", str(ctx.exception))
            self.assertIn("volume has no trash", str(ctx.exception))
            self.assertTrue(os.path.isfile(file_path))

    def test_trash_files_permanently_deletes_with_flag(self):
        path = "/Volumes/Storage/Movies/test.mp4"
        group = dedup.DuplicateGroup("hash", (dedup.FileInfo(path, 4, 1),))
        send_to_trash = mock.Mock(side_effect=OSError("Directory not found"))

        with mock.patch.object(dedup, "CURRENT_OS", dedup.OS_MACOS):
            with mock.patch.object(dedup, "MACOS_TRASH_CMD", "/usr/bin/trash"):
                with mock.patch.object(dedup, "load_send_to_trash", return_value=send_to_trash):
                    with mock.patch.object(dedup, "revalidate_file", return_value=(True, "")):
                        with mock.patch.object(
                            dedup, "move_to_trash_with_cmd", side_effect=OSError("volume has no trash")
                        ):
                            with mock.patch.object(dedup, "find_nas_recycle_root", return_value=None):
                                with mock.patch.object(dedup.os, "remove") as os_remove:
                                    prompt = mock.Mock()
                                    with contextlib.redirect_stdout(io.StringIO()):
                                        result = dedup.trash_files(
                                            [path], [group], dry_run=False,
                                            permanent_on_no_trash=True,
                                            interactive=False,
                                            prompt_func=prompt,
                                        )

        self.assertEqual(result.permanently_deleted, 1)
        self.assertEqual(result.trashed, 0)
        self.assertEqual(result.errors, 0)
        os_remove.assert_called_once_with(path)
        prompt.assert_not_called()

    def test_trash_files_prompts_once_per_volume_in_interactive_mode(self):
        paths = [
            "/Volumes/Storage/a.zip",
            "/Volumes/Storage/sub/b.zip",
            "/Volumes/Other/c.zip",
        ]
        group = dedup.DuplicateGroup(
            "hash", tuple(dedup.FileInfo(p, 4, 1) for p in paths)
        )
        send_to_trash = mock.Mock(side_effect=OSError("Directory not found"))

        with mock.patch.object(dedup, "CURRENT_OS", dedup.OS_MACOS):
            with mock.patch.object(dedup, "MACOS_TRASH_CMD", "/usr/bin/trash"):
                with mock.patch.object(dedup, "load_send_to_trash", return_value=send_to_trash):
                    with mock.patch.object(dedup, "revalidate_file", return_value=(True, "")):
                        with mock.patch.object(
                            dedup, "move_to_trash_with_cmd", side_effect=OSError("volume has no trash")
                        ):
                            with mock.patch.object(dedup, "find_nas_recycle_root", return_value=None):
                                with mock.patch.object(dedup.os, "remove"):
                                    prompt = mock.Mock(return_value="permanent")
                                    with contextlib.redirect_stdout(io.StringIO()):
                                        dedup.trash_files(
                                            paths, [group], dry_run=False,
                                            interactive=True, prompt_func=prompt,
                                        )

        self.assertEqual(prompt.call_count, 2)
        prompt_volumes = {call.args[0] for call in prompt.call_args_list}
        self.assertEqual(prompt_volumes, {"/Volumes/Storage", "/Volumes/Other"})
        storage_call = next(c for c in prompt.call_args_list if c.args[0] == "/Volumes/Storage")
        self.assertEqual(
            set(storage_call.args[1]),
            {"/Volumes/Storage/a.zip", "/Volumes/Storage/sub/b.zip"},
        )

    def test_trash_files_skips_when_prompt_declines(self):
        path = "/Volumes/Storage/Movies/test.mp4"
        group = dedup.DuplicateGroup("hash", (dedup.FileInfo(path, 4, 1),))
        send_to_trash = mock.Mock(side_effect=OSError("Directory not found"))

        with mock.patch.object(dedup, "CURRENT_OS", dedup.OS_MACOS):
            with mock.patch.object(dedup, "MACOS_TRASH_CMD", "/usr/bin/trash"):
                with mock.patch.object(dedup, "load_send_to_trash", return_value=send_to_trash):
                    with mock.patch.object(dedup, "revalidate_file", return_value=(True, "")):
                        with mock.patch.object(
                            dedup, "move_to_trash_with_cmd", side_effect=OSError("volume has no trash")
                        ):
                            with mock.patch.object(dedup, "find_nas_recycle_root", return_value=None):
                                with mock.patch.object(dedup.os, "remove") as os_remove:
                                    with mock.patch.object(dedup, "move_to_local_trash") as local_trash:
                                        prompt = mock.Mock(return_value="skip")
                                        with contextlib.redirect_stderr(io.StringIO()):
                                            result = dedup.trash_files(
                                                [path], [group], dry_run=False,
                                                interactive=True, prompt_func=prompt,
                                            )

        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.trashed, 0)
        self.assertEqual(result.permanently_deleted, 0)
        self.assertEqual(result.errors, 0)
        os_remove.assert_not_called()
        local_trash.assert_not_called()

    def test_trash_files_yes_mode_skips_without_flags(self):
        path = "/Volumes/Storage/Movies/test.mp4"
        group = dedup.DuplicateGroup("hash", (dedup.FileInfo(path, 4, 1),))
        send_to_trash = mock.Mock(side_effect=OSError("Directory not found"))

        with mock.patch.object(dedup, "CURRENT_OS", dedup.OS_MACOS):
            with mock.patch.object(dedup, "MACOS_TRASH_CMD", "/usr/bin/trash"):
                with mock.patch.object(dedup, "load_send_to_trash", return_value=send_to_trash):
                    with mock.patch.object(dedup, "revalidate_file", return_value=(True, "")):
                        with mock.patch.object(
                            dedup, "move_to_trash_with_cmd", side_effect=OSError("volume has no trash")
                        ):
                            with mock.patch.object(dedup, "find_nas_recycle_root", return_value=None):
                                with mock.patch.object(dedup.os, "remove") as os_remove:
                                    with mock.patch.object(dedup, "move_to_local_trash") as local_trash:
                                        prompt = mock.Mock()
                                        with contextlib.redirect_stderr(io.StringIO()):
                                            result = dedup.trash_files(
                                                [path], [group], dry_run=False,
                                                interactive=False, prompt_func=prompt,
                                            )

        self.assertEqual(result.skipped, 1)
        os_remove.assert_not_called()
        local_trash.assert_not_called()
        prompt.assert_not_called()

    def test_trash_files_yes_mode_with_allow_slow_local_trash_uses_local(self):
        path = "/Volumes/Storage/Movies/test.mp4"
        group = dedup.DuplicateGroup("hash", (dedup.FileInfo(path, 4, 1),))
        send_to_trash = mock.Mock(side_effect=OSError("Directory not found"))

        with mock.patch.object(dedup, "CURRENT_OS", dedup.OS_MACOS):
            with mock.patch.object(dedup, "MACOS_TRASH_CMD", "/usr/bin/trash"):
                with mock.patch.object(dedup, "load_send_to_trash", return_value=send_to_trash):
                    with mock.patch.object(dedup, "revalidate_file", return_value=(True, "")):
                        with mock.patch.object(
                            dedup, "move_to_trash_with_cmd", side_effect=OSError("volume has no trash")
                        ):
                            with mock.patch.object(dedup, "find_nas_recycle_root", return_value=None):
                                with mock.patch.object(dedup, "move_to_local_trash") as local_trash:
                                    with contextlib.redirect_stdout(io.StringIO()):
                                        result = dedup.trash_files(
                                            [path], [group], dry_run=False,
                                            interactive=False,
                                            allow_slow_local_trash=True,
                                        )

        self.assertEqual(result.trashed, 1)
        local_trash.assert_called_once_with(path)

    def test_trash_files_interactive_allow_slow_local_trash_can_choose_local(self):
        path = "/Volumes/Storage/Movies/test.mp4"
        group = dedup.DuplicateGroup("hash", (dedup.FileInfo(path, 4, 1),))
        send_to_trash = mock.Mock(side_effect=OSError("Directory not found"))

        with mock.patch.object(dedup, "CURRENT_OS", dedup.OS_MACOS):
            with mock.patch.object(dedup, "MACOS_TRASH_CMD", "/usr/bin/trash"):
                with mock.patch.object(dedup, "load_send_to_trash", return_value=send_to_trash):
                    with mock.patch.object(dedup, "revalidate_file", return_value=(True, "")):
                        with mock.patch.object(
                            dedup, "move_to_trash_with_cmd", side_effect=OSError("volume has no trash")
                        ):
                            with mock.patch.object(dedup, "find_nas_recycle_root", return_value=None):
                                with mock.patch.object(dedup, "move_to_local_trash") as local_trash:
                                    prompt = mock.Mock(return_value="local")
                                    with contextlib.redirect_stdout(io.StringIO()):
                                        result = dedup.trash_files(
                                            [path], [group], dry_run=False,
                                            interactive=True,
                                            allow_slow_local_trash=True,
                                            prompt_func=prompt,
                                        )

        self.assertEqual(result.trashed, 1)
        self.assertEqual(result.errors, 0)
        local_trash.assert_called_once_with(path)
        prompt.assert_called_once()
        self.assertTrue(prompt.call_args.kwargs["allow_slow_local_trash"])

    def test_trash_cmd_passes_path_as_list_argument(self):
        path = "/Volumes/Storage/Movies/a file 'quoted'.mp4"

        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(dedup, "MACOS_TRASH_CMD", "/usr/bin/trash"):
            with mock.patch.object(dedup.subprocess, "run", return_value=completed) as run_command:
                dedup.move_to_trash_with_cmd(path)

        run_args, run_kwargs = run_command.call_args
        self.assertIsInstance(run_args[0], list)
        self.assertEqual(run_args[0], ["/usr/bin/trash", path])
        self.assertNotIn("shell", run_kwargs)
        self.assertTrue(run_kwargs["capture_output"])
        self.assertTrue(run_kwargs["text"])


class VolumeHelperTests(unittest.TestCase):
    def _make_file(self, volume, *parts):
        file_path = os.path.join(volume, *parts)
        parent = os.path.dirname(file_path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(file_path, "wb") as file_obj:
            file_obj.write(b"x")
        return file_path

    def test_get_macos_volume_root(self):
        self.assertEqual(
            dedup.get_macos_volume_root("/Volumes/Storage/driver/test.zip"),
            "/Volumes/Storage",
        )
        self.assertIsNone(dedup.get_macos_volume_root("/tmp/foo.zip"))
        self.assertIsNone(dedup.get_macos_volume_root("/Volumes"))

    def test_find_nas_recycle_root_picks_existing_synology_folder(self):
        with tempfile.TemporaryDirectory() as volume:
            os.mkdir(os.path.join(volume, "#recycle"))
            self.assertEqual(
                dedup.find_nas_recycle_root(volume),
                os.path.join(volume, "#recycle"),
            )

    def test_find_nas_recycle_root_picks_qnap_folder(self):
        with tempfile.TemporaryDirectory() as volume:
            os.mkdir(os.path.join(volume, "@Recycle"))
            self.assertEqual(
                dedup.find_nas_recycle_root(volume),
                os.path.join(volume, "@Recycle"),
            )

    def test_find_nas_recycle_root_returns_none_when_missing(self):
        with tempfile.TemporaryDirectory() as volume:
            self.assertIsNone(dedup.find_nas_recycle_root(volume))

    def test_find_nas_recycle_root_does_not_create(self):
        with tempfile.TemporaryDirectory() as volume:
            dedup.find_nas_recycle_root(volume)
            self.assertEqual(os.listdir(volume), [])

    def test_move_to_nas_recycle_preserves_directory_structure(self):
        with tempfile.TemporaryDirectory() as volume:
            recycle = os.path.join(volume, "#recycle")
            os.mkdir(recycle)
            file_path = self._make_file(volume, "deep", "nested", "f.zip")
            dest = dedup.move_to_nas_recycle(file_path, recycle, volume)
            self.assertEqual(dest, os.path.join(recycle, "deep", "nested", "f.zip"))
            self.assertTrue(os.path.isfile(dest))
            self.assertFalse(os.path.exists(file_path))

    def test_move_to_nas_recycle_resolves_collision(self):
        with tempfile.TemporaryDirectory() as volume:
            recycle = os.path.join(volume, "#recycle")
            os.makedirs(os.path.join(recycle, "sub"))
            with open(os.path.join(recycle, "sub", "f.zip"), "wb") as fh:
                fh.write(b"old")
            file_path = self._make_file(volume, "sub", "f.zip")
            dest = dedup.move_to_nas_recycle(file_path, recycle, volume)
            self.assertNotEqual(dest, os.path.join(recycle, "sub", "f.zip"))
            self.assertTrue(os.path.isfile(dest))
            with open(os.path.join(recycle, "sub", "f.zip"), "rb") as fh:
                self.assertEqual(fh.read(), b"old")

    def test_move_to_nas_recycle_resolves_repeated_collision(self):
        with tempfile.TemporaryDirectory() as volume:
            recycle = os.path.join(volume, "#recycle")
            os.makedirs(os.path.join(recycle, "sub"))
            original_dest = os.path.join(recycle, "sub", "f.txt")
            first_collision = os.path.join(recycle, "sub", "f 123.txt")
            with open(original_dest, "wb") as file_obj:
                file_obj.write(b"original recycle")
            with open(first_collision, "wb") as file_obj:
                file_obj.write(b"first collision")
            file_path = self._make_file(volume, "sub", "f.txt")

            with mock.patch.object(dedup.os, "getpid", return_value=123):
                dest = dedup.move_to_nas_recycle(file_path, recycle, volume)

            self.assertEqual(dest, os.path.join(recycle, "sub", "f 123-1.txt"))
            with open(original_dest, "rb") as file_obj:
                self.assertEqual(file_obj.read(), b"original recycle")
            with open(first_collision, "rb") as file_obj:
                self.assertEqual(file_obj.read(), b"first collision")
            with open(dest, "rb") as file_obj:
                self.assertEqual(file_obj.read(), b"x")

    def test_move_to_local_trash_resolves_repeated_collision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            trash_dir = os.path.join(temp_dir, ".Trash")
            os.mkdir(trash_dir)
            original_dest = os.path.join(trash_dir, "f.txt")
            first_collision = os.path.join(trash_dir, "f 123.txt")
            with open(original_dest, "wb") as file_obj:
                file_obj.write(b"original trash")
            with open(first_collision, "wb") as file_obj:
                file_obj.write(b"first collision")
            source = self._make_file(temp_dir, "source", "f.txt")

            with mock.patch.object(dedup.os.path, "expanduser", return_value=trash_dir):
                with mock.patch.object(dedup.os, "getpid", return_value=123):
                    dest = dedup.move_to_local_trash(source)

            self.assertEqual(dest, os.path.join(trash_dir, "f 123-1.txt"))
            with open(original_dest, "rb") as file_obj:
                self.assertEqual(file_obj.read(), b"original trash")
            with open(first_collision, "rb") as file_obj:
                self.assertEqual(file_obj.read(), b"first collision")
            with open(dest, "rb") as file_obj:
                self.assertEqual(file_obj.read(), b"x")
            self.assertFalse(os.path.exists(source))


class EmptyDirTrashTests(unittest.TestCase):
    def make_options(self, path):
        return dedup.ScanOptions(
            path=os.path.abspath(path),
            real_path=os.path.realpath(path),
            ignore_dirs=frozenset(dedup.DEFAULT_IGNORE_DIRS),
            ignore_files=frozenset(dedup.DEFAULT_IGNORE_FILES),
            progress_every=1000000,
        )

    def test_trash_empty_dirs_skips_directory_that_is_no_longer_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            empty_dir = os.path.join(temp_dir, "empty")
            os.mkdir(empty_dir)
            options = self.make_options(temp_dir)

            self.assertEqual(dedup.find_empty_dirs(options), [empty_dir])
            with open(os.path.join(empty_dir, "real.txt"), "wb") as file_obj:
                file_obj.write(b"new")

            with mock.patch.object(dedup, "load_send_to_trash") as load_trash:
                with contextlib.redirect_stderr(io.StringIO()):
                    result = dedup.trash_empty_dirs(
                        [empty_dir],
                        options,
                        dry_run=False,
                    )

            self.assertEqual(result.skipped, 1)
            self.assertEqual(result.trashed, 0)
            load_trash.assert_called_once()
            self.assertTrue(os.path.isdir(empty_dir))

    def test_find_empty_dirs_aborts_when_scan_root_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            options = self.make_options(temp_dir)
            changed_root = os.path.join(temp_dir, "changed")

            with mock.patch.object(dedup.os.path, "realpath", return_value=changed_root):
                with self.assertRaisesRegex(ValueError, "Scan root changed since validation"):
                    dedup.find_empty_dirs(options)

    def test_empty_dir_selection_deduplicates_ancestor_paths(self):
        paths = [
            "/tmp/root/empty",
            "/tmp/root/empty/child",
            "/tmp/root/other",
        ]

        self.assertEqual(
            dedup._deduplicate_by_ancestry(paths),
            ["/tmp/root/empty", "/tmp/root/other"],
        )


class PromptPermanentDeleteTests(unittest.TestCase):
    def test_yes_input_returns_permanent(self):
        for answer in ("y", "Y", "yes", "YES "):
            with self.subTest(answer=answer):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = dedup.prompt_permanent_delete(
                        "/Volumes/Storage", ["/Volumes/Storage/a"],
                        input_func=lambda _, a=answer: a,
                    )
                self.assertEqual(result, "permanent")

    def test_no_or_empty_input_returns_skip(self):
        for answer in ("", "n", "no", "anything"):
            with self.subTest(answer=answer):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = dedup.prompt_permanent_delete(
                        "/Volumes/Storage", ["/Volumes/Storage/a"],
                        input_func=lambda _, a=answer: a,
                    )
                self.assertEqual(result, "skip")

    def test_eof_returns_skip(self):
        def raise_eof(_):
            raise EOFError()

        with contextlib.redirect_stdout(io.StringIO()):
            result = dedup.prompt_permanent_delete(
                "/Volumes/Storage", ["/Volumes/Storage/a"],
                input_func=raise_eof,
            )
        self.assertEqual(result, "skip")

    def test_prompt_lists_files_with_truncation(self):
        files = [f"/Volumes/Storage/f{i}.zip" for i in range(10)]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            dedup.prompt_permanent_delete(
                "/Volumes/Storage", files, input_func=lambda _: "n",
            )
        output = buf.getvalue()
        self.assertIn("/Volumes/Storage/f0.zip", output)
        self.assertIn("/Volumes/Storage/f4.zip", output)
        self.assertNotIn("/Volumes/Storage/f5.zip", output)
        self.assertIn("and 5 more", output)


class JsonCacheTests(unittest.TestCase):
    def test_json_cache_is_pre_serialized(self):
        group = dedup.DuplicateGroup(
            "abc",
            (dedup.FileInfo("/tmp/photo.jpg", 4, 1),),
        )
        state = dedup.BrowserSelectionState([group])
        # _groups_json must be bytes containing valid JSON with the required fields.
        parsed = json.loads(state._groups_json.decode("utf-8"))
        self.assertIsInstance(parsed["groups"], list)
        self.assertFalse(parsed["requireMoveConfirmation"])
        self.assertIsInstance(parsed["sessionId"], str)
        self.assertTrue(parsed["sessionId"])
        self.assertEqual(parsed["totalGroupCount"], 1)

    def test_json_cache_has_require_move_confirmation(self):
        group = dedup.DuplicateGroup(
            "abc",
            (dedup.FileInfo("/tmp/photo.jpg", 4, 1),),
        )
        state = dedup.BrowserSelectionState([group], require_move_confirmation=True)
        parsed = json.loads(state._groups_json.decode("utf-8"))
        self.assertTrue(parsed["requireMoveConfirmation"])


class PaginationTests(unittest.TestCase):
    def setUp(self):
        self.groups = [
            dedup.DuplicateGroup(
                f"hash{i}",
                (dedup.FileInfo(f"/tmp/file{i}.txt", 100, i),),
            )
            for i in range(10)
        ]
        self.state = dedup.BrowserSelectionState(self.groups)
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), dedup.make_browser_handler(self.state))
        except PermissionError:
            self.skipTest("loopback bind not permitted")
        self.port = self.server.server_address[1]
        self.token = self.state.session_id
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_pagination_default_returns_all(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/groups?token={self.token}") as response:
            data = json.loads(response.read().decode("utf-8"))
        self.assertEqual(len(data["groups"]), 10)
        self.assertIn("requireMoveConfirmation", data)
        self.assertIn("sessionId", data)
        self.assertEqual(data["totalGroupCount"], 10)

    def test_pagination_with_offset_and_limit(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/groups?offset=2&limit=3&token={self.token}") as response:
            data = json.loads(response.read().decode("utf-8"))
        self.assertEqual(len(data["groups"]), 3)
        self.assertEqual(data["totalGroupCount"], 10)
        self.assertNotIn("requireMoveConfirmation", data)

    def test_pagination_first_page_has_require_move_confirmation(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/groups?offset=0&limit=5&token={self.token}") as response:
            data = json.loads(response.read().decode("utf-8"))
        self.assertIn("requireMoveConfirmation", data)
        self.assertEqual(data["totalGroupCount"], 10)

    def test_pagination_past_end(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/groups?offset=999999&limit=500&token={self.token}") as response:
            data = json.loads(response.read().decode("utf-8"))
        self.assertEqual(len(data["groups"]), 0)
        self.assertEqual(data["totalGroupCount"], 10)

    def test_pagination_total_group_count_always_present(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/groups?offset=5&limit=3&token={self.token}") as response:
            data = json.loads(response.read().decode("utf-8"))
        self.assertEqual(data["totalGroupCount"], 10)


class RangeRequestTests(unittest.TestCase):

    def _create_temp_file(self, suffix=".jpg", size=1000):
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.write(fd, b"X" * size)
        os.close(fd)
        self.addCleanup(os.unlink, path)
        return path

    def _start_server(self, file_path):
        group = dedup.DuplicateGroup(
            "abc",
            (dedup.FileInfo(file_path, os.path.getsize(file_path), 1),),
        )
        state = dedup.BrowserSelectionState([group])
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), dedup.make_browser_handler(state))
        except PermissionError:
            self.skipTest("loopback bind not permitted")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        server.dedup_token = state.session_id

        def _cleanup():
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.addCleanup(_cleanup)

        base_url = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(f"{base_url}/api/groups?token={state.session_id}") as resp:
            groups = json.loads(resp.read().decode("utf-8"))["groups"]
        file_id = groups[0]["files"][0]["id"]
        return server, thread, file_id

    def test_range_closed_request(self):
        path = self._create_temp_file()
        server, _thread, file_id = self._start_server(path)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            conn.request("GET", f"/media/{file_id}?token={server.dedup_token}", headers={"Range": "bytes=0-99"})
            response = conn.getresponse()
            body = response.read()
            self.assertEqual(response.status, 206)
            self.assertEqual(response.getheader("Content-Range"), "bytes 0-99/1000")
            self.assertEqual(len(body), 100)
            self.assertEqual(body, b"X" * 100)
        finally:
            conn.close()

    def test_range_open_ended(self):
        path = self._create_temp_file()
        server, _thread, file_id = self._start_server(path)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            conn.request("GET", f"/media/{file_id}?token={server.dedup_token}", headers={"Range": "bytes=100-"})
            response = conn.getresponse()
            body = response.read()
            self.assertEqual(response.status, 206)
            self.assertEqual(response.getheader("Content-Range"), "bytes 100-999/1000")
            self.assertEqual(len(body), 900)
            self.assertEqual(body, b"X" * 900)
        finally:
            conn.close()

    def test_range_past_eof(self):
        path = self._create_temp_file()
        server, _thread, file_id = self._start_server(path)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            conn.request("GET", f"/media/{file_id}?token={server.dedup_token}", headers={"Range": "bytes=999999999-"})
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.status, 416)
            self.assertEqual(response.getheader("Content-Range"), "bytes */1000")
        finally:
            conn.close()

    def test_range_invalid_header(self):
        path = self._create_temp_file()
        server, _thread, file_id = self._start_server(path)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            conn.request("GET", f"/media/{file_id}?token={server.dedup_token}", headers={"Range": "invalid"})
            response = conn.getresponse()
            body = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(len(body), 1000)
            self.assertEqual(body, b"X" * 1000)
        finally:
            conn.close()

    def test_range_no_header(self):
        path = self._create_temp_file()
        server, _thread, file_id = self._start_server(path)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            conn.request("GET", f"/media/{file_id}?token={server.dedup_token}")
            response = conn.getresponse()
            body = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Accept-Ranges"), "bytes")
            self.assertEqual(len(body), 1000)
            self.assertEqual(body, b"X" * 1000)
        finally:
            conn.close()

    def test_range_pdf_endpoint(self):
        path = self._create_temp_file(suffix=".pdf", size=500)
        server, _thread, file_id = self._start_server(path)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            conn.request("GET", f"/pdf/{file_id}?token={server.dedup_token}", headers={"Range": "bytes=0-49"})
            response = conn.getresponse()
            body = response.read()
            self.assertEqual(response.status, 206)
            self.assertEqual(response.getheader("Content-Range"), "bytes 0-49/500")
            self.assertEqual(len(body), 50)
        finally:
            conn.close()

    def test_audio_file_served_at_media_endpoint(self):
        path = self._create_temp_file(suffix=".mp3", size=800)
        server, _thread, file_id = self._start_server(path)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            conn.request("GET", f"/media/{file_id}?token={server.dedup_token}")
            response = conn.getresponse()
            body = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(len(body), 800)
            self.assertEqual(response.getheader("Accept-Ranges"), "bytes")
        finally:
            conn.close()

    def test_audio_file_supports_range_requests(self):
        path = self._create_temp_file(suffix=".flac", size=600)
        server, _thread, file_id = self._start_server(path)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            conn.request("GET", f"/media/{file_id}?token={server.dedup_token}", headers={"Range": "bytes=0-99"})
            response = conn.getresponse()
            body = response.read()
            self.assertEqual(response.status, 206)
            self.assertEqual(response.getheader("Content-Range"), "bytes 0-99/600")
            self.assertEqual(len(body), 100)
        finally:
            conn.close()

    def test_non_media_file_rejected_at_media_endpoint(self):
        path = self._create_temp_file(suffix=".pdf", size=400)
        server, _thread, file_id = self._start_server(path)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            conn.request("GET", f"/media/{file_id}?token={server.dedup_token}")
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.status, 404)
        finally:
            conn.close()


class DoubleSubmitGuardTests(unittest.TestCase):
    def _make_state(self):
        group = dedup.DuplicateGroup(
            "abc",
            (dedup.FileInfo("/tmp/a.jpg", 100, 0), dedup.FileInfo("/tmp/b.jpg", 100, 1)),
        )
        return dedup.BrowserSelectionState([group])

    def _start_server(self, state):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), dedup.make_browser_handler(state))
        except PermissionError:
            self.skipTest("loopback bind not permitted")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def _post_selection(self, port, payload, token=None, raw_body=None):
        body = raw_body if raw_body is not None else json.dumps(payload).encode("utf-8")
        suffix = f"?token={token}" if token else ""
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/selection{suffix}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_first_submit_succeeds(self):
        state = self._make_state()
        server, thread = self._start_server(state)
        try:
            status, data = self._post_selection(server.server_address[1], {"cancelled": True, "trashIds": []}, state.session_id)
            self.assertEqual(status, 200)
            self.assertTrue(data["ok"])
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_second_submit_returns_409(self):
        state = self._make_state()
        server, thread = self._start_server(state)
        try:
            port = server.server_address[1]
            self._post_selection(port, {"cancelled": True, "trashIds": []}, state.session_id)
            status, data = self._post_selection(port, {"cancelled": True, "trashIds": []}, state.session_id)
            self.assertEqual(status, 409)
            self.assertFalse(data["ok"])
            self.assertEqual(data["reason"], "already-submitted")
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_malformed_submit_does_not_consume_session(self):
        state = self._make_state()
        server, thread = self._start_server(state)
        try:
            port = server.server_address[1]
            status, data = self._post_selection(port, {}, state.session_id, raw_body=b"{")
            self.assertEqual(status, 400)
            self.assertFalse(data["ok"])
            self.assertFalse(state.done.is_set())

            status, data = self._post_selection(port, {"cancelled": True, "trashIds": []}, state.session_id)
            self.assertEqual(status, 200)
            self.assertTrue(data["ok"])
            self.assertTrue(state.done.is_set())
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_submit_requires_session_token(self):
        state = self._make_state()
        server, thread = self._start_server(state)
        try:
            status, data = self._post_selection(server.server_address[1], {"cancelled": True, "trashIds": []})
            self.assertEqual(status, 403)
            self.assertEqual(data["reason"], "unauthorized")
            self.assertFalse(state.done.is_set())
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_session_id_in_first_api_groups_response(self):
        state = self._make_state()
        server, thread = self._start_server(state)
        try:
            port = server.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/groups?offset=0&limit=500&token={state.session_id}") as resp:
                data = json.loads(resp.read())
            self.assertIn("sessionId", data)
            self.assertEqual(data["sessionId"], state.session_id)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


class CloudPlaceholderTests(unittest.TestCase):
    def _stat_with_flags(self, st_flags):
        s = mock.MagicMock()
        s.st_flags = st_flags
        return s

    def test_sf_dataless_flag_detected_on_macos(self):
        s = self._stat_with_flags(dedup._SF_DATALESS)
        with mock.patch.object(dedup, "CURRENT_OS", dedup.OS_MACOS):
            self.assertTrue(dedup._is_cloud_placeholder(s))

    def test_normal_file_is_not_placeholder(self):
        s = self._stat_with_flags(0)
        with mock.patch.object(dedup, "CURRENT_OS", dedup.OS_MACOS):
            self.assertFalse(dedup._is_cloud_placeholder(s))

    def test_other_flags_not_triggered(self):
        # A flag that isn't SF_DATALESS must not cause a false positive.
        s = self._stat_with_flags(0x00000100)
        with mock.patch.object(dedup, "CURRENT_OS", dedup.OS_MACOS):
            self.assertFalse(dedup._is_cloud_placeholder(s))

    def test_returns_false_on_non_macos(self):
        s = self._stat_with_flags(dedup._SF_DATALESS)
        with mock.patch.object(dedup, "CURRENT_OS", dedup.OS_LINUX):
            self.assertFalse(dedup._is_cloud_placeholder(s))

    def test_windows_recall_attribute_detected(self):
        s = mock.MagicMock()
        s.st_file_attributes = dedup._FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
        with mock.patch.object(dedup, "CURRENT_OS", dedup.OS_WINDOWS):
            self.assertTrue(dedup._is_cloud_placeholder(s))

    def test_missing_st_flags_attribute_returns_false(self):
        # On platforms where st_flags isn't present, getattr fallback must not raise.
        s = mock.MagicMock(spec=[])
        with mock.patch.object(dedup, "CURRENT_OS", dedup.OS_MACOS):
            self.assertFalse(dedup._is_cloud_placeholder(s))


class HardlinkDetectionTests(unittest.TestCase):
    def _group(self, files):
        return dedup.DuplicateGroup("deadbeef", tuple(files))

    def test_shared_inode_flagged_as_hardlink(self):
        files = [
            dedup.FileInfo("/a/img1.jpg", 100, 0, st_dev=5, st_ino=42),
            dedup.FileInfo("/b/img2.jpg", 100, 0, st_dev=5, st_ino=42),
        ]
        result = dedup.build_browser_payload([self._group(files)])["groups"][0]["files"]
        self.assertTrue(result[0]["isHardlink"])
        self.assertTrue(result[1]["isHardlink"])
        self.assertFalse(result[0]["defaultTrash"])
        self.assertFalse(result[1]["defaultTrash"])

    def test_different_inodes_not_hardlinks(self):
        files = [
            dedup.FileInfo("/a/img1.jpg", 100, 0, st_dev=5, st_ino=42),
            dedup.FileInfo("/b/img2.jpg", 100, 0, st_dev=5, st_ino=43),
        ]
        result = dedup.build_browser_payload([self._group(files)])["groups"][0]["files"]
        self.assertFalse(result[0]["isHardlink"])
        self.assertFalse(result[1]["isHardlink"])

    def test_zero_inode_sentinel_not_treated_as_hardlink(self):
        # st_dev=0, st_ino=0 is the default "unknown" value — two files both
        # having it must NOT be reported as hardlinks of each other.
        files = [
            dedup.FileInfo("/a/img1.jpg", 100, 0, st_dev=0, st_ino=0),
            dedup.FileInfo("/b/img2.jpg", 100, 0, st_dev=0, st_ino=0),
        ]
        result = dedup.build_browser_payload([self._group(files)])["groups"][0]["files"]
        self.assertFalse(result[0]["isHardlink"])
        self.assertFalse(result[1]["isHardlink"])

    def test_hardlink_key_includes_device(self):
        # Same inode number on different devices must not be treated as hardlinks.
        files = [
            dedup.FileInfo("/a/img1.jpg", 100, 0, st_dev=5, st_ino=42),
            dedup.FileInfo("/b/img2.jpg", 100, 0, st_dev=6, st_ino=42),
        ]
        result = dedup.build_browser_payload([self._group(files)])["groups"][0]["files"]
        self.assertFalse(result[0]["isHardlink"])
        self.assertFalse(result[1]["isHardlink"])


class SendToTrashStartupTests(unittest.TestCase):
    def test_missing_send2trash_aborts_before_scan(self):
        # ImportError must be caught immediately; scan must never start.
        with mock.patch("dedup.load_send_to_trash", side_effect=ImportError):
            with tempfile.TemporaryDirectory() as d:
                result = dedup.find_and_process_duplicates([d])
        self.assertEqual(result, 1)

    def test_dry_run_skips_send2trash_check(self):
        # --dry-run never moves files so the import check must be skipped entirely.
        with mock.patch("dedup.load_send_to_trash", side_effect=ImportError):
            with tempfile.TemporaryDirectory() as d:
                result = dedup.find_and_process_duplicates(["--dry-run", d])
        self.assertEqual(result, 0)


class PhotoLibraryConfirmTests(unittest.TestCase):
    def _make_library(self, parent):
        lib = os.path.join(parent, "Photos Library.photoslibrary")
        os.makedirs(lib)
        return lib

    def test_non_interactive_stdin_aborts(self):
        with tempfile.TemporaryDirectory() as d:
            lib = self._make_library(d)
            with mock.patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = False
                result = dedup.find_and_process_duplicates(
                    ["--dry-run", "--allow-photo-library", lib]
                )
        self.assertEqual(result, 2)

    def test_wrong_phrase_aborts(self):
        with tempfile.TemporaryDirectory() as d:
            lib = self._make_library(d)
            with mock.patch("sys.stdin") as mock_stdin, \
                 mock.patch("builtins.input", return_value="yes"):
                mock_stdin.isatty.return_value = True
                result = dedup.find_and_process_duplicates(
                    ["--dry-run", "--allow-photo-library", lib]
                )
        self.assertEqual(result, 2)

    def test_correct_phrase_proceeds(self):
        with tempfile.TemporaryDirectory() as d:
            lib = self._make_library(d)
            with mock.patch("sys.stdin") as mock_stdin, \
                 mock.patch("builtins.input", return_value="I have a backup"):
                mock_stdin.isatty.return_value = True
                result = dedup.find_and_process_duplicates(
                    ["--dry-run", "--allow-photo-library", lib]
                )
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
