"""Install a verified Windows yt-dlp executable inside this project."""

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import urllib.request


VERSION = "latest"
# Follow the official stable release so the UI's update button really updates.
RELEASE_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download"


def install_downloader(tools_dir=None):
    """Download and verify yt-dlp before atomically replacing the local tool."""
    folder = Path(tools_dir) if tools_dir is not None else Path(__file__).resolve().parent / ".tools"
    destination = folder / "yt-dlp.exe"
    with urllib.request.urlopen(f"{RELEASE_URL}/SHA2-256SUMS", timeout=60) as response:
        manifest = response.read().decode("ascii")

    expected_hash = None
    for line in manifest.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == "yt-dlp.exe":
            expected_hash = parts[0].lower()
            break
    if not expected_hash or len(expected_hash) != 64 or any(
        character not in "0123456789abcdef" for character in expected_hash
    ):
        raise ValueError("官方 SHA-256 清单中缺少有效的 yt-dlp.exe 校验值。")

    folder.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".yt-dlp-", suffix=".tmp", dir=folder, delete=False) as output:
            temporary_path = Path(output.name)
            digest = hashlib.sha256()
            with urllib.request.urlopen(f"{RELEASE_URL}/yt-dlp.exe", timeout=60) as response:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
        if digest.hexdigest() != expected_hash:
            raise ValueError("下载文件的 SHA-256 校验失败，现有下载器已保留。")
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return destination


def main():
    print(f"正在安装项目专用 yt-dlp {VERSION}，请稍候……", flush=True)
    try:
        destination = install_downloader()
    except (OSError, ValueError) as error:
        print(f"安装失败：{error}\n请检查网络连接后重试。", file=sys.stderr)
        return 1
    print(f"安装完成，SHA-256 校验通过：{destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
