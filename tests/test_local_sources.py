import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_sources import resolve_local_sources


class LocalSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_directory_finds_sorted_nonempty_videos_without_recursion(self):
        folder = self.root / '课程 videos'
        folder.mkdir()
        for name in ('b.MP4', 'A.avi', '笔记.txt', '讲义.pdf'):
            (folder / name).write_bytes(b'content')
        (folder / 'empty.mp4').touch()
        (folder / 'nested.mp4').mkdir()
        (folder / 'nested.mp4' / 'inner.avi').write_bytes(b'content')

        self.assertEqual(resolve_local_sources(f'  "{folder}"  '),
                         [str(folder / 'A.avi'), str(folder / 'b.MP4')])

    def test_file_accepts_chinese_name_spaces_and_quotes(self):
        video = self.root / '第 1 课.MP4'
        video.write_bytes(b'content')
        for source in (str(video), f' "{video}" ', f" '{video}' "):
            with self.subTest(source=source):
                self.assertEqual(resolve_local_sources(source), [str(video)])

    def test_path_is_normalized(self):
        video = self.root / 'lesson.mp4'
        video.write_bytes(b'content')
        child = self.root / 'child'
        child.mkdir()
        self.assertEqual(resolve_local_sources(str(child / '..' / video.name)), [str(video)])

    def test_invalid_inputs_report_how_to_enter_path(self):
        for source in (None, 123, '', '  ', '""', "''"):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, '请填写.*路径'):
                    resolve_local_sources(source)

    def test_relative_path_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '绝对路径'):
            resolve_local_sources('课程/lesson.mp4')

    def test_missing_path_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '路径不存在'):
            resolve_local_sources(str(self.root / 'missing'))

    def test_empty_video_is_rejected(self):
        video = self.root / 'empty.mp4'
        video.touch()
        with self.assertRaisesRegex(ValueError, '视频文件为空'):
            resolve_local_sources(str(video))

    def test_unsupported_file_is_rejected(self):
        document = self.root / '讲义.pdf'
        document.write_bytes(b'content')
        with self.assertRaisesRegex(ValueError, '视频格式不受支持'):
            resolve_local_sources(str(document))

    def test_empty_directory_explains_scan_scope(self):
        with self.assertRaisesRegex(ValueError, '没有非空视频.*不包含子文件夹'):
            resolve_local_sources(str(self.root))

    def test_unreadable_directory_reports_friendly_error(self):
        with patch.object(Path, 'iterdir', side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(ValueError, '无法读取.*权限'):
                resolve_local_sources(str(self.root))


if __name__ == '__main__':
    unittest.main()
