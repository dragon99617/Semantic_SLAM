#!/usr/bin/env python3
"""Run a declared LIVO baseline, archive inputs/outputs and audit reception.

Source scripts/env.sh. Runs are serial because upstream uses shared log files.
Input JSON names a bag, mapper/camera YAMLs, dataset, sequence and pose reference.
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import subprocess
import time

import cv2
import numpy as np
import rclpy
from sensor_msgs.msg import Image
from rclpy.qos import qos_profile_sensor_data
import yaml

from run_fusionportable import Observer
from run_reproduction import stop_process, stop_mapper


def tail_bytes(path,size):
    with Path(path).open('rb') as f:
        f.seek(max(0,Path(path).stat().st_size-size))
        return f.read()


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(8*1024**2),b''):h.update(b)
    return h.hexdigest()


def input_headers(bag, topics):
    cache=bag/'input_headers.npz'
    if cache.exists():
        with np.load(cache) as data:
            if all(t in data for t in topics):return {t:data[t].copy() for t in topics}
    out=collections.defaultdict(list)
    meta=yaml.safe_load((bag/'metadata.yaml').read_text())['rosbag2_bagfile_information']
    for name in meta['relative_file_paths']:
        with sqlite3.connect(f'file:{(bag/name).resolve()}?mode=ro',uri=True) as db:
            ids={t:i for i,t in db.execute('SELECT id,name FROM topics')}
            for topic in topics:
                for (raw,) in db.execute('SELECT substr(data,5,8) FROM messages WHERE topic_id=? ORDER BY timestamp,id',(ids[topic],)):
                    s,ns=struct.unpack('<iI',raw);out[topic].append(s*10**9+ns)
    result={t:np.array(v,dtype=np.int64) for t,v in out.items()}
    np.savez_compressed(cache,**result)
    return result


class BaselineObserver(Observer):
    def __init__(self, run, image_topic, full_clouds):
        super().__init__()
        self.run=run
        self.odom=[]
        self.conditions=[]
        self.profile_time=-1
        self.thumb_time=-1
        self.raw_points=0
        self.nonfinite_points=0
        self.cloud_file=(run/'registered_outputs.bin').open('wb') if full_clouds else None
        self.extra_sub=self.create_subscription(Image,image_topic,self.input_image,qos_profile_sensor_data)

    def observe(self,topic,msg):
        super().observe(topic,msg)
        if topic=='/aft_mapped_to_init':
            p=msg.pose.pose.position;q=msg.pose.pose.orientation
            self.odom.append([msg.header.stamp.sec+msg.header.stamp.nanosec/1e9,p.x,p.y,p.z,q.x,q.y,q.z,q.w])
        if topic=='/cloud_registered' and self.cloud_file and msg.width*msg.height:
            fs={f.name:f.offset for f in msg.fields};names=['x','y','z']+(['rgb'] if 'rgb' in fs else [])
            dt=np.dtype({'names':names,'formats':['<f4']*3+(['<u4'] if 'rgb' in fs else []),
                'offsets':[fs[n] for n in names],'itemsize':msg.point_step})
            pts=np.frombuffer(msg.data,dtype=dt)
            xyz=np.column_stack([pts[n] for n in ['x','y','z']]);ok=np.isfinite(xyz).all(axis=1)
            self.nonfinite_points+=int((~ok).sum())
            out=np.empty(int(ok.sum()),dtype=[('x','<f4'),('y','<f4'),('z','<f4'),('rgb','<u4')])
            for n in ['x','y','z']:out[n]=pts[n][ok]
            out['rgb']=pts['rgb'][ok] if 'rgb' in names else 0
            self.cloud_file.write(out.tobytes());self.raw_points+=len(out)

    def input_image(self,msg):
        stamp=msg.header.stamp.sec+msg.header.stamp.nanosec/1e9
        if stamp-self.profile_time<1:return
        self.profile_time=stamp
        if msg.encoding in ('bgr8','rgb8'):
            pixels=np.frombuffer(msg.data,dtype=np.uint8).reshape(msg.height,msg.step)[:,:msg.width*3].reshape(msg.height,msg.width,3)
            gray=cv2.cvtColor(pixels,cv2.COLOR_BGR2GRAY if msg.encoding=='bgr8' else cv2.COLOR_RGB2GRAY)
        elif msg.encoding in ('mono8','8UC1'):
            gray=np.frombuffer(msg.data,dtype=np.uint8).reshape(msg.height,msg.step)[:,:msg.width]
            pixels=cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR)
        else:return
        small=cv2.resize(gray,(160,120))
        self.conditions.append([stamp,float(gray.mean()),float(np.percentile(small,5)),float(np.percentile(small,95)),
            float((gray<=5).mean()),float((gray>=250).mean()),float(cv2.Laplacian(small,cv2.CV_64F).var())])
        if stamp-self.thumb_time>=10:
            self.thumb_time=stamp
            folder=self.run/'input_thumbnails';folder.mkdir(exist_ok=True)
            cv2.imwrite(str(folder/f'{stamp:.6f}.jpg'),cv2.resize(pixels,(480,360)))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('descriptor',type=Path)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--rate',type=float,default=.5)
    ap.add_argument('--diagnostic',action='store_true')
    ap.add_argument('--record-clouds',action='store_true')
    ap.add_argument('--pacing-test',action='store_true',help='Diagnostic only: exercise pause/resume once')
    a=ap.parse_args()
    if a.pacing_test and not a.diagnostic:ap.error('--pacing-test requires --diagnostic')
    cv2.setNumThreads(1)
    root=Path(__file__).resolve().parent.parent
    desc=json.loads(a.descriptor.read_text())
    bag=(root/desc['bag']).resolve()
    run=a.output.resolve();run.mkdir(parents=True,exist_ok=False)
    config=yaml.safe_load((root/desc['mapper_config']).read_text())
    p=config['/**']['ros__parameters']
    name=f"Phase1_{desc['id']}_{run.name}"
    p['evo'].update(seq_name=name,pose_output_en=True)
    p['pcd_save'].update(pcd_save_en=False,colmap_output_en=False)
    p['image_save']['img_save_en']=False
    cfg=run/'mapper.yaml';cfg.write_text(yaml.safe_dump(config,sort_keys=False))
    camera=run/'camera.yaml';shutil.copy2(root/desc['camera_config'],camera)
    shutil.copy2(a.descriptor,run/'descriptor.json')
    if (bag/'preparation.json').exists():shutil.copy2(bag/'preparation.json',run/'bag_preparation.json')
    topics=p['common'];topic_names=[topics[k] for k in ['lid_topic','imu_topic','img_topic']]
    headers=input_headers(bag,topic_names)
    np.savez_compressed(run/'expected_input_headers.npz',**headers)
    expected=headers[topics['lid_topic']]
    first=expected[0]/1e9+p['time_offset'].get('lidar_time_offset',0)
    last=expected[-1]/1e9+p['time_offset'].get('lidar_time_offset',0)
    if not a.diagnostic and desc.get('expected_lidar_count')!=len(expected):raise ValueError('Full sequence count mismatch')
    source=root/'fast_livo2_ws/src/FAST-LIVO2'
    prefix=Path(subprocess.check_output(['ros2','pkg','prefix','fast_livo'],text=True).strip())
    binary=prefix/'lib/fast_livo/fastlivo_mapping'
    libs=[root/'fast_livo2_ws/install/vikit_ros/lib/libvikit_ros.so']
    (run/'binary.json').write_text(json.dumps({'path':str(binary),'sha256':sha(binary),'libraries':{str(x):sha(x) for x in libs}},indent=2)+'\n')
    for repo,path in [('FAST-LIVO2',source),('rpg_vikit',root/'fast_livo2_ws/src/rpg_vikit')]:
        (run/f'{repo}.patch').write_bytes(subprocess.check_output(['git','-C',str(path),'diff','--binary']))
    mapper_cmd=['ros2','launch','fast_livo','mapping_aviz.launch.py','use_rviz:=False','use_compressed_image:=False',f'avia_params_file:={cfg}',f'camera_params_file:={camera}']
    qos=run/'playback_qos.yaml'
    qos.write_text(yaml.safe_dump({t:{'history':'keep_last','depth':1000,'reliability':'reliable','durability':'volatile'} for t in topic_names}))
    play_cmd=['ros2','bag','play',str(bag),'--rate',str(a.rate),'--read-ahead-queue-size','300',
        '--start-paused','--wait-for-all-acked','5000','--qos-profile-overrides-path',str(qos)]
    (run/'commands.json').write_text(json.dumps({'mapper':mapper_cmd,'player':play_cmd,'transport_protocol':'startup_sync_plus_lag_capped_playback','ROS_DOMAIN_ID':os.environ.get('ROS_DOMAIN_ID'),'OMP_NUM_THREADS':os.environ.get('OMP_NUM_THREADS')},indent=2)+'\n')
    trajectory=source/'Log/result'/f'{name}.txt'
    if trajectory.exists():raise FileExistsError('Native output already exists; archive it or choose a new run name: '+str(trajectory))
    rclpy.init();obs=BaselineObserver(run,topics['img_topic'],a.record_clouds)
    started=time.monotonic();mapper=player=None;completed=clean=False;error=None
    log=(run/'mapping.log').open('w');playlog=(run/'playback.log').open('w')
    resources=[];pid=None;kernel_peak=0;max_swap=0
    pacing_events=[];pacing_paused=False;pause_started=0.;total_paused=0.;forced_test_done=False
    image_offset=float(p['time_offset'].get('img_time_offset',0.))
    print('Run',run,flush=True)
    try:
        mapper=subprocess.Popen(mapper_cmd,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        deadline=time.monotonic()+50
        while not obs.get_publishers_info_by_topic('/aft_mapped_to_init'):
            rclpy.spin_once(obs,timeout_sec=.1)
            if mapper.poll() is not None or time.monotonic()>deadline or 'process has died' in (run/'mapping.log').read_text(errors='replace'):
                raise RuntimeError('Mapper startup failed')
        for node,file in [('/laserMapping','mapper_parameters.yaml'),('/parameter_blackboard','camera_parameters.yaml')]:
            for attempt in range(3):
                with (run/file).open('w') as f:
                    result=subprocess.run(['ros2','param','dump','--no-daemon','--spin-time','4',node],stdout=f,timeout=30)
                if not result.returncode:break
                if attempt==2:raise RuntimeError('Parameter discovery failed')
        text=(run/'mapping.log').read_text(errors='replace');matches=re.findall(r'fastlivo_mapping-\d+\]: process started with pid \[(\d+)\]',text)
        if matches:pid=int(matches[-1])
        player=subprocess.Popen(play_cmd,stdout=playlog,stderr=subprocess.STDOUT,start_new_session=True)
        from rosbag2_interfaces.srv import Resume,Pause
        resume=obs.create_client(Resume,'/rosbag2_player/resume')
        pause=obs.create_client(Pause,'/rosbag2_player/pause')
        if not resume.wait_for_service(timeout_sec=30):raise RuntimeError('Player resume service not ready')
        discovery_until=time.monotonic()+3
        while time.monotonic()<discovery_until:rclpy.spin_once(obs,timeout_sec=.05)
        publishers={t:[{'node':v.node_name,'reliability':str(v.qos_profile.reliability),'depth':v.qos_profile.depth}
            for v in obs.get_publishers_info_by_topic(t)] for t in topic_names}
        (run/'player_publishers.json').write_text(json.dumps(publishers,indent=2)+'\n')
        if not all(publishers.values()):raise RuntimeError('Paused player did not advertise all inputs')
        future=resume.call_async(Resume.Request());rclpy.spin_until_future_complete(obs,future,timeout_sec=15)
        if future.result() is None:raise RuntimeError('Player resume failed')
        report=resource_at=time.monotonic()
        deadline=started+(last-first+60)/a.rate+240
        while player.poll() is None:
            rclpy.spin_once(obs,timeout_sec=.02)
            if mapper.poll() is not None or time.monotonic()>deadline+total_paused+(time.monotonic()-pause_started if pacing_paused else 0.):raise RuntimeError('Mapper exited or playback timed out')
            if time.monotonic()-resource_at>=1:
                resource_at=time.monotonic()
                if pid and Path(f'/proc/{pid}/status').exists():
                    status=Path(f'/proc/{pid}/status').read_text();kv={k:int(v) for k,v in re.findall(r'^(VmRSS|VmHWM|VmSwap):\s+(\d+)',status,re.M)}
                    kernel_peak=max(kernel_peak,kv.get('VmHWM',0));max_swap=max(max_swap,kv.get('VmSwap',0))
                    resources.append([resource_at-started,kv.get('VmRSS',0),kv.get('VmSwap',0)])
                    if kv.get('VmRSS',0)>13*1024**2:raise RuntimeError('RSS exceeds declared 13 GiB mapper budget')
                tail=tail_bytes(run/'mapping.log',20000).decode(errors='replace')
                if 'imu time stamp Jumps' in tail:raise RuntimeError('Native IMU jump guard triggered; inspect input receipt gaps, do not patch estimator')
                last_state=None
                if trajectory.exists():
                    lines=tail_bytes(trajectory,1024).splitlines()
                    if len(lines)>1:last_state=float(lines[-1].split()[0])
                lag=obs.profile_time+image_offset-last_state if last_state is not None and obs.profile_time>0 else None
                force=a.pacing_test and not forced_test_done and time.monotonic()-started>40
                action=None
                if not pacing_paused and (force or lag is not None and lag>2.):
                    future=pause.call_async(Pause.Request());rclpy.spin_until_future_complete(obs,future,timeout_sec=10)
                    if future.result() is None:raise RuntimeError('Pacing pause service failed')
                    pacing_paused=True;pause_started=time.monotonic();forced_test_done=forced_test_done or force;action='pause'
                elif pacing_paused and (time.monotonic()-pause_started>=2 and lag is not None and lag<=.25):
                    future=resume.call_async(Resume.Request());rclpy.spin_until_future_complete(obs,future,timeout_sec=10)
                    if future.result() is None:raise RuntimeError('Pacing resume service failed')
                    total_paused+=time.monotonic()-pause_started;pacing_paused=False;action='resume'
                if action:
                    event={'action':action,'wall_s':time.monotonic()-started,'image_to_state_lag_s':lag,'forced_diagnostic':bool(force)}
                    pacing_events.append(event);(run/'pacing_events.json').write_text(json.dumps(pacing_events,indent=2)+'\n');print('PACING',json.dumps(event),flush=True)
            if time.monotonic()-report>=30:
                tail=tail_bytes(run/'mapping.log',12000).decode(errors='replace')
                if 'process has died' in tail:raise RuntimeError('Mapper process died')
                poses=trajectory.read_text().splitlines() if trajectory.exists() else []
                t=float(poses[-1].split()[0]) if poses else None
                progress={'elapsed_s':round(time.monotonic()-started),'poses':len(poses),'sensor_seconds':round(t-first,3) if t else None,'outputs':dict(obs.counts),'peak_mapper_GiB':kernel_peak/1024**2}
                (run/'progress.json').write_text(json.dumps(progress,indent=2)+'\n');print(json.dumps(progress),flush=True);report=time.monotonic()
        if player.returncode:raise RuntimeError(f'Player returned {player.returncode}')
        until=time.monotonic()+15
        while time.monotonic()<until:rclpy.spin_once(obs,timeout_sec=.02)
        poses=np.loadtxt(trajectory,ndmin=2)
        if poses.shape[1]!=8 or not np.isfinite(poses).all() or not (np.diff(poses[:,0])>0).all():raise RuntimeError('Invalid saved trajectory')
        if poses[-1,0]-poses[0,0]<.95*(last-first) or abs(poses[-1,0]-last)>1:raise RuntimeError('Insufficient trajectory coverage')
        if any(not obs.counts[t] for t in ['/aft_mapped_to_init','/cloud_registered','/rgb_img']):raise RuntimeError('Required actual outputs missing')
        completed=True
    except (Exception,KeyboardInterrupt) as e:
        error=str(e) or 'Interrupted';print('FAILED',error,flush=True)
    finally:
        stop_process(player)
        if mapper:clean=stop_mapper(mapper,run/'mapping.log')
        log.close();playlog.close()
        if trajectory.exists():shutil.copy2(trajectory,run/'estimate_imu.tum')
        for f in ['imu.txt','mat_pre.txt','mat_out.txt']:
            if (source/'Log'/f).exists():shutil.copy2(source/'Log'/f,run/f)
        if obs.cloud_file:obs.cloud_file.close()
        if obs.last_rgb is not None:
            from cv_bridge import CvBridge
            cv2.imwrite(str(run/'last_tracking_image.png'),CvBridge().imgmsg_to_cv2(obs.last_rgb,'bgr8'))
        if obs.cloud_samples:
            xyz=np.concatenate([v for v,_ in obs.cloud_samples]);rgb=np.concatenate([v for _,v in obs.cloud_samples]);ok=np.isfinite(xyz).all(axis=1);xyz,rgb=xyz[ok],rgb[ok]
            _,idx=np.unique(np.floor(xyz/.1).astype(np.int64),axis=0,return_index=True)
            np.savez_compressed(run/'sampled_map.npz',xyz=xyz[idx],rgb=rgb[idx])
        np.savetxt(run/'observed_odometry.tum',np.array(obs.odom),fmt='%.9f')
        np.savetxt(run/'image_conditions.csv',np.array(obs.conditions),delimiter=',',header='sensor_s,mean_gray,p05,p95,black_fraction,saturated_fraction,laplacian_variance')
        np.savetxt(run/'resources.csv',np.array(resources),delimiter=',',header='wall_s,rss_kib,swap_kib')
        text=(run/'mapping.log').read_text(errors='replace')
        audit={}
        for tag,topic in zip(['LIDAR','IMU','IMAGE'],topic_names):
            got=np.array([int(s) for s in re.findall(r'RX_'+tag+r' (\d+)',text)],dtype=np.int64)
            want=headers[topic];missing=np.setdiff1d(want,got)
            audit[topic]={'expected':len(want),'received_callbacks':len(got),'unique_received':len(np.unique(got)),
                'missing':len(missing),'missing_ns':missing.tolist(),'received_is_exact_multiset':bool(np.array_equal(np.sort(want),np.sort(got)))}
        (run/'input_audit.json').write_text(json.dumps(audit,indent=2)+'\n')
        if completed and not all(v['received_is_exact_multiset'] for v in audit.values()):
            completed=False;error='Input callback multiset mismatch; retain as failed transport attempt'
        (run/'pacing_events.json').write_text(json.dumps(pacing_events,indent=2)+'\n')
        summary={'status':('diagnostic_completed' if a.diagnostic else 'playback_completed') if completed and clean else 'failed',
            'diagnostic':a.diagnostic,'error':error,'dataset':desc['dataset'],'sequence':desc['sequence'],'rate':a.rate,
            'pacing':{'nominal_rate':a.rate,'pause_lag_threshold_s':2.,'resume_lag_threshold_s':.25,'pauses':sum(v['action']=='pause' for v in pacing_events),'paused_wall_s':total_paused,'scope':'Playback flow control only; fixed sensor timestamps, point/image payloads and estimator settings'},
            'mapper_clean_exit':clean,'player_returncode':player.returncode if player else None,'wall_seconds':time.monotonic()-started,
            'received_outputs':dict(obs.counts),'expected_lidar_span_s':last-first,'peak_mapper_rss_kib':kernel_peak,'peak_mapper_swap_kib':max_swap,
            'registered_output_points_captured':obs.raw_points,'registered_output_nonfinite_points':obs.nonfinite_points,
            'input_callback_audit':audit,'pose_timestamp_basis':'sensor acquisition time; ROS odometry/TF and native TUM use last_lio_update_time',
            'limitation':'RX traces establish callback receipt, not that every point contributes to an estimator update.'}
        (run/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        obs.destroy_node()
        if rclpy.ok():rclpy.shutdown()
        print(json.dumps({k:v for k,v in summary.items() if k!='input_callback_audit'},indent=2),flush=True)
    if not(completed and clean):raise SystemExit(1)


if __name__=='__main__':main()
