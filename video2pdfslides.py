import os
import time
import cv2
import imutils
import shutil
import img2pdf
import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
import numpy as np
import zxingcpp

############# Define constants

OUTPUT_SLIDES_DIR = f"./output"
PROJECT_DOWNLOADER = Path(__file__).resolve().parent / '.tools' / 'yt-dlp.exe'

FRAME_RATE = 1                   # Sample every 1 second to retain briefly completed slides.
PIXEL_CHANGE_THRESHOLD = 20      # Ignore small compression/color fluctuations in the analysis image.
STABLE_CHANGE_PERCENT = 0.4      # Tolerate small pointer movements between adjacent samples.
PAGE_CHANGE_PERCENT = 0.1        # Persistent content change relative to the selected page.
SCENE_CHANGE_PERCENT = 3         # Preserve the preceding frame before a large transition.


def resolve_video_source(source, referer=None, cookies_from_browser=None, add_headers=None):
    '''Download HTTP(S) video links; leave local paths unchanged.'''
    if source.lower().startswith(('http://', 'https://')):
        if not urlsplit(source).hostname:
            raise ValueError('视频网址无效，请输入完整的 http:// 或 https:// 网址。')
        return download_video(
            source, referer=referer,
            cookies_from_browser=cookies_from_browser, add_headers=add_headers,
        )
    if '://' in source:
        raise ValueError('仅支持 http:// 或 https:// 视频网址。')
    return source


def get_downloader():
    '''Prefer the project tool and reject versions with the old YouTube client.'''
    downloader = str(PROJECT_DOWNLOADER) if PROJECT_DOWNLOADER.is_file() else shutil.which('yt-dlp')
    setup_hint = r'请运行 .\.venv\Scripts\python.exe setup_downloader.py 安装项目下载器。'
    if downloader is None:
        raise RuntimeError('未找到 yt-dlp。' + setup_hint)
    try:
        result = subprocess.run(
            [downloader, '--version'], check=True, capture_output=True,
            encoding='utf-8', errors='replace', timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError('无法启动 yt-dlp。' + setup_hint) from error
    version = result.stdout.strip()
    match = re.match(r'^(\d{4})\.(\d{1,2})\.(\d{1,2})(?:\.|$)', version)
    if not match or tuple(map(int, match.groups())) < (2026, 8, 19):
        raise RuntimeError(f'yt-dlp 版本过旧或无法识别（{version}），需要 2026.08.19 或更新版本。' + setup_hint)
    return downloader


VIDYARD_REFERER = 'https://play.vidyard.com/'


def default_referer(url):
    '''Vidyard CDN streams return HTTP 403 without a Referer header.'''
    host = (urlsplit(url).hostname or '').lower()
    if host == 'vidyard.com' or host.endswith('.vidyard.com'):
        return VIDYARD_REFERER
    return None


def upgrade_vidyard_master(url):
    '''A Vidyard variant playlist (index-*.m3u8) locks to one quality; the sibling
    master.m3u8 lists every resolution so best-quality selection can reach 1080p.'''
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    if not (host == 'vidyard.com' or host.endswith('.vidyard.com')):
        return url
    if '.urlset/' not in parts.path:
        return url
    directory, sep, last = parts.path.rpartition('/')
    if not sep or last == 'master.m3u8' or not last.endswith('.m3u8'):
        return url
    return parts._replace(path=f'{directory}/master.m3u8').geturl()


def download_video(url, download_dir='input/downloads', referer=None, cookies_from_browser=None, add_headers=None):
    '''Use the external yt-dlp command without changing the project Python environment.'''
    downloader = get_downloader()

    upgraded = upgrade_vidyard_master(url)
    if upgraded != url:
        print('已切换到 Vidyard 主播放列表以获取最高清晰度:', upgraded, flush=True)
        url = upgraded

    if referer is None:
        referer = default_referer(url)

    folder = Path(download_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    url_id = hashlib.sha256(url.encode('utf-8')).hexdigest()[:12]
    print('下载工具:', downloader, flush=True)
    if referer:
        print('使用 Referer:', referer, flush=True)
    if cookies_from_browser:
        print('使用浏览器 Cookie:', cookies_from_browser, flush=True)
    print('正在下载视频，保存目录:', folder, flush=True)
    with tempfile.TemporaryDirectory(prefix='.download-', dir=folder) as temp:
        result_file = Path(temp) / 'video-path.jsonl'
        options = []
        if referer:
            options += ['--referer', referer]
        if cookies_from_browser:
            options += ['--cookies-from-browser', cookies_from_browser]
        for header in add_headers or []:
            options += ['--add-header', header]
        command = [
            downloader, '--ignore-config', '--no-cache-dir', '--no-playlist', '--playlist-items', '1',
            '--match-filters', '!is_live & !playlist & live_status!=?is_upcoming',
            '--format', 'bv*[vcodec^=avc1]/bv*[vcodec^=h264]/bv*',
            '--check-formats',
            '--restrict-filenames', '--windows-filenames', '--no-overwrites',
            '--paths', str(folder),
            '--output', '%(extractor_key)s-%(id)s-' + url_id + '.%(ext)s',
            '--print-to-file', 'after_move:%(filepath)j', str(result_file).replace('%', '%%'),
            '--no-simulate', '--newline', '--encoding', 'utf-8',
            '--socket-timeout', '30', '--retries', '3', '--fragment-retries', '3',
            *options,
            '--', url,
        ]
        try:
            subprocess.run(command, check=True, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.CalledProcessError) as error:
            raise RuntimeError('视频下载失败，请查看上方 yt-dlp 报错，检查网络、网址或更新下载工具。') from error
        if not result_file.is_file():
            raise RuntimeError('未下载到视频，请使用单个非直播视频的网址；不支持纯播放列表或未开始的视频。')
        results = result_file.read_text(encoding='utf-8').splitlines()
        if len(results) != 1:
            raise RuntimeError('下载结果不是单个视频，请使用具体视频的网址。')
        downloaded = Path(json.loads(results[0]))
        if not downloaded.is_file() or downloaded.stat().st_size == 0:
            raise RuntimeError('下载的视频文件不存在或为空，请重新下载。')
    print('视频已下载:', downloaded, flush=True)
    return str(downloaded)


def get_frames(video_path):
    '''A fucntion to return the frames from a video located at video_path
    this function skips frames as defined in FRAME_RATE'''
    
    
    # open a pointer to the video file initialize the width and height of the frame
    vs = cv2.VideoCapture(video_path)
    if not vs.isOpened():
        raise Exception(f'unable to open file {video_path}')


    total_frames = vs.get(cv2.CAP_PROP_FRAME_COUNT)
    fps = vs.get(cv2.CAP_PROP_FPS)
    duration = total_frames / fps if total_frames > 0 and fps > 0 else None
    frame_time = 0
    frame_count = 0
    print("total_frames: ", total_frames)
    print("FRAME_RATE", FRAME_RATE)

    # loop over the frames of the video
    # Some decoders keep returning the last frame after an out-of-range seek.
    while duration is None or frame_time < duration:
        # grab a frame from the video

        vs.set(cv2.CAP_PROP_POS_MSEC, frame_time * 1000)    # move frame to a timestamp

        (success, frame) = vs.read()
        # if the frame is None, then we have reached the end of the video file
        if not success or frame is None:
            break

        frame_count += 1
        yield frame_count, frame_time, frame
        frame_time = frame_count / FRAME_RATE

    vs.release()
 


def crop_frame(frame, crop_top=0, crop_bottom=0):
    '''Remove header and footer rows without modifying the source frame.'''
    height = frame.shape[0]
    if crop_top < 0 or crop_bottom < 0:
        raise ValueError('crop sizes must be non-negative')
    if crop_top + crop_bottom >= height:
        raise ValueError(f'crop sizes must add up to less than the frame height ({height})')
    return frame[crop_top:height - crop_bottom, :]


def remove_qr_codes(frame):
    '''Cover detected QR polygons using nearby background colors.'''
    codes = zxingcpp.read_barcodes(frame, formats=zxingcpp.BarcodeFormat.QRCode)
    if not codes:
        return frame

    cleaned = frame.copy()
    kernel = np.ones((9, 9), dtype=np.uint8)
    for code in codes:
        position = code.position
        corners = [position.top_left, position.top_right, position.bottom_right, position.bottom_left]
        polygon = np.array([(point.x, point.y) for point in corners], dtype=np.int32)
        mask = np.zeros(frame.shape[:2], dtype=np.uint8)
        cv2.fillConvexPoly(mask, polygon, 255)
        mask = cv2.dilate(mask, kernel)  # Include four pixels around the detected code.
        ring = (cv2.dilate(mask, kernel) != 0) & (mask == 0)
        pixels = frame[ring] if np.any(ring) else frame[mask != 0]
        # Favor the lighter background over nearby text and slide borders.
        background = np.percentile(pixels, 75, axis=0).astype(frame.dtype)
        cleaned[mask != 0] = background
    return cleaned


def detect_unique_screenshots(video_path, output_folder_screenshot_path, crop_top=0, crop_bottom=0, remove_qr=False):
    '''Select stable content changes, retaining short pages and final animation states.'''
    start_time = time.time()
    screenshots_count = 0
    previous = None
    before_previous = None
    pending = None
    anchor = None
    motion = 0
    previous_motion = 0
    persistent_change = 0

    def changed_pixels(first, second):
        return cv2.absdiff(first, second) > PIXEL_CHANGE_THRESHOLD

    def percent(mask):
        return 100 * np.count_nonzero(mask) / mask.size

    def flush():
        nonlocal screenshots_count
        if pending is None:
            return
        image, _, timestamp = pending
        filename = f'{screenshots_count:04}_{round(timestamp / 60, 2)}.png'
        path = os.path.join(output_folder_screenshot_path, filename)
        print('saving', path)
        if not cv2.imwrite(path, image):
            raise OSError(f'unable to save screenshot {path}')
        screenshots_count += 1

    def select(candidate, reference=None):
        nonlocal pending, anchor
        if anchor is None or percent(changed_pixels(anchor, candidate[1])) >= PAGE_CHANGE_PERCENT:
            flush()
            pending = candidate
            anchor = candidate[1] if reference is None else reference

    for _, frame_time, frame in get_frames(video_path):
        if remove_qr:
            frame = remove_qr_codes(frame)
        frame = crop_frame(frame, crop_top, crop_bottom)
        small = imutils.resize(frame, width=600)
        feature = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (3, 3), 0)
        current = (frame.copy(), feature, frame_time)
        if previous is not None:
            motion = percent(changed_pixels(previous[1], feature))
            # A brief completed page may have only one sample before a transition.
            if motion >= SCENE_CHANGE_PERCENT and previous_motion < SCENE_CHANGE_PERCENT:
                select(previous)
            if anchor is not None:
                persistent_change = percent(
                    changed_pixels(anchor, previous[1]) & changed_pixels(anchor, feature)
                )
            if motion <= STABLE_CHANGE_PERCENT:
                if anchor is None or persistent_change >= PAGE_CHANGE_PERCENT:
                    if before_previous is not None:
                        # Median reference removes transient moving pointers without changing the saved image.
                        a, b, c = before_previous[1], previous[1], feature
                        reference = cv2.max(cv2.min(a, b), cv2.min(cv2.max(a, b), c))
                        select(current, reference)
                    elif motion < PAGE_CHANGE_PERCENT:
                        select(current)
                elif percent(changed_pixels(anchor, feature)) < PAGE_CHANGE_PERCENT:
                    # Keep later small reveals without resetting the fixed comparison baseline.
                    pending = current
        previous_motion = motion
        before_previous, previous = previous, current

    if previous is not None:
        if anchor is None or motion > STABLE_CHANGE_PERCENT or persistent_change >= PAGE_CHANGE_PERCENT:
            select(previous)
        elif percent(changed_pixels(anchor, previous[1])) < PAGE_CHANGE_PERCENT:
            pending = previous
    flush()
    print(f'{screenshots_count} screenshots Captured!')
    print(f'Time taken {time.time()-start_time}s')




def initialize_output_folder(video_path, crop_top=0, crop_bottom=0, remove_qr=False):
    '''Clean the output folder if already exists'''
    video_name = Path(video_path.replace("\\", "/")).stem
    if crop_top or crop_bottom:
        video_name += f"_crop_t{crop_top}_b{crop_bottom}"
    if remove_qr:
        video_name += "_noqr"
    output_folder_screenshot_path = os.path.join(OUTPUT_SLIDES_DIR, video_name)

    if os.path.exists(output_folder_screenshot_path):
        shutil.rmtree(output_folder_screenshot_path)

    os.makedirs(output_folder_screenshot_path, exist_ok=True)
    print('initialized output folder', output_folder_screenshot_path)
    return output_folder_screenshot_path


def convert_screenshots_to_pdf(output_folder_screenshot_path):
    output_pdf_path = str(output_folder_screenshot_path) + '.pdf'
    print('output_folder_screenshot_path', output_folder_screenshot_path)
    print('output_pdf_path', output_pdf_path)
    print('converting images to pdf..')
    with open(output_pdf_path, "wb") as f:
        f.write(img2pdf.convert([str(path) for path in sorted(Path(output_folder_screenshot_path).glob("*.png"))]))
    print('Pdf Created!')
    print('pdf saved at', output_pdf_path)


if __name__ == "__main__":
    
#     video_path = "./input/Test Video 2.mp4"
#     choice = 'y'
#     output_folder_screenshot_path = initialize_output_folder(video_path)
    
    
    parser = argparse.ArgumentParser("video_path")
    parser.add_argument("video_path", help="local video path or HTTP(S) video URL to convert to PDF slides", type=str)
    parser.add_argument("--review", action="store_true", help="pause to review screenshots before creating the PDF")
    parser.add_argument("--crop-top", type=int, default=0, metavar="PIXELS", help="crop this many pixels from the top before detection and saving")
    parser.add_argument("--crop-bottom", type=int, default=0, metavar="PIXELS", help="crop this many pixels from the bottom before detection and saving")
    parser.add_argument("--remove-qr", action="store_true", help="detect QR codes and cover them with nearby background colors")
    parser.add_argument("--referer", type=str, default=None, metavar="URL", help="Referer header for the downloader (auto-set for Vidyard URLs)")
    parser.add_argument("--cookies-from-browser", type=str, default=None, metavar="BROWSER", help="use cookies from this browser for login-gated URLs, e.g. chrome, edge, firefox")
    parser.add_argument("--add-header", action="append", default=None, metavar="NAME:VALUE", help="extra HTTP header for the downloader; may be repeated")
    args = parser.parse_args()
    try:
        video_path = resolve_video_source(
            args.video_path, referer=args.referer,
            cookies_from_browser=args.cookies_from_browser, add_headers=args.add_header,
        )
    except ValueError as error:
        parser.error(str(error))
    except (RuntimeError, OSError) as error:
        parser.exit(1, f'错误: {error}\n')

    if args.crop_top or args.crop_bottom or video_path != args.video_path:
        preview_video = cv2.VideoCapture(video_path)
        try:
            success, preview_frame = preview_video.read()
        finally:
            preview_video.release()
        if not success or preview_frame is None:
            parser.error(f'unable to open file {video_path}')
        try:
            crop_frame(preview_frame, args.crop_top, args.crop_bottom)
        except ValueError as error:
            parser.error(str(error))
        if args.crop_top or args.crop_bottom:
            print(f'cropping top {args.crop_top}px and bottom {args.crop_bottom}px')

    print('video_path', video_path)
    output_folder_screenshot_path = initialize_output_folder(video_path, args.crop_top, args.crop_bottom, args.remove_qr)
    detect_unique_screenshots(video_path, output_folder_screenshot_path, args.crop_top, args.crop_bottom, args.remove_qr)

    choice = 'y'
    if args.review:
        print('Please Manually verify screenshots and delete duplicates')
        while True:
            choice = input("Press y to continue and n to terminate")
            choice = choice.lower().strip()
            if choice in ['y', 'n']:
                break
            else:
                print('please enter a valid choice')

    if choice == 'y':
        convert_screenshots_to_pdf(output_folder_screenshot_path)
