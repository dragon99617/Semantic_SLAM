#!/usr/bin/env python3
"""Download the official FusionPortableV2 ugv_transition00 archive with resume.

Uses anonymous official Drive downloads. Every part must have a matching HTTP
206 Content-Range; HTML error pages are rejected. No authentication is needed.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

FILE_ID = '15ChHaSBrR05EL6X7W1VGSuo_zfebCvIm'
NAME = 'ugv_transition00.7z'
CHUNK = 2 * 1024 * 1024


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    output = root / 'datasets/FusionPortableV2/original'
    output.mkdir(parents=True, exist_ok=True)
    target = output / NAME
    if target.exists():
        raise SystemExit(f'Already exists: {target}')
    warning_url = f'https://drive.google.com/uc?export=download&id={FILE_ID}'
    with urllib.request.urlopen(warning_url, timeout=60) as response:
        html = response.read().decode()
    fields = dict(re.findall(r'name="([^"]+)" value="([^"]+)"', html))
    if fields.get('id') != FILE_ID or 'uuid' not in fields:
        raise RuntimeError('Official Drive confirmation form unavailable')
    url = 'https://drive.usercontent.google.com/download?' + urllib.parse.urlencode(fields)
    with urllib.request.urlopen(urllib.request.Request(url, headers={'Range': 'bytes=0-31'}), timeout=60) as response:
        size = int(response.headers['Content-Range'].split('/')[-1])
        if response.status != 206 or response.read()[:6] != b"7z\xbc\xaf'\x1c":
            raise RuntimeError('Response is not a 7z archive')
    parts = output / (NAME + '.parts')
    parts.mkdir(exist_ok=True)
    count = (size + CHUNK - 1) // CHUNK
    started = time.monotonic()

    def fetch(index):
        begin, end = index * CHUNK, min(size, (index + 1) * CHUNK) - 1
        destination = parts / f'{index:06d}'
        if destination.exists() and destination.stat().st_size == end - begin + 1:
            return destination.stat().st_size
        for attempt in range(5):
            try:
                request = urllib.request.Request(url, headers={'Range': f'bytes={begin}-{end}'})
                with urllib.request.urlopen(request, timeout=90) as response:
                    expected = f'bytes {begin}-{end}/{size}'
                    if response.status != 206 or response.headers.get('Content-Range') != expected:
                        raise RuntimeError(f'Invalid response for range {expected}')
                    data = response.read()
                if len(data) != end - begin + 1:
                    raise RuntimeError('Truncated response')
                temporary = destination.with_suffix('.partial')
                temporary.write_bytes(data)
                temporary.replace(destination)
                return len(data)
            except Exception as exc:
                # Quota/error HTML is a hard failure, not a partial archive.
                if isinstance(exc, RuntimeError) or attempt == 4:
                    raise
                time.sleep(1 + attempt)

    print(json.dumps({'archive': NAME, 'bytes': size, 'parts': count, 'workers': args.workers}), flush=True)
    completed = 0
    report = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        # Bound queued work so a quota error does not leave thousands of
        # requests running while the executor waits to shut down.
        indices = iter(range(count))
        pending = {pool.submit(fetch, i) for i in range(min(args.workers, count))}
        try:
            while pending:
                done, pending = concurrent.futures.wait(pending,
                    return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    completed += future.result()
                for _ in done:
                    index = next(indices, None)
                    if index is not None:
                        pending.add(pool.submit(fetch, index))
                if time.monotonic() - report >= 30:
                    elapsed = time.monotonic() - started
                    print(json.dumps({'downloaded_GiB': round(completed / 2**30, 3),
                        'percent': round(100 * completed / size, 2), 'elapsed_s': round(elapsed),
                        'MiB_s': round(completed / 2**20 / elapsed, 2)}), flush=True)
                    report = time.monotonic()
        finally:
            for future in pending:
                future.cancel()
    digest = hashlib.sha256()
    assembling = target.with_suffix('.assembling')
    with assembling.open('wb') as stream:
        for index in range(count):
            data = (parts / f'{index:06d}').read_bytes()
            stream.write(data)
            digest.update(data)
        stream.flush()
        os.fsync(stream.fileno())
    assert assembling.stat().st_size == size
    assembling.replace(target)
    provenance = {'source': warning_url, 'official_dataset_page':
        'https://fusionportable.github.io/dataset/fusionportable_v2_data/',
        'bytes': size, 'sha256': digest.hexdigest(), 'all_ranges_verified': True,
        'seconds': time.monotonic() - started}
    (output / (NAME + '.download.json')).write_text(json.dumps(provenance, indent=2) + '\n')
    print(json.dumps(provenance, indent=2), flush=True)


if __name__ == '__main__':
    main()
