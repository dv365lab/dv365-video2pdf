import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pikepdf

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
        self.assertEqual(len(screenshots), 1)
        self.assert_contains(screenshots, frame)

    def test_each_animation_group_keeps_only_its_last_frame(self):
        frames, complete = [], []
        for title in ('First topic', 'Second topic'):
            frame = self.page(title)
            frames.extend([frame.copy()] * 3)
            for index in range(3):
                cv2.rectangle(frame, (40 + 170 * index, 130), (160 + 170 * index, 270), (40, 80, 120), -1)
                frames.extend([frame.copy()] * 4)
            complete.append(frame.copy())
        screenshots = self.extract(frames)
        self.assertEqual(len(screenshots), 2)
        for image, expected in zip(screenshots, complete):
            np.testing.assert_array_equal(image, expected)

    def test_dark_slide_reveals_stay_in_one_group_despite_long_pauses(self):
        frame = np.full((338, 600, 3), 25, dtype=np.uint8)
        cv2.putText(frame, 'Dark slide', (30, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        frames = [frame.copy()] * 40
        for index in range(3):
            cv2.putText(frame, f'item {index}', (30, 150 + 45 * index), cv2.FONT_HERSHEY_SIMPLEX,
                        .7, (150, 210, 255), 2)
            frames.extend([frame.copy()] * 40)
        screenshots = self.extract(frames)
        self.assertEqual(len(screenshots), 1)
        np.testing.assert_array_equal(screenshots[0], frame)

    def test_large_graphic_and_later_text_are_one_animation_group(self):
        title = self.page('Large graphic')
        graphic = title.copy()
        cv2.rectangle(graphic, (20, 100), (580, 300), (100, 160, 220), -1)
        complete = graphic.copy()
        cv2.putText(complete, 'Final caption', (30, 330), cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 0, 0), 2)
        screenshots = self.extract([title] * 4 + [graphic] * 4 + [complete] * 4)
        self.assertEqual(len(screenshots), 1)
        np.testing.assert_array_equal(screenshots[0], complete)

    def test_fading_in_text_finishes_at_full_contrast(self):
        title = self.page('Fading in')
        frames = [title.copy()] * 3
        for brightness in (170, 100, 0):
            frame = title.copy()
            cv2.putText(frame, 'Final content', (30, 180), cv2.FONT_HERSHEY_SIMPLEX,
                        1, (brightness,) * 3, 2)
            frames.extend([frame] * 3)
        screenshots = self.extract(frames)
        self.assertEqual(len(screenshots), 1)
        np.testing.assert_array_equal(screenshots[0], frame)

    def test_background_change_is_not_an_additive_reveal(self):
        first = self.page('Same title', 255)
        second = self.page('Same title', 180)
        screenshots = self.extract([first] * 4 + [second] * 4)
        self.assertEqual(len(screenshots), 2)
        for image, expected in zip(screenshots, (first, second)):
            np.testing.assert_array_equal(image, expected)

    def test_complete_large_reveal_is_kept_even_if_visible_for_only_one_sample(self):
        partial = self.page('Brief final reveal')
        complete = partial.copy()
        cv2.rectangle(complete, (40, 130), (550, 280), (30, 90, 150), -1)
        next_page = self.page('Next page', 160)
        for tail in ([], [next_page] * 4):
            with self.subTest(at_end=not tail):
                screenshots = self.extract([partial] * 4 + [complete] + tail)
                self.assertEqual(len(screenshots), 1 + bool(tail))
                np.testing.assert_array_equal(screenshots[0], complete)
                if tail:
                    np.testing.assert_array_equal(screenshots[1], next_page)

    def test_small_text_replacement_under_shared_title_is_a_separate_page(self):
        first = self.page('Shared presentation title')
        cv2.putText(first, 'Version 001', (30, 160), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
        second = self.page('Shared presentation title')
        cv2.putText(second, 'Version 002', (30, 160), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
        screenshots = self.extract([first] * 5 + [second] * 5)
        self.assertEqual(len(screenshots), 2)
        for image, expected in zip(screenshots, (first, second)):
            np.testing.assert_array_equal(image, expected)

    def test_complete_slide_is_not_replaced_by_a_subsequent_blank_frame(self):
        partial = self.page('Completed before blank')
        complete = partial.copy()
        cv2.putText(complete, 'Final item', (30, 160), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
        screenshots = self.extract([partial] * 4 + [complete] * 4 + [np.full_like(complete, 255)] * 3)
        np.testing.assert_array_equal(screenshots[0], complete)

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

    def test_cli_png_and_pdf_contain_only_complete_animation_groups(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            folder = Path(temp).resolve()
            video = folder / 'animations.avi'
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 2, (600, 338))
            self.assertTrue(writer.isOpened())
            complete = []
            try:
                for title in ('First topic', 'Second topic'):
                    frame = self.page(title)
                    for stage in range(4):
                        if stage:
                            cv2.putText(frame, f'Item {stage}', (30, 110 + 50 * stage),
                                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
                        for _ in range(6):
                            writer.write(frame)
                    complete.append(frame.copy())
            finally:
                writer.release()
            result = subprocess.run(
                [sys.executable, str(Path(video2pdfslides.__file__).resolve()), str(video)],
                cwd=folder, input='', capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            output = folder / 'output' / 'animations'
            screenshots = sorted(output.glob('*.png'))
            self.assertEqual(len(screenshots), 2, result.stdout)
            self.assertEqual([path.name for path in screenshots], ['0000_0.18.png', '0001_0.38.png'])
            for path, expected in zip(screenshots, complete):
                image = cv2.imread(str(path))
                self.assertEqual(image.shape, expected.shape)
                self.assertLess(np.mean(cv2.absdiff(image, expected)), 1)
            with pikepdf.open(str(output) + '.pdf') as document:
                self.assertEqual(len(document.pages), 2)


if __name__ == '__main__':
    unittest.main()
