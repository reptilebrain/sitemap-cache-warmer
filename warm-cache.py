#!/usr/bin/env python3
"""Fetch sitemap pages and print cache information. Python 3, no dependencies."""
import argparse
import gzip
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


def clean(value):
    return ' '.join(str(value or '-').split())


def fetch(url, timeout, keep_body=False):
    if urllib.parse.urlsplit(url).scheme not in ('http', 'https'):
        raise ValueError('Only HTTP and HTTPS URLs are supported')
    request = urllib.request.Request(url, headers={
        'User-Agent': 'SitemapCacheWarmer/1.0',
        'Accept-Encoding': 'gzip',
    })
    start = time.monotonic()
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc  # Print HTTP errors and continue with other pages.
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sitemap', help='Full URL to sitemap.xml or sitemap index')
    parser.add_argument('--delay', type=float, default=0.2,
                        help='Pause between pages in seconds (default: 0.2)')
    parser.add_argument('--timeout', type=float, default=30,
                        help='Socket timeout in seconds (default: 30)')
    args = parser.parse_args()
    if args.delay < 0 or args.timeout <= 0:
        parser.error('Delay must be >= 0 and timeout must be > 0')
    maps, pages = set(), set()
    pending = [args.sitemap]
    count = failures = map_failures = 0
    print(f'{"HTTP":<5} {"CF-CACHE":<12} {"TIME_s":>8} {"AGE_s":>7}  URL', flush=True)
    while pending:
        sitemap = pending.pop(0)
        if sitemap in maps:
            continue
        maps.add(sitemap)
        print(f'Reading sitemap: {clean(sitemap)}', file=sys.stderr, flush=True)
        try:
            status, _, _, _, body = fetch(sitemap, args.timeout, True)
            if not 200 <= status < 300:
                raise ValueError(f'HTTP {status}')
            root = ET.fromstring(body)
            tag = lambda element: element.tag.rsplit('}', 1)[-1]
            kind = tag(root)
            if kind not in ('sitemapindex', 'urlset'):
                raise ValueError('Expected sitemapindex or urlset')
        except (OSError, ValueError, ET.ParseError) as exc:
            print(f'Sitemap error: {clean(exc)}', file=sys.stderr, flush=True)
            map_failures += 1
            continue
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
            if url in pages:
                continue
            pages.add(url)
            count += 1
            try:
                status, headers, effective, elapsed, _ = fetch(url, args.timeout)
                print(f'{status:<5} {clean(headers.get("CF-Cache-Status")):<12} '
                      f'{elapsed:8.3f} {clean(headers.get("Age")):>7}  {clean(url)}', flush=True)
                print(f'      Last-Modified: {clean(headers.get("Last-Modified"))}'
                      f' | Sitemap lastmod: {clean(fields.get("lastmod"))}', flush=True)
                if effective != url:
                    print(f'      Redirect -> {clean(effective)}', flush=True)
                if not 200 <= status < 300:
                    failures += 1
            except (OSError, ValueError) as exc:
                failures += 1
                print(f'ERROR {clean(url)}: {clean(exc)}', flush=True)
            time.sleep(args.delay)
    print(f'\nDone: {count} pages, {failures} failed / non-2xx, '
          f'{map_failures} sitemap errors.', flush=True)
    return int(bool(failures or map_failures))


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\nInterrupted.', file=sys.stderr)
        sys.exit(130)
