#!/usr/bin/env python3
"""Compare requested YAML values with actual ROS2 parameters and pinned constants."""
import argparse,json
from pathlib import Path
import yaml


def flatten(d,prefix=''):
    out={}
    for k,v in d.items():
        if isinstance(v,dict):out.update(flatten(v,prefix+k+'.'))
        else:out[prefix+k]=v
    return out


def audit(run):
    run=Path(run);checks={}
    for expected_file,actual_file,node in [('mapper.yaml','mapper_parameters.yaml','/laserMapping'),('camera.yaml','camera_parameters.yaml','/parameter_blackboard')]:
        expected=flatten(yaml.safe_load((run/expected_file).read_text())['/**']['ros__parameters']);actual=flatten(yaml.safe_load((run/actual_file).read_text())[node]['ros__parameters']);special={}
        for key in list(expected):
            if key in actual:continue
            if key in ('imu.b_acc_cov','imu.b_gyr_cov'):
                special[key]={'requested':expected[key],'effective':.0001,'basis':'Pinned LIVMapper.cpp initializeComponents sets both bias covariances to V3D(0.0001); YAML keys are not declared/read','matches':expected[key]==.0001}
            elif key=='lio.max_iterations':
                special[key]={'requested':expected[key],'effective':actual.get('lio.min_iterations'),'basis':'Pinned voxel_map.cpp uses ROS2 lio.min_iterations as config_setting_.max_iterations_; lio.max_iterations YAML key is not declared/read','matches':expected[key]==actual.get('lio.min_iterations')}
        missing=sorted(set(expected)-set(actual)-set(special));diff={k:{'requested':v,'actual':actual[k]} for k,v in expected.items() if k in actual and v!=actual[k]}
        checks[expected_file]={'requested_values':len(expected),'declared_values_matching':sum(k in actual and v==actual[k] for k,v in expected.items()),'upstream_unexposed_yaml_values':special,'unresolved_missing':missing,'different_declared_values':diff,'passed':not missing and not diff and all(v['matches'] for v in special.values())}
    out={'passed':all(v['passed'] for v in checks.values()),'source_contract':'Frozen FAST-LIVO2 ROS2 837b7bbc1431cb04cf936528e52c83c835efba8e plus recorded non-estimator patch','checks':checks}
    (run/'parameter_checks.json').write_text(json.dumps(out,indent=2)+'\n');return out


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('runs',nargs='+',type=Path);a=ap.parse_args()
    for run in a.runs:
        result=audit(run);print(run.name,result['passed'])
        if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
