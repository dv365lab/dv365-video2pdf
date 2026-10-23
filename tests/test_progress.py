import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

import video2pdfslides as v
import web_ui


class ProgressTests(unittest.TestCase):
    def test_extraction_progress_tracks_processed_samples(self):
        capture = Mock()
        capture.isOpened.return_value = True
        capture.get.side_effect = lambda key: {v.cv2.CAP_PROP_FRAME_COUNT: 100, v.cv2.CAP_PROP_FPS: 10}.get(key, 0)
        capture.read.return_value = (True, np.zeros((8, 8, 3), dtype=np.uint8))
        with patch.object(v.cv2, 'VideoCapture', return_value=capture), patch.object(v, 'FRAME_RATE', 0.5), \
                patch.object(v, 'report_progress', create=True) as progress:
            list(v.get_frames('sample.avi'))
        events = [call.kwargs for call in progress.call_args_list if call.args[0] == 'extract']
        self.assertEqual([event['current'] for event in events], [0, 1, 2, 3, 4, 5])
        self.assertTrue(all(event['total'] == 5 for event in events))

    def test_unknown_duration_does_not_invent_percentage(self):
        capture = Mock()
        capture.isOpened.return_value = True
        capture.get.return_value = 0
        capture.read.side_effect = [(True, np.zeros((8, 8, 3), dtype=np.uint8)), (False, None)]
        with patch.object(v.cv2, 'VideoCapture', return_value=capture), \
                patch.object(v, 'report_progress', create=True) as progress:
            list(v.get_frames('sample.avi'))
        self.assertTrue(progress.called)
        self.assertTrue(all(call.kwargs['total'] is None for call in progress.call_args_list))

    def test_pdf_reports_current_page_before_ocr_and_separate_write_stage(self):
        events = []
        observed = []
        def clean(frame):
            observed.append(events[-1].copy())
            return frame
        def report(stage, **values):
            events.append(dict(stage=stage, **values))
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'slides'
            folder.mkdir()
            for index in range(2):
                Image.new('RGB', (32, 32), 'white').save(folder / f'{index}.png')
            with patch.object(v, 'report_progress', side_effect=report, create=True), \
                    patch.object(v, 'remove_branding_text', side_effect=clean):
                v.convert_screenshots_to_pdf(folder)
        self.assertEqual(observed, [dict(stage='clean', current=1, total=2, completed=0),
                                    dict(stage='clean', current=2, total=2, completed=1)])
        self.assertEqual(events[-1]['stage'], 'write')
        self.assertEqual(events[-1]['total'], 2)

    def test_parser_reports_real_phase_progress_and_keeps_unknown_total(self):
        examples = [
            ('VIDEO2PDF_PROGRESS {"stage":"extract","current":25,"total":100}', 'extract', 25),
            ('VIDEO2PDF_PROGRESS {"stage":"clean","current":3,"completed":2,"total":8}', 'clean', 25),
            ('VIDEO2PDF_PROGRESS {"stage":"write","current":8,"total":8}', 'write', None),
            ('VIDEO2PDF_PROGRESS {"stage":"extract","current":5,"total":null}', 'extract', None),
            ('[download]  37.8% of 12MiB at 1MiB/s ETA 00:07', 'download', 37),
        ]
        for line, stage, percent in examples:
            with self.subTest(line=line):
                result = web_ui.parse_progress(line)
                self.assertEqual(result['stage'], stage)
                self.assertEqual(result['percent'], percent)
        for line in ['normal log', 'VIDEO2PDF_PROGRESS not json', 'VIDEO2PDF_PROGRESS {"stage":"unknown"}',
                     'VIDEO2PDF_PROGRESS {"stage":"extract","current":NaN,"total":5}']:
            with self.subTest(line=line):
                self.assertIsNone(web_ui.parse_progress(line))

    def test_subprocess_progress_is_available_in_api_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            server = web_ui.create_server(port=0, work_root=Path(temp) / 'jobs')
            try:
                job_id = server.add_job('sample.avi')
                process = Mock()
                process.__enter__ = Mock(return_value=process)
                process.__exit__ = Mock(return_value=False)
                process.stdout = ['starting\n', 'VIDEO2PDF_PROGRESS {"stage":"clean","current":2,"completed":1,"total":4}\n']
                process.wait.return_value = 0
                with patch.object(web_ui.subprocess, 'Popen', return_value=process):
                    server.run_command(job_id, ['unused'], temp)
                job = server.snapshot(job_id)
                self.assertEqual(job['progress']['current'], 2)
                self.assertEqual(job['progress']['total'], 4)
                self.assertEqual(job['progress']['percent'], 25)
                self.assertIn('starting', job['log'])
                self.assertNotIn('VIDEO2PDF_PROGRESS', job['log'])
            finally:
                server.server_close()


if __name__ == '__main__':
    unittest.main()
