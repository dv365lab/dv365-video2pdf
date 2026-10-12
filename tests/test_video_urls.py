import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import video2pdfslides


class VideoUrlTests(unittest.TestCase):
    def test_local_paths_do_not_invoke_downloader(self):
        for source in (r"F:\Video Files\lesson.1.mp4", "input/sample.avi"):
            with self.subTest(source=source), patch.object(video2pdfslides.shutil, "which") as which:
                self.assertEqual(video2pdfslides.resolve_video_source(source), source)
                which.assert_not_called()

    def test_http_urls_are_downloaded_before_processing(self):
        for source in ("https://www.youtube.com/watch?v=example&list=123", "HTTP://example.com/video.mp4"):
            with self.subTest(source=source), patch.object(video2pdfslides, "download_video", return_value="input/downloads/sample.mp4") as download:
                self.assertEqual(video2pdfslides.resolve_video_source(source), "input/downloads/sample.mp4")
                download.assert_called_once_with(source, referer=None, cookies_from_browser=None, add_headers=None)

    def test_invalid_urls_fail_without_downloading(self):
        for source in ("https://", "https:///video.mp4", "ftp://example.com/video.mp4"):
            with self.subTest(source=source), patch.object(video2pdfslides, "download_video") as download:
                with self.assertRaises(ValueError):
                    video2pdfslides.resolve_video_source(source)
                download.assert_not_called()

    def test_missing_downloader_has_installation_hint(self):
        with patch.object(video2pdfslides, "PROJECT_DOWNLOADER", Path(__file__).parent / 'missing-yt-dlp.exe'), \
                patch.object(video2pdfslides.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "yt-dlp"):
                video2pdfslides.download_video("https://example.com/video.mp4")

    def test_download_returns_actual_file_and_preserves_url_argument(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            folder = Path(temp).resolve()
            downloaded = folder / "downloaded.video.mp4"
            downloaded.write_bytes(b"video")
            url = "https://example.com/video?id=1&name=lesson%20one!"

            def run(command, **kwargs):
                import json
                self.assertEqual(command[-1], url)
                self.assertFalse(kwargs.get("shell", False))
                self.assertIn("--ignore-config", command)
                self.assertIn("--no-playlist", command)
                result_file = Path(command[command.index("--print-to-file") + 2])
                result_file.write_text(json.dumps(str(downloaded)) + "\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0)

            with patch.object(video2pdfslides, "get_downloader", return_value="yt-dlp.exe"), \
                    patch.object(video2pdfslides.subprocess, "run", side_effect=run):
                self.assertEqual(video2pdfslides.download_video(url, folder), str(downloaded))

    def test_download_failure_or_missing_result_never_returns_a_video(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            for result in (subprocess.CalledProcessError(1, ["yt-dlp"]), None):
                with self.subTest(result=result), \
                        patch.object(video2pdfslides, "get_downloader", return_value="yt-dlp.exe"), \
                        patch.object(video2pdfslides.subprocess, "run", side_effect=result):
                    with self.assertRaises(RuntimeError):
                        video2pdfslides.download_video("https://example.com/video.mp4", temp)

    def test_cli_url_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            folder = Path(temp).resolve()
            output = folder / "output"
            output.mkdir()
            original = output / "existing.pdf"
            original.write_bytes(b"existing result")
            env = os.environ.copy()
            env["PATH"] = ""
            script = folder / 'video2pdfslides.py'
            script.write_bytes(Path(video2pdfslides.__file__).read_bytes())
            result = subprocess.run(
                [sys.executable, str(script), "https://example.com/video.mp4"],
                cwd=folder, env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=15,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("yt-dlp", result.stderr)
            self.assertNotIn("usage:", result.stderr)
            self.assertEqual(list(output.iterdir()), [original])
            self.assertEqual(original.read_bytes(), b"existing result")


if __name__ == "__main__":
    unittest.main()
