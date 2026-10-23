import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlencode

import cv2
import numpy as np
import pikepdf

import web_ui


class WebUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = web_ui.create_server(port=0, work_root=self.root / "jobs")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def upload(self, name, data, **options):
        query = urlencode(dict(filename=name, **options))
        status, _, body = self.request("POST", "/api/jobs?" + query, data, {
            "Content-Type": "application/octet-stream",
            "X-Upload-Token": self.server.upload_token,
        })
        return status, json.loads(body)

    def post_json(self, path, payload=None):
        status, _, body = self.request('POST', path, json.dumps(payload or {}).encode(), {
            'Content-Type': 'application/json', 'X-Upload-Token': self.server.upload_token,
        })
        return status, json.loads(body)

    def wait_for_job(self, job_id):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status, _, body = self.request("GET", "/api/jobs/" + job_id)
            self.assertEqual(status, 200)
            job = json.loads(body)
            if job["status"] in ("done", "error", "review", "screenshots"):
                return job
            time.sleep(0.05)
        self.fail("conversion did not finish")

    def make_video(self):
        path = self.root / "source.avi"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 2, (64, 64))
        self.assertTrue(writer.isOpened())
        try:
            for _ in range(8):
                writer.write(np.full((64, 64, 3), 255, dtype=np.uint8))
        finally:
            writer.release()
        return path.read_bytes()

    def test_home_serves_local_assets_and_upload_token(self):
        status, _, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(self.server.upload_token.encode(), body)
        for path in ("/app.js", "/style.css"):
            self.assertEqual(self.request("GET", path)[0], 200)

    def test_open_output_uses_fixed_directory_and_requires_token(self):
        with patch.object(web_ui, 'open_folder', create=True) as opened:
            status, result = self.post_json('/api/output/open', {'path': 'C:/Windows'})
            self.assertEqual(status, 200, result)
            opened.assert_called_once_with(web_ui.PROJECT_ROOT / 'output')
            status, _, _ = self.request('POST', '/api/output/open', b'{}')
            self.assertEqual(status, 403)
            self.assertEqual(opened.call_count, 1)

    def test_folder_open_failure_is_reported(self):
        with patch.object(web_ui, 'open_folder', side_effect=OSError('Explorer unavailable'), create=True):
            status, result = self.post_json('/api/output/open')
            self.assertEqual(status, 500)
            self.assertIn('Explorer unavailable', result['error'])

    def test_review_releases_queue_then_composes_remaining_screenshots(self):
        status, job = self.upload('review.avi', self.make_video(), mode='review', sample_interval='0.5')
        self.assertEqual(status, 202, job)
        result = self.wait_for_job(job['id'])
        self.assertEqual(result['status'], 'review', result)
        self.assertIsNone(result['download_url'])
        folder = Path(result['screenshots_path'])
        # Review pauses this job without blocking another conversion.
        status, second = self.upload('next.avi', self.make_video())
        self.assertEqual(self.wait_for_job(second['id'])['status'], 'done')
        with patch.object(web_ui, 'open_folder', create=True) as opened:
            self.assertEqual(self.post_json('/api/jobs/' + job['id'] + '/open')[0], 200)
            opened.assert_called_once_with(folder)
        self.assertEqual(self.post_json('/api/jobs/' + job['id'] + '/review', {'action': 'generate'})[0], 202)
        self.assertEqual(self.wait_for_job(job['id'])['status'], 'done')
        self.assertEqual(self.post_json('/api/jobs/' + job['id'] + '/review', {'action': 'generate'})[0], 409)

    def test_png_only_and_review_keep_do_not_generate_pdf(self):
        for mode in ['png', 'review']:
            status, job = self.upload(mode + '.avi', self.make_video(), mode=mode)
            self.assertEqual(status, 202, job)
            result = self.wait_for_job(job['id'])
            if mode == 'review':
                self.assertEqual(result['status'], 'review')
                self.assertEqual(self.post_json('/api/jobs/' + job['id'] + '/review', {'action': 'keep'})[0], 200)
                result = self.wait_for_job(job['id'])
            self.assertEqual(result['status'], 'screenshots')
            self.assertFalse(Path(result['screenshots_path'] + '.pdf').exists())

    def test_existing_png_folder_rebuilds_in_isolated_output(self):
        from PIL import Image
        source = self.root / '已有截图 [1.1]'
        source.mkdir()
        Image.new('RGB', (64, 64), 'white').save(source / '000.png')
        original = (source / '000.png').read_bytes()
        status, job = self.post_json('/api/sources', {'kind': 'screenshots', 'source': str(source)})
        self.assertEqual(status, 202, job)
        result = self.wait_for_job(job['id'])
        self.assertEqual(result['status'], 'done', result)
        self.assertEqual((source / '000.png').read_bytes(), original)
        self.assertFalse(Path(str(source) + '.pdf').exists())
        self.assertEqual(self.request('GET', result['download_url'])[0], 200)

    def test_local_path_converts_without_copying_video(self):
        original = self.make_video()
        status, job = self.post_json('/api/sources', {
            'kind': 'local', 'source': str(self.root / 'source.avi'),
            'options': {'crop_top': 4, 'mode': 'png', 'sample_interval': 2},
        })
        self.assertEqual(status, 202, job)
        result = self.wait_for_job(job['id'])
        self.assertEqual(result['status'], 'screenshots', result)
        self.assertEqual((self.root / 'source.avi').read_bytes(), original)
        self.assertFalse(list((self.root / 'jobs' / job['id']).glob('*.avi')))

    def test_video_folder_scan_filters_and_does_not_start_jobs(self):
        folder = self.root / '课程 videos'
        folder.mkdir()
        for name in ['02.MP4', '01.avi', 'notes.txt']:
            (folder / name).write_bytes(b'content')
        (folder / 'empty.mp4').touch()
        (folder / 'nested.mp4').mkdir()
        (folder / 'nested.mp4' / 'nested.avi').write_bytes(b'content')
        status, result = self.post_json('/api/video-folder', {'source': f' "{folder}" '})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['videos'], [str(folder / '01.avi'), str(folder / '02.MP4')])
        self.assertEqual(self.server.jobs, {})

    def test_local_sources_api_resolves_both_directory_and_video(self):
        self.make_video()
        for source in [str(self.root / 'source.avi'), str(self.root)]:
            status, result = self.post_json('/api/local-sources', {'source': source})
            self.assertEqual(status, 200, result)
            self.assertEqual(result['videos'], [str(self.root / 'source.avi')])
        self.assertEqual(self.server.jobs, {})
        status, _, _ = self.request('POST', '/api/local-sources', b'{}')
        self.assertEqual(status, 403)

    def test_video_folder_rejects_missing_empty_and_relative_paths(self):
        file = self.root / 'file.mp4'
        file.write_bytes(b'content')
        empty = self.root / 'empty'
        empty.mkdir()
        for source in ['', None, [], 'relative/path', str(file), str(empty), str(self.root / 'missing')]:
            with self.subTest(source=source):
                status, result = self.post_json('/api/video-folder', {'source': source})
                self.assertEqual(status, 400, result)
                self.assertTrue(result['error'])
        self.assertEqual(self.server.jobs, {})

    def test_folder_batch_creates_one_pdf_per_video_and_continues_after_failure(self):
        folder = self.root / '批量 视频'
        folder.mkdir()
        video = self.make_video()
        (folder / '01.avi').write_bytes(video)
        (folder / '02-broken.mp4').write_bytes(b'broken')
        (folder / '03.avi').write_bytes(video)
        status, scan = self.post_json('/api/video-folder', {'source': str(folder)})
        self.assertEqual(status, 200, scan)
        jobs = []
        for source in scan['videos']:
            status, job = self.post_json('/api/sources', {'kind': 'local', 'source': source})
            self.assertEqual(status, 202, job)
            jobs.append(job)
        results = [self.wait_for_job(job['id']) for job in jobs]
        self.assertEqual([job['status'] for job in results], ['done', 'error', 'done'], results)
        for job, name in [(results[0], '01'), (results[2], '03')]:
            pdfs = list((self.root / 'jobs' / job['id'] / 'output').glob('*.pdf'))
            self.assertEqual([pdf.name for pdf in pdfs], [name + '.pdf'])
            with pikepdf.open(pdfs[0]) as document:
                self.assertEqual(len(document.pages), 1)
            self.assertEqual(self.request('GET', job['download_url'])[0], 200)
            self.assertEqual((folder / (name + '.avi')).read_bytes(), video)
            self.assertFalse(list((self.root / 'jobs' / job['id']).glob('*.avi')))
        self.assertEqual(len(list(folder.iterdir())), 3)

    def test_url_and_download_options_are_validated_and_queued(self):
        options = {'referer': 'https://play.vidyard.com/', 'cookies_from_browser': 'edge:Default',
                   'add_headers': ['Authorization: Bearer test'], 'mode': 'review', 'sample_interval': 0.5}
        with patch.object(self.server.executor, 'submit') as submit:
            status, job = self.post_json('/api/sources', {
                'kind': 'url', 'source': 'https://example.com/lesson.mp4?a=1&b=2', 'options': options,
            })
            self.assertEqual(status, 202, job)
            submitted = submit.call_args.args
            self.assertIn('https://example.com/lesson.mp4?a=1&b=2', submitted)
            self.assertEqual(submitted[-1]['add_headers'], options['add_headers'])
            self.assertEqual(submitted[-1]['cookies_from_browser'], 'edge:Default')
        for payload in [
            {'kind': 'url', 'source': 'file:///C:/Windows'},
            {'kind': 'url', 'source': 'https://'},
            {'kind': 'local', 'source': str(self.root / 'missing.mp4')},
            {'kind': 'screenshots', 'source': str(self.root)},
            {'kind': 'url', 'source': 'https://example.com', 'options': {'referer': 'invalid'}},
            {'kind': 'url', 'source': 'https://example.com', 'options': {'add_headers': ['broken']}},
        ]:
            with self.subTest(payload=payload):
                self.assertEqual(self.post_json('/api/sources', payload)[0], 400)

    def test_download_settings_and_sampling_reach_cli_as_separate_arguments(self):
        options = web_ui.parse_options({'referer': 'https://example.com/page?a=1&b=2',
            'cookies_from_browser': 'edge:Default', 'add_headers': ['User-Agent: Example Test'],
            'sample_interval': 0.5, 'remove_qr': True})
        job_id = self.server.add_job('url test', 'url')
        with patch.object(self.server, 'run_command') as run, patch.object(self.server, 'finish_pdf'):
            self.server.convert(job_id, 'https://example.com/lesson?a=1&b=2', options)
        command = run.call_args.args[1]
        for flag, expected in [('--referer', options['referer']), ('--cookies-from-browser', 'edge:Default'),
                               ('--add-header', 'User-Agent: Example Test'), ('--sample-interval', '0.5')]:
            self.assertEqual(command[command.index(flag) + 1], expected)
        self.assertIn('--remove-qr', command)

    @unittest.skipUnless((web_ui.PROJECT_ROOT / '.tools/yt-dlp.exe').is_file() or shutil.which('yt-dlp'), 'yt-dlp unavailable')
    def test_http_video_url_converts_through_web_queue(self):
        self.make_video()
        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass
        source_server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(self.root)))
        thread = threading.Thread(target=source_server.serve_forever, daemon=True)
        thread.start()
        try:
            status, job = self.post_json('/api/sources', {
                'kind': 'url', 'source': f'http://127.0.0.1:{source_server.server_port}/source.avi?a=1&b=2',
            })
            self.assertEqual(status, 202, job)
            result = self.wait_for_job(job['id'])
            self.assertEqual(result['status'], 'done', result)
            self.assertEqual(self.request('GET', result['download_url'])[0], 200)
        finally:
            source_server.shutdown()
            source_server.server_close()
            thread.join(timeout=3)

    def test_empty_review_folder_keeps_review_state_and_reports_error(self):
        status, job = self.upload('empty-review.avi', self.make_video(), mode='review')
        result = self.wait_for_job(job['id'])
        for image in Path(result['screenshots_path']).glob('*.png'):
            image.unlink()
        status, response = self.post_json('/api/jobs/' + job['id'] + '/review', {'action': 'generate'})
        self.assertEqual(status, 400, response)
        self.assertEqual(self.server.snapshot(job['id'])['status'], 'review')

    def test_downloader_setup_uses_existing_setup_script(self):
        with patch.object(self.server.executor, 'submit') as submit:
            status, job = self.post_json('/api/downloader')
            self.assertEqual(status, 202, job)
            self.assertEqual(job['kind'], 'downloader')
            submit.assert_called_once()

    def test_uploaded_video_converts_and_downloads_cropped_pdf(self):
        status, job = self.upload("课程 1.1 [6u5TQ3UscDo].avi", self.make_video(), crop_top=4, crop_bottom=6, remove_qr="true")
        self.assertEqual(status, 202, job)
        result = self.wait_for_job(job["id"])
        self.assertEqual(result["status"], "done", result)
        self.assertEqual(result["pages"], 1)
        status, headers, pdf = self.request("GET", result["download_url"])
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/pdf")
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn("_crop_t4_b6_noqr.pdf", headers["Content-Disposition"])
        pdf_path = self.root / "download.pdf"
        pdf_path.write_bytes(pdf)
        with pikepdf.open(pdf_path) as document:
            self.assertEqual(len(document.pages), 1)
            image = next(iter(document.pages[0].images.values()))
            self.assertEqual((image.Width, image.Height), (64, 54))

    def test_identical_filenames_have_independent_results(self):
        data = self.make_video()
        jobs = [self.upload("same.avi", data)[1] for _ in range(2)]
        self.assertNotEqual(jobs[0]["id"], jobs[1]["id"])
        for job in jobs:
            result = self.wait_for_job(job["id"])
            self.assertEqual(result["status"], "done", result)
            self.assertEqual(self.request("GET", result["download_url"])[0], 200)

    def test_invalid_video_reports_error_without_download(self):
        status, job = self.upload("broken.mp4", b"not a video")
        self.assertEqual(status, 202)
        result = self.wait_for_job(job["id"])
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["log"])
        self.assertIsNone(result["download_url"])
        self.assertEqual(self.request("GET", "/api/jobs/" + job["id"] + "/pdf")[0], 404)

    def test_invalid_uploads_are_rejected_before_creating_jobs(self):
        for name, data, options in (
            ("notes.txt", b"text", {}),
            ("empty.mp4", b"", {}),
            ("lesson.mp4", b"data", {"crop_top": -1}),
            ("lesson.mp4", b"data", {"crop_bottom": "1.5"}),
            ("lesson.mp4", b"data", {"remove_qr": "maybe"}),
            ("lesson.mp4", b"data", {"mode": "invalid"}),
            ("lesson.mp4", b"data", {"sample_interval": "0"}),
            ("lesson.mp4", b"data", {"sample_interval": "nan"}),
            ("lesson.mp4", b"data", {"sample_interval": "inf"}),
            ("../escape.mp4", b"data", {}),
            ("C:\\escape.mp4", b"data", {}),
        ):
            with self.subTest(name=name, options=options):
                status, result = self.upload(name, data, **options)
                self.assertEqual(status, 400, result)
        self.assertFalse(list((self.root / "jobs").iterdir()))

    def test_crop_exceeding_video_height_reports_conversion_error(self):
        status, job = self.upload("crop.avi", self.make_video(), crop_top=64)
        self.assertEqual(status, 202)
        result = self.wait_for_job(job["id"])
        self.assertEqual(result["status"], "error", result)
        self.assertIn("frame height", result["log"])
        self.assertIsNone(result["download_url"])

    def test_cross_origin_upload_is_rejected(self):
        status, _, _ = self.request("POST", "/api/jobs?filename=test.mp4", b"data")
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/jobs?filename=test.mp4", b"data", {
            "X-Upload-Token": self.server.upload_token,
            "Origin": "https://example.com",
        })
        self.assertEqual(status, 403)

    def test_unknown_files_and_jobs_are_not_served(self):
        for path in ("/../README.md", "/video2pdfslides.py", "/api/jobs/missing", "/api/jobs/missing/pdf"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)


class FrontendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable for frontend event tests")
    def test_drag_drop_and_bulk_submission(self):
        result = subprocess.run(
            [shutil.which("node"), str(Path(__file__).with_name("test_web_app.js"))],
            capture_output=True, encoding="utf-8", errors="replace", timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(os.name == "nt", "Windows Web launcher")
class WebLauncherTests(unittest.TestCase):
    def test_launcher_uses_project_directory_and_passes_arguments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project 空格 ! &"
            project.mkdir()
            shutil.copy2(web_ui.PROJECT_ROOT / "start_web.bat", project / "start_web.bat")
            scripts = project / ".venv" / "Scripts"
            scripts.mkdir(parents=True)
            shutil.copy2(sys.executable, scripts / "python.exe")
            shutil.copy2(Path(sys.prefix) / "pyvenv.cfg", project / ".venv" / "pyvenv.cfg")
            (project / "web_ui.py").write_text(
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "Path('launch.json').write_text(json.dumps({'cwd': os.getcwd(), 'argv': sys.argv[1:]}), encoding='utf-8')\n",
                encoding="utf-8",
            )
            command = '"{}" /d /c ""{}" --port 8787 --no-browser"'.format(os.environ["COMSPEC"], project / "start_web.bat")
            result = subprocess.run(command, cwd=root, input="\n", capture_output=True, encoding="utf-8", errors="replace", timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            record = json.loads((project / "launch.json").read_text(encoding="utf-8"))
            self.assertEqual(Path(record["cwd"]), project)
            self.assertEqual(record["argv"], ["--port", "8787", "--no-browser"])


if __name__ == "__main__":
    unittest.main()
