import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pikepdf
from PIL import Image

import video2pdfslides as v


class PdfQualityTests(unittest.TestCase):
    def convert(self, frame, enhance_pdf=None):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'slides'
            folder.mkdir()
            path = folder / '0000.png'
            Image.fromarray(frame).save(path, dpi=(144, 144))
            original = path.read_bytes()
            options = {} if enhance_pdf is None else {'enhance_pdf': enhance_pdf}
            with patch.object(v, 'remove_branding_text', side_effect=lambda image: image):
                v.convert_screenshots_to_pdf(folder, **options)
            self.assertEqual(path.read_bytes(), original, 'PDF enhancement must preserve the review PNG')
            with pikepdf.open(str(folder) + '.pdf') as pdf:
                self.assertEqual(len(pdf.pages), 1)
                page = pdf.pages[0]
                image = next(iter(page.images.values()))
                self.assertEqual((int(image.Width), int(image.Height)), (frame.shape[1], frame.shape[0]))
                self.assertEqual(str(image.Filter), '/FlateDecode')
                self.assertAlmostEqual(float(page.MediaBox[2]), frame.shape[1] / 2)
                self.assertAlmostEqual(float(page.MediaBox[3]), frame.shape[0] / 2)
                return np.array(pikepdf.PdfImage(image).as_pil_image().convert('RGB'))

    def test_original_mode_preserves_4k_one_pixel_details_exactly(self):
        frame = np.full((2160, 3840, 3), 255, dtype=np.uint8)
        frame[100:2060:17, 100:3740] = (20, 60, 100)
        frame[100:2060, 100:3740:19] = (80, 20, 50)
        cv2.putText(frame, '4K native pixel detail', (130, 80), cv2.FONT_HERSHEY_SIMPLEX,
                    1, (0, 0, 0), 1, cv2.LINE_AA)
        np.testing.assert_array_equal(self.convert(frame), frame)

    def test_enhancement_increases_soft_text_edge_contrast_without_resizing(self):
        frame = np.full((650, 1152, 3), 252, dtype=np.uint8)
        cv2.putText(frame, 'AXI3/AXI4 protocol - AR AW W B R', (40, 150),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (20, 20, 20), 2, cv2.LINE_AA)
        cv2.line(frame, (40, 220), (1080, 220), (80, 120, 170), 1, cv2.LINE_AA)
        frame = cv2.GaussianBlur(frame, (3, 3), .65)
        enhanced = self.convert(frame, enhance_pdf=True)
        sharpness = lambda pixels: cv2.Laplacian(
            cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY), cv2.CV_32F).var()
        self.assertGreater(sharpness(enhanced), sharpness(frame) * 1.15)
        np.testing.assert_array_equal(enhanced[300:], frame[300:])


if __name__ == '__main__':
    unittest.main()
