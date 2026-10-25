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
import tempfile
import threading
import time
import uuid
import webbrowser
import zipfile
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from local_sources import VIDEO_EXTENSIONS, resolve_local_sources


PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = PROJECT_ROOT / "web"
CONVERSION_OPTION_KEYS = ('crop_top', 'crop_bottom', 'sample_interval', 'remove_qr', 'mode')


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
    request_queue_size = 128

    def __init__(self, port, work_root):
        self.work_root = Path(work_root).resolve()
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.upload_token = secrets.token_urlsafe(32)
        self.jobs = {}
        self.job_specs = {}
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1)
        super().__init__(("127.0.0.1", port), RequestHandler)
        self.load_jobs()

    def persist_job(self, job_id):
        """Called under the lock; never write URL/download credentials or CLI logs."""
        job = self.jobs[job_id]
        record = {key: value for key, value in job.items() if key not in ('log', 'progress')}
        record['log'] = ''
        record['progress'] = None
        spec = self.job_specs.get(job_id)
        saved_spec = None
        if spec and job['kind'] in ('video', 'local', 'screenshots'):
            saved_spec = dict(source=spec['source'], options={
                key: spec['options'][key] for key in CONVERSION_OPTION_KEYS})
        elif job['kind'] == 'url':
            record['filename'] = '视频网址任务（重启前）'
            record['retry_blocked_reason'] = '网址及下载凭据未保存；请重新添加视频网址后转换。'
        folder = self.work_root / job_id
        temporary = folder / 'job.json.tmp'
        temporary.write_text(json.dumps(dict(version=1, job=record, spec=saved_spec),
                                        ensure_ascii=False), encoding='utf-8')
        temporary.replace(folder / 'job.json')

    def load_jobs(self):
        for folder in sorted(self.work_root.iterdir()):
            if not re.fullmatch(r'[a-f0-9]{32}', folder.name) or not folder.is_dir() or folder.is_symlink():
                continue
            job, spec = None, None
            metadata = folder / 'job.json'
            if metadata.is_file():
                try:
                    record = json.loads(metadata.read_text(encoding='utf-8'))
                    job = record['job']
                    if (record.get('version') != 1 or job['id'] != folder.name
                            or job['status'] not in ('queued', 'running', 'interrupted', 'error', 'done', 'review', 'screenshots')):
                        raise ValueError('Invalid job metadata')
                    if job.get('deleted') is True:
                        continue
                    saved_spec = record.get('spec')
                    if saved_spec and job['kind'] in ('video', 'local', 'screenshots'):
                        source = Path(saved_spec['source'])
                        if not source.is_absolute():
                            raise ValueError('Invalid source path')
                        spec = dict(source=str(source), options=parse_options(saved_spec['options']))
                except (OSError, ValueError, TypeError, KeyError):
                    job, spec = None, None
            if job is None:
                # Earlier versions kept uploads, but neither source options nor job state.
                sources = [p for p in folder.iterdir() if p.is_file()
                           and p.suffix.lower() in VIDEO_EXTENSIONS and p.stat().st_size > 0]
                if len(sources) != 1:
                    continue
                job = self.new_job(folder.name, sources[0].name, 'video')
                job.update(status='interrupted', legacy=True,
                           recovery_note='已恢复旧版上传视频；原裁剪、采样及去二维码参数未记录，重试将使用默认参数。')
                spec = dict(source=str(sources[0]), options=parse_options({}))
                pdf = self.recoverable_pdf(folder)
                if pdf:
                    screenshots = pdf.with_suffix('')
                    job.update(status='done', download_url=f'/api/jobs/{folder.name}/pdf',
                               screenshots_path=str(screenshots), pages=len(list(screenshots.glob('*.png'))))
            elif job['status'] == 'done' and job['kind'] != 'downloader' and not self.recoverable_pdf(folder):
                job.update(status='interrupted', download_url=None, pages=None, progress=None,
                           recovery_note='原 PDF 不存在、已损坏或没有页面；可重试，重新读取原视频生成 PDF。')
            if job['status'] in ('queued', 'running'):
                job.update(status='interrupted', progress=None,
                           recovery_note='上次服务在处理完成前停止；可点击重试，重新读取原视频并转换 PDF。')
            self.jobs[folder.name] = job
            if spec:
                self.job_specs[folder.name] = spec
            self.persist_job(folder.name)

    @staticmethod
    def recoverable_pdf(folder):
        pdfs = [path for path in (folder / 'output').glob('*.pdf') if path.is_file()]
        if len(pdfs) != 1:
            return None
        # A terminated CLI can leave a nonempty but truncated PDF behind.
        import pikepdf
        try:
            with pikepdf.open(pdfs[0], attempt_recovery=False) as document:
                return pdfs[0] if len(document.pages) else None
        except (OSError, pikepdf.PdfError):
            return None

    @staticmethod
    def new_job(job_id, filename, kind):
        return dict(id=job_id, filename=filename, kind=kind, status='queued', log='',
                    pages=None, download_url=None, screenshots_path=None, progress=None,
                    created_at=time.time())

    def update(self, job_id, **values):
        with self.lock:
            self.jobs[job_id].update(values)
            if any(key not in ('log', 'progress') for key in values):
                self.persist_job(job_id)

    def snapshot(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            result = dict(job)
            result['retry_available'] = job_id in self.job_specs and job['status'] in ('error', 'interrupted')
            if not result['retry_available'] and job['status'] in ('error', 'interrupted'):
                result.setdefault('retry_blocked_reason', '未保存可重试的视频来源；请重新添加。')
            return result

    def list_jobs(self):
        with self.lock:
            return [self.snapshot(job['id']) for job in sorted(self.jobs.values(), key=lambda job: job.get('created_at', 0))]

    def delete_completed_jobs(self):
        deleted, failed = [], []
        with self.lock:
            completed = [job_id for job_id, job in self.jobs.items()
                         if job['status'] in ('done', 'screenshots')]
            for job_id in completed:
                job = self.jobs[job_id]
                job['deleted'] = True
                try:
                    self.persist_job(job_id)
                except OSError:
                    job.pop('deleted', None)
                    failed.append(job_id)
                    continue
                del self.jobs[job_id]
                self.job_specs.pop(job_id, None)
                deleted.append(job_id)
        return {'deleted_ids': deleted, 'failed_ids': failed}

    def add_job(self, filename, kind='video', job_id=None, source=None, options=None):
        job_id = job_id or uuid.uuid4().hex
        (self.work_root / job_id).mkdir(exist_ok=True)
        job = self.new_job(job_id, filename, kind)
        with self.lock:
            self.jobs[job_id] = job
            if source is not None:
                self.job_specs[job_id] = dict(source=str(source), options=dict(options or parse_options({})))
            self.persist_job(job_id)
        return job_id

    def retry_job(self, job_id):
        with self.lock:
            job = self.jobs[job_id]
            if job['status'] in ('queued', 'running', 'done'):
                return 200, self.snapshot(job_id)
            successor = job.get('retry_job_id')
            if successor in self.jobs:
                return 202, self.snapshot(successor)
            if job['status'] not in ('error', 'interrupted'):
                raise ValueError('仅失败或中断的任务可以重新转换。')
            spec = self.job_specs.get(job_id)
            if not spec:
                raise ValueError(job.get('retry_blocked_reason', '没有可恢复的视频来源，请重新添加视频。'))
            source = spec['source']
            if job['kind'] in ('video', 'local', 'screenshots'):
                source = Path(source)
                if job['kind'] == 'screenshots':
                    if not source.is_dir() or not any(source.glob('*.png')):
                        raise ValueError('原 PNG 文件夹不存在或已没有截图，请重新添加。')
                elif not source.is_file() or not source.stat().st_size:
                    raise ValueError('原视频不存在或为空，请恢复视频文件或重新添加。')
            options = dict(spec['options'], mode='auto')
            retry_id = self.add_job(job['filename'], job['kind'], source=source, options=options)
            if job.get('legacy'):
                self.update(retry_id, legacy=True, recovery_note=job['recovery_note'])
            self.update(job_id, retry_job_id=retry_id)
            if job['kind'] == 'screenshots':
                self.executor.submit(self.import_screenshots, retry_id, source, 'auto')
            else:
                self.executor.submit(self.convert, retry_id, source, options)
            return 202, self.snapshot(retry_id)

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

    def download_all_pdfs(self):
        jobs = [job for job in self.server.list_jobs()
                if job['status'] == 'done' and job.get('download_url')]
        with tempfile.TemporaryFile() as bundle:
            try:
                used_names = set()
                with zipfile.ZipFile(bundle, 'w', compression=zipfile.ZIP_STORED) as archive:
                    for job in jobs:
                        pdfs = [path for path in (self.server.work_root / job['id'] / 'output').glob('*')
                                if path.suffix.lower() == '.pdf' and path.is_file() and path.stat().st_size]
                        if len(pdfs) != 1:
                            continue
                        pdf = pdfs[0]
                        name, number = pdf.name, 2
                        while name.casefold() in used_names:
                            name = f'{pdf.stem} ({number}){pdf.suffix}'
                            number += 1
                        used_names.add(name.casefold())
                        archive.write(pdf, arcname=name)
                if not used_names:
                    self.send_json(404, {'error': '暂无已完成且可下载的 PDF。'})
                    return
            except OSError:
                self.send_json(500, {'error': 'PDF 打包失败，请检查结果文件和磁盘空间后重试。'})
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Length', str(bundle.tell()))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            bundle.seek(0)
            shutil.copyfileobj(bundle, self.wfile, length=1024 * 1024)

    def do_GET(self):
        if not self.allowed_request():
            return
        path = urlsplit(self.path).path
        if path == '/api/jobs/pdf-bundle':
            self.download_all_pdfs()
            return
        if path == '/api/session':
            self.send_json(200, {'upload_token': self.server.upload_token})
            return
        if path == '/api/jobs':
            self.send_json(200, {'jobs': self.server.list_jobs()})
            return
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
        self.server.add_job(filename, job_id=job_id, source=source, options=options)
        self.server.executor.submit(self.server.convert, job_id, source, options)
        self.send_json(202, self.server.snapshot(job_id))

    def handle_action(self, path):
        if path == '/api/jobs/completed/delete':
            self.read_json()
            self.send_json(200, self.server.delete_completed_jobs())
        elif path == '/api/local-sources':
            self.send_json(200, {'videos': resolve_local_sources(self.read_json().get('source'))})
        elif path == '/api/video-folder':
            source = self.read_json().get('source')
            if not isinstance(source, str) or not source.strip().strip('"'):
                raise ValueError('请填写视频文件夹的完整路径。')
            folder = Path(source.strip().strip('"')).expanduser()
            if not folder.is_absolute():
                raise ValueError('请输入本机文件夹的完整绝对路径。')
            folder = folder.resolve()
            if not folder.is_dir():
                raise ValueError('视频文件夹不存在或路径不是文件夹。')
            videos = sorted(
                (file for file in folder.iterdir()
                 if file.is_file() and file.suffix.lower() in VIDEO_EXTENSIONS and file.stat().st_size > 0),
                key=lambda file: file.name.casefold(),
            )
            if not videos:
                raise ValueError('该文件夹中没有非空视频文件；仅扫描当前层，不包含子文件夹。')
            self.send_json(200, {'videos': [str(file) for file in videos]})
        elif path == '/api/output/open':
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
            job_id = self.server.add_job(filename, kind, source=source, options=options)
            if kind == 'screenshots':
                self.server.executor.submit(self.server.import_screenshots, job_id, source, options['mode'])
            else:
                self.server.executor.submit(self.server.convert, job_id, source, options)
            self.send_json(202, self.server.snapshot(job_id))
        else:
            match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})/(open|review|retry)', path)
            if not match:
                return False
            job_id, action = match.groups()
            job = self.server.snapshot(job_id)
            if not job:
                self.send_json(404, {'error': '任务不存在。'})
            elif action == 'retry':
                self.read_json()
                status, result = self.server.retry_job(job_id)
                self.send_json(status, result)
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
                    self.server.persist_job(job_id)
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
