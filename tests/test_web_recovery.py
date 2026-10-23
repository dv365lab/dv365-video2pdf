import http.client
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import web_ui


class WebRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.servers = []
        self.server = self.start_server()
        self.addCleanup(self.close_servers)

    def start_server(self):
        server = web_ui.create_server(0, self.root / 'jobs')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.servers.append((server, thread))
        return server

    def close_servers(self):
        for server, thread in self.servers:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def request(self, method, path, payload=None, server=None, token=None):
        server = server or self.server
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        headers = {'X-Upload-Token': token or server.upload_token}
        body = json.dumps(payload or {}).encode() if method == 'POST' else None
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def add_local(self, options=None):
        source = self.root / 'lesson.mp4'
        source.write_bytes(b'test video source')
        with patch.object(self.server.executor, 'submit'):
            status, job = self.request('POST', '/api/sources', {
                'kind': 'local', 'source': str(source), 'options': options or {},
            })
        self.assertEqual(status, 202, job)
        return source, job

    def test_session_and_jobs_are_restored_after_restart(self):
        source, job = self.add_local({'crop_top': 7, 'sample_interval': 2})
        self.server.update(job['id'], status='running')
        restarted = self.start_server()
        status, session = self.request('GET', '/api/session', server=restarted)
        self.assertEqual(status, 200)
        self.assertNotEqual(session['upload_token'], self.server.upload_token)
        status, listing = self.request('GET', '/api/jobs', server=restarted)
        self.assertEqual(status, 200)
        restored = next(value for value in listing['jobs'] if value['id'] == job['id'])
        self.assertEqual(restored['status'], 'interrupted')
        self.assertTrue(restored['retry_available'])
        with patch.object(restarted.executor, 'submit') as submit:
            status, retried = self.request('POST', '/api/jobs/' + job['id'] + '/retry', server=restarted)
        self.assertEqual(status, 202, retried)
        self.assertEqual(submit.call_args.args[2], source)
        self.assertEqual(submit.call_args.args[3]['crop_top'], 7)

    def test_retry_reads_original_source_preserves_options_and_forces_pdf(self):
        source, job = self.add_local({'crop_top': 3, 'crop_bottom': 4, 'sample_interval': 0.5,
                                      'remove_qr': True, 'mode': 'review'})
        old_output = self.root / 'jobs' / job['id'] / 'output'
        old_output.mkdir()
        (old_output / 'partial.png').write_bytes(b'keep this file')
        self.server.fail_job(job['id'], 'conversion failed')
        with patch.object(self.server.executor, 'submit') as submit:
            status, retried = self.request('POST', '/api/jobs/' + job['id'] + '/retry')
        self.assertEqual(status, 202, retried)
        self.assertNotEqual(retried['id'], job['id'])
        self.assertEqual(submit.call_args.args[2], source)
        options = submit.call_args.args[3]
        self.assertEqual((options['crop_top'], options['crop_bottom'], options['sample_interval']), (3, 4, 0.5))
        self.assertTrue(options['remove_qr'])
        self.assertEqual(options['mode'], 'auto')
        self.assertEqual((old_output / 'partial.png').read_bytes(), b'keep this file')

    def test_failed_video_is_reread_and_retried_to_a_real_pdf(self):
        import cv2
        import numpy as np
        import pikepdf

        source = self.root / 'repair.avi'
        source.write_bytes(b'broken video')
        status, original = self.request('POST', '/api/sources', {'kind': 'local', 'source': str(source)})
        self.assertEqual(status, 202, original)

        def wait_for(job_id):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                job = self.server.snapshot(job_id)
                if job['status'] in ('done', 'error'):
                    return job
                time.sleep(0.05)
            self.fail('Conversion did not finish')

        self.assertEqual(wait_for(original['id'])['status'], 'error')
        writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*'MJPG'), 2, (64, 64))
        self.assertTrue(writer.isOpened())
        try:
            for _ in range(8):
                writer.write(np.full((64, 64, 3), 255, dtype=np.uint8))
        finally:
            writer.release()
        status, retry = self.request('POST', '/api/jobs/' + original['id'] + '/retry')
        self.assertEqual(status, 202, retry)
        result = wait_for(retry['id'])
        self.assertEqual(result['status'], 'done', result)
        pdfs = list((self.root / 'jobs' / retry['id'] / 'output').glob('*.pdf'))
        self.assertEqual(len(pdfs), 1)
        with pikepdf.open(pdfs[0]) as document:
            self.assertEqual(len(document.pages), 1)
        self.assertEqual(self.server.snapshot(original['id'])['status'], 'error')

    def test_concurrent_retries_only_enqueue_once_and_keep_link_after_restart(self):
        _, job = self.add_local()
        self.server.fail_job(job['id'], 'failure')
        with patch.object(self.server.executor, 'submit') as submit, ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: self.request('POST', '/api/jobs/' + job['id'] + '/retry'), range(24)))
        self.assertEqual({status for status, _ in results}, {202})
        self.assertEqual(len({result['id'] for _, result in results}), 1)
        submit.assert_called_once()
        restarted = self.start_server()
        with patch.object(restarted.executor, 'submit') as submit:
            status, result = self.request('POST', '/api/jobs/' + job['id'] + '/retry', server=restarted)
        self.assertEqual(result['id'], results[0][1]['id'])
        submit.assert_not_called()

    def test_running_and_successful_jobs_are_not_requeued(self):
        _, job = self.add_local()
        for status in ('queued', 'running', 'done'):
            self.server.update(job['id'], status=status)
            with patch.object(self.server.executor, 'submit') as submit:
                response_status, result = self.request('POST', '/api/jobs/' + job['id'] + '/retry')
            self.assertEqual(response_status, 200, result)
            self.assertEqual(result['id'], job['id'])
            submit.assert_not_called()

    def test_missing_source_retry_reports_actionable_error_without_new_job(self):
        source, job = self.add_local()
        self.server.fail_job(job['id'], 'failure')
        source.unlink()
        with patch.object(self.server.executor, 'submit') as submit:
            status, result = self.request('POST', '/api/jobs/' + job['id'] + '/retry')
        self.assertEqual(status, 400, result)
        self.assertIn('视频', result['error'])
        self.assertEqual(len(self.server.jobs), 1)
        submit.assert_not_called()

    def test_url_credentials_remain_in_memory_only_and_restart_blocks_retry(self):
        source = 'https://example.com/lesson?token=source-secret'
        options = {'referer': 'https://example.com/?token=referer-secret',
                   'cookies_from_browser': 'edge:profile-secret',
                   'add_headers': ['Authorization: Bearer header-secret']}
        with patch.object(self.server.executor, 'submit'):
            status, job = self.request('POST', '/api/sources', {'kind': 'url', 'source': source, 'options': options})
        self.assertEqual(status, 202, job)
        self.server.fail_job(job['id'], 'failure with header-secret in log')
        with patch.object(self.server.executor, 'submit') as submit:
            _, retried = self.request('POST', '/api/jobs/' + job['id'] + '/retry')
        self.assertEqual(submit.call_args.args[2], source)
        self.assertEqual(submit.call_args.args[3]['add_headers'], options['add_headers'])
        stored = '\n'.join(path.read_text('utf-8') for path in (self.root / 'jobs').rglob('*.json'))
        for secret in ('source-secret', 'referer-secret', 'profile-secret', 'header-secret'):
            self.assertNotIn(secret, stored)
        restarted = self.start_server()
        result = restarted.snapshot(retried['id'])
        self.assertFalse(result['retry_available'])
        self.assertIn('重新添加', result['retry_blocked_reason'])

    def test_legacy_uploads_are_recovered_without_inventing_original_options(self):
        old_id = 'a' * 32
        old_folder = self.root / 'jobs' / old_id
        old_folder.mkdir()
        (old_folder / 'old.mp4').write_bytes(b'old upload')
        restarted = self.start_server()
        job = restarted.snapshot(old_id)
        self.assertIsNotNone(job)
        self.assertEqual(job['status'], 'interrupted')
        self.assertTrue(job['legacy'])
        self.assertIn('默认', job['recovery_note'])
        self.assertTrue(job['retry_available'])

    def test_legacy_completed_pdf_is_available_after_restart(self):
        import pikepdf

        old_id = 'b' * 32
        old_folder = self.root / 'jobs' / old_id
        (old_folder / 'output').mkdir(parents=True)
        (old_folder / 'old.mp4').write_bytes(b'old upload')
        with pikepdf.new() as document:
            document.add_blank_page()
            document.save(old_folder / 'output' / 'old.pdf')
        restarted = self.start_server()
        job = restarted.snapshot(old_id)
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['download_url'], '/api/jobs/' + old_id + '/pdf')

    def test_truncated_legacy_pdf_remains_interrupted_and_can_be_retried(self):
        old_id = 'c' * 32
        old_folder = self.root / 'jobs' / old_id
        (old_folder / 'output').mkdir(parents=True)
        (old_folder / 'old.mp4').write_bytes(b'old upload')
        (old_folder / 'output' / 'old.pdf').write_bytes(b'%PDF-1.7\ntruncated')
        restarted = self.start_server()
        job = restarted.snapshot(old_id)
        self.assertEqual(job['status'], 'interrupted')
        self.assertIsNone(job['download_url'])
        self.assertTrue(job['retry_available'])

    def test_completed_metadata_with_missing_or_invalid_pdf_is_retryable(self):
        import pikepdf

        for variant in ('missing', 'truncated', 'empty'):
            with self.subTest(variant=variant):
                _, job = self.add_local()
                output = self.root / 'jobs' / job['id'] / 'output'
                output.mkdir()
                if variant == 'truncated':
                    (output / 'lesson.pdf').write_bytes(b'%PDF-1.7\ntruncated')
                elif variant == 'empty':
                    with pikepdf.new() as document:
                        document.save(output / 'lesson.pdf')
                self.server.update(job['id'], status='done', download_url='/api/jobs/' + job['id'] + '/pdf')
                restarted = self.start_server()
                restored = restarted.snapshot(job['id'])
                self.assertEqual(restored['status'], 'interrupted')
                self.assertIsNone(restored['download_url'])
                self.assertTrue(restored['retry_available'])


if __name__ == '__main__':
    unittest.main()
