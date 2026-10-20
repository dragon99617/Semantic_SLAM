#!/usr/bin/env python3
"""Create a Humble-compatible copy of the downloaded ROS 2 sequence.

The source stays untouched. All message payloads, record timestamps, IDs and
topic names are preserved. Only container metadata and the compatible Livox
type name change. The embedded schema was checked against driver2's .msg files.
"""
import json
import sqlite3
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent.parent
    source = root / 'datasets/Retail_Street_mirror/Retail_Street.db3'
    output = root / 'datasets/Retail_Street_humble'
    output.mkdir(exist_ok=True)
    destination = output / 'Retail_Street.db3'
    if destination.exists():
        raise SystemExit(f'Output exists; leaving it unchanged: {destination}')
    temporary = output / 'Retail_Street.db3.part'
    if temporary.exists():
        raise SystemExit(f'Incomplete output exists; inspect it before retrying: {temporary}')

    with sqlite3.connect(str(temporary), uri=True) as conn:
        conn.execute('ATTACH DATABASE ? AS source', (source.resolve(strict=True).as_uri() + '?mode=ro',))
        conn.executescript('''
            CREATE TABLE schema(schema_version INTEGER PRIMARY KEY, ros_distro TEXT NOT NULL);
            CREATE TABLE metadata(id INTEGER PRIMARY KEY, metadata_version INTEGER NOT NULL, metadata TEXT NOT NULL);
            CREATE TABLE topics(id INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL,
                                serialization_format TEXT NOT NULL, offered_qos_profiles TEXT NOT NULL);
            CREATE TABLE messages(id INTEGER PRIMARY KEY, topic_id INTEGER NOT NULL,
                                  timestamp INTEGER NOT NULL, data BLOB NOT NULL);
            INSERT INTO schema VALUES (3, 'humble');
        ''')
        conn.execute('''INSERT INTO topics
            SELECT id,name,CASE type WHEN 'livox_ros_driver/msg/CustomMsg'
              THEN 'livox_ros_driver2/msg/CustomMsg' ELSE type END,
              serialization_format,offered_qos_profiles FROM source.topics''')
        print('Copying serialized messages without changing payloads or timestamps...', flush=True)
        conn.execute('INSERT INTO messages SELECT id,topic_id,timestamp,data FROM source.messages')
        conn.execute('CREATE INDEX timestamp_idx ON messages(timestamp ASC)')
        conn.commit()
        count, start, end = conn.execute('SELECT count(*),min(timestamp),max(timestamp) FROM messages').fetchone()
        source_count = conn.execute('SELECT count(*) FROM source.messages').fetchone()[0]
        mismatches = conn.execute('''SELECT count(*) FROM source.messages s
            LEFT JOIN messages d ON d.id=s.id WHERE d.id IS NULL OR d.topic_id!=s.topic_id
            OR d.timestamp!=s.timestamp OR d.data!=s.data''').fetchone()[0]
        if count != source_count or mismatches:
            raise RuntimeError('Message preservation check failed')
        if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Output database integrity check failed')
        topics = []
        for name, msgtype, serialization, qos, messages in conn.execute('''
            SELECT t.name,t.type,t.serialization_format,t.offered_qos_profiles,count(m.id)
            FROM topics t LEFT JOIN messages m ON t.id=m.topic_id GROUP BY t.id ORDER BY t.id'''):
            topics.append({'topic_metadata': {'name': name, 'type': msgtype,
                'serialization_format': serialization, 'offered_qos_profiles': qos},
                'message_count': messages})

    duration = {'nanoseconds': end - start}
    starting_time = {'nanoseconds_since_epoch': start}
    metadata = {'rosbag2_bagfile_information': {
        'version': 5, 'storage_identifier': 'sqlite3',
        'relative_file_paths': ['Retail_Street.db3'],
        'duration': duration, 'starting_time': starting_time,
        'message_count': count, 'topics_with_message_count': topics,
        'compression_format': '', 'compression_mode': '',
        'files': [{'path': 'Retail_Street.db3', 'starting_time': starting_time,
                   'duration': duration, 'message_count': count}],
    }}
    # JSON is a YAML subset; YAML-CPP/rosbag2 can read this .yaml file.
    (output / 'metadata.yaml').write_text(json.dumps(metadata, indent=2) + '\n')
    temporary.rename(destination)
    provenance = {
        'source': str(source), 'destination': str(destination),
        'source_provenance': '../Retail_Street_mirror/provenance.json',
        'message_count': count, 'all_payloads_and_timestamps_identical': True,
        'changes': ['SQLite schema 4 to Humble schema 3', 'metadata version 9 to 5',
                    'livox_ros_driver/msg/CustomMsg to livox_ros_driver2/msg/CustomMsg'],
    }
    (output / 'preparation.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print(f'Prepared {count} messages; every payload and timestamp verified identical.', flush=True)


if __name__ == '__main__':
    main()
