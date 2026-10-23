"""Resolve local video files and folders before adding conversion jobs."""

from pathlib import Path


VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".wmv", ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".flv"}


def resolve_local_sources(source):
    """Return absolute video paths from one file or the current folder level."""
    if not isinstance(source, str):
        raise ValueError('请填写本地视频或视频文件夹的完整路径。')
    source = source.strip()
    if len(source) >= 2 and source[0] == source[-1] and source[0] in ('"', "'"):
        source = source[1:-1]
    if not source:
        raise ValueError('请填写本地视频或视频文件夹的完整路径。')

    path = Path(source).expanduser()
    if not path.is_absolute():
        raise ValueError('请输入本机视频文件或文件夹的完整绝对路径。')
    try:
        path = path.resolve()
        if not path.exists():
            raise ValueError('本地路径不存在，请检查视频文件或文件夹路径。')
        if path.is_dir():
            videos = sorted(
                (file for file in path.iterdir()
                 if file.is_file() and file.suffix.lower() in VIDEO_EXTENSIONS and file.stat().st_size > 0),
                key=lambda file: file.name.casefold(),
            )
            if not videos:
                raise ValueError('该文件夹中没有非空视频文件；仅扫描当前层，不包含子文件夹。')
            return [str(file.resolve()) for file in videos]
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError('视频格式不受支持，请填写视频文件或视频文件夹路径。')
        if path.stat().st_size == 0:
            raise ValueError('视频文件为空，请检查原始视频。')
        return [str(path)]
    except OSError as error:
        raise ValueError('无法读取本地路径，请检查路径、文件占用及访问权限。') from error
