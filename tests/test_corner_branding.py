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
    frame = np.full((720, 1280, 3), 255 if style == 'white' else 30, dtype=np.uint8)
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

    def test_same_logo_in_body_does_not_trigger_corner_clearing(self):
        frame = np.full((720, 1280, 3), 255, dtype=np.uint8)
        with Image.open(FIXTURES / 'eeeknow-corner-white.png') as image:
            region = np.asarray(image.convert('RGB'))[:, :, ::-1]
        frame[300:440, 400:828] = region
        np.testing.assert_array_equal(v.remove_corner_branding(frame), frame)

    def test_corner_cleanup_runs_when_ocr_finds_no_text(self):
        frame = example_frame('white')
        engine = Mock(return_value=(None, None))
        with patch.object(v, 'get_text_ocr', return_value=engine):
            cleaned = v.remove_branding_text(frame)
        self.assertTrue(np.all(cleaned[604:, 961:] == 255))

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
