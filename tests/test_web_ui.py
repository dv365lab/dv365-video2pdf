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

    def wait_for_job(self, job_id):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status, _, body = self.request("GET", "/api/jobs/" + job_id)
            self.assertEqual(status, 200)
            job = json.loads(body)
            if job["status"] in ("done", "error"):
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
