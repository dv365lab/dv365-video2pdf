import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pikepdf
from PIL import Image

import video2pdfslides


@unittest.skipUnless(video2pdfslides.PROJECT_DOWNLOADER.is_file() or shutil.which('yt-dlp'), 'yt-dlp is not installed')
class UrlDownloadIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        video = self.folder / 'sample.avi'
        writer = video2pdfslides.cv2.VideoWriter(
            str(video), video2pdfslides.cv2.VideoWriter_fourcc(*'MJPG'), 2, (64, 64)
        )
        self.assertTrue(writer.isOpened())
        try:
            frame = np.full((64, 64, 3), 255, dtype=np.uint8)
            frame[20:44, 20:44] = (0, 0, 0)
            for _ in range(8):
                writer.write(frame)
        finally:
            writer.release()

        self.requests = []
        requests = self.requests

        class Handler(SimpleHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                super().do_GET()

            def log_message(self, format, *args):
                pass

        self.server = ThreadingHTTPServer(
            ('127.0.0.1', 0), partial(Handler, directory=str(self.folder))
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f'http://127.0.0.1:{self.server.server_port}/sample.avi?a=1&b=2'
        self.env = os.environ.copy()
        self.env['NO_PROXY'] = self.env['no_proxy'] = '127.0.0.1'
        self.env['PYTHONIOENCODING'] = 'utf-8'

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_http_url_downloads_video_and_creates_readable_png_and_pdf(self):
        result = subprocess.run(
            [sys.executable, str(Path(video2pdfslides.__file__).resolve()), self.url],
            cwd=self.folder, env=self.env, stdin=subprocess.DEVNULL,
            capture_output=True, encoding='utf-8', errors='replace', timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('/sample.avi?a=1&b=2', self.requests)
        downloads = list((self.folder / 'input' / 'downloads').glob('*.avi'))
        self.assertEqual(len(downloads), 1, result.stdout)
        self.assertEqual(downloads[0].read_bytes(), (self.folder / 'sample.avi').read_bytes())
        output = self.folder / 'output' / downloads[0].stem
        screenshots = list(output.glob('*.png'))
        self.assertEqual(len(screenshots), 1, result.stdout)
        with Image.open(screenshots[0]) as screenshot:
            self.assertEqual(screenshot.size, (64, 64))
        with pikepdf.open(str(output) + '.pdf') as document:
            self.assertEqual(len(document.pages), 1)
        self.assertFalse(list((self.folder / 'input' / 'downloads').glob('.download-*')))

    def test_real_downloader_rejects_playlist_live_and_upcoming_before_download(self):
        video = {
            'id': 'sample', 'title': 'sample', 'url': self.url, 'ext': 'avi',
            'vcodec': 'mjpeg', 'acodec': 'none', 'extractor': 'generic',
            'extractor_key': 'Generic', 'webpage_url': self.url,
        }
        cases = {
            'playlist': {
                '_type': 'playlist', 'id': 'lessons', 'title': 'lessons', 'entries': [video],
            },
            'live': dict(video, is_live=True, live_status='is_live'),
            'upcoming': dict(video, is_live=False, live_status='is_upcoming'),
        }
        real_run = subprocess.run
        downloader = video2pdfslides.get_downloader()
        for name, info in cases.items():
            with self.subTest(source=name):
                info_file = self.folder / f'{name}.info.json'
                info_file.write_text(json.dumps(info), encoding='utf-8')
                calls = []

                def run_with_fixture(command, **kwargs):
                    # Keep the application's real options and inject local extractor metadata.
                    command = command[:command.index('--')] + ['--load-info-json', str(info_file)]
                    result = real_run(
                        command, **kwargs, cwd=self.folder, env=self.env,
                        capture_output=True, encoding='utf-8', errors='replace', timeout=30,
                    )
                    calls.append(result)
                    return result

                downloads = self.folder / name
                with patch.object(video2pdfslides, 'get_downloader', return_value=downloader), \
                        patch.object(video2pdfslides.subprocess, 'run', side_effect=run_with_fixture):
                    with self.assertRaisesRegex(RuntimeError, '未下载到视频'):
                        video2pdfslides.download_video(self.url, downloads)
                self.assertEqual(len(calls), 1)
                self.assertIn('does not pass filter', calls[0].stdout + calls[0].stderr)
                self.assertEqual(list(downloads.iterdir()), [])
        self.assertEqual(self.requests, [])


if __name__ == '__main__':
    unittest.main()
