#!/usr/bin/env python3
"""Fetch sitemap pages and print cache information. Python 3, no dependencies."""
import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
from html.parser import HTMLParser
import json
import math
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


DEFAULT_DELAY = 0.5
CACHE_STATUS_HEADERS = (
    'Cache-Status', 'CF-Cache-Status', 'CDN-Cache-Status',
    'X-Cache-Status', 'X-Cache',
)
CACHE_STATUS_PATTERN = re.compile(
    r'\b(hit|miss|stale|bypass|expired|revalidated)\b', re.IGNORECASE)


def clean(value):
    return ' '.join(str(value or '-').split())


def parse_delay(value):
    try:
        delay = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Delay must be a number') from exc
    if delay < 0 or not math.isclose(delay * 10, round(delay * 10)):
        raise argparse.ArgumentTypeError('Delay must be a non-negative multiple of 0.1 seconds')
    return delay


def cache_details(headers):
    for header_name in CACHE_STATUS_HEADERS:
        value = headers.get(header_name)
        if not value:
            continue
        match = CACHE_STATUS_PATTERN.search(value)
        status = match.group(1).upper() if match else value
        return status, f'{header_name}: {value}'
    return None, None


class RequestPacer:
    def __init__(self, delay):
        self.delay = delay
        self.lock = threading.Lock()
        self.next_request = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            wait = max(0.0, self.next_request - now)
            self.next_request = max(now, self.next_request) + self.delay
        if wait:
            time.sleep(wait)


def fetch(url, timeout, keep_body=False, pacer=None):
    if urllib.parse.urlsplit(url).scheme not in ('http', 'https'):
        raise ValueError('Only HTTP and HTTPS URLs are supported')
    request = urllib.request.Request(url, headers={
        'User-Agent': 'SitemapCacheWarmer/1.0',
        'Accept-Encoding': 'gzip',
    })
    if pacer:
        pacer.wait()
    start = time.monotonic()
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        chunks = []
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            if keep_body:
                chunks.append(chunk)
        body = b''.join(chunks)
        if keep_body and body.startswith(b'\x1f\x8b'):
            body = gzip.decompress(body)
        return (response.status, response.headers, response.geturl(),
                time.monotonic() - start, body)


class ImageFinder(HTMLParser):
    """Collect image URLs referenced by common HTML image attributes."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag.lower() == 'img':
            for name in ('src', 'data-src', 'data-original', 'data-lazy-src'):
                self.add(attrs.get(name))
            for name in ('srcset', 'data-srcset'):
                self.add_srcset(attrs.get(name))
        elif tag.lower() == 'source':
            media_type = attrs.get('type', '')
            if not media_type or media_type.lower().startswith('image/'):
                self.add(attrs.get('src'))
                self.add_srcset(attrs.get('srcset'))
        elif tag.lower() == 'link' and 'image_src' in attrs.get('rel', '').lower().split():
            self.add(attrs.get('href'))

    def add_srcset(self, value):
        if value:
            for candidate in value.split(','):
                parts = candidate.strip().split()
                if parts:
                    self.add(parts[0])

    def add(self, value):
        if value:
            self.urls.add(value.strip())


def image_urls(body, headers, page_url):
    charset = headers.get_content_charset() or 'utf-8'
    parser = ImageFinder()
    parser.feed(body.decode(charset, errors='replace'))
    urls = set()
    for value in parser.urls:
        url = urllib.parse.urljoin(page_url, value)
        if (urllib.parse.urlsplit(url).scheme in ('http', 'https')
                and not any(c.isspace() for c in url)):
            urls.add(url)
    return urls


def report_row(kind, url, status=None, headers=None, elapsed=None,
               effective=None, error=None, lastmod=None):
    headers = headers or {}
    cache_status, cache_header = cache_details(headers)
    return {
        'type': kind,
        'url': url,
        'status': status,
        'cache_status': cache_status,
        'cache_header': cache_header,
        'cf_cache_status': headers.get('CF-Cache-Status'),
        'time_seconds': round(elapsed, 3) if elapsed is not None else None,
        'age_seconds': headers.get('Age'),
        'cache_control': headers.get('Cache-Control'),
        'cdn_cache_control': headers.get('CDN-Cache-Control'),
        'surrogate_control': headers.get('Surrogate-Control'),
        'expires': headers.get('Expires'),
        'etag': headers.get('ETag'),
        'last_modified': headers.get('Last-Modified'),
        'vary': headers.get('Vary'),
        'sitemap_lastmod': lastmod,
        'redirect': effective if effective and effective != url else None,
        'error': str(error) if error else None,
    }


def write_reports(rows, csv_path=None, json_path=None):
    fields = list(rows[0]) if rows else [
        'type', 'url', 'status', 'cache_status', 'cache_header', 'cf_cache_status',
        'time_seconds', 'age_seconds', 'cache_control', 'cdn_cache_control',
        'surrogate_control', 'expires', 'etag', 'last_modified', 'vary',
        'sitemap_lastmod', 'redirect', 'error']
    if csv_path:
        with open(csv_path, 'w', newline='', encoding='utf-8') as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    if json_path:
        with open(json_path, 'w', encoding='utf-8') as output:
            json.dump(rows, output, ensure_ascii=False, indent=2)
            output.write('\n')


def completed_tasks(items, worker, max_workers):
    pool = ThreadPoolExecutor(max_workers=max_workers)
    futures = {pool.submit(worker, item): item for item in items}
    try:
        for future in as_completed(futures):
            yield future, futures[future]
    except BaseException:
        for future in futures:
            future.cancel()
        pool.shutdown(wait=False)
        raise
    else:
        pool.shutdown()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sitemap', help='Full URL to sitemap.xml or sitemap index')
    parser.add_argument('--delay', type=parse_delay, default=DEFAULT_DELAY,
                        help=f'Minimum spacing between request starts in 0.1-second '
                             f'increments (default: {DEFAULT_DELAY:.1f})')
    parser.add_argument('--workers', type=int, default=12,
                        help='Maximum concurrent page/image requests (default: 12)')
    parser.add_argument('--timeout', type=float, default=30,
                        help='Socket timeout in seconds (default: 30)')
    parser.add_argument('--no-images', action='store_true',
                        help='Do not fetch images referenced by pages')
    parser.add_argument('--verbose', action='store_true',
                        help='Show detailed cache and modification headers')
    parser.add_argument('--csv', metavar='FILE', help='Write a CSV report')
    parser.add_argument('--json', metavar='FILE', help='Write a JSON report')
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.workers <= 0:
        parser.error('Timeout and workers must be > 0')
    pacer = RequestPacer(args.delay)
    maps, pages, images = set(), set(), set()
    rows = []
    pending = [args.sitemap]
    count = failures = map_failures = image_failures = 0
    print(f'{"HTTP":<5} {"CACHE":<12} {"TIME_s":>8} {"AGE_s":>7}  URL', flush=True)
    while pending:
        sitemap = pending.pop(0)
        if sitemap in maps:
            continue
        maps.add(sitemap)
        print(f'Reading sitemap: {clean(sitemap)}', file=sys.stderr, flush=True)
        try:
            status, _, _, _, body = fetch(sitemap, args.timeout, True, pacer)
            if not 200 <= status < 300:
                raise ValueError(f'HTTP {status}')
            root = ET.fromstring(body)
            tag = lambda element: element.tag.rsplit('}', 1)[-1]
            kind = tag(root)
            if kind not in ('sitemapindex', 'urlset'):
                raise ValueError('Expected sitemapindex or urlset')
        except (OSError, ValueError, ET.ParseError) as exc:
            print(f'Sitemap error: {clean(exc)}', file=sys.stderr, flush=True)
            rows.append(report_row('sitemap', sitemap, error=exc))
            map_failures += 1
            continue
        page_entries = []
        for entry in root:
            if tag(entry) != ('sitemap' if kind == 'sitemapindex' else 'url'):
                continue
            fields = {tag(e): (e.text or '').strip() for e in entry}
            url = fields.get('loc', '')
            if (urllib.parse.urlsplit(url).scheme not in ('http', 'https')
                    or any(c.isspace() for c in url)):
                print(f'Invalid sitemap URL: {clean(url)}', file=sys.stderr, flush=True)
                map_failures += 1
                continue
            if kind == 'sitemapindex':
                pending.append(url)
                continue
            if url not in pages:
                pages.add(url)
                page_entries.append((url, fields.get('lastmod')))

        def get_page(item):
            url, lastmod = item
            status, headers, effective, elapsed, body = fetch(
                url, args.timeout, not args.no_images, pacer)
            found = image_urls(body, headers, effective) if (
                200 <= status < 300 and not args.no_images) else set()
            return url, lastmod, status, headers, effective, elapsed, found

        for future, item in completed_tasks(page_entries, get_page, args.workers):
            url = item[0]
            count += 1
            try:
                url, lastmod, status, headers, effective, elapsed, found = future.result()
                cache_status, cache_header = cache_details(headers)
                rows.append(report_row('page', url, status, headers, elapsed,
                                       effective, lastmod=lastmod))
                print(f'{status:<5} {clean(cache_status):<12} '
                      f'{elapsed:8.3f} {clean(headers.get("Age")):>7}  {clean(url)}', flush=True)
                if args.verbose:
                    print(f'      Cache header: {clean(cache_header)}'
                          f' | Cache-Control: {clean(headers.get("Cache-Control"))}', flush=True)
                    print(f'      Last-Modified: {clean(headers.get("Last-Modified"))}'
                          f' | Sitemap lastmod: {clean(lastmod)}', flush=True)
                    if effective != url:
                        print(f'      Redirect -> {clean(effective)}', flush=True)
                if not 200 <= status < 300:
                    failures += 1
                images.update(found)
            except (OSError, ValueError) as exc:
                failures += 1
                rows.append(report_row('page', url, error=exc))
                print(f'ERROR {clean(url)}: {clean(exc)}', flush=True)

    if images:
        def get_image(image_url):
            return image_url, fetch(image_url, args.timeout, pacer=pacer)

        for future, image_url in completed_tasks(images, get_image, args.workers):
            try:
                image_url, (status, headers, effective, elapsed, _) = future.result()
                cache_status, cache_header = cache_details(headers)
                rows.append(report_row('image', image_url, status, headers,
                                       elapsed, effective))
                print(f'IMG   {clean(cache_status):<12} '
                      f'{elapsed:8.3f} {clean(headers.get("Age")):>7}  '
                      f'{clean(image_url)}', flush=True)
                if args.verbose:
                    print(f'      Cache header: {clean(cache_header)}'
                          f' | Cache-Control: {clean(headers.get("Cache-Control"))}', flush=True)
                    if effective != image_url:
                        print(f'      Redirect -> {clean(effective)}', flush=True)
                if not 200 <= status < 300:
                    image_failures += 1
            except (OSError, ValueError) as exc:
                image_failures += 1
                rows.append(report_row('image', image_url, error=exc))
                print(f'IMG ERROR {clean(image_url)}: {clean(exc)}', flush=True)
    print(f'\nDone: {count} pages, {failures} failed / non-2xx, '
          f'{len(images)} unique images, {image_failures} image failures, '
          f'{map_failures} sitemap errors.', flush=True)
    try:
        write_reports(rows, args.csv, args.json)
        for report_path in (args.csv, args.json):
            if report_path:
                print(f'Report written: {clean(report_path)}', flush=True)
    except OSError as exc:
        print(f'Report error: {clean(exc)}', file=sys.stderr, flush=True)
        return 1
    return int(bool(failures or image_failures or map_failures))


def entrypoint():
    try:
        return main()
    except KeyboardInterrupt:
        print('\nInterrupted.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(entrypoint())
