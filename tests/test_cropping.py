import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pikepdf
from PIL import Image

import video2pdfslides


class CropFrameTests(unittest.TestCase):
    def test_crops_requested_rows_without_modifying_input(self):
        frame = np.arange(10 * 6 * 3, dtype=np.uint8).reshape(10, 6, 3)
        original = frame.copy()
        cropped = video2pdfslides.crop_frame(frame, crop_top=2, crop_bottom=3)
        self.assertEqual(cropped.shape, (5, 6, 3))
        np.testing.assert_array_equal(cropped, original[2:7])
        np.testing.assert_array_equal(frame, original)

    def test_zero_crop_preserves_the_whole_frame(self):
        frame = np.arange(8 * 6 * 3, dtype=np.uint8).reshape(8, 6, 3)
        np.testing.assert_array_equal(video2pdfslides.crop_frame(frame), frame)
        np.testing.assert_array_equal(
            video2pdfslides.crop_frame(frame, crop_top=2), frame[2:]
        )

    def test_invalid_crop_rejects_negative_or_empty_result(self):
        frame = np.zeros((10, 6, 3), dtype=np.uint8)
        for top, bottom in [(-1, 0), (0, -1), (10, 0), (0, 10), (4, 6), (8, 3)]:
            with self.subTest(top=top, bottom=bottom):
                with self.assertRaises(ValueError):
                    video2pdfslides.crop_frame(frame, top, bottom)

    def test_detection_and_png_saving_use_the_cropped_frame(self):
        frame = np.arange(10 * 6 * 3, dtype=np.uint8).reshape(10, 6, 3)
        original = frame.copy()
        expected = original[2:7]
        expected_resized = video2pdfslides.imutils.resize(expected, width=600)
        with patch.object(video2pdfslides, "get_frames", return_value=[(1, 0, frame)]), \
                patch.object(video2pdfslides.imutils, "resize", wraps=video2pdfslides.imutils.resize) as resize, \
                patch.object(video2pdfslides.cv2, "cvtColor", wraps=video2pdfslides.cv2.cvtColor) as grayscale, \
                patch.object(video2pdfslides.cv2, "imwrite", return_value=True) as save:
            video2pdfslides.detect_unique_screenshots(
                "sample.mp4", "unused-output", crop_top=2, crop_bottom=3
            )
        resize.assert_called_once()
        grayscale.assert_called_once()
        save.assert_called_once()
        np.testing.assert_array_equal(resize.call_args.args[0], expected)
        np.testing.assert_array_equal(grayscale.call_args.args[0], expected_resized)
        self.assertEqual(grayscale.call_args.args[1], video2pdfslides.cv2.COLOR_BGR2GRAY)
        np.testing.assert_array_equal(save.call_args.args[1], expected)
        np.testing.assert_array_equal(frame, original)


class CropCliTests(unittest.TestCase):
    def create_video(self, folder):
        video = folder / "sample.1.avi"
        writer = video2pdfslides.cv2.VideoWriter(
            str(video), video2pdfslides.cv2.VideoWriter_fourcc(*"MJPG"), 2, (64, 64)
        )
        self.assertTrue(writer.isOpened())
        try:
            frame = np.full((64, 64, 3), 255, dtype=np.uint8)
            frame[:8] = (0, 0, 255)
            frame[-12:] = (255, 0, 0)
            for _ in range(8):
                writer.write(frame)
        finally:
            writer.release()
        return video

    def run_cli(self, folder, video, top, bottom):
        return subprocess.run(
            [
                sys.executable, str(Path(video2pdfslides.__file__).resolve()),
                str(video), "--crop-top", str(top), "--crop-bottom", str(bottom),
            ],
            cwd=folder, input="", text=True, capture_output=True, timeout=15,
        )

    def test_cli_generates_matching_png_pdf_and_preserves_baseline(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            folder = Path(temp).resolve()
            video = self.create_video(folder)
            baseline = folder / "output" / "sample.1"
            baseline.mkdir(parents=True)
            old_png = baseline / "original.png"
            old_png.write_bytes(b"original screenshot")
            old_pdf = folder / "output" / "sample.1.pdf"
            old_pdf.write_bytes(b"original pdf")

            result = self.run_cli(folder, video, 8, 12)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(old_png.read_bytes(), b"original screenshot")
            self.assertEqual(old_pdf.read_bytes(), b"original pdf")
            output = folder / "output" / "sample.1_crop_t8_b12"
            screenshots = sorted(output.glob("*.png"))
            self.assertEqual(len(screenshots), 1, result.stdout)
            with Image.open(screenshots[0]) as screenshot:
                self.assertEqual(screenshot.size, (64, 44))
            with pikepdf.open(str(output) + ".pdf") as document:
                self.assertEqual(len(document.pages), len(screenshots))
                images = [
                    item for _, item in document.pages[0].Resources.XObject.items()
                    if item.get("/Subtype") == "/Image"
                ]
                self.assertEqual(len(images), 1)
                self.assertEqual((int(images[0].Width), int(images[0].Height)), (64, 44))
            self.assertNotIn("Press y", result.stdout)

    def test_cli_rejects_invalid_crop_before_creating_outputs(self):
        for top, bottom in [(-1, 0), (0, -1), (32, 32), (65, 0)]:
            with self.subTest(top=top, bottom=bottom):
                with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
                    folder = Path(temp).resolve()
                    video = self.create_video(folder)
                    result = self.run_cli(folder, video, top, bottom)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertNotIn("unrecognized arguments", result.stderr)
                    self.assertFalse((folder / "output").exists(), result.stdout)


if __name__ == "__main__":
    unittest.main()
