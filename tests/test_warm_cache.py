import argparse
import contextlib
import http.server
import importlib.util
import io
import json
from email.message import Message
from pathlib import Path
import tempfile
import threading
import unittest


MODULE_PATH = Path(__file__).parents[1] / 'warm-cache.py'
MODULE_SPEC = importlib.util.spec_from_file_location('warm_cache', MODULE_PATH)
warm_cache = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(warm_cache)


class FixtureHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/sitemap.xml':
            body = (
                '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f'<url><loc>http://127.0.0.1:{self.server.server_port}/page</loc>'
                '<lastmod>2026-01-01</lastmod></url></urlset>'
            ).encode()
            content_type = 'application/xml'
        elif self.path == '/page':
            body = b'<html><body><img src="/image.webp"></body></html>'
            content_type = 'text/html; charset=utf-8'
        elif self.path == '/image.webp':
            body = b'image data'
            content_type = 'image/webp'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Status', 'fixture; hit')
        self.send_header('Cache-Control', 'public, max-age=60')
        self.send_header('Age', '12')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string, *args):
        return


class WarmCacheTests(unittest.TestCase):
    def test_delay_accepts_tenths_and_rejects_other_precision(self):
        self.assertEqual(warm_cache.parse_delay('0.5'), 0.5)
        self.assertEqual(warm_cache.parse_delay('0'), 0)
        with self.assertRaises(argparse.ArgumentTypeError):
            warm_cache.parse_delay('0.05')
        with self.assertRaises(argparse.ArgumentTypeError):
            warm_cache.parse_delay('-0.1')

    def test_cache_details_prefers_standard_header_and_keeps_source(self):
        headers = Message()
        headers['Cache-Status'] = 'edge; hit'
        headers['CF-Cache-Status'] = 'MISS'
        self.assertEqual(
            warm_cache.cache_details(headers),
            ('HIT', 'Cache-Status: edge; hit'),
        )

    def test_image_urls_resolves_supported_attributes(self):
        headers = Message()
        headers['Content-Type'] = 'text/html; charset=utf-8'
        body = b'''
            <img src="/one.webp" srcset="/two.webp 1x, /three.webp 2x"
                 data-src="/four.webp">
            <source type="image/avif" src="/five.avif">
            <link rel="image_src" href="/six.webp">
            <img src="data:image/gif;base64,abc">
        '''
        self.assertEqual(
            warm_cache.image_urls(body, headers, 'https://example.com/page'),
            {
                'https://example.com/one.webp',
                'https://example.com/two.webp',
                'https://example.com/three.webp',
                'https://example.com/four.webp',
                'https://example.com/five.avif',
                'https://example.com/six.webp',
            },
        )

    def test_reports_are_sorted_by_type_and_url(self):
        rows = [
            warm_cache.report_row('image', 'https://example.com/z.webp'),
            warm_cache.report_row('page', 'https://example.com/z'),
            warm_cache.report_row('page', 'https://example.com/a'),
            warm_cache.report_row('sitemap', 'https://example.com/sitemap.xml'),
        ]
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / 'report.json'
            csv_path = Path(directory) / 'report.csv'
            warm_cache.write_reports(rows, csv_path, json_path)
            json_rows = json.loads(json_path.read_text(encoding='utf-8'))
            csv_lines = csv_path.read_text(encoding='utf-8').splitlines()
            self.assertEqual(
                [(row['type'], row['url']) for row in json_rows],
                [
                    ('sitemap', 'https://example.com/sitemap.xml'),
                    ('page', 'https://example.com/a'),
                    ('page', 'https://example.com/z'),
                    ('image', 'https://example.com/z.webp'),
                ],
            )
            self.assertIn('https://example.com/sitemap.xml', csv_lines[1])
            self.assertIn('https://example.com/z.webp', csv_lines[-1])

    def test_main_warms_pages_and_images_and_writes_metadata(self):
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            sitemap = f'http://127.0.0.1:{server.server_port}/sitemap.xml'
            with tempfile.TemporaryDirectory() as directory:
                report_path = Path(directory) / 'report.json'
                output = io.StringIO()
                errors = io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    result = warm_cache.main([
                        sitemap, '--delay', '0', '--workers', '1',
                        '--json', str(report_path),
                    ])
                self.assertEqual(result, 0)
                rows = json.loads(report_path.read_text(encoding='utf-8'))
                self.assertEqual({row['type'] for row in rows}, {'page', 'image'})
                self.assertTrue(all(row['cache_status'] == 'HIT' for row in rows))
                self.assertTrue(all(row['age_seconds'] == '12' for row in rows))
                self.assertIn('CACHE', output.getvalue())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
