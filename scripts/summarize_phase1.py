#!/usr/bin/env python3
"""Build phase-one results and explicit completion checks from retained artifacts."""
import argparse,datetime,hashlib,json,subprocess,time
from pathlib import Path
import numpy as np
from audit_livo_parameters import audit

ROOT=Path(__file__).resolve().parent.parent
BASE=ROOT/'experiments/phase1_20261022'


def distribution(values):
    a=np.asarray(values,dtype=float)
    return {'n':len(a),'mean':float(a.mean()),'sample_std':float(a.std(ddof=1)) if len(a)>1 else None,'min':float(a.min()),'max':float(a.max())}


def build():
    jobs=json.loads((BASE/'jobs.json').read_text())['jobs'];rows=[]
    for job in jobs:
        run=BASE/'runs'/job['id'];desc=json.loads((ROOT/job['descriptor']).read_text());row={**job,'dataset':desc['dataset'],'sequence':desc['sequence'],'run':str(run.relative_to(ROOT)),'status':'pending'}
        if not (run/'summary.json').exists():rows.append(row);continue
        s=json.loads((run/'summary.json').read_text());row.update(status=s['status'],dataset=s['dataset'],sequence=s['sequence'],peak_mapper_rss_GiB=s['peak_mapper_rss_kib']/1024**2,peak_mapper_swap_kib=s['peak_mapper_swap_kib'],wall_seconds=s['wall_seconds'],input_callbacks_exact=all(x['received_is_exact_multiset'] for x in s['input_callback_audit'].values()),input_missing={k:v['missing'] for k,v in s['input_callback_audit'].items()},mapper_clean_exit=s['mapper_clean_exit'])
        metrics=run/'accuracy/metrics.json'
        if metrics.exists():
            parameter_check=audit(run)
            row['parameters_match_effective_values']=parameter_check['passed']
            row['playback_pacing']=s.get('pacing',{'pauses':0,'legacy_no_lag_trigger_evidence':'experiments/phase1_20261022/pre_pacing_compatibility.json'})
            m=json.loads(metrics.read_text());row.update(evaluated=True,poses=m['poses'],coverage=m['trajectory_span_fraction'],position_metrics={k:{'nearest_rmse_m':v['nearest']['stats_m']['rmse'],'nearest_matched_fraction':v['nearest']['associated_estimate_fraction'],'interpolated_rmse_m':v['interpolated']['rmse_m'],'interpolated_matched_fraction':v['interpolated']['matched_poses']/m['poses']} for k,v in m['comparisons'].items()},RPE=m.get('RPE'),ros_pose_time_check=m['ros_pose_time_check'],registered_map=m.get('registered_map'),registered_map_tls_check=m.get('registered_map_tls_check'))
            diag=run/'diagnostics.json'
            if not diag.exists():
                with (BASE/(job['id']+'_diagnostics.log')).open('w') as log:
                    subprocess.run([str(ROOT/'third_party/accuracy_venv/bin/python'),str(ROOT/'scripts/analyze_livo_transitions.py'),str(run)],stdout=log,stderr=subprocess.STDOUT,check=True)
            d=json.loads(diag.read_text());row.update(diagnostics_complete=True,visual_trace_valid=d['visual']['log_order_mapping_validated'],pose_gaps_over_0_5_s=d['pose_gaps_over_0_5_s'],transition_events=len(d['events']),visual_diagnostics=d['visual'])
        else:row['evaluated']=False
        rows.append(row)
    grouped={}
    for seq in ['ntu_eee01','fpv2_00','oxford02']:
        core=[r for r in rows if r['id'].startswith(seq+'_') and r.get('evaluated')]
        data={'runs':[r['id'] for r in core],'completed_repeats':len(core),'peak_mapper_rss_GiB':distribution([r['peak_mapper_rss_GiB'] for r in core]) if core else None,'position_metrics':{}}
        if core:
            for variant in core[0]['position_metrics']:
                data['position_metrics'][variant]={key:distribution([r['position_metrics'][variant][key] for r in core]) for key in ['nearest_rmse_m','interpolated_rmse_m']}
        grouped[seq]=data
    frozen=json.loads((BASE/'baseline_freeze.json').read_text());protocol=json.loads((BASE/'protocol_hashes.json').read_text())
    hashes={str(p):hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==digest for p,digest in {**frozen['artifacts'],**protocol['files']}.items()}
    semantic=json.loads((BASE/'semantic_baseline_freeze_check.json').read_text());old=json.loads((ROOT/'experiments/multibench_20261020/final_result_checks.json').read_text())
    checks={'prepared_inputs_passed':json.loads((BASE/'prepared_inputs_check.json').read_text())['passed'],'seconds_rpe_independent_crosscheck_passed':(BASE/'runs/oxford02_r1/accuracy/rpe_independent_check.json').exists() and json.loads((BASE/'runs/oxford02_r1/accuracy/rpe_independent_check.json').read_text())['passed'],'all_actual_parameters_match':all(r.get('parameters_match_effective_values',False) for r in rows),'sensor_time_pose_outputs_match':all(r.get('ros_pose_time_check',{}).get('matched_to_native',0)>0 and r['ros_pose_time_check']['max_matched_dt_s']<=2e-6 and r['ros_pose_time_check']['max_matched_position_difference_m']<=5e-6 for r in rows),'all_12_full_runs_evaluated':len(rows)==12 and all(r['status']=='playback_completed' and r.get('evaluated') for r in rows),'native_artifacts_and_protocol_hashes_match':all(hashes.values()),'all_input_callback_multisets_exact':all(r.get('input_callbacks_exact',False) for r in rows),'all_diagnostics_complete_and_log_order_valid':all(r.get('diagnostics_complete',False) and r.get('visual_trace_valid',False) for r in rows),'three_repeats_each_core':all(g['completed_repeats']==3 for g in grouped.values()),'primary_maps_exported':all(r.get('registered_map',{}).get('voxel_points',0)>0 for r in rows if r['capture_full_registered_outputs']),'semantic_18_archived_runs_independently_verified':old['passed'] and len(old['runs'])==18,'semantic_source_and_weight_checks_pass':all(v.get('matches',True) for v in semantic.values() if isinstance(v,dict)) and semantic['OVO_patch']['working_diff_matches_archived_patch'],'oxford_primary_tls_map_checks_available':all(r.get('registered_map_tls_check') is not None for r in rows if r['id'].startswith('oxford') and r['capture_full_registered_outputs']),'oxford_full_pose_rpe_available':all(r.get('RPE') and r['RPE']['translation_m']['pairs']>0 and r['RPE']['rotation_deg']['pairs']>0 for r in rows if r['id'].startswith('oxford'))}
    failures=[]
    for path in sorted((BASE/'failed_full_attempts').glob('*/summary.json')):
        failed=json.loads(path.read_text());note=path.parent/'controller_note.json'
        failures.append({'run':str(path.parent.relative_to(ROOT)),'status':failed['status'],'error':failed.get('error'),'controller_note':json.loads(note.read_text()) if note.exists() else None})
    report={'retained_failed_full_attempts':failures,'status':'completed_available_dataset_scope' if all(checks.values()) else 'in_progress_or_checks_pending','updated_at':datetime.datetime.now().astimezone().isoformat(),'scope':'Six selected available geometric sequences, three repeats on three core sequences; unchanged 18 reference-pose OVO runs','checks':checks,'hash_checks':hashes,'geometry':rows,'repeatability':grouped,'semantics':{'reference_pose_only':True,'published_scene_runs':6,'local_paired_lighting_runs':12,'rgb_frames':23340,'Replica_mean_mIoU_percent':25.34,'OSMa_published_mean_mIoU_percent':27.21,'OSMa_local_baseline_mean_mIoU_percent':27.69,'OSMa_local_dynamic_mean_mIoU_percent':24.04,'seeds':[0],'runs_per_scene_condition':1,'freeze_check':'experiments/phase1_20261022/semantic_baseline_freeze_check.json','independent_result_check':'experiments/multibench_20261020/final_result_checks.json'},'optional_unavailable':json.loads((BASE/'m2dgr_availability.json').read_text()),'optional_unavailable_latest_recheck':json.loads((BASE/'m2dgr_recheck_20261023.json').read_text()),'limitations':['Selected scenes and sequences, not full official datasets or paper reproduction','Community ROS2 port plus declared compatibility fixes; no new estimator or semantic fusion module','Half speed playback with resource adaptations; wall time is not a real-time benchmark','NTU/FPV2 positional GT only; FPV2 measurement reference point ambiguous, both variants reported','Oxford GT derives from LiDAR-to-TLS registration, not independent mocap; sequence02 GT ends about 30s before recording end','Map checks cover observed registered outputs, not all raw points or full-map completeness','Transition boundaries are coarse assistant visual annotations, windows can overlap or truncate','OVO single seed reference-pose results, no integrated LIVO-to-OVO result','M2DGR door datasets unavailable, explicitly excluded from completed available scope']}
    batch_path=BASE/'batch_status.json'
    batch=json.loads(batch_path.read_text()) if batch_path.exists() else {}
    if batch.get('status')=='paused_user_requested' and not all(checks.values()):
        report['status']='paused_user_requested'
        report['pause']={'updated_at':batch['updated_at'],'remaining_jobs':batch['remaining_jobs'],'checkpoint':'experiments/phase1_20261022/execution_checkpoint_20261023.json'}
    temp=BASE/'results.tmp';temp.write_text(json.dumps(report,indent=2)+'\n');temp.replace(BASE/'results.json')
    lines=['# 阶段一运行汇总','',f"状态：`{report['status']}`。更新：{report['updated_at']}。",'', '| 运行 | 状态 | 位姿 | 最近邻位置 RMSE (m) | 插值 RMSE (m) | 峰值 GiB | 输入回调完整 |','|---|---|---:|---|---|---:|---|']
    for r in rows:
        pos=r.get('position_metrics',{});nearest='; '.join(k+': '+f"{v['nearest_rmse_m']:.5f}" for k,v in pos.items()) or '—';interp='; '.join(k+': '+f"{v['interpolated_rmse_m']:.5f}" for k,v in pos.items()) or '—';peak=f"{r['peak_mapper_rss_GiB']:.2f}" if 'peak_mapper_rss_GiB' in r else '—'
        lines.append(f"| [{r['id']}](runs/{r['id']}/summary.json) | {r['status']} | {r.get('poses','—')} | {nearest} | {interp} | {peak} | {r.get('input_callbacks_exact','—')} |")
    lines+=['','Retail 无可核验 GT，不能给出定位精度。FPV2 的 IMU / 棱镜解释并列保留。最近邻与插值具有不同匹配覆盖，不能互相替代。','', '复现范围、限制与协议见 [本目录说明](README.md)；完整数值、重复性与验收检查见 [results.json](results.json)。','']
    (BASE/'results.md').write_text('\n'.join(lines))
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--watch',action='store_true');a=ap.parse_args();last=None
    while True:
        result=build();done=sum(bool(r.get('diagnostics_complete')) for r in result['geometry']);state=(done,result['status'])
        if state!=last:print('Phase1',done,'/12',result['status'],flush=True);last=state
        if not a.watch or done==12:break
        for _ in range(3):time.sleep(20)


if __name__=='__main__':main()
