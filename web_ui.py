"""Local browser UI, using the existing CLI in isolated job directories."""

import argparse
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit


PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = PROJECT_ROOT / "web"
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".wmv", ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".flv"}


def open_folder(path):
    path = Path(path).resolve()
    if not path.is_dir():
        raise OSError('文件夹不存在。')
    if os.name == 'nt':
        os.startfile(str(path))
    else:
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)])


def http_url(value):
    parts = urlsplit(value)
    return parts.scheme in ('http', 'https') and bool(parts.hostname)


def parse_progress(line):
    '''Read CLI stage events and yt-dlp's current-file download percentage.'''
    if line.startswith('VIDEO2PDF_PROGRESS '):
        try:
            event = json.loads(line[len('VIDEO2PDF_PROGRESS '):])
            stage = event['stage']
            if stage not in ('extract', 'clean', 'write'):
                return None
            current, total = event.get('current', 0), event.get('total')
            completed = event.get('completed', current)
            if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                   for value in (current, completed)):
                return None
            if total is not None and (type(total) not in (int, float) or not math.isfinite(total) or total < 0):
                return None
            percent = min(100, int(completed * 100 / total)) if total and stage != 'write' else None
            return dict(stage=stage, current=current, total=total, completed=completed, percent=percent)
        except (ValueError, TypeError, KeyError):
            return None
    match = re.match(r'\[download\]\s+(\d+(?:\.\d+)?)%', line)
    if match:
        return dict(stage='download', percent=min(100, int(float(match.group(1)))))
    return None


def parse_options(values):
    if not isinstance(values, dict):
        raise ValueError('处理选项无效。')
    top, bottom = int(str(values.get('crop_top', 0))), int(str(values.get('crop_bottom', 0)))
    interval = float(values.get('sample_interval', 1))
    if top < 0 or bottom < 0:
        raise ValueError('裁剪像素必须是非负整数。')
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError('采样间隔必须是大于 0 的有限秒数。')
    qr = str(values.get('remove_qr', False)).lower()
    mode = values.get('mode', 'auto')
    if qr not in ('true', 'false') or mode not in ('auto', 'review', 'png'):
        raise ValueError('二维码或输出方式选项无效。')
    referer = values.get('referer', '')
    cookies = values.get('cookies_from_browser', '')
    if not isinstance(referer, str) or not isinstance(cookies, str):
        raise ValueError('下载设置必须为文本。')
    referer, cookies = referer.strip(), cookies.strip()
    headers = values.get('add_headers', [])
    if referer and (not http_url(referer) or '\n' in referer or '\r' in referer):
        raise ValueError('Referer 必须是完整的 HTTP(S) 网址。')
    if '\n' in cookies or '\r' in cookies or cookies.startswith('-'):
        raise ValueError('浏览器 Cookie 来源无效。')
    if not isinstance(headers, list) or any(
        not isinstance(header, str) or not re.match(r'^[A-Za-z0-9-]+:', header)
        or '\n' in header or '\r' in header for header in headers
    ):
        raise ValueError('请求头请按每行“名称:值”填写。')
    return dict(crop_top=top, crop_bottom=bottom, sample_interval=interval,
                remove_qr=qr == 'true', mode=mode, referer=referer,
                cookies_from_browser=cookies, add_headers=headers)


class WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, work_root):
        self.work_root = Path(work_root).resolve()
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.upload_token = secrets.token_urlsafe(32)
        self.jobs = {}
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1)
        super().__init__(("127.0.0.1", port), RequestHandler)

    def update(self, job_id, **values):
        with self.lock:
            self.jobs[job_id].update(values)

    def snapshot(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            return dict(job) if job else None

    def add_job(self, filename, kind='video', job_id=None):
        job_id = job_id or uuid.uuid4().hex
        (self.work_root / job_id).mkdir(exist_ok=True)
        job = dict(id=job_id, filename=filename, kind=kind, status='queued', log='',
                   pages=None, download_url=None, screenshots_path=None, progress=None)
        with self.lock:
            self.jobs[job_id] = job
        return job_id

    def run_command(self, job_id, command, cwd):
        self.update(job_id, status='running', progress=None)
        log = self.snapshot(job_id)['log']
        with subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              encoding='utf-8', errors='replace') as process:
            for line in process.stdout:
                progress = parse_progress(line)
                if progress is not None:
                    self.update(job_id, progress=progress)
                if not line.startswith('VIDEO2PDF_PROGRESS '):
                    log = (log + line)[-16000:]
                    self.update(job_id, log=log)
            if process.wait() != 0:
                raise RuntimeError('处理失败，请查看上方日志。')

    def fail_job(self, job_id, error):
        self.update(job_id, status='error', log=(self.snapshot(job_id)['log'] + '\n' + str(error))[-16000:])

    def finish_pdf(self, job_id):
        pdfs = list((self.work_root / job_id / 'output').glob('*.pdf'))
        if len(pdfs) != 1 or not pdfs[0].stat().st_size:
            raise ValueError('未生成 PDF，请检查视频或截图。')
        folder = pdfs[0].with_suffix('')
        self.update(job_id, status='done', pages=len(list(folder.glob('*.png'))),
                    screenshots_path=str(folder), download_url=f'/api/jobs/{job_id}/pdf',
                    progress=dict(stage='done', percent=100))

    def convert(self, job_id, source, options):
        command = [
            sys.executable, "-X", "utf8", "-u", str(PROJECT_ROOT / "video2pdfslides.py"),
            str(source), '--crop-top', str(options['crop_top']), '--crop-bottom', str(options['crop_bottom']),
            '--sample-interval', str(options['sample_interval']),
        ]
        if options['remove_qr']:
            command.append("--remove-qr")
        if options['mode'] != 'auto':
            command.append('--extract-only')
        for key, flag in [('referer', '--referer'), ('cookies_from_browser', '--cookies-from-browser')]:
            if options[key]:
                command.extend([flag, options[key]])
        for header in options['add_headers']:
            command.extend(['--add-header', header])
        try:
            self.run_command(job_id, command, self.work_root / job_id)
            if options['mode'] == 'auto':
                self.finish_pdf(job_id)
            else:
                folders = [p for p in (self.work_root / job_id / 'output').iterdir() if p.is_dir() and any(p.glob('*.png'))]
                if len(folders) != 1:
                    raise ValueError('没有可供检查的 PNG 截图。')
                self.update(job_id, status='review' if options['mode'] == 'review' else 'screenshots',
                            screenshots_path=str(folders[0]), pages=len(list(folders[0].glob('*.png'))))
        except Exception as error:
            self.fail_job(job_id, error)

    def compose(self, job_id):
        folder = self.snapshot(job_id)['screenshots_path']
        command = [sys.executable, '-X', 'utf8', '-u', '-c',
                   'import sys; sys.path.insert(0, sys.argv[1]); '
                   'from video2pdfslides import convert_screenshots_to_pdf; convert_screenshots_to_pdf(sys.argv[2])',
                   str(PROJECT_ROOT), folder]
        try:
            self.run_command(job_id, command, self.work_root / job_id)
            self.finish_pdf(job_id)
        except Exception as error:
            self.fail_job(job_id, error)

    def import_screenshots(self, job_id, source, mode):
        try:
            self.update(job_id, status='running')
            folder = self.work_root / job_id / 'output' / (source.name or 'screenshots')
            folder.mkdir(parents=True)
            files = sorted(source.glob('*.png'))
            for index, path in enumerate(files):
                shutil.copy2(path, folder / path.name)
                self.update(job_id, progress=dict(stage='copy', current=index + 1, total=len(files),
                                                 percent=int((index + 1) * 100 / len(files))))
            self.update(job_id, screenshots_path=str(folder), pages=len(files))
            if mode in ('review', 'png'):
                self.update(job_id, status='review' if mode == 'review' else 'screenshots')
            else:
                self.compose(job_id)
        except Exception as error:
            self.fail_job(job_id, error)

    def setup_downloader(self, job_id):
        try:
            self.run_command(job_id, [sys.executable, '-X', 'utf8', '-u', str(PROJECT_ROOT / 'setup_downloader.py')], PROJECT_ROOT)
            self.update(job_id, status='done')
        except Exception as error:
            self.fail_job(job_id, error)

    def server_close(self):
        super().server_close()
        self.executor.shutdown(wait=True)


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Polling should not flood the launcher's console.
        pass

    def allowed_request(self):
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if host not in allowed or (origin and origin != "http://" + host):
            self.send_json(403, {"error": "只允许本机页面访问。"})
            return False
        return True

    def send_bytes(self, status, body, content_type, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, value):
        self.send_bytes(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def read_json(self):
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= 65536 or self.headers.get('Transfer-Encoding'):
            raise ValueError('请求内容长度无效。')
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError('请求内容必须是 JSON 对象。')
        return value

    def do_GET(self):
        if not self.allowed_request():
            return
        path = urlsplit(self.path).path
        assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
        if path in assets:
            filename, content_type = assets[path]
            body = (STATIC_ROOT / filename).read_bytes()
            if path == "/":
                body = body.replace(b"__UPLOAD_TOKEN__", self.server.upload_token.encode("ascii"))
            self.send_bytes(200, body, content_type + "; charset=utf-8")
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})(/pdf)?", path)
        if match:
            job_id, download = match.groups()
            job = self.server.snapshot(job_id)
            if job and not download:
                self.send_json(200, job)
                return
            if job and download and job["status"] == "done":
                pdfs = list((self.server.work_root / job_id / "output").glob("*.pdf"))
                if len(pdfs) == 1 and pdfs[0].is_file():
                    pdf = pdfs[0]
                    self.send_response(200)
                    self.send_header("Content-Type", "application/pdf")
                    self.send_header("Content-Length", str(pdf.stat().st_size))
                    self.send_header("Content-Disposition", "attachment; filename=slides.pdf; filename*=UTF-8''" + quote(pdf.name))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    with pdf.open("rb") as stream:
                        while True:
                            chunk = stream.read(1024 * 1024)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                    return
        self.send_json(404, {"error": "文件或任务不存在。"})

    def do_POST(self):
        if not self.allowed_request():
            return
        if self.headers.get("X-Upload-Token") != self.server.upload_token:
            self.send_json(403, {"error": "请从本机页面添加视频。"})
            return
        path = urlsplit(self.path).path
        if path != '/api/jobs':
            try:
                if self.handle_action(path):
                    return
            except (ValueError, TypeError) as error:
                self.send_json(400, {'error': str(error)})
                return
            except OSError as error:
                self.send_json(500, {'error': f'无法打开或读取文件夹：{error}'})
                return
            self.send_json(404, {"error": "接口不存在。"})
            return
        try:
            query = parse_qs(urlsplit(self.path).query)
            filename = query.get("filename", [""])[0]
            if (not filename or len(filename) > 180 or re.search(r'[<>:"/\\|?*\x00-\x1f]', filename)
                    or filename.startswith(".") or filename.endswith((" ", "."))
                    or filename.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}):
                raise ValueError("视频文件名无效，请重命名后重试。")
            if Path(filename).suffix.lower() not in VIDEO_EXTENSIONS:
                raise ValueError("请选择 MP4、AVI、MOV、MKV 等视频文件。")
            options = parse_options({key: value[0] for key, value in query.items()})
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or self.headers.get("Transfer-Encoding"):
                raise ValueError("视频文件为空，或上传长度无效。")
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
            return

        job_id = uuid.uuid4().hex
        folder = self.server.work_root / job_id
        folder.mkdir()
        source = folder / filename
        try:
            self.connection.settimeout(120)
            with source.open("wb") as stream:
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("上传中断，请重新添加视频。")
                    stream.write(chunk)
                    remaining -= len(chunk)
        except (OSError, ValueError) as error:
            source.unlink(missing_ok=True)
            folder.rmdir()
            self.send_json(400, {"error": str(error)})
            return
        self.server.add_job(filename, job_id=job_id)
        self.server.executor.submit(self.server.convert, job_id, source, options)
        self.send_json(202, self.server.snapshot(job_id))

    def handle_action(self, path):
        if path == '/api/output/open':
            folder = PROJECT_ROOT / 'output'
            folder.mkdir(exist_ok=True)
            open_folder(folder)
            self.send_json(200, {'path': str(folder)})
        elif path == '/api/downloader':
            job_id = self.server.add_job('安装 / 更新视频下载器', 'downloader')
            self.server.executor.submit(self.server.setup_downloader, job_id)
            self.send_json(202, self.server.snapshot(job_id))
        elif path == '/api/sources':
            payload = self.read_json()
            kind, source = payload.get('kind'), payload.get('source')
            if not isinstance(source, str) or not source.strip():
                raise ValueError('请填写视频网址、本地视频路径或 PNG 文件夹。')
            source = source.strip().strip('"')
            options = parse_options(payload.get('options', {}))
            if kind == 'url':
                if not http_url(source):
                    raise ValueError('请输入完整的 HTTP(S) 视频网址。')
                filename = source
            elif kind in ('local', 'screenshots'):
                source = Path(source).expanduser()
                if not source.is_absolute():
                    raise ValueError('请输入本机的完整绝对路径。')
                source = source.resolve()
                if kind == 'local' and (not source.is_file() or source.suffix.lower() not in VIDEO_EXTENSIONS):
                    raise ValueError('本地视频不存在或格式不受支持。')
                if kind == 'screenshots' and (not source.is_dir() or not any(source.glob('*.png'))):
                    raise ValueError('该文件夹不存在或没有 PNG 截图。')
                filename = source.name
            else:
                raise ValueError('输入来源无效。')
            job_id = self.server.add_job(filename, kind)
            if kind == 'screenshots':
                self.server.executor.submit(self.server.import_screenshots, job_id, source, options['mode'])
            else:
                self.server.executor.submit(self.server.convert, job_id, source, options)
            self.send_json(202, self.server.snapshot(job_id))
        else:
            match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})/(open|review)', path)
            if not match:
                return False
            job_id, action = match.groups()
            job = self.server.snapshot(job_id)
            if not job:
                self.send_json(404, {'error': '任务不存在。'})
            elif action == 'open':
                folder = Path(job['screenshots_path']) if job['screenshots_path'] else self.server.work_root / job_id
                open_folder(folder)
                self.send_json(200, {'path': str(folder)})
            else:
                choice = self.read_json().get('action')
                if choice not in ('generate', 'keep'):
                    raise ValueError('检查操作无效。')
                with self.server.lock:
                    current = self.server.jobs[job_id]
                    if current['status'] != 'review':
                        self.send_json(409, {'error': '该任务当前不在人工检查阶段。'})
                        return True
                    if choice == 'generate' and not any(Path(current['screenshots_path']).glob('*.png')):
                        raise ValueError('截图目录为空，请保留至少一张 PNG。')
                    current['status'] = 'queued' if choice == 'generate' else 'screenshots'
                    current['pages'] = len(list(Path(current['screenshots_path']).glob('*.png')))
                if choice == 'generate':
                    self.server.executor.submit(self.server.compose, job_id)
                self.send_json(202 if choice == 'generate' else 200, self.server.snapshot(job_id))
        return True


def create_server(port=8786, work_root=None):
    return WebServer(port, work_root if work_root is not None else PROJECT_ROOT / "output" / "web")


def main():
    parser = argparse.ArgumentParser(description="video2pdf 本地 Web UI")
    parser.add_argument("--port", type=int, default=8786, help="本地端口（默认 8786）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()
    try:
        server = create_server(args.port)
    except OSError as error:
        print(f"无法启动 Web UI：{error}。可使用 --port 更换端口。", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Web UI：{url}\n结果目录：{server.work_root}\n按 Ctrl+C 停止服务（等待当前转换结束）。", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭服务…", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
