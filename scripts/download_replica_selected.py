#!/usr/bin/env python3
"""Fetch selected official Replica ZIP members using verified HTTP ranges.

The sparse ZIP is a local cache, not a complete archive. Each extracted member
is checked against the official ZIP CRC by zipfile and given a SHA-256 manifest.
"""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import struct
import time
import zipfile

import requests

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://cvg-data.inf.ethz.ch/nice-slam/data/Replica.zip'
SIZE = 12442855671
CACHE = ROOT / 'datasets/Replica_original'
SCENES = ('room0', 'office0', 'office1')


def fetch(start, end):
    for attempt in range(5):
        try:
            r = requests.get(URL, headers={'Range': f'bytes={start}-{end}'}, timeout=180)
            r.raise_for_status()
            assert r.status_code == 206
            assert r.headers['Content-Range'] == f'bytes {start}-{end}/{SIZE}'
            assert len(r.content) == end - start + 1
            return r.content
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    sparse = CACHE / 'Replica.selected.sparse.zip'
    if not sparse.exists():
        tail = fetch(SIZE - 65536, SIZE - 1)
        i = tail.rfind(b'PK\x05\x06')
        z64off = struct.unpack('<4sLQL', tail[i-20:i])[2]
        k = z64off - (SIZE - len(tail))
        cdoff = struct.unpack('<4sQ2H2L4Q', tail[k:k+56])[-1]
        central = fetch(cdoff, SIZE-1)
        with sparse.open('wb') as f:
            f.truncate(SIZE)
            f.seek(cdoff)
            f.write(central)
    with zipfile.ZipFile(sparse) as z:
        selected = [i for i in z.infolist() if any(
            i.filename.startswith(f'Replica/{s}/') or i.filename == f'Replica/{s}_mesh.ply'
            for s in SCENES)]
    intervals = []
    for i in sorted(selected, key=lambda x: x.header_offset):
        start, end = i.header_offset, i.header_offset + 30 + len(i.filename.encode()) + len(i.extra) + i.compress_size + 32
        if intervals and start - intervals[-1][1] < 1024 * 1024:
            intervals[-1][1] = max(end, intervals[-1][1])
        else:
            intervals.append([start, end])
    chunk_size = 16 * 1024 * 1024
    chunks = [(s, min(s+chunk_size, end)-1) for start, end in intervals
              for s in range(start, end, chunk_size)]
    flags = CACHE / 'ranges'
    flags.mkdir(exist_ok=True)
    fd = os.open(sparse, os.O_RDWR)

    def download(chunk):
        start, end = chunk
        flag = flags / f'{start}-{end}.done'
        if flag.exists():
            return
        data = fetch(start, end)
        written = os.pwrite(fd, data, start)
        assert written == len(data)
        flag.write_text(hashlib.sha256(data).hexdigest() + '\n')

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            for j, _ in enumerate(pool.map(download, chunks), 1):
                print(f'Ranges {j}/{len(chunks)}', flush=True)
    finally:
        os.close(fd)
    manifest = {'url': URL, 'official_archive_size': SIZE,
                'scenes': SCENES, 'cache_is_partial': True, 'members': []}
    with zipfile.ZipFile(sparse) as z:
        for i in selected:
            if i.is_dir():
                continue
            assert '..' not in Path(i.filename).parts
            target = ROOT / 'datasets' / i.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha256()
            with z.open(i) as source, target.open('wb') as dest:
                while data := source.read(1024*1024):
                    dest.write(data)
                    h.update(data)
            manifest['members'].append({'name': i.filename, 'size': i.file_size,
                                        'crc32': i.CRC, 'sha256': h.hexdigest()})
    (CACHE / 'selected_provenance.json').write_text(json.dumps(manifest, indent=2))
    print('Selected scenes extracted; CRC and SHA-256 manifest saved.', flush=True)


if __name__ == '__main__':
    main()
