import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

import video2pdfslides


VIDEO = (
    Path(__file__).resolve().parents[1]
    / "input/downloads/Youtube-y1TXjYlRjBU-38b5dfb87e48.mp4"
)
REFERENCES = [
    (2.0, "Pi Agent / SUB AGENT + WORKFLOW"),
    (10.5, "一个Agent，搞定简单任务"),
    (14.5, "但复杂任务，需要专业分工"),
    (18.0, "四个角色，组成团队"),
    (24.0, "不同任务，选不同模型"),
    (29.0, "搭建一支多智能体团队"),
    (39.0, "在Pi Agent里搭建团队"),
    (228.0, "工作流，定义怎么推进"),
    (244.0, "专家Agent，定义怎么做"),
    (250.0, "三种编排，按需组合"),
    (262.0, "把协作变成可控的系统"),
    (272.0, "Herdr Workspace，看全局"),
]


def comparison_image(frame):
    """Compare the complete slide, tolerating minor compression differences."""
    height, width = frame.shape[:2]
    small = cv2.resize(frame, (600, int(height * 600 / width)), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (3, 3), 0)


@unittest.skipUnless(VIDEO.is_file(), "local YouTube regression video is unavailable")
class RealSlideCoverageTests(unittest.TestCase):
    def sampled_frames(self, video_path):
        capture = cv2.VideoCapture(str(video_path))
        self.assertTrue(capture.isOpened(), str(video_path))
        frame_count = 0
        try:
            for start, end in [(0, 41), (219, 274)]:
                for half_second in range(start * 2, end * 2 + 1):
                    seconds = half_second / 2
                    capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
                    success, frame = capture.read()
                    self.assertTrue(success and frame is not None, f"unable to read {seconds}s")
                    frame_count += 1
                    yield frame_count, seconds, frame
        finally:
            capture.release()

    def test_all_twelve_previously_missing_slide_themes_are_present(self):
        with tempfile.TemporaryDirectory(prefix="real_slide_coverage_", dir=Path(__file__).parent) as temp:
            folder = Path(temp)
            logs = io.StringIO()
            with patch.object(video2pdfslides, "get_frames", side_effect=self.sampled_frames), \
                    contextlib.redirect_stdout(logs):
                video2pdfslides.detect_unique_screenshots(str(VIDEO), str(folder))

            files = sorted(folder.glob("*.png"))
            self.assertTrue(files, logs.getvalue())
            screenshots = []
            for path in files:
                frame = cv2.imread(str(path))
                self.assertIsNotNone(frame, f"unable to read screenshot {path.name}")
                screenshots.append((path.name, frame.shape, comparison_image(frame)))

            capture = cv2.VideoCapture(str(VIDEO))
            self.assertTrue(capture.isOpened(), str(VIDEO))
            try:
                for seconds, title in REFERENCES:
                    with self.subTest(seconds=seconds, title=title):
                        capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
                        success, reference = capture.read()
                        self.assertTrue(success and reference is not None, f"unable to read reference at {seconds}s")
                        expected = comparison_image(reference)
                        matches = []
                        for name, shape, screenshot in screenshots:
                            self.assertEqual(shape, reference.shape, f"changed image dimensions: {name}")
                            changed = cv2.absdiff(expected, screenshot) > 20
                            percent = 100 * np.count_nonzero(changed) / changed.size
                            matches.append((percent, name))
                        closest_percent, closest_name = min(matches)
                        self.assertLess(
                            closest_percent, 0.15,
                            f"missing slide '{title}' at {seconds}s; nearest output "
                            f"{closest_name} differs by {closest_percent:.4f}% of pixels",
                        )
            finally:
                capture.release()


if __name__ == "__main__":
    unittest.main()
