"""Interactive launcher; keep user input out of CMD command expansion."""

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def _has_buffered_input():
    '''True when more pasted text is already waiting in the console buffer.'''
    try:
        import msvcrt
    except ImportError:
        return False
    return msvcrt.kbhit()


def read_source():
    '''Read a path/URL, tolerating a paste that begins with a blank line.'''
    value = input("视频路径或网址: ").strip()
    # A paste starting with a newline leaves the real text buffered, so only an
    # empty prompt with nothing buffered (a bare Enter) counts as cancel.
    while not value and _has_buffered_input():
        value = input().strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        value = value[1:-1].strip()
    return value


def run():
    print("video2pdf - 视频截图与 PDF 生成工具")
    print("请输入本地视频路径或视频网址，也可将视频文件拖入此窗口，然后按回车。")
    print("直接回车可取消。\n")
    value = read_source()
    if not value:
        print("已取消。")
        return 0

    is_url = value.lower().startswith(("http://", "https://"))
    if not is_url:
        video = Path(value)
        if not video.is_absolute():
            video = PROJECT_ROOT / video
        if not video.is_file():
            print("[错误] 视频文件不存在，或输入的是文件夹。请重新运行并输入视频文件路径。")
            return 1

    extra_args = []
    if is_url:
        cookies = input("（可选）需登录的网址可输入浏览器名借用其 Cookie（chrome/edge/firefox 等），直接回车跳过: ").strip()
        if cookies:
            extra_args += ["--cookies-from-browser", cookies]

    print()
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-u", str(PROJECT_ROOT / "video2pdfslides.py"), value, *extra_args],
        cwd=PROJECT_ROOT,
    )
    if result.returncode == 0:
        print("转换完成，结果位于项目的 output 目录。")
    else:
        print("[错误] 转换失败，请查看上方报错信息。")
    return result.returncode


def main():
    try:
        exit_code = run()
    except EOFError:
        print("\n已取消。")
        exit_code = 0
    except KeyboardInterrupt:
        print("\n已取消。")
        exit_code = 130
    except OSError as error:
        print(f"[错误] 无法启动转换：{error}")
        exit_code = 1
    try:
        input("\n按回车键关闭窗口。")
    except (EOFError, KeyboardInterrupt):
        pass
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
