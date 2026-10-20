#!/usr/bin/env python3
"""Inspect SQLite rosbag2 and validate representative CDR messages without ROS.

The first and last message of each input topic are checked. This is a data
integrity/schema check, not a SLAM accuracy test or full-message validation.
"""
import argparse
import json
import sqlite3
import struct
from pathlib import Path


class Cdr:
    def __init__(self, data):
        if data[:4] not in (b'\x00\x01\x00\x00', b'\x00\x00\x00\x00'):
            raise ValueError('Unsupported CDR encapsulation')
        self.data = memoryview(data)[4:]
        self.endian = '<' if data[1] == 1 else '>'
        self.offset = 0

    def scalar(self, fmt, alignment):
        self.offset = (self.offset + alignment - 1) // alignment * alignment
        value = struct.unpack_from(self.endian + fmt, self.data, self.offset)[0]
        self.offset += struct.calcsize(fmt)
        return value

    def string(self):
        size = self.scalar('I', 4)
        value = bytes(self.data[self.offset:self.offset + size])
        self.offset += size
        if not value.endswith(b'\0'):
            raise ValueError('Invalid CDR string')
        return value[:-1].decode()

    def header(self):
        seconds = self.scalar('i', 4)
        nanoseconds = self.scalar('I', 4)
        if not 0 <= nanoseconds < 1_000_000_000:
            raise ValueError('Invalid header nanoseconds')
        frame = self.string()
        return {'stamp_ns': seconds * 1_000_000_000 + nanoseconds, 'frame_id': frame}


def inspect_message(msgtype, data):
    cdr = Cdr(data)
    result = cdr.header()
    if msgtype.endswith('/CustomMsg'):
        result['timebase'] = cdr.scalar('Q', 8)
        result['point_num'] = cdr.scalar('I', 4)
        result['lidar_id'] = cdr.scalar('B', 1)
        cdr.offset += 3
        result['points_length'] = cdr.scalar('I', 4)
        if result['point_num'] != result['points_length'] or result['points_length'] <= 0:
            raise ValueError('Invalid Livox point count')
        required_bytes = 20 * (result['points_length'] - 1) + 19
        if len(cdr.data) - cdr.offset < required_bytes:
            raise ValueError('Livox payload is shorter than its point count')
        result['first_point'] = {
            'offset_time': cdr.scalar('I', 4),
            'x': cdr.scalar('f', 4), 'y': cdr.scalar('f', 4), 'z': cdr.scalar('f', 4),
        }
    elif msgtype == 'sensor_msgs/msg/Image':
        result['height'] = cdr.scalar('I', 4)
        result['width'] = cdr.scalar('I', 4)
        result['encoding'] = cdr.string()
        result['is_bigendian'] = cdr.scalar('B', 1)
        result['step'] = cdr.scalar('I', 4)
        result['data_length'] = cdr.scalar('I', 4)
        if result['data_length'] != result['height'] * result['step']:
            raise ValueError('Image payload dimensions do not match')
        if len(cdr.data) - cdr.offset != result['data_length']:
            raise ValueError('Image CDR payload length does not match')
    elif msgtype == 'sensor_msgs/msg/Imu':
        result['orientation_xyzw'] = [cdr.scalar('d', 8) for _ in range(4)]
        for _ in range(9):
            cdr.scalar('d', 8)
        result['angular_velocity'] = [cdr.scalar('d', 8) for _ in range(3)]
        for _ in range(9):
            cdr.scalar('d', 8)
        result['linear_acceleration'] = [cdr.scalar('d', 8) for _ in range(3)]
        for _ in range(9):
            cdr.scalar('d', 8)
        if cdr.offset != len(cdr.data):
            raise ValueError('IMU CDR payload length does not match')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('database', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    database = args.database.resolve(strict=True)
    report = {'database': str(database), 'bytes': database.stat().st_size}
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as conn:
        report['integrity'] = conn.execute('PRAGMA quick_check').fetchone()[0]
        if report['integrity'] != 'ok':
            raise ValueError('SQLite integrity check failed')
        report['schema'] = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall()
        report['topics'] = []
        for topic_id, name, msgtype, serialization in conn.execute(
            'SELECT id,name,type,serialization_format FROM topics'
        ):
            count, start, end = conn.execute(
                'SELECT count(*),min(timestamp),max(timestamp) FROM messages WHERE topic_id=?',
                (topic_id,),
            ).fetchone()
            topic = {'name': name, 'type': msgtype, 'serialization': serialization,
                     'count': count, 'start_ns': start, 'end_ns': end}
            if count > 1:
                topic['mean_rate_hz'] = (count - 1) * 1e9 / (end - start)
            topic['samples'] = []
            for order in ('ASC', 'DESC'):
                sample = conn.execute(
                    f'SELECT timestamp,data FROM messages WHERE topic_id=? ORDER BY timestamp {order} LIMIT 1',
                    (topic_id,),
                ).fetchone()
                if sample:
                    topic['samples'].append({'bag_timestamp_ns': sample[0], **inspect_message(msgtype, sample[1])})
            report['topics'].append(topic)
    text = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
    if args.output:
        args.output.write_text(text)
    print(text)


if __name__ == '__main__':
    main()
