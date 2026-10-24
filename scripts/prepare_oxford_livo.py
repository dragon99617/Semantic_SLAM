#!/usr/bin/env python3
"""Prepare Oxford Spires raw front-camera/Hesai/IMU data for native LIVO.

Source scripts/env.sh. Preserve sensor and recording timestamps and every point;
stable-sort point records by acquisition time for the existing XT32 handler.
Decode JPEG to owned BGR8; keep the native calibrated equidistant camera model.
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import cv2
import numpy as np
from rclpy.serialization import deserialize_message, serialize_message
from sensor_msgs.msg import PointCloud2, CompressedImage, Image, Imu
import yaml

LIDAR = '/hesai/pandar'
IMU = '/alphasense_driver_ros/imu'
IMAGE = '/alphasense_driver_ros/cam0/debayered/image/compressed'
TOPICS = {LIDAR: LIDAR, IMU: IMU, IMAGE: '/left_camera/image'}
CLASSES = {LIDAR: PointCloud2, IMU: Imu, IMAGE: CompressedImage}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('sequence')
    ap.add_argument('output', type=Path)
    ap.add_argument('--duration', type=float)
    ap.add_argument('--image-scale',type=float,default=1.,choices=[1.,.5])
    a = ap.parse_args()
    root = Path(__file__).resolve().parent.parent
    src = root/'datasets/OxfordSpires/sequences'/a.sequence
    a.output.mkdir(parents=True, exist_ok=False)
    cv2.setNumThreads(2)
    inputs = []
    for f in src.glob('raw/ros2bag/*/metadata.yaml'):
        meta = yaml.safe_load(f.read_text())['rosbag2_bagfile_information']
        for name in meta['relative_file_paths']:
            inputs.append((meta['starting_time']['nanoseconds_since_epoch'], f.parent/name, meta))
    inputs.sort()
    if not inputs:
        raise ValueError('No source bags')
    cutoff = inputs[0][0]+int(a.duration*1e9) if a.duration else None
    dbfile = a.output/'oxford.db3'
    db = sqlite3.connect(dbfile)
    db.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; '
        'CREATE TABLE schema(schema_version INTEGER PRIMARY KEY,ros_distro TEXT NOT NULL); '
        "INSERT INTO schema VALUES(3,'humble'); "
        'CREATE TABLE metadata(id INTEGER PRIMARY KEY,metadata_version INTEGER NOT NULL,metadata TEXT NOT NULL); '
        'CREATE TABLE topics(id INTEGER PRIMARY KEY,name TEXT NOT NULL,type TEXT NOT NULL,serialization_format TEXT NOT NULL,offered_qos_profiles TEXT NOT NULL); '
        'CREATE TABLE messages(id INTEGER PRIMARY KEY,topic_id INTEGER NOT NULL,timestamp INTEGER NOT NULL,data BLOB NOT NULL);')
    ids = {topic:i+1 for i,topic in enumerate(TOPICS)}
    kinds = {LIDAR:'sensor_msgs/msg/PointCloud2',IMU:'sensor_msgs/msg/Imu',IMAGE:'sensor_msgs/msg/Image'}
    for topic,i in ids.items():
        db.execute('INSERT INTO topics VALUES(?,?,?,?,?)',(i,TOPICS[topic],kinds[topic],'cdr',''))
    stamps = collections.defaultdict(list)
    expected = collections.Counter()
    timings = []
    source_cloud_digest, sorted_cloud_digest = hashlib.sha256(),hashlib.sha256()
    reordered = 0
    records = []
    first = last = None
    started = report = time.monotonic()
    for _,path,meta in inputs:
        with sqlite3.connect(f'file:{path.resolve()}?mode=ro',uri=True) as source:
            topics = {i:name for i,name in source.execute('SELECT id,name FROM topics') if name in TOPICS}
            if set(topics.values()) != set(TOPICS):
                raise ValueError('Missing required source topics')
            for row in meta['topics_with_message_count']:
                name=row['topic_metadata']['name']
                if name in TOPICS: expected[name]+=row['message_count']
            query='SELECT topic_id,timestamp,data FROM messages WHERE topic_id IN ('+','.join('?' for _ in topics)+') ORDER BY timestamp,id'
            for tid,record_ns,payload in source.execute(query,tuple(topics)):
                if cutoff is not None and record_ns>cutoff:
                    break
                topic=topics[tid]
                msg=deserialize_message(payload,CLASSES[topic])
                ns=msg.header.stamp.sec*10**9+msg.header.stamp.nanosec
                if topic==LIDAR:
                    if msg.is_bigendian or msg.row_step!=msg.width*msg.point_step:
                        raise ValueError('Unsupported point layout')
                    f={v.name:v for v in msg.fields}
                    assert f['timestamp'].datatype==8 and f['ring'].datatype==4 and f['intensity'].datatype==7
                    dtype=np.dtype({'names':['t','ring'],'formats':['<f8','<u2'],
                        'offsets':[f['timestamp'].offset,f['ring'].offset],'itemsize':msg.point_step})
                    points=np.frombuffer(msg.data,dtype=dtype)
                    ts=points['t']
                    if not np.isfinite(ts).all() or not .05<float(np.ptp(ts))<.15:
                        raise ValueError('Unexpected point times')
                    if abs(ns/1e9-float(ts.min()))>2e-5 or int(points['ring'].max())>63:
                        raise ValueError('Cloud header is not scan start or ring range is invalid')
                    source_cloud_digest.update(msg.data)
                    reverse=int((np.diff(ts)<0).sum())
                    if reverse:
                        order=np.argsort(ts,kind='stable')
                        raw=np.frombuffer(msg.data,dtype=f'V{msg.point_step}')
                        msg.data=raw[order].tobytes()
                        reordered+=1
                        payload=serialize_message(msg)
                    sorted_cloud_digest.update(msg.data)
                    timings.append((ns,float(ts.min()),float(ts.max()),reverse))
                elif topic==IMAGE:
                    pixels=cv2.imdecode(np.frombuffer(msg.data,dtype=np.uint8),cv2.IMREAD_COLOR)
                    if pixels is None or pixels.shape!=(1080,1440,3):
                        raise ValueError('Invalid JPEG dimensions')
                    if a.image_scale!=1:
                        pixels=cv2.resize(pixels,(720,540),interpolation=cv2.INTER_LINEAR)
                    out=Image(header=msg.header,height=pixels.shape[0],width=pixels.shape[1],encoding='bgr8',
                        is_bigendian=0,step=pixels.shape[1]*3,data=pixels.tobytes())
                    payload=serialize_message(out)
                    checked=deserialize_message(payload,Image)
                    if checked.header!=msg.header or bytes(checked.data)!=pixels.tobytes():
                        raise ValueError('Image CDR/pixel roundtrip mismatch')
                    if not stamps[topic]: cv2.imwrite(str(a.output/'first_decoded.jpg'),pixels)
                stamps[topic].append(ns)
                first=record_ns if first is None else min(first,record_ns)
                last=record_ns if last is None else max(last,record_ns)
                records.append((ids[topic],record_ns,bytes(payload)))
                if len(records)>=32:
                    db.executemany('INSERT INTO messages(topic_id,timestamp,data) VALUES(?,?,?)',records)
                    db.commit();records.clear()
                if time.monotonic()-report>=30:
                    print(a.sequence,{k:len(v) for k,v in stamps.items()},f'{time.monotonic()-started:.0f}s',flush=True)
                    report=time.monotonic()
        if cutoff is not None and last is not None and last>=cutoff-10000000:break
    if records:db.executemany('INSERT INTO messages(topic_id,timestamp,data) VALUES(?,?,?)',records)
    db.execute('CREATE INDEX timestamp_idx ON messages(timestamp)');db.commit()
    actual={t:len(s) for t,s in stamps.items()}
    if cutoff is None and actual!=dict(expected):raise ValueError(f'Incomplete conversion: {actual} != {dict(expected)}')
    assert db.execute('PRAGMA quick_check').fetchall()==[('ok',)]
    db.close()
    metadata={'rosbag2_bagfile_information':{'version':5,'storage_identifier':'sqlite3','relative_file_paths':[dbfile.name],
        'duration':{'nanoseconds':last-first},'starting_time':{'nanoseconds_since_epoch':first},'message_count':sum(actual.values()),
        'topics_with_message_count':[{'topic_metadata':{'name':TOPICS[t],'type':kinds[t],'serialization_format':'cdr','offered_qos_profiles':''},'message_count':n} for t,n in actual.items()],
        'compression_format':'','compression_mode':'','files':[{'path':dbfile.name,'starting_time':{'nanoseconds_since_epoch':first},'duration':{'nanoseconds':last-first},'message_count':sum(actual.values())}]}}
    (a.output/'metadata.yaml').write_text(yaml.safe_dump(metadata,sort_keys=False))
    summaries={}
    for t,values in stamps.items():
        d=np.diff(values);assert (d>0).all(),t
        summaries[TOPICS[t]]={'count':len(values),'first_ns':values[0],'last_ns':values[-1],
            'dt_min_s':float(d.min()/1e9),'dt_median_s':float(np.median(d)/1e9),'dt_max_s':float(d.max()/1e9)}
    np.savez_compressed(a.output/'input_headers.npz',**{TOPICS[t]:np.array(v,dtype=np.int64) for t,v in stamps.items()})
    np.savetxt(a.output/'scan_timing.csv',np.array(timings),delimiter=',',header='header_ns,min_point_s,max_point_s,backward_point_steps',fmt=['%.0f','%.9f','%.9f','%.0f'])
    prep={'dataset':'OxfordSpires','sequence':a.sequence,'diagnostic':bool(a.duration),'source_databases':[str(p.resolve()) for _,p,_ in inputs],
        'headers':summaries,'selected_topics':TOPICS,'all_selected_record_counts_match_source':cutoff is None,
        'timestamps_preserved':True,'point_records_preserved_by_stable_permutation':True,
        'reordered_clouds':reordered,'source_point_bytes_sha256':source_cloud_digest.hexdigest(),
        'output_point_bytes_sha256':sorted_cloud_digest.hexdigest(),'native_camera_model':'EquidistantCamera',
        'images':'JPEG decoded to BGR8; optional declared linear half-resolution, no rectification','image_scale':a.image_scale,
        'image_cdr_pixel_roundtrip_verified':True,
        'duration_record_s':(last-first)/1e9,'conversion_seconds':time.monotonic()-started}
    (a.output/'preparation.json').write_text(json.dumps(prep,indent=2)+'\n')
    print(json.dumps(prep,indent=2),flush=True)


if __name__=='__main__':main()
