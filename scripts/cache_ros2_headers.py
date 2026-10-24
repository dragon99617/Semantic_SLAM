#!/usr/bin/env python3
"""Index standard CDR Header stamps without reading full image/cloud BLOBs.
Use Python>=3.11 with NumPy and PyYAML (local semantic_venv qualifies).
This reads sensor metadata only and never changes a bag.
"""
import argparse,collections,json,sqlite3,struct,time
from pathlib import Path
import numpy as np,yaml


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('bag',type=Path);a=ap.parse_args();started=time.monotonic()
    meta=yaml.safe_load((a.bag/'metadata.yaml').read_text())['rosbag2_bagfile_information'];stamps=collections.defaultdict(list)
    expected={t['topic_metadata']['name']:t['message_count'] for t in meta['topics_with_message_count']}
    for name in meta['relative_file_paths']:
        with sqlite3.connect(f'file:{(a.bag/name).resolve()}?mode=ro',uri=True) as db:
            topics={i:(n,k) for i,n,k in db.execute('SELECT id,name,type FROM topics')}
            assert all(k in ('sensor_msgs/msg/PointCloud2','sensor_msgs/msg/Imu','sensor_msgs/msg/Image') for n,k in topics.values())
            for rowid,tid in db.execute('SELECT id,topic_id FROM messages ORDER BY timestamp,id'):
                with db.blobopen('messages','data',rowid,readonly=True) as blob:raw=blob.read(12)
                assert raw[:4]==b'\0\1\0\0',raw[:4]
                sec,nsec=struct.unpack('<iI',raw[4:12]);assert 0<=nsec<10**9
                stamps[topics[tid][0]].append(sec*10**9+nsec)
    assert {k:len(v) for k,v in stamps.items()}==expected
    arrays={k:np.array(v,dtype=np.int64) for k,v in stamps.items()}
    assert all((np.diff(v)>0).all() for v in arrays.values())
    cache=a.bag/'input_headers.npz';tmp=cache.with_suffix('.tmp')
    with tmp.open('wb') as f:np.savez_compressed(f,**arrays)
    tmp.replace(cache)
    record={'method':'Read only 12-byte CDR prefix with SQLite incremental BLOB access','counts':expected,'strictly_increasing_all_topics':True,'seconds':time.monotonic()-started}
    (a.bag/'header_cache_check.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2),flush=True)


if __name__=='__main__':main()
