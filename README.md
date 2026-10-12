# Sitemap Cache Warmer

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![No external dependencies](https://img.shields.io/badge/dependencies-standard%20library-success.svg)](https://docs.python.org/3/library/)

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
--delay SECONDS    Minimum spacing between requests, in 0.1-second increments
                   (default: 0.5; for example: --delay 0.5)
--timeout SECONDS  Socket timeout per request (default: 30)
--retries N        Retries for transient failures (default: 1; use 0 to disable)
--no-images        Skip images referenced by pages
--verbose          Show detailed cache and modification headers
--csv FILE         Write a CSV report
--json FILE        Write a JSON report
```

The terminal output stays compact by default. Use `--verbose` for detailed
headers; JSON and CSV reports always include the full metadata.
Report rows are sorted consistently by type and URL, even though requests run
in parallel.

The script supports sitemap indexes, gzip-compressed responses, redirects,
common cache-status headers, image URLs in common HTML attributes, and
deduplicates pages, sitemaps, and images. Reports include cache status and
source header, age, cache directives, validators, expiry, and `Vary`.
Transient failures are retried once by default; permanent HTTP failures are
not retried. It returns a non-zero status when a sitemap, page, or image fails.

Reports are written only when `--csv` or `--json` is supplied. The project does
not create or manage a site registry; invoke the script with the sitemap URL
you want to warm.

## Tests

Run the dependency-free test suite with:

```sh
python3 -m unittest discover -s tests -v
```

## Requirements

Python 3.8 or newer. The script uses only the Python standard library.
