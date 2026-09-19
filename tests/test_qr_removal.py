import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np
import pikepdf
import zxingcpp

import video2pdfslides


def detected_codes(frame):
    return zxingcpp.read_barcodes(frame, formats=zxingcpp.BarcodeFormat.QRCode)


def add_qr(frame, x, y, background=(255, 255, 255)):
    code = np.asarray(zxingcpp.write_barcode(
        zxingcpp.BarcodeFormat.QRCode, "Test QR", width=100, height=100,
        quiet_zone=0,
    ))
    height, width = code.shape
    patch_pixels = np.where(
        (code == 0)[:, :, None], np.uint8(0), np.array(background, dtype=np.uint8)
    )
    frame[y:y + height, x:x + width] = patch_pixels
    return x, y, x + width, y + height


def make_frame(background=(255, 255, 255)):
    frame = np.full((240, 320, 3), background, dtype=np.uint8)
    cv2.putText(frame, "Slide title", (8, 30), cv2.FONT_HERSHEY_SIMPLEX,
                0.6, (20, 60, 100), 1)
    cv2.line(frame, (12, 180), (140, 180), (180, 40, 10), 3)
    return frame


class RemoveQrTests(unittest.TestCase):
    def assert_only_qr_regions_changed(self, source, cleaned, boxes):
        self.assertEqual(cleaned.shape, source.shape)
        outside = np.ones(source.shape[:2], dtype=bool)
        for x1, y1, x2, y2 in boxes:
            outside[max(0, y1 - 4):y2 + 5, max(0, x1 - 4):x2 + 5] = False
        np.testing.assert_array_equal(cleaned[outside], source[outside])
        self.assertEqual(len(detected_codes(cleaned)), 0)

    def test_no_qr_preserves_all_pixels(self):
        frame = make_frame()
        original = frame.copy()
        self.assertEqual(len(detected_codes(frame)), 0)
        cleaned = video2pdfslides.remove_qr_codes(frame)
        np.testing.assert_array_equal(cleaned, original)
        np.testing.assert_array_equal(frame, original)

    def test_single_qr_uses_surrounding_background_and_preserves_title(self):
        background = (230, 240, 250)
        frame = make_frame(background)
        box = add_qr(frame, 214, 45, background)
        original = frame.copy()
        self.assertEqual(len(detected_codes(frame)), 1)
        cleaned = video2pdfslides.remove_qr_codes(frame)
        self.assert_only_qr_regions_changed(original, cleaned, [box])
        x1, y1, x2, y2 = box
        np.testing.assert_array_equal(
            cleaned[y1:y2, x1:x2],
            np.full((y2 - y1, x2 - x1, 3), background, dtype=np.uint8),
        )
        np.testing.assert_array_equal(frame, original)

    def test_multiple_qr_codes_removed_without_changing_body(self):
        frame = make_frame()
        boxes = [add_qr(frame, 16, 55), add_qr(frame, 215, 100)]
        original = frame.copy()
        self.assertEqual(len(detected_codes(frame)), 2)
        cleaned = video2pdfslides.remove_qr_codes(frame)
        self.assert_only_qr_regions_changed(original, cleaned, boxes)
        np.testing.assert_array_equal(frame, original)

    def test_qr_touching_top_and_right_edges_is_removed(self):
        frame = make_frame()
        box = add_qr(frame, 236, 0)
        original = frame.copy()
        self.assertEqual(len(detected_codes(frame)), 1)
        cleaned = video2pdfslides.remove_qr_codes(frame)
        self.assert_only_qr_regions_changed(original, cleaned, [box])
        self.assertTrue(np.all(cleaned[:84, 236:] == 255))
        np.testing.assert_array_equal(frame, original)

    def test_detection_and_png_save_both_receive_cleaned_frame(self):
        frame = make_frame()
        box = add_qr(frame, 214, 45)
        original = frame.copy()
        subtractor = Mock()
        subtractor.apply.return_value = np.zeros(frame.shape[:2], dtype=np.uint8)
        with patch.object(video2pdfslides, "get_frames", return_value=[(2, 4, frame)]), \
                patch.object(video2pdfslides.cv2, "createBackgroundSubtractorMOG2", return_value=subtractor), \
                patch.object(video2pdfslides.imutils, "resize", side_effect=lambda image, width: image), \
                patch.object(video2pdfslides.cv2, "imwrite", return_value=True) as save:
            video2pdfslides.detect_unique_screenshots(
                "sample.mp4", "unused-output", remove_qr=True
            )
        subtractor.apply.assert_called_once()
        save.assert_called_once()
        detected_frame = subtractor.apply.call_args.args[0]
        saved_frame = save.call_args.args[1]
        self.assert_only_qr_regions_changed(original, detected_frame, [box])
        np.testing.assert_array_equal(saved_frame, detected_frame)
        np.testing.assert_array_equal(frame, original)

    def test_cli_generates_noqr_png_pdf_with_optional_crop_and_keeps_baseline(self):
        for top, bottom in [(0, 0), (8, 12)]:
            with self.subTest(top=top, bottom=bottom):
                with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
                    folder = Path(temp).resolve()
                    video = folder / "sample.avi"
                    frame = make_frame()
                    add_qr(frame, 214, 45)
                    writer = cv2.VideoWriter(
                        str(video), cv2.VideoWriter_fourcc(*"MJPG"), 2, (320, 240)
                    )
                    self.assertTrue(writer.isOpened())
                    try:
                        for _ in range(12):
                            writer.write(frame)
                    finally:
                        writer.release()
                    baseline = folder / "output" / "sample"
                    baseline.mkdir(parents=True)
                    original_png = baseline / "original.png"
                    original_png.write_bytes(b"original screenshot")
                    original_pdf = folder / "output" / "sample.pdf"
                    original_pdf.write_bytes(b"original pdf")
                    command = [sys.executable, str(Path(video2pdfslides.__file__).resolve()),
                               str(video), "--remove-qr"]
                    suffix = "_noqr"
                    if top or bottom:
                        command += ["--crop-top", str(top), "--crop-bottom", str(bottom)]
                        suffix = f"_crop_t{top}_b{bottom}_noqr"
                    result = subprocess.run(command, cwd=folder, input="", text=True,
                                            capture_output=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    output = folder / "output" / ("sample" + suffix)
                    screenshots = sorted(output.glob("*.png"))
                    self.assertEqual(len(screenshots), 1, result.stdout)
                    cleaned = cv2.imread(str(screenshots[0]))
                    self.assertEqual(cleaned.shape, (240 - top - bottom, 320, 3))
                    self.assertEqual(len(detected_codes(cleaned)), 0)
                    self.assertTrue(np.all(cleaned[50 - top:120 - top, 220:290] > 240))
                    with pikepdf.open(str(output) + ".pdf") as document:
                        self.assertEqual(len(document.pages), len(screenshots))
                        images = [item for _, item in document.pages[0].Resources.XObject.items()
                                  if item.get("/Subtype") == "/Image"]
                        self.assertEqual(len(images), 1)
                        self.assertEqual((int(images[0].Width), int(images[0].Height)),
                                         (320, 240 - top - bottom))
                    self.assertEqual(original_png.read_bytes(), b"original screenshot")
                    self.assertEqual(original_pdf.read_bytes(), b"original pdf")
                    self.assertNotIn("Press y", result.stdout)


if __name__ == "__main__":
    unittest.main()
