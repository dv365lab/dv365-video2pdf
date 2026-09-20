import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

import video2pdfslides


class SlideDetectionTests(unittest.TestCase):
    def page(self, label, color=255):
        frame = np.full((338, 600, 3), color, dtype=np.uint8)
        cv2.putText(frame, label, (30, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
        return frame

    def extract(self, frames):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            samples = [(i + 1, i / video2pdfslides.FRAME_RATE, frame.copy()) for i, frame in enumerate(frames)]
            with patch.object(video2pdfslides, 'get_frames', return_value=iter(samples)):
                video2pdfslides.detect_unique_screenshots('sample.mp4', temp)
            return [cv2.imread(str(path)) for path in sorted(Path(temp).glob('*.png'))]

    def assert_contains(self, screenshots, frame):
        self.assertTrue(any(np.array_equal(image, frame) for image in screenshots), 'A displayed slide was omitted')

    def test_single_sample_video_produces_a_page(self):
        frame = self.page('Only page')
        screenshots = self.extract([frame])
        self.assertEqual(len(screenshots), 1)
        self.assert_contains(screenshots, frame)

    def test_first_brief_middle_and_last_pages_are_preserved(self):
        a, b, c = self.page('A', 255), self.page('B', 180), self.page('C', 100)
        screenshots = self.extract([a, b, b, c])
        for page in (a, b, c):
            self.assert_contains(screenshots, page)

    def test_small_text_change_is_not_swallowed_by_background_model(self):
        a, b = self.page('Bug001'), self.page('Bug002')
        screenshots = self.extract([a] * 8 + [b] * 8)
        self.assertEqual(len(screenshots), 2)
        self.assert_contains(screenshots, a)
        self.assert_contains(screenshots, b)

    def test_returning_to_previous_slide_is_captured_in_sequence(self):
        a, b = self.page('A', 255), self.page('B', 180)
        screenshots = self.extract([a] * 6 + [b] * 6 + [a] * 6)
        self.assertEqual(len(screenshots), 3)
        for image, expected in zip(screenshots, (a, b, a)):
            np.testing.assert_array_equal(image, expected)

    def test_progressive_reveal_retains_the_complete_final_slide(self):
        frame = self.page('Progressive reveal')
        frames = [frame.copy()] * 2
        for i in range(4):
            cv2.putText(frame, f'item {i}', (30, 130 + 30 * i), cv2.FONT_HERSHEY_SIMPLEX, .4, (0, 0, 0), 1)
            frames.extend([frame.copy()] * 2)
        screenshots = self.extract(frames)
        self.assert_contains(screenshots, frame)

    def test_moving_pointer_neither_blocks_nor_floods_a_static_page(self):
        frames = []
        for i in range(20):
            frame = self.page('Static slide')
            x = 30 + (i % 10) * 50
            frame[240:257, x:x + 17] = 0
            frames.append(frame)
        screenshots = self.extract(frames)
        self.assertGreaterEqual(len(screenshots), 1)
        self.assertLessEqual(len(screenshots), 2)

    def test_continuous_motion_does_not_turn_every_sample_into_a_page(self):
        a, b = self.page('Before motion'), self.page('After motion', 180)
        rng = np.random.default_rng(42)
        motion = [rng.integers(0, 256, (338, 600, 3), dtype=np.uint8) for _ in range(20)]
        screenshots = self.extract([a] * 3 + motion + [b] * 3)
        self.assert_contains(screenshots, a)
        self.assert_contains(screenshots, b)
        self.assertLessEqual(len(screenshots), 3)


if __name__ == '__main__':
    unittest.main()
