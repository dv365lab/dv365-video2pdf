import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import video2pdfslides


class DownloaderSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.local = Path(self.temp.name).resolve() / 'yt-dlp.exe'
        local_patch = patch.object(video2pdfslides, 'PROJECT_DOWNLOADER', self.local)
        local_patch.start()
        self.addCleanup(local_patch.stop)

    def test_project_tool_takes_precedence_over_old_path_tool(self):
        self.local.write_bytes(b'fixture')
        with patch.object(video2pdfslides.shutil, 'which', return_value='old-yt-dlp.exe') as which, \
                patch.object(video2pdfslides.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '2026.08.19\n')) as run:
            self.assertEqual(video2pdfslides.get_downloader(), str(self.local))
            which.assert_not_called()
            self.assertEqual(run.call_args.args[0], [str(self.local), '--version'])

    def test_outdated_path_tool_is_rejected_with_setup_command(self):
        with patch.object(video2pdfslides.shutil, 'which', return_value='old-yt-dlp.exe'), \
                patch.object(video2pdfslides.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '2026.03.17\n')):
            with self.assertRaisesRegex(RuntimeError, 'setup_downloader.py'):
                video2pdfslides.get_downloader()

    def test_current_path_tool_can_be_used(self):
        with patch.object(video2pdfslides.shutil, 'which', return_value='yt-dlp.exe'), \
                patch.object(video2pdfslides.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '2026.09.20.123456\n')):
            self.assertEqual(video2pdfslides.get_downloader(), 'yt-dlp.exe')

    def test_missing_or_broken_tool_reports_setup_command(self):
        with patch.object(video2pdfslides.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'setup_downloader.py'):
                video2pdfslides.get_downloader()
        with patch.object(video2pdfslides.shutil, 'which', return_value='yt-dlp.exe'), \
                patch.object(video2pdfslides.subprocess, 'run', side_effect=OSError('broken exe')):
            with self.assertRaisesRegex(RuntimeError, 'setup_downloader.py'):
                video2pdfslides.get_downloader()


if __name__ == '__main__':
    unittest.main()
