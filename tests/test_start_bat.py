import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt", "Windows batch launcher")
class StartBatTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((PROJECT_ROOT / "start.bat").is_file(), "start.bat is missing")
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.project = self.folder / "project 空格 ! &"
        self.project.mkdir()
        self.caller = self.folder / "caller"
        self.caller.mkdir()
        self.make_launcher(self.project)
        video_folder = self.folder / "videos 课程 ! &"
        video_folder.mkdir()
        self.video = video_folder / "sample 片段 ! &.mp4"
        self.video.touch()

    def make_launcher(self, project):
        shutil.copy2(PROJECT_ROOT / "start.bat", project / "start.bat")
        shutil.copy2(PROJECT_ROOT / "launcher.py", project / "launcher.py")
        scripts = project / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(sys.executable, scripts / "python.exe")
        shutil.copy2(Path(sys.prefix) / "pyvenv.cfg", project / ".venv" / "pyvenv.cfg")
        (project / "video2pdfslides.py").write_text(
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "Path('launch.json').write_text(\n"
            "    json.dumps({'cwd': os.getcwd(), 'argv': sys.argv[1:]}),\n"
            "    encoding='utf-8')\n"
            "sys.exit(int(os.environ.get('LAUNCH_TEST_EXIT_CODE', '0')))\n",
            encoding="utf-8",
        )

    def run_launcher(self, video_input, *, project=None, exit_code=0, env=None):
        project = project or self.project
        child_env = os.environ.copy()
        child_env["LAUNCH_TEST_EXIT_CODE"] = str(exit_code)
        if env:
            child_env.update(env)
        # cmd /c requires an outer quote pair around the quoted batch path.
        command = '"{}" /d /c ""{}""'.format(
            os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"),
            project / "start.bat",
        )
        result = subprocess.run(
            command, cwd=self.caller, input=video_input + "\n\n",
            encoding="utf-8", errors="replace", capture_output=True,
            timeout=20, env=child_env,
        )
        self.assertNotIn("not recognized", result.stdout + result.stderr)
        return result

    def assert_launch(self, result, video):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "", result.stdout + result.stderr)
        record = json.loads((self.project / "launch.json").read_text(encoding="utf-8"))
        self.assertEqual(Path(record["cwd"]), self.project)
        self.assertEqual(record["argv"], [str(video)])
        self.assertFalse((self.caller / "launch.json").exists())

    def test_pasted_path_runs_from_project_directory(self):
        self.assert_launch(self.run_launcher(str(self.video)), self.video)

    def test_dragged_path_removes_surrounding_quotes(self):
        self.assert_launch(self.run_launcher('"{}"'.format(self.video)), self.video)

    def test_pasted_urls_preserve_query_characters(self):
        for url in (
            "https://www.youtube.com/watch?v=y1TXjY1RjBU&feature=shared",
            "http://example.com/video%20(1)!.mp4?token=a%25b&download=1",
            "HTTPS://example.com/video.mp4?value=(hello)!&literal=%PATH%",
            "HTTP://example.com/video.mp4",
        ):
            with self.subTest(url=url):
                self.assert_launch(self.run_launcher(url), url)

    def test_quoted_url_removes_surrounding_quotes(self):
        url = "https://example.com/video%20(1)!.mp4?token=a%25b&download=1"
        self.assert_launch(self.run_launcher('"{}"'.format(url)), url)

    def test_pasted_path_trims_whitespace(self):
        self.assert_launch(self.run_launcher('  "{}"  '.format(self.video)), self.video)

    def test_batch_update_while_waiting_for_input_does_not_run_fragments(self):
        batch = self.project / "start.bat"
        command = '"{}" /d /c ""{}""'.format(os.environ["COMSPEC"], batch)
        child = subprocess.Popen(
            command, cwd=self.caller, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        prefix = bytearray()
        ready = threading.Event()

        def read_prompt():
            while not prefix.endswith(b": "):
                value = child.stdout.read(1)
                if not value:
                    break
                prefix.extend(value)
            ready.set()

        reader = threading.Thread(target=read_prompt, daemon=True)
        reader.start()
        try:
            self.assertTrue(ready.wait(5), bytes(prefix))
            self.assertTrue(prefix.endswith(b": "), bytes(prefix))
            # CMD otherwise resumes at its old byte offset in the edited file.
            batch.write_bytes(b"@rem updated launcher\r\n" + batch.read_bytes())
            url = "https://www.youtube.com/watch?v=y1TXjY1RjBU&feature=shared"
            stdout, stderr = child.communicate((url + "\n\n").encode(), timeout=10)
            result = subprocess.CompletedProcess(
                command, child.returncode,
                (prefix + stdout).decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace"),
            )
            self.assert_launch(result, url)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()
            reader.join(timeout=1)

    def test_empty_input_cancels_without_running_python(self):
        for value in ("", '""'):
            with self.subTest(value=value):
                result = self.run_launcher(value)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse((self.project / "launch.json").exists())

    def test_missing_virtual_environment_reports_error(self):
        (self.project / ".venv" / "Scripts" / "python.exe").unlink()
        result = self.run_launcher(str(self.video))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(".venv", result.stdout + result.stderr)
        self.assertFalse((self.project / "launch.json").exists())

    def test_missing_video_does_not_run_python(self):
        missing = self.video.with_name("missing video.mp4")
        result = self.run_launcher(str(missing))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse((self.project / "launch.json").exists())

    def test_directory_input_does_not_run_python(self):
        result = self.run_launcher(str(self.video.parent))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse((self.project / "launch.json").exists())

    def test_python_failure_exit_code_survives_pause(self):
        result = self.run_launcher(str(self.video), exit_code=7)
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertTrue((self.project / "launch.json").is_file())

    def test_real_conversion_generates_pdf_in_project_output(self):
        import cv2
        import numpy as np
        import pikepdf

        project = self.folder / "integration-project"
        project.mkdir()
        self.make_launcher(project)
        shutil.copy2(PROJECT_ROOT / "video2pdfslides.py", project / "video2pdfslides.py")
        video = project / "sample.1.avi"
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 2, (64, 64))
        self.assertTrue(writer.isOpened())
        try:
            for _ in range(8):
                writer.write(np.full((64, 64, 3), 255, dtype=np.uint8))
        finally:
            writer.release()
        result = self.run_launcher(
            str(video), project=project,
            env={"PYTHONPATH": str(Path(sys.prefix) / "Lib" / "site-packages")},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        screenshots = list((project / "output" / "sample.1").glob("*.png"))
        self.assertEqual(len(screenshots), 1, result.stdout)
        with pikepdf.open(project / "output" / "sample.1.pdf") as document:
            self.assertEqual(len(document.pages), 1)
        self.assertFalse((self.caller / "output").exists())


if __name__ == "__main__":
    unittest.main()
