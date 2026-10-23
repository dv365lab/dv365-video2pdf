import os
import time
import cv2
import imutils
import shutil
import img2pdf
import argparse
import hashlib
import json
import math
import re
import subprocess
import tempfile
import unicodedata
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit
import numpy as np
import zxingcpp
from PIL import Image

############# Define constants

OUTPUT_SLIDES_DIR = f"./output"
PROJECT_DOWNLOADER = Path(__file__).resolve().parent / '.tools' / 'yt-dlp.exe'

FRAME_RATE = 1                   # Sample every 1 second to retain briefly completed slides.
PIXEL_CHANGE_THRESHOLD = 20      # Ignore small compression/color fluctuations in the analysis image.
STABLE_CHANGE_PERCENT = 0.4      # Tolerate small pointer movements between adjacent samples.
PAGE_CHANGE_PERCENT = 0.1        # Persistent content change relative to the selected page.
SCENE_CHANGE_PERCENT = 3         # Preserve the preceding frame before a large transition.


def report_progress(stage, **values):
    '''Send flushed stage progress to the GUI without estimating remaining work.'''
    print('VIDEO2PDF_PROGRESS ' + json.dumps(dict(stage=stage, **values)), flush=True)


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
    total_samples = math.ceil(duration * FRAME_RATE) if duration and math.isfinite(duration) else None
    report_progress('extract', current=0, total=total_samples)
    last_percent = 0

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
        # The caller has finished processing this frame when the generator resumes.
        percent = min(100, int(frame_count * 100 / total_samples)) if total_samples else None
        if percent is None or percent != last_percent:
            report_progress('extract', current=frame_count, total=total_samples)
            last_percent = percent
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


BRANDING_TEXT = re.compile(
    r'半导体教育领航者|(?:https?://)?(?:www\.)?eeeknow\.com(?:\(移知\))?|\(?移知\)?'
)


def normalize_branding_text(text):
    return ''.join(unicodedata.normalize('NFKC', text).casefold().split())


@lru_cache(maxsize=1)
def get_text_ocr():
    '''Load the bundled Chinese/English OCR models once, without online services.'''
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as error:
        raise RuntimeError('缺少文字识别依赖，请先安装 requirements.txt 中的依赖。') from error
    # Higher detection resolution also finds the small cyan lettering on dark covers.
    return RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1, det_limit_side_len=1080)


@lru_cache(maxsize=1)
def get_branding_logo_template():
    '''Binary shape taken from the supplied cyan interlocking-square logo.'''
    path = Path(__file__).resolve().parent / 'assets' / 'eeeknow-logo-mask.png'
    return cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)


def remove_corner_branding(frame):
    '''Locate the known corner logo, then cover its entire logo/name/date block.'''
    height, width = frame.shape[:2]
    left, top = int(width * 0.7), int(height * 0.75)
    hsv = cv2.cvtColor(frame[top:, left:], cv2.COLOR_BGR2HSV)
    cyan = cv2.inRange(hsv, np.array([80, 90, 75]), np.array([105, 255, 255]))
    if np.count_nonzero(cyan) < 40:
        return frame
    template = get_branding_logo_template()
    # Padding permits a slightly cropped logo at the bottom/right video edges.
    cyan = cv2.copyMakeBorder(cyan, 0, max(1, round(height * 0.05)),
                             0, max(1, round(width * 0.01)), cv2.BORDER_CONSTANT, value=0)
    step = max(1, round(width / 640))
    best_score, best_box = 0.78, None
    for logo_width in range(max(12, int(width * 0.015)) // step * step, round(width * 0.1) + 1, step):
        logo_height = round(logo_width * template.shape[0] / template.shape[1])
        if logo_height > cyan.shape[0] or logo_width > cyan.shape[1]:
            continue
        scaled = cv2.resize(template, (logo_width, logo_height), interpolation=cv2.INTER_AREA)
        scores = cv2.matchTemplate(cyan, scaled, cv2.TM_CCOEFF_NORMED)
        # The reference mark has its name to the right and date directly below.
        # Require this corner layout; a cyan shape elsewhere must not erase a page.
        x1, y1 = max(0, round(width - 3.6 * logo_width) - left), max(0, round(height - 1.6 * logo_height) - top)
        x2 = min(scores.shape[1], round(width - 0.7 * logo_width) - left + 1)
        y2 = min(scores.shape[0], round(height - 0.55 * logo_height) - top + 1)
        if x2 <= x1 or y2 <= y1:
            continue
        _, score, _, position = cv2.minMaxLoc(scores[y1:y2, x1:x2])
        px, py = x1 + position[0], y1 + position[1]
        visible_width = min(logo_width, width - left - px)
        visible_height = min(logo_height, height - top - py)
        if visible_width < logo_width or visible_height < logo_height:
            # A crop removes pixels, not shape evidence. Score only the visible
            # portion instead of treating the missing strip as a black mismatch.
            visible = cyan[py:py + visible_height, px:px + visible_width]
            score = float(cv2.matchTemplate(visible, scaled[:visible_height, :visible_width], cv2.TM_CCOEFF_NORMED)[0, 0])
        if score > best_score:
            best_score = score
            best_box = (left + px, top + py, logo_height)
    if best_box is None:
        return frame
    x, y, logo_height = best_box
    margin = max(3, round(logo_height * 0.08))
    x, y = max(0, x - margin), max(0, y - margin)
    background = np.concatenate([
        frame[max(0, y - margin):y, x:].reshape(-1, 3),
        frame[y:, max(0, x - margin):x].reshape(-1, 3),
    ])
    if not len(background):
        return frame
    cleaned = frame.copy()
    cleaned[y:, x:] = np.median(background, axis=0).astype(frame.dtype)
    return cleaned


def remove_branding_text(frame):
    '''Clean the known corner mark and recognized branding text in PDF images.'''
    frame = remove_corner_branding(frame)
    results, _ = get_text_ocr()(frame, return_word_box=True, text_score=0.6)
    polygons = []
    for row in results or []:
        box, text, confidence = row[:3]
        normalized = normalize_branding_text(text)
        if confidence < 0.6 or not BRANDING_TEXT.search(normalized):
            continue
        if BRANDING_TEXT.fullmatch(normalized):
            polygons.append(np.rint(box).astype(np.int32))
        elif len(row) >= 5 and len(row[3]) == len(row[4]):
            # OCR can merge branding and body text into one line. Use actual
            # character coordinates rather than covering the entire line.
            characters, boxes = [], []
            for char_box, token in zip(row[3], row[4]):
                token = normalize_branding_text(token)
                characters.extend(token)
                boxes.extend([char_box] * len(token))
            for match in BRANDING_TEXT.finditer(''.join(characters)):
                points = np.rint(boxes[match.start():match.end()]).astype(np.int32)
                polygons.append(cv2.convexHull(points.reshape(-1, 2)))
    if not polygons:
        return frame

    cleaned = frame.copy()
    kernel = np.ones((7, 7), dtype=np.uint8)
    for polygon in polygons:
        mask = np.zeros(frame.shape[:2], dtype=np.uint8)
        cv2.fillConvexPoly(mask, polygon, 255)
        mask = cv2.dilate(mask, kernel)  # Include antialiasing around the text.
        ring = (cv2.dilate(mask, kernel) != 0) & (mask == 0)
        if np.any(ring):
            cleaned[mask != 0] = np.median(frame[ring], axis=0).astype(frame.dtype)
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
    screenshots = sorted(Path(output_folder_screenshot_path).glob('*.png'))
    # Keep the review PNGs intact. Only cleaned copies are embedded in the PDF,
    # so rebuilding a PDF from existing screenshots also removes branding.
    with tempfile.TemporaryDirectory(prefix='video2pdf-text-') as temp:
        pdf_images = []
        for index, path in enumerate(screenshots):
            report_progress('clean', current=index + 1, total=len(screenshots), completed=index)
            print(f'清理 PDF 品牌标记 {index + 1}/{len(screenshots)}: {path.name}', flush=True)
            with Image.open(path) as source:
                frame = np.ascontiguousarray(np.asarray(source.convert('RGB'))[:, :, ::-1])
                cleaned = remove_branding_text(frame)
                if cleaned is not frame:
                    cleaned_path = Path(temp) / f'{index:06}.png'
                    options = {'dpi': source.info['dpi']} if 'dpi' in source.info else {}
                    Image.fromarray(cleaned[:, :, ::-1]).save(cleaned_path, **options)
                    pdf_images.append(str(cleaned_path))
                else:
                    pdf_images.append(str(path))
        # Do not truncate an existing PDF if OCR or image conversion fails.
        report_progress('write', current=len(screenshots), total=len(screenshots))
        pdf_bytes = img2pdf.convert(pdf_images)
        with open(output_pdf_path, "wb") as f:
            f.write(pdf_bytes)
    print('Pdf Created!')
    print('pdf saved at', output_pdf_path)


if __name__ == "__main__":
    
#     video_path = "./input/Test Video 2.mp4"
#     choice = 'y'
#     output_folder_screenshot_path = initialize_output_folder(video_path)
    
    
    parser = argparse.ArgumentParser("video_path")
    parser.add_argument("video_path", help="local video path or HTTP(S) video URL to convert to PDF slides", type=str)
    parser.add_argument("--review", action="store_true", help="pause to review screenshots before creating the PDF")
    parser.add_argument('--extract-only', action='store_true', help='save PNG screenshots without creating a PDF')
    parser.add_argument('--sample-interval', type=float, default=1 / FRAME_RATE, metavar='SECONDS', help='seconds between sampled frames (default: 1)')
    parser.add_argument("--crop-top", type=int, default=0, metavar="PIXELS", help="crop this many pixels from the top before detection and saving")
    parser.add_argument("--crop-bottom", type=int, default=0, metavar="PIXELS", help="crop this many pixels from the bottom before detection and saving")
    parser.add_argument("--remove-qr", action="store_true", help="detect QR codes and cover them with nearby background colors")
    parser.add_argument("--referer", type=str, default=None, metavar="URL", help="Referer header for the downloader (auto-set for Vidyard URLs)")
    parser.add_argument("--cookies-from-browser", type=str, default=None, metavar="BROWSER", help="use cookies from this browser for login-gated URLs, e.g. chrome, edge, firefox")
    parser.add_argument("--add-header", action="append", default=None, metavar="NAME:VALUE", help="extra HTTP header for the downloader; may be repeated")
    args = parser.parse_args()
    if not math.isfinite(args.sample_interval) or args.sample_interval <= 0:
        parser.error('sample interval must be a positive finite number')
    if args.review and args.extract_only:
        parser.error('--review and --extract-only cannot be used together')
    FRAME_RATE = 1 / args.sample_interval
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

    if choice == 'y' and not args.extract_only:
        convert_screenshots_to_pdf(output_folder_screenshot_path)
