import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np
import pikepdf
from PIL import Image

import video2pdfslides


def text_row(text, x=10, y=20, confidence=0.99):
    """OCR-shaped rows with separated glyphs for precise pixel assertions."""
    boxes = [[[x + i * 12, y], [x + i * 12 + 10, y],
              [x + i * 12 + 10, y + 20], [x + i * 12, y + 20]]
             for i in range(len(text))]
    polygon = [boxes[0][0], boxes[-1][1], boxes[-1][2], boxes[0][3]]
    return [polygon, text, confidence, boxes, list(text), [confidence] * len(text)]


def paint_rows(rows, background=(240, 245, 250)):
    frame = np.full((180, 800, 3), background, dtype=np.uint8)
    for row in rows:
        for box in row[3]:
            x, y = box[0]
            frame[y + 4:y + 17, x + 4:x + 7] = (80, 80, 80)
    return frame


class BrandingRemovalTests(unittest.TestCase):
    def clean(self, frame, rows):
        engine = Mock(return_value=(rows, None))
        with patch.object(video2pdfslides, 'get_text_ocr', return_value=engine, create=True):
            return video2pdfslides.remove_branding_text(frame)

    def test_requested_terms_and_spacing_case_fullwidth_variants(self):
        for text in ['半导体教育领航者', '移知', 'www.eeeknow.com',
                     'www.eeeknow.com（移知）', 'WWW.EEEKNOW.COM (移知)',
                     '半 导 体 教 育 领 航 者', 'ｗｗｗ．ｅｅｅｋｎｏｗ．ｃｏｍ',
                     'https://www.eeeknow.com', 'eeeknow.com', '(移知)']:
            with self.subTest(text=text):
                row = text_row(text)
                frame = paint_rows([row])
                original = frame.copy()
                cleaned = self.clean(frame, [row])
                np.testing.assert_array_equal(cleaned, paint_rows([]))
                np.testing.assert_array_equal(frame, original)

    def test_unrelated_and_low_confidence_text_preserves_every_pixel(self):
        for rows in [None, [], [text_row('半导体设计原理')],
                     [text_row('www.example.com')], [text_row('移知', confidence=0.2)]]:
            with self.subTest(rows=rows):
                frame = paint_rows(rows or [])
                np.testing.assert_array_equal(self.clean(frame, rows), frame)

    def test_only_matching_characters_removed_from_mixed_line(self):
        text = '课程正文 www.eeeknow.com（移知） 第三章'
        row = text_row(text)
        frame = paint_rows([row])
        cleaned = self.clean(frame, [row])
        start, end = text.index('www'), text.index(' 第三章')
        expected = frame.copy()
        for box in row[3][start:end]:
            x, y = box[0]
            expected[y + 4:y + 17, x + 4:x + 7] = (240, 245, 250)
        np.testing.assert_array_equal(cleaned, expected)

    def test_missing_character_boxes_does_not_erase_mixed_body_line(self):
        row = text_row('课程正文 移知 第三章')
        frame = paint_rows([row])
        np.testing.assert_array_equal(self.clean(frame, [row[:3]]), frame)

    def test_multiple_locations_and_dark_background(self):
        rows = [text_row('移知', x=0, y=0), text_row('www.eeeknow.com', x=500, y=159)]
        frame = paint_rows(rows, background=(10, 20, 30))
        cleaned = self.clean(frame, rows)
        np.testing.assert_array_equal(cleaned, paint_rows([], background=(10, 20, 30)))

    def test_pdf_automatically_cleans_images_without_changing_png_or_page_order(self):
        row = text_row('www.eeeknow.com（移知）')
        frame = paint_rows([row])
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / '课程 [1.1]'
            folder.mkdir()
            first = folder / '0000.png'
            Image.fromarray(frame[:, :, ::-1]).save(first)
            second = folder / '0001.png'
            Image.new('RGB', (60, 40), 'white').save(second)
            original = first.read_bytes()
            engine = Mock(side_effect=[([row], None), (None, None)])
            with patch.object(video2pdfslides, 'get_text_ocr', return_value=engine, create=True):
                video2pdfslides.convert_screenshots_to_pdf(folder)
            with pikepdf.open(str(folder) + '.pdf') as pdf:
                self.assertEqual(len(pdf.pages), 2)
                image = pikepdf.PdfImage(next(iter(pdf.pages[0].images.values()))).as_pil_image()
                np.testing.assert_array_equal(np.asarray(image), paint_rows([])[:, :, ::-1])
                image2 = next(iter(pdf.pages[1].images.values()))
                self.assertEqual((image2.Width, image2.Height), (60, 40))
            self.assertEqual(first.read_bytes(), original)
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ['0000.png', '0001.png'])

    def test_ocr_failure_preserves_existing_pdf_and_original_screenshot(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'slides'
            folder.mkdir()
            source = folder / '0000.png'
            Image.new('RGB', (100, 100), 'white').save(source)
            original = source.read_bytes()
            pdf = Path(str(folder) + '.pdf')
            pdf.write_bytes(b'previous PDF')
            engine = Mock(side_effect=RuntimeError('OCR failed'))
            with patch.object(video2pdfslides, 'get_text_ocr', return_value=engine, create=True):
                with self.assertRaisesRegex(RuntimeError, 'OCR failed'):
                    video2pdfslides.convert_screenshots_to_pdf(folder)
            self.assertEqual(pdf.read_bytes(), b'previous PDF')
            self.assertEqual(source.read_bytes(), original)


class RealOcrTests(unittest.TestCase):
    @unittest.skipUnless((Path(__file__).resolve().parents[1] / 'output/1.1_noqr/000_0.1.png').is_file(),
                         'local dark-background course screenshot is unavailable')
    def test_brand_name_on_textured_dark_cover_is_removed(self):
        path = Path(__file__).resolve().parents[1] / 'output/1.1_noqr/000_0.1.png'
        with Image.open(path) as image:
            frame = np.ascontiguousarray(np.asarray(image.convert('RGB'))[:, :, ::-1])
        cleaned = video2pdfslides.remove_branding_text(frame)
        before = frame[665:704, 1180:1275]
        after = cleaned[665:704, 1180:1275]
        # The cyan glyphs must disappear even when a second OCR pass would miss them.
        cyan = lambda pixels: (pixels[:, :, 0] > 100) & (pixels[:, :, 1] > 90) & (pixels[:, :, 2] < 80)
        self.assertGreater(np.count_nonzero(cyan(before)), 100)
        self.assertLess(np.count_nonzero(cyan(after)), 10)
        np.testing.assert_array_equal(cleaned[330:410, 120:560], frame[330:410, 120:560])

    def make_frame(self):
        import zxingcpp
        frame = np.full((480, 800, 3), 255, dtype=np.uint8)
        cv2.putText(frame, 'PCIE training', (40, 200), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
        cv2.putText(frame, 'www.eeeknow.com', (150, 440), cv2.FONT_HERSHEY_SIMPLEX, 1, (100, 100, 100), 2)
        code = np.asarray(zxingcpp.write_barcode(zxingcpp.BarcodeFormat.QRCode, 'test', width=120, height=120))
        h, w = code.shape
        frame[20:20 + h, 650:650 + w] = code[:, :, None]
        return frame

    def test_real_ocr_and_qr_removal_keep_slide_body(self):
        import zxingcpp
        frame = self.make_frame()
        original = frame.copy()
        cleaned = video2pdfslides.remove_branding_text(video2pdfslides.remove_qr_codes(frame))
        self.assertFalse(zxingcpp.read_barcodes(cleaned, formats=zxingcpp.BarcodeFormat.QRCode))
        self.assertTrue(np.all(cleaned[405:450, 140:480] == 255))
        np.testing.assert_array_equal(cleaned[160:230, 20:400], original[160:230, 20:400])
        np.testing.assert_array_equal(frame, original)

    def test_cli_defaults_to_text_removal_and_preserves_qr_option_with_cropping(self):
        import zxingcpp
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            folder = Path(temp).resolve()
            video = folder / 'sample.avi'
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 2, (800, 480))
            self.assertTrue(writer.isOpened())
            try:
                for _ in range(6):
                    writer.write(self.make_frame())
            finally:
                writer.release()
            for remove_qr in [False, True]:
                with self.subTest(remove_qr=remove_qr):
                    command = [sys.executable, '-X', 'utf8', str(Path(video2pdfslides.__file__).resolve()),
                               str(video), '--crop-top', '8', '--crop-bottom', '12']
                    if remove_qr:
                        command.append('--remove-qr')
                    result = subprocess.run(command, cwd=folder, input='', capture_output=True,
                                            encoding='utf-8', timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    output = folder / 'output' / ('sample_crop_t8_b12' + ('_noqr' if remove_qr else ''))
                    png = np.asarray(Image.open(next(output.glob('*.png'))).convert('RGB'))
                    with pikepdf.open(str(output) + '.pdf') as pdf:
                        self.assertEqual(len(pdf.pages), 1)
                        image = pikepdf.PdfImage(next(iter(pdf.pages[0].images.values()))).as_pil_image()
                        pixels = np.asarray(image)
                        self.assertEqual(pixels.shape, (460, 800, 3))
                        self.assertLess(np.min(png[400:442, 140:480]), 150)
                        self.assertTrue(np.all(pixels[400:442, 140:480] > 245))
                        np.testing.assert_array_equal(pixels[150:230, 20:400], png[150:230, 20:400])
                        codes = zxingcpp.read_barcodes(pixels, formats=zxingcpp.BarcodeFormat.QRCode)
                        self.assertEqual(bool(codes), not remove_qr)


if __name__ == '__main__':
    unittest.main()
