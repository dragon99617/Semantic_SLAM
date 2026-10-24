#!/usr/bin/env python3
"""Score a completed phase-one run using declared reference frames and protocol.

Use accuracy_venv. Never fit scale/time offsets, choose a lever arm by score,
or independently align transition segments. FPV2 position references remain
ambiguous and therefore retain both direct-IMU and calibrated-prism results.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from scipy.spatial.transform import Rotation
from scipy.spatial import cKDTree
import yaml
from evo.core import sync,metrics,lie_algebra
from evo.tools import file_interface

from evaluate_ntu_accuracy import evaluate,interpolation_check
from livo_map_utils import reduce_recorded


def transform_estimate(poses,rotation,translation):
    out=poses.copy();r=Rotation.from_quat(out[:,4:8])
    out[:,1:4]+=r.apply(np.broadcast_to(translation,(len(out),3)))
    out[:,4:8]=(r*Rotation.from_matrix(rotation)).as_quat()
    return out


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('run',type=Path);a=ap.parse_args()
    root=Path(__file__).resolve().parent.parent;run=a.run.resolve()
    summary=json.loads((run/'summary.json').read_text());desc=json.loads((run/'descriptor.json').read_text())
    if summary['status']!='playback_completed' or summary['diagnostic']:raise ValueError('Full successful run required')
    out=run/'accuracy';out.mkdir(exist_ok=True)
    est=np.loadtxt(run/'estimate_imu.tum',ndmin=2)
    if not np.isfinite(est).all() or not (np.diff(est[:,0])>0).all():raise ValueError('Invalid estimate')
    if np.max(abs(np.linalg.norm(est[:,4:8],axis=1)-1))>1e-4:raise ValueError('Invalid estimate quaternion')
    result={'status':'evaluated','dataset':desc['dataset'],'sequence':desc['sequence'],'poses':len(est),
        'trajectory_duration_s':float(est[-1,0]-est[0,0]),'path_length_m':float(np.linalg.norm(np.diff(est[:,1:4],axis=0),axis=1).sum()),
        'trajectory_span_fraction':float((est[-1,0]-est[0,0])/summary['expected_lidar_span_s']),
        'protocol':{'alignment':'One global SE3 per association protocol','scale':1.,'extra_time_offset_s':0.,'time_offset_search':False,
            'nearest_tolerance_s':.01,'interpolation_max_each_neighbor_distance_s':.1,'interpolation_max_total_bracket_span_s':.2},'comparisons':{},'RPE_evaluated':False,
        'input_audit':summary['input_callback_audit'],'reference_file':desc.get('reference')}
    observed=np.loadtxt(run/'observed_odometry.tum',ndmin=2)
    idx=np.clip(np.searchsorted(est[:,0],observed[:,0]),0,len(est)-1);left=np.maximum(idx-1,0)
    choose=np.where(abs(est[left,0]-observed[:,0])<abs(est[idx,0]-observed[:,0]),left,idx)
    dt=abs(est[choose,0]-observed[:,0]);matched=dt<=2e-6
    result['ros_pose_time_check']={'observed':len(observed),'matched_to_native':int(matched.sum()),'max_matched_dt_s':float(dt[matched].max()) if matched.any() else None,
        'max_matched_position_difference_m':float(np.linalg.norm(est[choose[matched],1:4]-observed[matched,1:4],axis=1).max()) if matched.any() else None}
    reference=root/desc['reference'] if desc.get('reference') else None
    if reference:
        gt=np.loadtxt(reference,ndmin=2)
        assert np.isfinite(gt).all() and (np.diff(gt[:,0])>0).all()
        result.update(reference_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),reference_rows=len(gt),
            reference_gap_gt_0_1_s=int((np.diff(gt[:,0])>.1).sum()))
        if desc['dataset']=='OxfordSpires':
            sensor=yaml.safe_load((root/desc['sensor_config']).read_text())['sensor'];cam=sensor['cameras'][0]
            rci=Rotation.from_quat(cam['T_cam_imu_t_xyz_q_xyzw'][3:]).as_matrix();pci=np.array(cam['T_cam_imu_t_xyz_q_xyzw'][:3]);rcl=Rotation.from_quat(cam['T_cam_lidar_t_xyz_q_xyzw'][3:]).as_matrix();pcl=np.array(cam['T_cam_lidar_t_xyz_q_xyzw'][:3])
            ril=rci.T@rcl;pil=rci.T@(pcl-pci);base=np.array(sensor['T_base_lidar_t_xyz_q_xyzw']);rbl=Rotation.from_quat(base[3:]).as_matrix();pbl=base[:3]
            rib=ril@rbl.T;pib=pil-rib@pbl
            variants={'base':transform_estimate(est,rib,pib)}
            result['reference_point']='base frame; apply full IMU<-base rigid transform to estimate'
            result['T_imu_base']={'R':rib.tolist(),'t':pib.tolist()}
            result['reference_source']='Author LiDAR-to-Leica TLS registration, not independent motion capture'
        elif desc['dataset']=='NTU_VIRAL':
            variants={'prism':transform_estimate(est,np.eye(3),np.array([-.293656,-.012288,-.273095]))}
            result['reference_point']='Leica prism, calibrated IMU lever arm'
        else:
            variants={'imu_direct':est,'prism_compensated':transform_estimate(est,np.eye(3),np.array([-.001,0.,.1441]))}
            result['reference_point']='Published reference point not explicitly stated; retain both interpretations, do not select lower score'
        for name,values in variants.items():
            estimated=out/(name+'.tum');np.savetxt(estimated,values,fmt='%.9f')
            nearest=evaluate(reference,estimated,out,name)
            interpolated=interpolation_check(reference,estimated,out,name)
            result['comparisons'][name]={'nearest':nearest,'interpolated':interpolated}
            if desc['dataset']=='OxfordSpires':
                ref=file_interface.read_tum_trajectory_file(reference);trajectory=file_interface.read_tum_trajectory_file(estimated)
                ref,trajectory=sync.associate_trajectories(ref,trajectory,max_diff=.01)
                trajectory.align(ref,correct_scale=False)
                # This pinned evo version has no seconds-based pair filter.
                # Select consecutive reference-time pairs, then use evo's
                # unchanged standard TUM SE3 relative-error formula.
                pairs=[];i=0;times=ref.timestamps
                while i+1<len(times):
                    target=times[i]+1.;j=int(np.searchsorted(times,target))
                    candidates=[k for k in (j-1,j) if i<k<len(times)]
                    if not candidates:break
                    j=min(candidates,key=lambda k:abs(times[k]-target))
                    if abs(times[j]-target)<=.05:pairs.append((i,j));i=j
                    else:i+=1
                if not pairs:raise ValueError('No valid 1s reference-time RPE pairs')
                errors=np.array([metrics.RPE.rpe_base(ref.poses_se3[i],ref.poses_se3[j],trajectory.poses_se3[i],trajectory.poses_se3[j]) for i,j in pairs])
                translation=np.linalg.norm(errors[:,:3,3],axis=1)
                angle=np.array([abs(lie_algebra.so3_log_angle(e[:3,:3],True)) for e in errors])
                rpe={}
                for label,values in [('translation_m',translation),('rotation_deg',angle)]:
                    metric=metrics.RPE();metric.error=values
                    rpe[label]={'stats':{k:float(v) for k,v in metric.get_all_statistics().items()},'pairs':len(values)}
                    np.savetxt(out/(label+'_rpe_errors.csv'),values,delimiter=',')
                pair_times=np.array([[ref.timestamps[i],ref.timestamps[j],trajectory.timestamps[i],trajectory.timestamps[j]] for i,j in pairs])
                np.savetxt(out/'rpe_pairs.csv',np.column_stack([pair_times,pair_times[:,1]-pair_times[:,0],pair_times[:,3]-pair_times[:,2],translation,angle]),delimiter=',',header='reference_start_s,reference_end_s,estimate_start_s,estimate_end_s,reference_dt_s,estimate_dt_s,translation_error_m,rotation_error_deg')
                result.update(RPE_evaluated=True,RPE={'delta_s':1.,'relative_delta_tolerance':.05,'pairs_from_reference':True,'all_pairs':False,'pair_filter':'Nearest reference timestamp at +1s within0.05s, next pair starts at prior endpoint; no interpolation','error_formula':'evo.metrics.RPE.rpe_base, standard TUM SE3 relative error','reference_dt_min_s':float(np.min(pair_times[:,1]-pair_times[:,0])),'reference_dt_max_s':float(np.max(pair_times[:,1]-pair_times[:,0])),**rpe})
        if not result['RPE_evaluated']:result['RPE_not_evaluated_reason']='GT orientations are identity placeholders; do not construct full-pose RPE from them'
    else:
        result.update(status='engineering_run_no_verified_gt',reference_limitation='No verified matching Retail Street pose reference; no ATE/RPE or claimed accuracy')
    fig,ax=plt.subplots(figsize=(7,6));ax.plot(est[:,1],est[:,2],lw=.7);ax.scatter(est[[0,-1],1],est[[0,-1],2],c=['green','red']);ax.axis('equal');ax.grid(alpha=.3);ax.set(title=desc['dataset']+' '+desc['sequence'],xlabel='estimated world x [m]',ylabel='estimated world y [m]');fig.tight_layout();fig.savefig(run/'trajectory.png',dpi=160);plt.close(fig)
    raw=run/'registered_outputs.bin'
    if raw.exists() and raw.stat().st_size:
        result['registered_map']=reduce_recorded(raw,run/'registered_map_10cm.npz',voxel=.1)
    mapfile=run/'registered_map_10cm.npz'
    if not mapfile.exists():mapfile=run/'sampled_map.npz'
    if mapfile.exists():
        with np.load(mapfile) as data:xyz=data['xyz'];rgb=data['rgb']
        step=max(1,len(xyz)//150000);v=xyz[::step];colors=rgb[::step];color=np.column_stack([(colors>>16)&255,(colors>>8)&255,colors&255])/255
        fig,ax=plt.subplots(figsize=(8,7));ax.scatter(v[:,0],v[:,1],c=color,s=.3);ax.axis('equal');ax.set(title='Registered output map',xlabel='x [m]',ylabel='y [m]');fig.tight_layout();fig.savefig(run/'map_preview.png',dpi=160);plt.close(fig)
        tls=root/'datasets/OxfordSpires/christ_church_tls_5cm.npz'
        if desc['dataset']=='OxfordSpires' and tls.exists():
            m=result['comparisons']['base']['nearest'];aligned=xyz@np.array(m['alignment_R']).T+np.array(m['alignment_t'])
            with np.load(tls) as data:reference_xyz=data['xyz']
            tree=cKDTree(reference_xyz);distance=tree.query(aligned,k=1,workers=2)[0]
            result['registered_map_tls_check']={'scope':'Observed registered outputs to 5cm-voxel TLS, using the trajectory global alignment; not full-map completeness or raw-cloud registration accuracy',
                'output_voxel_m':.1,'tls_voxel_m':.05,'points':len(distance),'mean_m':float(distance.mean()),'rmse_m':float(np.sqrt(np.mean(distance**2))),
                'median_m':float(np.median(distance)),'p95_m':float(np.percentile(distance,95)),'max_m':float(distance.max()),
                'fraction_within_0_2_m':float((distance<=.2).mean()),'fraction_within_0_5_m':float((distance<=.5).mean())}
    (out/'metrics.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'dataset':desc['dataset'],'sequence':desc['sequence'],'poses':len(est),'comparisons':{k:{'nearest_rmse_m':v['nearest']['stats_m']['rmse'],'interpolated_rmse_m':v['interpolated']['rmse_m']} for k,v in result['comparisons'].items()},'RPE_evaluated':result['RPE_evaluated']},indent=2),flush=True)


if __name__=='__main__':main()
