#!/usr/bin/env python3
"""Check downloaded ROS2 bags, metadata counts and trajectory coverage."""

import argparse
import json
import math
from pathlib import Path
import sqlite3

import yaml


def sample_messages(db, rows):
    import cv2
    import numpy as np
    from rclpy.serialization import deserialize_message
    from rosidl_runtime_py.utilities import get_message

    samples = []
    for name, kind, count, _, _ in rows:
        if not count:
            continue
        topic_id = db.execute("SELECT id FROM topics WHERE name=?", (name,)).fetchone()[0]
        payload = db.execute("SELECT data FROM messages WHERE topic_id=? ORDER BY timestamp LIMIT 1", (topic_id,)).fetchone()[0]
        message = deserialize_message(payload, get_message(kind))
        item = {"topic": name, "header_time": message.header.stamp.sec + message.header.stamp.nanosec / 1e9,
                "frame_id": message.header.frame_id, "serialized_bytes": len(payload)}
        if kind.endswith("/CompressedImage"):
            decoded = cv2.imdecode(np.frombuffer(message.data, dtype=np.uint8), cv2.IMREAD_COLOR)
            if decoded is None:
                raise ValueError(f"Compressed image cannot be decoded: {name}")
            item.update({"format": message.format, "decoded_shape": list(decoded.shape)})
        elif kind.endswith("/PointCloud2"):
            if len(message.data) != message.row_step * message.height:
                raise ValueError("Invalid PointCloud2 buffer size")
            item.update({"width": message.width, "height": message.height, "point_step": message.point_step,
                         "fields": [{"name": f.name, "offset": f.offset, "datatype": f.datatype, "count": f.count} for f in message.fields]})
        elif kind.endswith("/Imu"):
            values = [message.angular_velocity.x, message.angular_velocity.y, message.angular_velocity.z,
                      message.linear_acceleration.x, message.linear_acceleration.y, message.linear_acceleration.z]
            if not all(math.isfinite(value) for value in values):
                raise ValueError("Non-finite IMU sample")
            item["gyro_accel"] = values
        samples.append(item)
    return samples


def check(root, manifest, inspect_samples=False):
    report = {"revision": manifest["revision"], "sequences": {}}
    for sequence in manifest["sequences"]:
        seq_root = root / "sequences" / sequence
        bags = []
        for metadata in sorted(seq_root.glob("raw/ros2bag/*/metadata.yaml")):
            meta = yaml.safe_load(metadata.read_text())["rosbag2_bagfile_information"]
            for filename in meta["relative_file_paths"]:
                path = metadata.parent / filename
                print(f"Checking {path.name}", flush=True)
                with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as db:
                    result = db.execute("PRAGMA quick_check").fetchall()
                    if result != [("ok",)]:
                        raise ValueError(f"SQLite check failed: {path}: {result}")
                    rows = db.execute("SELECT t.name,t.type,COUNT(m.id),MIN(m.timestamp),MAX(m.timestamp) FROM topics t LEFT JOIN messages m ON t.id=m.topic_id GROUP BY t.id").fetchall()
                    topics = [{"name": name, "type": kind, "count": count, "start_ns": start, "end_ns": end} for name, kind, count, start, end in rows]
                    expected = {item["topic_metadata"]["name"]: item["message_count"] for item in meta["topics_with_message_count"]}
                    if {item["name"]: item["count"] for item in topics} != expected:
                        raise ValueError(f"Topic counts differ from upstream metadata: {path}")
                    count = sum(item["count"] for item in topics)
                    # The selected upstream bags each have a single SQLite file.
                    if count != meta["message_count"]:
                        raise ValueError(f"Total count differs from metadata: {path}")
                    required = {"/alphasense_driver_ros/imu", "/hesai/pandar", "/alphasense_driver_ros/cam0/debayered/image/compressed"}
                    if not required.issubset(expected):
                        raise ValueError(f"Required LIVO topics missing: {path}")
                    bags.append({"path": str(path.relative_to(root)), "sqlite_quick_check": "ok", "message_count": count, "topics": topics,
                                 "start_ns": min(item["start_ns"] for item in topics), "end_ns": max(item["end_ns"] for item in topics)})
                    if inspect_samples:
                        bags[-1]["sample_messages"] = sample_messages(db, rows)
        if not bags:
            raise ValueError(f"No ROS2 bags found: {sequence}")
        gt = seq_root / "processed/trajectory/gt-tum.txt"
        records = []
        for line in gt.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            values = [float(value) for value in line.split()]
            if len(values) != 8 or not all(math.isfinite(value) for value in values):
                raise ValueError(f"Invalid GT pose: {gt}")
            if records and values[0] <= records[-1][0]:
                raise ValueError(f"GT timestamps are not strictly increasing: {gt}")
            records.append(values)
        if not records:
            raise ValueError(f"Empty GT trajectory: {gt}")
        start = min(bag["start_ns"] for bag in bags) / 1e9
        end = max(bag["end_ns"] for bag in bags) / 1e9
        if min(end, records[-1][0]) <= max(start, records[0][0]):
            raise ValueError(f"No GT/data timestamp overlap: {sequence}")
        norm_error = max(abs(math.sqrt(sum(v*v for v in row[4:8])) - 1) for row in records)
        if norm_error > 1e-3:
            raise ValueError(f"Invalid GT quaternions: {sequence}")
        bags.sort(key=lambda bag: bag["start_ns"])
        report["sequences"][sequence] = {"bags": bags, "record_time_span_seconds": end-start,
            "between_bag_gaps_seconds": [(right["start_ns"]-left["end_ns"])/1e9 for left, right in zip(bags, bags[1:])],
            "ground_truth": {"frame": "base", "poses": len(records), "start": records[0][0], "end": records[-1][0],
                "max_quaternion_norm_error": norm_error, "overlap_seconds": min(end, records[-1][0])-max(start, records[0][0]),
                "source": "LiDAR-to-TLS registration; not independent motion capture"}}
    report["status"] = "passed"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("datasets/OxfordSpires"))
    parser.add_argument("--manifest", type=Path, default=Path("experiments/oxford_spires/download_manifest.json"))
    parser.add_argument("--report", type=Path, default=Path("experiments/oxford_spires/data_checks.json"))
    parser.add_argument("--sample-messages", action="store_true", help="Also deserialize sensor samples and decode JPEGs (source scripts/env.sh first)")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    status = json.loads((args.manifest.parent / "download_status.json").read_text())
    if status["status"] != "completed" or status["revision"] != manifest["revision"]:
        raise ValueError("Download and upstream hash verification must complete first")
    report = check(args.root, manifest, args.sample_messages)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Passed: {len(report['sequences'])} sequences", flush=True)


if __name__ == "__main__":
    main()
