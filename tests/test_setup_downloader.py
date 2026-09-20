import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import setup_downloader


class SetupDownloaderTests(unittest.TestCase):
    def manifest(self, content):
        digest = hashlib.sha256(content).hexdigest()
        return io.BytesIO(f"{'0' * 64}  yt-dlp\n{digest}  yt-dlp.exe\n".encode("ascii"))

    def test_verified_download_atomically_replaces_existing_tool(self):
        content = b"verified Windows downloader"
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            destination = folder / "yt-dlp.exe"
            destination.write_bytes(b"existing downloader")
            with patch.object(setup_downloader.urllib.request, "urlopen", side_effect=[
                self.manifest(content), io.BytesIO(content),
            ]) as urlopen:
                result = setup_downloader.install_downloader(folder)

            self.assertEqual(result, destination)
            self.assertEqual(destination.read_bytes(), content)
            self.assertEqual(list(folder.iterdir()), [destination])
            self.assertEqual([call.args[0] for call in urlopen.call_args_list], [
                "https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/SHA2-256SUMS",
                "https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/yt-dlp.exe",
            ])

    def test_hash_mismatch_preserves_existing_tool_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            destination = folder / "yt-dlp.exe"
            destination.write_bytes(b"existing downloader")
            with patch.object(setup_downloader.urllib.request, "urlopen", side_effect=[
                self.manifest(b"expected content"), io.BytesIO(b"corrupt content"),
            ]):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    setup_downloader.install_downloader(folder)
            self.assertEqual(destination.read_bytes(), b"existing downloader")
            self.assertEqual(list(folder.iterdir()), [destination])

    def test_failed_download_preserves_tool_and_removes_partial_file(self):
        class InterruptedDownload(io.BytesIO):
            def read(self, size=-1):
                if self.tell():
                    raise URLError("connection interrupted")
                return super().read(size)

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            destination = folder / "yt-dlp.exe"
            destination.write_bytes(b"existing downloader")
            with patch.object(setup_downloader.urllib.request, "urlopen", side_effect=[
                self.manifest(b"complete download"), InterruptedDownload(b"partial download"),
            ]):
                with self.assertRaises(URLError):
                    setup_downloader.install_downloader(folder)
            self.assertEqual(destination.read_bytes(), b"existing downloader")
            self.assertEqual(list(folder.iterdir()), [destination])

    def test_missing_checksum_does_not_download_or_replace_tool(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            destination = folder / "yt-dlp.exe"
            destination.write_bytes(b"existing downloader")
            with patch.object(setup_downloader.urllib.request, "urlopen", return_value=io.BytesIO(
                f"{'0' * 64}  yt-dlp_macos\n".encode("ascii")
            )) as urlopen:
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    setup_downloader.install_downloader(folder)
                urlopen.assert_called_once()
            self.assertEqual(destination.read_bytes(), b"existing downloader")
            self.assertEqual(list(folder.iterdir()), [destination])


if __name__ == "__main__":
    unittest.main()
