import tempfile
import unittest
import subprocess
import sys
from itertools import islice
from pathlib import Path
from unittest.mock import Mock, patch

import pikepdf
import numpy as np
from PIL import Image

import video2pdfslides


class CliCompletionTests(unittest.TestCase):
    def run_conversion(self, review_answer=None):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            folder = Path(temp).resolve()
            video = folder / "sample.1.avi"
            writer = video2pdfslides.cv2.VideoWriter(
                str(video), video2pdfslides.cv2.VideoWriter_fourcc(*"MJPG"), 2, (64, 64)
            )
            self.assertTrue(writer.isOpened())
            try:
                frame = np.full((64, 64, 3), 255, dtype=np.uint8)
                for _ in range(8):
                    writer.write(frame)
            finally:
                writer.release()
            command = [sys.executable, str(Path(video2pdfslides.__file__).resolve()), str(video)]
            if review_answer is not None:
                command.append("--review")
            result = subprocess.run(
                command, cwd=folder, input=review_answer or "", text=True,
                capture_output=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(len(list((folder / "output" / "sample.1").glob("*.png"))), 1)
            pdf = folder / "output" / "sample.1.pdf"
            if review_answer == "n\n":
                self.assertFalse(pdf.exists())
            else:
                self.assertTrue(pdf.is_file())
                with pikepdf.open(pdf) as document:
                    self.assertEqual(len(document.pages), 1)
            if review_answer is None:
                self.assertNotIn("Press y", result.stdout)

    def test_default_run_generates_pdf_without_input(self):
        self.run_conversion()

    def test_review_yes_generates_pdf(self):
        self.run_conversion("y\n")

    def test_review_no_preserves_screenshots_without_pdf(self):
        self.run_conversion("n\n")


class FrameEndTests(unittest.TestCase):
    def make_capture(self, total_frames=25, fps=25):
        capture = Mock()
        capture.isOpened.return_value = True
        metadata = {
            video2pdfslides.cv2.CAP_PROP_FRAME_COUNT: total_frames,
            video2pdfslides.cv2.CAP_PROP_FPS: fps,
        }
        capture.get.side_effect = lambda key: metadata.get(key, 0)
        capture.read.return_value = (True, object())
        return capture

    def test_stops_at_duration_even_when_decoder_repeats_last_frame(self):
        capture = self.make_capture()
        with patch.object(video2pdfslides, "FRAME_RATE", 3), patch.object(video2pdfslides.cv2, "VideoCapture", return_value=capture):
            frames = list(islice(video2pdfslides.get_frames("video.mp4"), 6))
        self.assertEqual(len(frames), 3)
        self.assertEqual(capture.read.call_count, 3)
        for index, (_, timestamp, _) in enumerate(frames):
            self.assertAlmostEqual(timestamp, index / 3)
        capture.release.assert_called_once()

    def test_default_sampling_interval_is_one_second_with_unshifted_timestamps(self):
        capture = self.make_capture(total_frames=150, fps=25)
        with patch.object(video2pdfslides.cv2, "VideoCapture", return_value=capture):
            frames = list(islice(video2pdfslides.get_frames("video.mp4"), 10))
        self.assertEqual(len(frames), 6)
        self.assertEqual(capture.read.call_count, 6)
        self.assertEqual([frame_count for frame_count, _, _ in frames], list(range(1, 7)))
        self.assertEqual([timestamp for _, timestamp, _ in frames], [0, 1, 2, 3, 4, 5])
        for index, call in enumerate(capture.set.call_args_list):
            self.assertEqual(call.args[0], video2pdfslides.cv2.CAP_PROP_POS_MSEC)
            self.assertAlmostEqual(call.args[1], index * 1000)
            self.assertAlmostEqual(frames[index][1] * 1000, call.args[1])
        capture.release.assert_called_once()

    def test_failed_read_does_not_yield_stale_frame(self):
        capture = self.make_capture()
        capture.read.side_effect = [(False, object()), (False, None)]
        with patch.object(video2pdfslides.cv2, "VideoCapture", return_value=capture):
            frames = list(video2pdfslides.get_frames("video.mp4"))
        self.assertEqual(frames, [])
        capture.release.assert_called_once()

    def test_unknown_metadata_uses_read_failure(self):
        for total_frames, fps in [(0, 25), (25, 0)]:
            with self.subTest(total_frames=total_frames, fps=fps):
                capture = self.make_capture(total_frames, fps)
                capture.read.side_effect = [(True, object()), (False, None)]
                with patch.object(video2pdfslides.cv2, "VideoCapture", return_value=capture):
                    frames = list(video2pdfslides.get_frames("video.mp4"))
                self.assertEqual(len(frames), 1)
                capture.release.assert_called_once()


class OutputPathTests(unittest.TestCase):
    def test_pdf_handles_literal_brackets_in_screenshot_path_and_preserves_order(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            screenshots = Path(temp) / "国内怎么才能用上Opus5.5 [6u5TQ3UscDo]_noqr"
            screenshots.mkdir()
            Image.new("RGB", (64, 36), "white").save(screenshots / "0001_0.2.png")
            Image.new("RGB", (32, 18), "white").save(screenshots / "0000_0.1.png")
            video2pdfslides.convert_screenshots_to_pdf(str(screenshots))
            with pikepdf.open(str(screenshots) + ".pdf") as document:
                self.assertEqual(len(document.pages), 2)
                images = [next(iter(page.images.values())) for page in document.pages]
                self.assertEqual([(image.Width, image.Height) for image in images], [(32, 18), (64, 36)])

    def test_output_folder_uses_full_video_stem(self):
        cases = [
            (r"F:\Download\01-chapt1\1.1.mp4", "1.1"),
            ("F:/Download/01-chapt1/1.1.mp4", "1.1"),
            ("./input/Test Video 1.mp4", "Test Video 1"),
            (r".\input\lecture.part.2.mp4", "lecture.part.2"),
        ]
        for video_path, expected_name in cases:
            with self.subTest(video_path=video_path):
                with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
                    output_root = Path(temp).resolve()
                    with patch.object(video2pdfslides, "OUTPUT_SLIDES_DIR", str(output_root)):
                        output = Path(video2pdfslides.initialize_output_folder(video_path))
                    self.assertEqual(output, output_root / expected_name)
                    self.assertTrue(output.is_dir())

    def test_pdf_preserves_dots_and_matches_screenshot_folder(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            output_root = Path(temp).resolve()
            screenshots = output_root / "1.1"
            screenshots.mkdir()
            Image.new("RGB", (32, 18), "white").save(screenshots / "000.png")
            with patch.object(video2pdfslides, "OUTPUT_SLIDES_DIR", str(output_root)):
                with patch.object(video2pdfslides, "video_path", "F:/Download/01-chapt1/1.1.mp4", create=True):
                    video2pdfslides.convert_screenshots_to_pdf(str(screenshots))
            expected_pdf = output_root / "1.1.pdf"
            self.assertTrue(expected_pdf.is_file())
            with pikepdf.open(expected_pdf) as document:
                self.assertEqual(len(document.pages), 1)


if __name__ == "__main__":
    unittest.main()
