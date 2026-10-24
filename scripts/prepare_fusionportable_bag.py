#!/usr/bin/env python3
"""Convert FPV2 Ouster/STIM300/left RGB to a ROS 2 Humble SQLite bag.

Use accuracy_venv. Original files are read-only. Clouds retain every byte of
point data; only their header changes from scan end to scan start using max(t).
JPEG is decoded to BGR8. IMU and image sensor times, and bag record times, stay
unchanged. No ground truth is supplied to the mapper.
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

import numpy as np
from rosbags.rosbag1 import Reader
from rosbags.typesys import Stores, get_typestore

sys.path.append('/usr/lib/python3/dist-packages')
import cv2

LIDAR = '/os_cloud_node/points'
IMU = '/stim300/imu/data_raw'
IMAGE = '/stereo/frame_left/image_raw/compressed'
TOPICS = {LIDAR: LIDAR, IMU: IMU, IMAGE: '/left_camera/image'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    database = args.output / 'fusionportable.db3'
    source_store = get_typestore(Stores.ROS1_NOETIC)
    store = get_typestore(Stores.ROS2_HUMBLE)
    counts = collections.Counter()
    headers = collections.defaultdict(list)
    original_cloud_headers = []
    durations = []
    details = {}
    source_hash = hashlib.sha256()
    data_hash = hashlib.sha256()
    conn = sqlite3.connect(database)
    conn.executescript('''
        PRAGMA journal_mode=OFF;
        CREATE TABLE schema(schema_version INTEGER PRIMARY KEY, ros_distro TEXT NOT NULL);
        INSERT INTO schema VALUES(3, 'humble');
        CREATE TABLE metadata(id INTEGER PRIMARY KEY, metadata_version INTEGER NOT NULL, metadata TEXT NOT NULL);
        CREATE TABLE topics(id INTEGER PRIMARY KEY,name TEXT NOT NULL,type TEXT NOT NULL,
                            serialization_format TEXT NOT NULL,offered_qos_profiles TEXT NOT NULL);
        CREATE TABLE messages(id INTEGER PRIMARY KEY,topic_id INTEGER NOT NULL,timestamp INTEGER NOT NULL,data BLOB NOT NULL);
    ''')
    started = report = time.monotonic()
    first = last = None
    with Reader(args.source) as reader:
        all_topics = {c.topic: {'type': c.msgtype, 'count': c.msgcount} for c in reader.connections}
        (args.output / 'source_topics.json').write_text(json.dumps(all_topics, indent=2) + '\n')
        selected = [c for c in reader.connections if c.topic in TOPICS]
        if {c.topic for c in selected} != set(TOPICS):
            raise RuntimeError(f'Missing topics: {set(TOPICS) - {c.topic for c in selected}}')
        ids = {name: i + 1 for i, name in enumerate(sorted(TOPICS))}
        types = {c.topic: ('sensor_msgs/msg/Image' if c.topic == IMAGE else c.msgtype) for c in selected}
        for name, idx in ids.items():
            conn.execute('INSERT INTO topics VALUES(?,?,?,?,?)', (idx, TOPICS[name], types[name], 'cdr', ''))
        batch = []
        for connection, timestamp, raw in reader.messages(connections=selected):
            name = connection.topic
            source_hash.update(raw)
            original = source_store.deserialize_ros1(raw, connection.msgtype)
            original_ns = original.header.stamp.sec * 10**9 + original.header.stamp.nanosec
            cdr = store.ros1_to_cdr(raw, connection.msgtype)
            msg = store.deserialize_cdr(cdr, connection.msgtype)
            if name == LIDAR:
                if msg.is_bigendian or msg.row_step != msg.width * msg.point_step:
                    raise RuntimeError('Unsupported cloud endian/padding')
                f = next(f for f in msg.fields if f.name == 't')
                if f.datatype != 6 or f.count != 1:
                    raise RuntimeError('Expected uint32 nanosecond per-point t')
                dtype = np.dtype({'names': ['t'], 'formats': ['<u4'], 'offsets': [f.offset], 'itemsize': msg.point_step})
                times = np.frombuffer(msg.data, dtype=dtype)['t']
                duration = int(times.max())
                if int(times.min()) != 0 or not 50_000_000 < duration < 150_000_000:
                    raise RuntimeError(f'Unexpected per-point time range: {times.min()}..{duration}')
                original_cloud_headers.append(original_ns)
                durations.append(duration)
                new_ns = original_ns - duration
                msg.header.stamp.sec, msg.header.stamp.nanosec = divmod(new_ns, 10**9)
                assert np.array_equal(msg.data, original.data)
                data_hash.update(msg.data)
                if not counts[name]:
                    details['pointcloud'] = {'width': msg.width, 'height': msg.height,
                        'point_step': msg.point_step, 'fields': [dict(name=f.name, offset=f.offset,
                            datatype=f.datatype, count=f.count) for f in msg.fields]}
                cdr = store.serialize_cdr(msg, connection.msgtype)
            elif name == IMAGE:
                decoded = cv2.imdecode(msg.data, cv2.IMREAD_COLOR)
                if decoded is None or decoded.shape != (768, 1024, 3):
                    raise RuntimeError('Invalid JPEG image dimensions')
                msg = store.types['sensor_msgs/msg/Image'](header=msg.header,
                    height=decoded.shape[0], width=decoded.shape[1], encoding='bgr8',
                    is_bigendian=0, step=decoded.shape[1] * 3, data=decoded.reshape(-1))
                cdr = store.serialize_cdr(msg, 'sensor_msgs/msg/Image')
                if not counts[name]:
                    cv2.imwrite(str(args.output / 'first_decoded.jpg'), decoded)
                    details['image'] = {'source_format': original.format, 'width': msg.width,
                        'height': msg.height, 'encoding': msg.encoding}
            # Verify every written message against its intended content and time.
            check = store.deserialize_cdr(cdr, types[name])
            expected_ns = original_ns - durations[-1] if name == LIDAR else original_ns
            actual_ns = check.header.stamp.sec * 10**9 + check.header.stamp.nanosec
            assert actual_ns == expected_ns
            if hasattr(msg, 'data'):
                assert np.array_equal(check.data, msg.data)
            else:
                for attr in ('linear_acceleration', 'angular_velocity', 'orientation'):
                    components = ('x', 'y', 'z', 'w') if attr == 'orientation' else ('x', 'y', 'z')
                    for component in components:
                        assert getattr(getattr(check, attr), component) == getattr(getattr(original, attr), component)
            headers[name].append(actual_ns)
            batch.append((ids[name], timestamp, bytes(cdr)))
            counts[name] += 1
            first = timestamp if first is None else min(first, timestamp)
            last = timestamp if last is None else max(last, timestamp)
            if len(batch) >= 64:
                conn.executemany('INSERT INTO messages(topic_id,timestamp,data) VALUES(?,?,?)', batch)
                conn.commit()
                batch.clear()
            if time.monotonic() - report >= 30:
                print(json.dumps({'counts': dict(counts), 'elapsed_s': round(time.monotonic() - started),
                    'sensor_span_s': round((last - first) / 1e9, 2)}), flush=True)
                report = time.monotonic()
        conn.executemany('INSERT INTO messages(topic_id,timestamp,data) VALUES(?,?,?)', batch)
        conn.execute('CREATE INDEX timestamp_idx ON messages(timestamp ASC)')
        conn.commit()
        for c in selected:
            assert counts[c.topic] == sum(x.msgcount for x in selected if x.topic == c.topic)
        assert conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    conn.close()
    metadata = {'rosbag2_bagfile_information': {'version': 5, 'storage_identifier': 'sqlite3',
        'relative_file_paths': [database.name], 'duration': {'nanoseconds': last - first},
        'starting_time': {'nanoseconds_since_epoch': first}, 'message_count': sum(counts.values()),
        'topics_with_message_count': [{'topic_metadata': {'name': TOPICS[name], 'type': types[name],
            'serialization_format': 'cdr', 'offered_qos_profiles': ''}, 'message_count': counts[name]}
            for name in sorted(TOPICS)], 'compression_format': '', 'compression_mode': '',
        'files': [{'path': database.name, 'starting_time': {'nanoseconds_since_epoch': first},
            'duration': {'nanoseconds': last - first}, 'message_count': sum(counts.values())}]}}
    (args.output / 'metadata.yaml').write_text(json.dumps(metadata, indent=2) + '\n')
    summaries = {}
    for name, stamps in headers.items():
        d = np.diff(np.array(stamps, dtype=np.int64)) / 1e9
        summaries[name] = dict(count=len(stamps), first_ns=stamps[0], last_ns=stamps[-1],
            backward_steps=int(np.sum(d < 0)), duplicate_steps=int(np.sum(d == 0)),
            dt_min_s=float(d.min()), dt_median_s=float(np.median(d)), dt_max_s=float(d.max()))
    np.savetxt(args.output / 'lidar_stamp_corrections.csv',
        np.column_stack([original_cloud_headers, durations]), fmt='%d', delimiter=',',
        header='original_header_ns,subtracted_scan_duration_ns')
    provenance = {'source_bag': str(args.source.resolve()), 'selected_topics': TOPICS,
        'all_selected_record_counts_match_source': True, 'all_cdr_messages_roundtrip_verified': True,
        'all_pointcloud_buffers_preserved': True, 'image_pixels_equal_to_opencv_jpeg_decode': True,
        'record_timestamps_preserved': True, 'imu_and_image_headers_preserved': True,
        'cloud_header_transform': 'original scan-end header - max(uint32 per-point t) nanoseconds',
        'additional_lidar_time_offset_required': 0.0,
        'scan_duration_s': {'min': min(durations)/1e9, 'median': float(np.median(durations))/1e9,
                            'max': max(durations)/1e9},
        'concatenated_selected_source_payload_sha256': source_hash.hexdigest(),
        'concatenated_cloud_data_sha256': data_hash.hexdigest(),
        'headers': summaries, **details, 'conversion_seconds': time.monotonic() - started}
    (args.output / 'preparation.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print(json.dumps(provenance, indent=2), flush=True)


if __name__ == '__main__':
    main()
