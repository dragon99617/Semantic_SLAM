#!/usr/bin/env python3
"""Download the public ROS 2 Retail_Street mirror linked by DeepRoboticsLab.

No login/cookies supplied by the user are required. Refuse HTML error pages.
"""
import hashlib
import http.cookiejar
import json
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path


class DownloadForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form' and attrs.get('id') == 'download-form':
            self.action = attrs.get('action')
        if tag == 'input' and self.action and attrs.get('name'):
            self.fields[attrs['name']] = attrs.get('value', '')


def download(opener, file_id, destination, expected_size=None):
    if destination.exists():
        if expected_size is not None and destination.stat().st_size != expected_size:
            raise RuntimeError(f'Existing file has unexpected size: {destination}')
        print(f'Already exists: {destination}', flush=True)
        return
    url = 'https://drive.google.com/uc?' + urllib.parse.urlencode(
        {'export': 'download', 'id': file_id}
    )
    for _ in range(3):
        response = opener.open(url, timeout=60)
        if 'text/html' not in response.headers.get('content-type', ''):
            break
        html = response.read().decode(errors='replace')
        response.close()
        form = DownloadForm()
        form.feed(html)
        if not form.action:
            raise RuntimeError('Drive returned an HTML error page, with no public download form.')
        parsed = urllib.parse.urlparse(form.action)
        if parsed.scheme != 'https' or parsed.hostname not in (
            'drive.google.com', 'drive.usercontent.google.com'
        ):
            raise RuntimeError('Unexpected download form destination.')
        url = form.action + '?' + urllib.parse.urlencode(form.fields)
    else:
        raise RuntimeError('Drive confirmation did not produce a file.')

    partial = destination.with_suffix(destination.suffix + '.part')
    digest = hashlib.sha256()
    count = 0
    last_report = 0
    started = time.monotonic()
    with response, partial.open('wb') as output:
        while chunk := response.read(4 * 1024 * 1024):
            if count == 0 and destination.suffix == '.db3' and not chunk.startswith(b'SQLite format 3\x00'):
                raise RuntimeError('Downloaded database does not have a SQLite header.')
            output.write(chunk)
            digest.update(chunk)
            count += len(chunk)
            if count - last_report >= 128 * 1024 * 1024:
                elapsed = time.monotonic() - started
                print(f'{destination.name}: {count / 1e6:.0f} MB, {count / 1e6 / elapsed:.1f} MB/s', flush=True)
                last_report = count
    if expected_size is not None and count != expected_size:
        raise RuntimeError(f'Unexpected file size: {count}; expected {expected_size}')
    partial.rename(destination)
    destination.with_suffix(destination.suffix + '.sha256').write_text(
        digest.hexdigest() + '  ' + destination.name + '\n'
    )
    print(f'Completed {destination.name}: {count} bytes', flush=True)


def main():
    root = Path(__file__).resolve().parent.parent
    target = root / 'datasets' / 'Retail_Street_mirror'
    target.mkdir(parents=True, exist_ok=True)
    provenance = {
        'source_readme': 'https://github.com/DeepRoboticsLab/fast-livo2-deep-robotics/blob/main/README.md',
        'source_folder': 'https://drive.google.com/drive/folders/15RL0__M6ZcCCf0qx8_IM40qhhoPoYnNf',
        'description': 'Community ROS 2 conversion of Retail_Street. Not the original ROS 1 bag; not independently matched to an upstream hash.',
        'files': {
            'metadata.yaml': {'file_id': '1r-75Y3C11IQr1IdtDgQYX3ugat-ffmyR'},
            'Retail_Street.db3': {'file_id': '1G5AXUw4rZy_ODfVXW9yZZd2-PoXrWu03', 'expected_size': 1995526144},
        },
    }
    (target / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    opener.addheaders = [('User-Agent', 'Mozilla/5.0')]
    for name, info in provenance['files'].items():
        download(opener, info['file_id'], target / name, info.get('expected_size'))


if __name__ == '__main__':
    main()
