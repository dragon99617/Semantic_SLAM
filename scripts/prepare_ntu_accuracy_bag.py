#!/usr/bin/env python3
"""Convert required standard ROS 1 messages to a Humble SQLite bag.

Use third_party/accuracy_venv/bin/python. The original bag is never modified.
No restamping, point filtering, calibration or ground-truth corrections occur.
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import numpy as np
from rosbags.rosbag1 import Reader
from rosbags.typesys import Stores, get_typestore


TOPICS = {'/os1_cloud_node1/points', '/left/image_raw', '/imu/imu', '/leica/pose/relative'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    database = args.output / 'eee_01.db3'
    # The conversion generator handles ROS 1 Header.seq specially; use the
    # destination schema without seq rather than a ROS 1 destination schema.
    store = get_typestore(Stores.ROS2_HUMBLE)
    source_store = get_typestore(Stores.ROS1_NOETIC)
    counts = collections.Counter()
    headers = collections.defaultdict(list)
    gt = []
    details = {}
    first = last = None
    started = time.monotonic()
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
    ros1_hash = hashlib.sha256()
    cdr_hash = hashlib.sha256()
    with Reader(args.source) as reader:
        all_topics = {c.topic: {'type': c.msgtype, 'count': c.msgcount} for c in reader.connections}
        (args.output / 'source_topics.json').write_text(json.dumps(all_topics, indent=2) + '\n')
        connections = [c for c in reader.connections if c.topic in TOPICS]
        if {c.topic for c in connections} != TOPICS:
            raise RuntimeError(f'Missing topics: {TOPICS - {c.topic for c in connections}}')
        topic_ids = {name: i + 1 for i, name in enumerate(sorted(TOPICS))}
        types = {c.topic: c.msgtype for c in connections}
        for name, idx in topic_ids.items():
            conn.execute('INSERT INTO topics VALUES(?,?,?,?,?)', (idx, name, types[name], 'cdr', ''))
        batch = []
        for connection, timestamp, raw in reader.messages(connections=connections):
            cdr = store.ros1_to_cdr(raw, connection.msgtype)
            name = connection.topic
            msg = source_store.deserialize_ros1(raw, connection.msgtype)
            roundtrip = store.deserialize_cdr(cdr, connection.msgtype)
            assert roundtrip.header.stamp == msg.header.stamp
            if hasattr(msg, 'data'):
                assert np.array_equal(msg.data, roundtrip.data)
            if name == '/imu/imu':
                for attribute in ('linear_acceleration', 'angular_velocity', 'orientation'):
                    a, b = getattr(roundtrip, attribute), getattr(msg, attribute)
                    for component in ('x', 'y', 'z'):
                        assert getattr(a, component) == getattr(b, component)
                    if attribute == 'orientation':
                        assert a.w == b.w
            header_ns = msg.header.stamp.sec * 10**9 + msg.header.stamp.nanosec
            headers[name].append(header_ns)
            ros1_hash.update(raw)
            cdr_hash.update(cdr)
            # Save schema samples; payload bytes and header times are checked
            # for every converted image/pointcloud above.
            if not counts[name]:
                if name.endswith('/points'):
                    details['pointcloud'] = {'width': msg.width, 'height': msg.height,
                        'point_step': msg.point_step, 'row_step': msg.row_step,
                        'fields': [{'name': f.name, 'offset': f.offset, 'datatype': f.datatype,
                                    'count': f.count} for f in msg.fields]}
                elif name == '/left/image_raw':
                    details['image'] = {'width': msg.width, 'height': msg.height,
                                        'encoding': msg.encoding, 'step': msg.step}
            if name == '/leica/pose/relative':
                p = msg.pose.position
                gt.append((header_ns / 1e9, p.x, p.y, p.z, 0., 0., 0., 1.))
            batch.append((topic_ids[name], timestamp, bytes(cdr)))
            counts[name] += 1
            first = timestamp if first is None else min(first, timestamp)
            last = timestamp if last is None else max(last, timestamp)
            if len(batch) == 500:
                conn.executemany('INSERT INTO messages(topic_id,timestamp,data) VALUES(?,?,?)', batch)
                conn.commit()
                batch.clear()
            if sum(counts.values()) % 20000 == 0:
                print(dict(counts), flush=True)
        conn.executemany('INSERT INTO messages(topic_id,timestamp,data) VALUES(?,?,?)', batch)
        conn.execute('CREATE INDEX timestamp_idx ON messages(timestamp ASC)')
        conn.commit()
        for c in connections:
            assert counts[c.topic] == c.msgcount, (c.topic, counts[c.topic], c.msgcount)
        assert conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    conn.close()
    metadata = {'rosbag2_bagfile_information': {'version': 5, 'storage_identifier': 'sqlite3',
        'relative_file_paths': [database.name], 'duration': {'nanoseconds': last - first},
        'starting_time': {'nanoseconds_since_epoch': first}, 'message_count': sum(counts.values()),
        'topics_with_message_count': [{'topic_metadata': {'name': name, 'type': types[name],
            'serialization_format': 'cdr', 'offered_qos_profiles': ''}, 'message_count': counts[name]}
            for name in sorted(TOPICS)], 'compression_format': '', 'compression_mode': '',
        'files': [{'path': database.name, 'starting_time': {'nanoseconds_since_epoch': first},
            'duration': {'nanoseconds': last - first}, 'message_count': sum(counts.values())}]}}
    (args.output / 'metadata.yaml').write_text(json.dumps(metadata, indent=2) + '\n')
    np.savetxt(args.output / 'eee_01_gt_from_bag.tum', gt,
               fmt=['%.9f'] + ['%.9f'] * 7)
    header_summary = {}
    for name, stamps in headers.items():
        a = np.array(stamps, dtype=np.int64)
        delta = np.diff(a) / 1e9
        header_summary[name] = {'count': len(a), 'first_ns': int(a[0]), 'last_ns': int(a[-1]),
            'backward_steps': int(np.sum(delta < 0)), 'duplicate_steps': int(np.sum(delta == 0)),
            'dt_min_s': float(delta.min()), 'dt_median_s': float(np.median(delta)),
            'dt_max_s': float(delta.max())}
    provenance = {'source_bag': str(args.source.resolve()), 'selected_topics': sorted(TOPICS),
        'all_selected_record_counts_match_source': True, 'sensor_headers_preserved': True,
        'all_image_and_pointcloud_buffers_verified_equal_after_conversion': True,
        'record_timestamps_preserved': True, 'data_transform': 'ROS 1 wire serialization to ROS 2 CDR only; Header.seq omitted by ROS 2 schema',
        'ground_truth': 'Leica measured prism positions from message header times; identity quaternions are placeholders, not measured orientations',
        'restamping_applied': False, 'concatenated_ros1_payload_sha256': ros1_hash.hexdigest(),
        'concatenated_cdr_payload_sha256': cdr_hash.hexdigest(), 'headers': header_summary,
        **details, 'conversion_seconds': time.monotonic() - started}
    (args.output / 'preparation.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print(json.dumps(provenance, indent=2), flush=True)


if __name__ == '__main__':
    main()
