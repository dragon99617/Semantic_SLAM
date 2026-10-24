#!/usr/bin/env python3
"""Check prepared input counts/header caches and reference provenance without replay."""
import hashlib,json
from pathlib import Path
import numpy as np,yaml

ROOT=Path(__file__).resolve().parent.parent
BASE=ROOT/'experiments/phase1_20261022'


def main():
    rows=[]
    for name in ['ntu_eee01','retail','fpv2_00','fpv2_01','oxford02','oxford05']:
        desc=json.loads((BASE/(name+'.json')).read_text());bag=ROOT/desc['bag'];meta=yaml.safe_load((bag/'metadata.yaml').read_text())['rosbag2_bagfile_information']
        params=yaml.safe_load((ROOT/desc['mapper_config']).read_text())['/**']['ros__parameters']['common'];topics=[params[k] for k in ['lid_topic','imu_topic','img_topic']]
        counts={x['topic_metadata']['name']:x['message_count'] for x in meta['topics_with_message_count']}
        with np.load(bag/'input_headers.npz') as cache:headers={t:cache[t].copy() for t in topics}
        checks={t:{'cache_count':len(v),'metadata_count':counts[t],'nondecreasing':bool((np.diff(v)>=0).all()),'duplicate_count':int((np.diff(v)==0).sum()),'first_ns':int(v[0]),'last_ns':int(v[-1])} for t,v in headers.items()}
        passed=all(c['cache_count']==c['metadata_count'] and c['nondecreasing'] for c in checks.values()) and counts[topics[0]]==desc['expected_lidar_count']
        prep=bag/'preparation.json';preparation=json.loads(prep.read_text()) if prep.exists() else None
        if preparation and name.startswith(('fpv2','oxford')):passed=passed and preparation['all_selected_record_counts_match_source']
        rows.append({'id':name,'passed':passed,'checks':checks,'metadata_sha256':hashlib.sha256((bag/'metadata.yaml').read_bytes()).hexdigest(),'header_cache_sha256':hashlib.sha256((bag/'input_headers.npz').read_bytes()).hexdigest(),'preparation':preparation})
    out={'passed':all(x['passed'] for x in rows),'scope':'Metadata/cache agreement and existing full conversion checks; no new full database scan or repeated raw-file hash claim','sequences':rows}
    (BASE/'prepared_inputs_check.json').write_text(json.dumps(out,indent=2)+'\n');print({x['id']:x['passed'] for x in rows})
    if not out['passed']:raise SystemExit(1)


if __name__=='__main__':main()
