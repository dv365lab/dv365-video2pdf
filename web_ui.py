"""Local browser UI, using the existing CLI in isolated job directories."""

import argparse
import json
import re
import secrets
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

    def convert(self, job_id, source, crop_top, crop_bottom, remove_qr):
        self.update(job_id, status="running")
        command = [
            sys.executable, "-X", "utf8", "-u", str(PROJECT_ROOT / "video2pdfslides.py"),
            str(source), "--crop-top", str(crop_top), "--crop-bottom", str(crop_bottom),
        ]
        if remove_qr:
            command.append("--remove-qr")
        log = ""
        try:
            with subprocess.Popen(
                command, cwd=source.parent, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace",
            ) as process:
                for line in process.stdout:
                    log = (log + line)[-16000:]
                    self.update(job_id, log=log)
                return_code = process.wait()
            pdfs = list((source.parent / "output").glob("*.pdf"))
            if return_code != 0 or len(pdfs) != 1 or not pdfs[0].stat().st_size:
                self.update(job_id, status="error", log=log or "转换失败，请检查视频文件。")
                return
            pages = len(list(pdfs[0].with_suffix("").glob("*.png")))
            self.update(job_id, status="done", pages=pages, download_url=f"/api/jobs/{job_id}/pdf")
        except Exception as error:
            self.update(job_id, status="error", log=(log + "\n" + str(error))[-16000:])

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
        if urlsplit(self.path).path != "/api/jobs":
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
            crop_top = int(query.get("crop_top", ["0"])[0])
            crop_bottom = int(query.get("crop_bottom", ["0"])[0])
            if crop_top < 0 or crop_bottom < 0:
                raise ValueError("裁剪像素必须是非负整数。")
            remove_qr = query.get("remove_qr", ["false"])[0]
            if remove_qr not in ("true", "false"):
                raise ValueError("去二维码选项无效。")
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
        job = {"id": job_id, "filename": filename, "status": "queued", "log": "", "pages": None, "download_url": None}
        with self.server.lock:
            self.server.jobs[job_id] = job
        self.server.executor.submit(self.server.convert, job_id, source, crop_top, crop_bottom, remove_qr == "true")
        self.send_json(202, self.server.snapshot(job_id))


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
