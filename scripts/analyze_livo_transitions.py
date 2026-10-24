#!/usr/bin/env python3
"""Apply frozen input-only transition windows to completed baseline artifacts.
No per-window alignment or threshold tuning. Run with accuracy_venv.
"""
import argparse,json,re
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import yaml


def stats(v):
    v=np.asarray(v);v=v[np.isfinite(v)]
    return {'samples':len(v),'mean':float(v.mean()),'median':float(np.median(v)),'p95':float(np.percentile(v,95)),'max':float(v.max()),'rmse':float(np.sqrt(np.mean(v*v)))} if len(v) else {'samples':0}


def visual_trace(run,est):
    idx=-1;rows=[];current=None
    def flush():
        if current is not None:rows.append(current.copy())
    with (run/'mapping.log').open(errors='replace') as f:
        for line in f:
            if '|                         LIO Mapping Time' in line:idx+=1
            if '[ VIO ] Raw feature num:' in line:
                flush();current={'lio_index':idx,'raw_points':int(line.rsplit(':',1)[1]),'retrieved':None,'updated_map_points':None}
            match=re.search(r'\[ VIO \] Retrieve (\d+) points',line)
            if match and current is not None:current['retrieved']=int(match[1])
            match=re.search(r'\[ VIO \] Update (\d+) points in visual submap',line)
            if match and current is not None:current['updated_map_points']=int(match[1])
    flush()
    valid=idx+1==len(est) and all(0<=r['lio_index']<len(est) for r in rows)
    summary={'lio_blocks':idx+1,'native_poses':len(est),'visual_frames':len(rows),'log_order_mapping_validated':valid,'interpretation':'Retrieved sparse visual-map points and appearance-map point updates; positive retrieval is not an independently verified EKF acceptance flag. Native trajectory is saved after LIO update and before that frame VIO feedback.'}
    if not valid:return None,summary
    trace=np.array([[est[r['lio_index'],0],r['raw_points'],r['retrieved'] if r['retrieved'] is not None else np.nan,r['updated_map_points'] if r['updated_map_points'] is not None else np.nan] for r in rows])
    np.savetxt(run/'visual_diagnostics.csv',trace,delimiter=',',header='sensor_s,raw_cloud_points,retrieved_visual_points,updated_appearance_map_points')
    summary.update(retrieved_points=stats(trace[:,2]),updated_appearance_map_points=stats(trace[:,3]),missing_retrieval_entries=int(np.isnan(trace[:,2]).sum()),zero_retrieval_frames=int((trace[:,2]==0).sum()))
    return trace,summary


def recovery(data,center):
    x=data[(data[:,0]>=center)&(data[:,0]<=center+30)]
    if not len(x):return {'status':'reference_unavailable_in_post_transition_window','time_to_return_s':None,'observed_samples':0}
    high=np.flatnonzero(x[:,7]>.5)
    if not len(high):return {'status':'no_observed_excursion_above_0_5_m','time_to_return_s':None,'observed_samples':len(x)}
    begin=high[0]
    for i in range(begin+1,len(x)):
        j=np.searchsorted(x[:,0],x[i,0]+5)
        if j>=len(x):break
        v=x[i:j+1]
        if (v[:,7]<=.5).all() and np.diff(v[:,0]).max()<=.5:
            return {'status':'return_below_fixed_threshold','time_to_return_s':float(x[i,0]-x[begin,0]),'first_excursion_sensor_s':float(x[begin,0]),'returned_sensor_s':float(x[i,0])}
    return {'status':'right_censored_or_insufficient_reference','time_to_return_s':None,'first_excursion_sensor_s':float(x[begin,0]),'observed_until_sensor_s':float(x[-1,0])}


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('run',type=Path);a=ap.parse_args();run=a.run.resolve();root=Path(__file__).resolve().parent.parent;base=root/'experiments/phase1_20261022'
    desc=json.loads((run/'descriptor.json').read_text());m=json.loads((run/'accuracy/metrics.json').read_text());est=np.loadtxt(run/'estimate_imu.tum',ndmin=2)
    trace,visual=visual_trace(run,est);annotation=base/'input_annotations'/desc['id']/'events.json'
    result={'run':str(run.relative_to(root)),'visual':visual,'trajectory_intervals_s':stats(np.diff(est[:,0])),'pose_gaps_over_0_5_s':int((np.diff(est[:,0])>.5).sum()),'events':[],'reset_assessment':'No reset inference from position jumps. Initialization log entries are retained; this diagnostic does not establish relocalization.'}
    if not annotation.exists():
        (run/'diagnostics.json').write_text(json.dumps(result,indent=2)+'\n');print('Visual/continuity diagnostics',run.name);return
    ann=json.loads(annotation.read_text());protocol=json.loads((base/'input_annotations/analysis_protocol.json').read_text());result.update(annotation=str(annotation.relative_to(root)),protocol=protocol)
    conditions=np.loadtxt(run/'image_conditions.csv',delimiter=',',ndmin=2)
    image_offset=float(yaml.safe_load((run/'mapper.yaml').read_text())['/**']['ros__parameters']['time_offset'].get('img_time_offset',0.))
    conditions[:,0]+=image_offset
    result['camera_header_to_mapper_time_offset_s']=image_offset
    for event in ann['events']:
        center=event['center_sensor_s']+image_offset;ev={**event,'mapper_time_center_sensor_s':center,'comparisons':{},'windows':{}}
        for key,limits in protocol['windows_relative_to_boundary_midpoint_s'].items():
            lo,hi=center+limits[0],center+limits[1];poses=est[(est[:,0]>=lo)&(est[:,0]<=hi)];img=conditions[(conditions[:,0]>=lo)&(conditions[:,0]<=hi)]
            w={'requested_sensor_interval_s':[lo,hi],'observed_pose_samples':len(poses),'truncated_by_estimate':bool(lo<est[0,0] or hi>est[-1,0]),'other_boundary_overlaps':[other['id'] for other in ann['events'] if other['id']!=event['id'] and other['interval_start_sensor_s']+image_offset<=hi and other['interval_end_sensor_s']+image_offset>=lo],
              'pose_intervals_s':stats(np.diff(poses[:,0])) if len(poses)>1 else {'samples':0},'image_samples':len(img)}
            if len(img):w['appearance']={name:stats(img[:,i]) for name,i in [('mean_gray',1),('black_fraction',4),('saturated_fraction',5),('laplacian_variance',6)]}
            if trace is not None:
                v=trace[(trace[:,0]>=lo)&(trace[:,0]<=hi)];known=v[np.isfinite(v[:,2])];w['visual']={'frames':len(v),'retrieved_points':stats(v[:,2]) if len(v) else {'samples':0},'fraction_zero_retrieval_among_known':float((known[:,2]==0).mean()) if len(known) else None,'known_retrieval_frames':len(known),'appearance_map_updates':stats(v[:,3]) if len(v) else {'samples':0}}
            ev['windows'][key]=w
        for variant in m['comparisons']:
            scores={}
            for assoc,suffix in [('nearest','matched'),('interpolated','interpolated')]:
                data=np.loadtxt(run/'accuracy'/f'{variant}_{suffix}.csv',delimiter=',',skiprows=1,ndmin=2)
                values={}
                for key,limits in protocol['windows_relative_to_boundary_midpoint_s'].items():
                    lo,hi=center+limits[0],center+limits[1];v=data[(data[:,0]>=lo)&(data[:,0]<=hi)];values[key]={'ape_m':stats(v[:,7]),'reference_window_fraction_of_estimate_poses':len(v)/max(1,ev['windows'][key]['observed_pose_samples']),'matched_time_span_s':float(v[-1,0]-v[0,0]) if len(v)>1 else 0.}
                scores[assoc]={'windows':values,'exploratory_recovery':recovery(data,center)}
            ev['comparisons'][variant]=scores
        result['events'].append(ev)
    fig,axes=plt.subplots(3,1,figsize=(12,8),sharex=True);t0=est[0,0]
    for variant in m['comparisons']:
        d=np.loadtxt(run/'accuracy'/f'{variant}_interpolated.csv',delimiter=',',skiprows=1,ndmin=2);axes[0].plot(d[:,0]-t0,d[:,7],label=variant,lw=.6)
    axes[0].set_ylabel('Translation APE [m]');axes[0].legend();axes[1].plot(conditions[:,0]-t0,conditions[:,1],lw=.7);axes[1].set_ylabel('Input mean gray')
    if trace is not None:axes[2].plot(trace[:,0]-t0,trace[:,2],lw=.5)
    axes[2].set_ylabel('Retrieved points');axes[2].set_xlabel('Sensor seconds from first estimated pose')
    for ax in axes:
        for event in ann['events']:ax.axvspan(event['interval_start_sensor_s']+image_offset-t0,event['interval_end_sensor_s']+image_offset-t0,alpha=.2,color='orange')
        ax.grid(alpha=.2)
    fig.tight_layout();fig.savefig(run/'transition_diagnostics.png',dpi=150);plt.close(fig)
    (run/'diagnostics.json').write_text(json.dumps(result,indent=2)+'\n');print('Transition/visual diagnostics',run.name,len(result['events']))


if __name__=='__main__':main()
