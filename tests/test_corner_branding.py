import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np
import pikepdf
from PIL import Image

import video2pdfslides as v


FIXTURES = Path(__file__).parent / 'fixtures'


def example_frame(style, scale=1):
    with Image.open(FIXTURES / f'eeeknow-corner-{style}.png') as image:
        corner = np.asarray(image.convert('RGB'))[:, :, ::-1].copy()
    frame = np.full((720, 1280, 3), 30 if style == 'dark' else 255, dtype=np.uint8)
    cv2.putText(frame, 'PCIE slide body', (100, 300), cv2.FONT_HERSHEY_SIMPLEX, 1, (80, 80, 80), 2)
    h, w = corner.shape[:2]
    frame[-h:, -w:] = corner
    if scale != 1:
        frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return frame


class CornerBrandingTests(unittest.TestCase):
    def test_supplied_white_and_dark_marks_clear_logo_name_and_date(self):
        for style in ['white', 'dark']:
            for scale in [0.5, 1, 1.5]:
                with self.subTest(style=style, scale=scale):
                    frame = example_frame(style, scale)
                    original = frame.copy()
                    cleaned = v.remove_corner_branding(frame)
                    # All logo glyphs and the date strip in both supplied examples.
                    corner = cleaned[round(604 * scale):, round((961 if style == 'white' else 949) * scale):]
                    np.testing.assert_array_equal(corner, np.broadcast_to(corner[0, 0], corner.shape))
                    self.assertFalse(np.array_equal(cleaned, original))
                    np.testing.assert_array_equal(cleaned[:round(590 * scale)], original[:round(590 * scale)])
                    np.testing.assert_array_equal(cleaned[:, :round(930 * scale)], original[:, :round(930 * scale)])
                    np.testing.assert_array_equal(frame, original)
                    self.assertEqual(cleaned.shape, original.shape)

    def test_unrelated_corner_shapes_dates_and_blank_pages_are_unchanged(self):
        for kind in ['blank', 'text', 'squares', 'random']:
            with self.subTest(kind=kind):
                frame = np.full((720, 1280, 3), 255, dtype=np.uint8)
                if kind == 'text':
                    cv2.putText(frame, '2026/10/23', (1050, 700), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)
                elif kind == 'squares':
                    cv2.rectangle(frame, (1116, 661), (1172, 714), (190, 160, 0), 6)
                    cv2.rectangle(frame, (1190, 670), (1220, 700), (190, 160, 0), -1)
                elif kind == 'random':
                    rng = np.random.default_rng(42)
                    frame[-150:, -350:] = rng.integers(0, 256, (150, 350, 3), dtype=np.uint8)
                np.testing.assert_array_equal(v.remove_corner_branding(frame), frame)
                np.testing.assert_array_equal(v.remove_capture_branding(frame), frame)

    def test_same_logo_in_body_does_not_trigger_corner_clearing(self):
        frame = np.full((720, 1280, 3), 255, dtype=np.uint8)
        with Image.open(FIXTURES / 'eeeknow-corner-white.png') as image:
            region = np.asarray(image.convert('RGB'))[:, :, ::-1]
        frame[300:440, 400:828] = region
        np.testing.assert_array_equal(v.remove_corner_branding(frame), frame)
        np.testing.assert_array_equal(v.remove_capture_branding(frame), frame)

    def test_corner_cleanup_runs_when_ocr_finds_no_text(self):
        frame = example_frame('white')
        engine = Mock(return_value=(None, None))
        with patch.object(v, 'get_text_ocr', return_value=engine):
            cleaned = v.remove_branding_text(frame)
        self.assertTrue(np.all(cleaned[604:, 961:] == 255))

    def test_capture_cleans_reference_logo_before_cropping_and_saving_png(self):
        for style in ['reference', 'white', 'dark']:
            for top, bottom in [(0, 0), (40, 80)]:
                with self.subTest(style=style, crop=(top, bottom)), tempfile.TemporaryDirectory() as temp:
                    frame = example_frame(style)
                    original = frame.copy()
                    cleaned = v.remove_corner_branding(frame)
                    self.assertFalse(np.array_equal(cleaned, frame))
                    expected = v.crop_frame(cleaned, top, bottom)
                    with patch.object(v, 'get_frames', return_value=[(1, 0, frame)]), \
                            patch.object(v, 'get_text_ocr') as ocr:
                        v.detect_unique_screenshots('sample.mp4', temp, crop_top=top, crop_bottom=bottom)
                    files = list(Path(temp).glob('*.png'))
                    self.assertEqual(len(files), 1)
                    saved = cv2.imread(str(files[0]))
                    np.testing.assert_array_equal(saved, expected)
                    np.testing.assert_array_equal(saved[:500], original[top:top + 500])
                    np.testing.assert_array_equal(frame, original)
                    ocr.assert_not_called()

    def test_cli_extract_only_removes_reference_logo_without_generating_pdf(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            video = folder / 'reference-logo.avi'
            frame = example_frame('reference', scale=0.5)
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 2, (640, 360))
            self.assertTrue(writer.isOpened())
            try:
                for _ in range(6):
                    writer.write(frame)
            finally:
                writer.release()
            capture = cv2.VideoCapture(str(video))
            try:
                success, decoded = capture.read()
                self.assertTrue(success)
            finally:
                capture.release()
            expected = v.crop_frame(v.remove_corner_branding(decoded), 10, 30)
            self.assertFalse(np.array_equal(expected, v.crop_frame(decoded, 10, 30)))
            result = subprocess.run(
                [sys.executable, str(Path(v.__file__).resolve()), str(video), '--extract-only',
                 '--crop-top', '10', '--crop-bottom', '30'],
                cwd=folder, input='', capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            output = folder / 'output' / (video.stem + '_crop_t10_b30')
            screenshots = list(output.glob('*.png'))
            self.assertEqual(len(screenshots), 1, result.stdout)
            np.testing.assert_array_equal(cv2.imread(str(screenshots[0])), expected)
            self.assertFalse(output.with_suffix('.pdf').exists())

    def test_bottom_right_logo_away_from_corner_is_removed_locally(self):
        with Image.open(FIXTURES / 'eeeknow-corner-reference.png') as image:
            reference = np.asarray(image.convert('RGB'))[:, :, ::-1].copy()
        # Keep the supplied logo and its antialiased border, without the screenshot's frame.
        logo = reference[40:142, 88:196]
        height, width = logo.shape[:2]
        for x, y in [(850, 550), (970, 500), (1120, 530)]:
            with self.subTest(position=(x, y)), tempfile.TemporaryDirectory() as temp:
                frame = np.full((720, 1280, 3), 255, dtype=np.uint8)
                frame[y:y + height, x:x + width] = logo
                cv2.putText(frame, 'Keep this text', (900, 695), cv2.FONT_HERSHEY_SIMPLEX,
                            .6, (0, 0, 0), 2)
                cv2.rectangle(frame, (1180, 660), (1240, 705), (190, 160, 0), 4)
                original = frame.copy()
                with patch.object(v, 'get_frames', return_value=[(1, 0, frame)]):
                    v.detect_unique_screenshots('sample.mp4', temp)
                saved = cv2.imread(str(next(Path(temp).glob('*.png'))))
                region = saved[y:y + height, x:x + width]
                self.assertTrue(np.all(region > 240), 'Reference logo remains in the PNG')
                outside = np.ones(frame.shape[:2], dtype=bool)
                outside[y - 8:y + height + 8, x - 8:x + width + 8] = False
                np.testing.assert_array_equal(saved[outside], original[outside])
                np.testing.assert_array_equal(frame, original)

    def test_pdf_cleans_supplied_logo_and_date_without_modifying_png(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'slides'
            folder.mkdir()
            source = folder / '0000.png'
            Image.fromarray(example_frame('white')[:, :, ::-1]).save(source)
            original = source.read_bytes()
            with patch.object(v, 'get_text_ocr', return_value=Mock(return_value=(None, None))):
                v.convert_screenshots_to_pdf(folder)
            with pikepdf.open(str(folder) + '.pdf') as pdf:
                image = pikepdf.PdfImage(next(iter(pdf.pages[0].images.values()))).as_pil_image()
                self.assertTrue(np.all(np.asarray(image)[604:, 961:] == 255))
            self.assertEqual(source.read_bytes(), original)

    @unittest.skipUnless((Path(__file__).resolve().parents[1] / 'output/1.1_noqr/001_0.3.png').is_file(),
                         'local course screenshots unavailable')
    def test_real_slides_and_bottom_crop_preserve_body(self):
        root = Path(__file__).resolve().parents[1] / 'output/1.1_noqr'
        for name in ['000_0.1.png', '001_0.3.png', '044_30.53.png']:
            for crop in [0, 8, 12]:
                with self.subTest(name=name, crop=crop):
                    with Image.open(root / name) as image:
                        frame = np.ascontiguousarray(np.asarray(image.convert('RGB'))[:, :, ::-1])
                    frame = v.crop_frame(frame, crop_bottom=crop)
                    cleaned = v.remove_corner_branding(frame)
                    corner = cleaned[661:, 1116:]
                    np.testing.assert_array_equal(corner, np.broadcast_to(corner[0, 0], corner.shape))
                    np.testing.assert_array_equal(cleaned[:650], frame[:650])
                    np.testing.assert_array_equal(cleaned[:, :1100], frame[:, :1100])


if __name__ == '__main__':
    unittest.main()
