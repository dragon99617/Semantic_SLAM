#!/usr/bin/env python3
"""Fetch a pinned Oxford Spires selection, resume ranges, verify upstream hashes."""

import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import threading
import time

import requests


LOCAL = threading.local()


def session():
    if not hasattr(LOCAL, "session"):
        LOCAL.session = requests.Session()
    return LOCAL.session


def digest_file(path, entry):
    sha256 = hashlib.sha256()
    git_sha = hashlib.sha1(f"blob {entry['size']}\0".encode())
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024**2), b""):
            sha256.update(chunk)
            git_sha.update(chunk)
    if path.stat().st_size != entry["size"]:
        raise RuntimeError(f"Size mismatch: {entry['path']}")
    expected = entry.get("lfs", {}).get("oid")
    actual = sha256.hexdigest() if expected else git_sha.hexdigest()
    expected = expected or entry["oid"]
    if actual != expected:
        raise RuntimeError(f"Upstream hash mismatch: {entry['path']}")
    return sha256.hexdigest()


def fetch(url, dest, size, start=None, end=None):
    """Resume a chunk using small, bounded HTTP ranges and exact byte checks."""
    expected = size if start is None else end - start + 1
    if dest.exists() and dest.stat().st_size == expected:
        return
    temporary = dest.with_name(dest.name + ".tmp")
    while True:
        offset = temporary.stat().st_size if temporary.exists() else 0
        if offset > expected:
            temporary.unlink()
            offset = 0
        if offset == expected:
            temporary.replace(dest)
            return
        for attempt in range(8):
            try:
                # Re-read after a truncated response; keep every received byte.
                offset = temporary.stat().st_size if temporary.exists() else 0
                if offset == expected:
                    break
                headers = {"Accept-Encoding": "identity"}
                request_url = url
                response_expected = expected
                if start is not None:
                    request_start = start + offset
                    request_end = min(end, request_start + 8 * 1024**2 - 1)
                    response_expected = request_end - request_start + 1
                    headers["Range"] = f"bytes={request_start}-{request_end}"
                    request_url += f"?download=true&segment={request_start}-{request_end}&request={time.time_ns()}"
                response_started = time.monotonic()
                with session().get(request_url, headers=headers, timeout=(20, 20), stream=True) as response:
                    response.raise_for_status()
                    if start is not None:
                        wanted = f"bytes {request_start}-{request_end}/{size}"
                        if response.status_code != 206 or response.headers.get("Content-Range") != wanted:
                            raise RuntimeError("Incorrect HTTP range response")
                    if "text/html" in response.headers.get("Content-Type", ""):
                        raise RuntimeError("HTML returned instead of dataset")
                    count = 0
                    with temporary.open("ab" if start is not None and offset else "wb") as output:
                        for block in response.iter_content(128 * 1024):
                            count += len(block)
                            if count > response_expected:
                                raise RuntimeError("Response exceeds expected size")
                            output.write(block)
                            if time.monotonic() - response_started > 60 and count < response_expected:
                                raise RuntimeError("Slow response; resume with a new connection")
                    if count != response_expected:
                        raise RuntimeError("Truncated response")
                break
            except (requests.RequestException, RuntimeError) as error:
                if attempt == 7:
                    raise RuntimeError(f"Download failed ({type(error).__name__}): {dest.name}") from None
                reason = str(error) if isinstance(error, RuntimeError) else type(error).__name__
                print(f"Retry {attempt + 1}: {dest.name} ({reason})", flush=True)
                time.sleep(min(2 ** attempt, 15))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("experiments/oxford_spires/download_manifest.json"))
    parser.add_argument("--output", type=Path, default=Path("datasets/OxfordSpires"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--chunk-mib", type=int, default=64)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    state_path = args.manifest.parent / "download_status.json"
    state = {"status": "downloading", "revision": manifest["revision"], "total_bytes": manifest["total_bytes"], "verified": []}
    started = time.monotonic()

    def save():
        state["elapsed_seconds"] = round(time.monotonic() - started, 1)
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n")
        tmp.replace(state_path)

    save()
    try:
        # Small calibration, metadata and GT files are immediately available.
        for entry in sorted(manifest["files"], key=lambda item: item["size"]):
            relative = Path(entry["path"])
            dest = root / relative
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Manifest path escapes output directory")
            dest.parent.mkdir(parents=True, exist_ok=True)
            url = f"https://huggingface.co/datasets/{manifest['repository']}/resolve/{manifest['revision']}/{entry['path']}"
            state["current_file"] = entry["path"]
            save()
            if not dest.exists():
                if entry["size"] <= 8 * 1024**2:
                    fetch(url, dest, entry["size"])
                else:
                    chunks_dir = dest.with_name(dest.name + ".chunks")
                    chunks_dir.mkdir(exist_ok=True)
                    chunk_size = args.chunk_mib * 1024**2
                    ranges = [(start, min(start + chunk_size, entry["size"]) - 1) for start in range(0, entry["size"], chunk_size)]
                    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
                        pending = {pool.submit(fetch, url, chunks_dir / f"{start:012d}.part", entry["size"], start, end): (start, end) for start, end in ranges}
                        complete_bytes = 0
                        report_at = 0
                        for future in concurrent.futures.as_completed(pending):
                            future.result()
                            start, end = pending[future]
                            complete_bytes += end - start + 1
                            if time.monotonic() - report_at >= 25 or complete_bytes == entry["size"]:
                                report_at = time.monotonic()
                                state["current_complete_bytes"] = complete_bytes
                                save()
                                print(f"{dest.name}: {complete_bytes / 1e9:.2f}/{entry['size'] / 1e9:.2f} GB", flush=True)
                    partial = dest.with_name(dest.name + ".part")
                    with partial.open("wb") as output:
                        for start, _ in ranges:
                            with (chunks_dir / f"{start:012d}.part").open("rb") as source:
                                for data in iter(lambda: source.read(8 * 1024**2), b""):
                                    output.write(data)
                    digest_file(partial, entry)
                    partial.replace(dest)
                    for chunk in chunks_dir.iterdir():
                        chunk.unlink()
                    chunks_dir.rmdir()
            sha256 = digest_file(dest, entry)
            state["verified"].append({"path": entry["path"], "bytes": entry["size"], "sha256": sha256, "upstream_hash_verified": True})
            state.pop("current_complete_bytes", None)
            save()
            print(f"Verified: {entry['path']}", flush=True)
        state["status"] = "completed"
        state["verified_bytes"] = sum(item["bytes"] for item in state["verified"])
        state.pop("current_file", None)
        save()
        (args.manifest.parent / "SHA256SUMS").write_text("".join(f"{item['sha256']}  {item['path']}\n" for item in state["verified"]))
        print(f"Completed: {state['verified_bytes'] / 1e9:.3f} GB; all upstream hashes verified", flush=True)
    except BaseException as error:
        state["status"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
        state["error"] = f"{type(error).__name__}: {error}"
        save()
        raise


if __name__ == "__main__":
    main()
