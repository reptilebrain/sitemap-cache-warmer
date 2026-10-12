# Sitemap Cache Warmer

`warm-cache.py` is a local, dependency-free sitemap cache warmer. It fetches
pages listed in a sitemap, reports HTTP and cache headers, and optionally
fetches images referenced by those pages.

## Usage

```sh
python3 warm-cache.py https://example.com/sitemap.xml
```

Useful options:

```text
--workers N        Maximum concurrent page/image requests (default: 12)
--delay SECONDS    Minimum spacing in 0.1-second increments (default: 0.5)
--timeout SECONDS  Socket timeout (default: 30)
--no-images        Skip images referenced by pages
--csv FILE         Write a CSV report
--json FILE        Write a JSON report
```

The script supports sitemap indexes, gzip-compressed responses, redirects,
Cloudflare cache headers, image URLs in common HTML attributes, and deduplicates
pages, sitemaps, and images. It returns a non-zero status when a sitemap, page,
or image fails.

Reports are written only when `--csv` or `--json` is supplied. The project does
not create or manage a site registry; invoke the script with the sitemap URL
you want to warm.

## Requirements

Python 3.8 or newer. The script uses only the Python standard library.
