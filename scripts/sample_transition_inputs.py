#!/usr/bin/env python3
"""Sample input-only images for manual transition labels, independently of pose errors.
Source scripts/env.sh. Original compressed and prepared raw image topics supported.
"""
import argparse,json,sqlite3
from pathlib import Path
import cv2,numpy as np,yaml
from rclpy.serialization import deserialize_message
from sensor_msgs.msg import Image,CompressedImage


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('bags',nargs='+',type=Path);ap.add_argument('--topic',required=True)
    ap.add_argument('--output',required=True,type=Path);ap.add_argument('--step',type=float,default=5.)
    args=ap.parse_args();cv2.setNumThreads(1);args.output.mkdir(parents=True,exist_ok=True)
    files=[]
    for bag in args.bags:
        meta=yaml.safe_load((bag/'metadata.yaml').read_text())['rosbag2_bagfile_information']
        files.extend(bag/p for p in meta['relative_file_paths'])
    rows=[];target=None
    for file in files:
        with sqlite3.connect(f'file:{file.resolve()}?mode=ro',uri=True) as db:
            tid,kind=db.execute('SELECT id,type FROM topics WHERE name=?',(args.topic,)).fetchone()
            start,end=db.execute('SELECT MIN(timestamp),MAX(timestamp) FROM messages WHERE topic_id=?',(tid,)).fetchone()
            if target is None:target=start
            while target<=end:
                row=db.execute('SELECT timestamp,data FROM messages WHERE timestamp>=? AND topic_id=? ORDER BY timestamp LIMIT 1',(max(target,start),tid)).fetchone()
                if row is None:break
                recorded,raw=row
                if kind.endswith('CompressedImage'):
                    msg=deserialize_message(raw,CompressedImage);pixels=cv2.imdecode(np.frombuffer(msg.data,np.uint8),cv2.IMREAD_COLOR)
                else:
                    msg=deserialize_message(raw,Image)
                    data=np.frombuffer(msg.data,np.uint8).reshape(msg.height,msg.step)
                    if msg.encoding in ('bgr8','rgb8'):
                        pixels=data[:,:msg.width*3].reshape(msg.height,msg.width,3)
                        if msg.encoding=='rgb8':pixels=cv2.cvtColor(pixels,cv2.COLOR_RGB2BGR)
                    elif msg.encoding in ('mono8','8UC1'):pixels=cv2.cvtColor(data[:,:msg.width],cv2.COLOR_GRAY2BGR)
                    else:raise ValueError(msg.encoding)
                stamp=msg.header.stamp.sec+msg.header.stamp.nanosec/1e9
                name=f'{len(rows):03d}_{stamp:.6f}.jpg';cv2.imwrite(str(args.output/name),cv2.resize(pixels,(360,270)))
                rows.append({'index':len(rows),'header_s':stamp,'record_s':recorded/1e9,'file':name})
                target=recorded+int(args.step*1e9)
    t0=rows[0]['header_s']
    for page in range((len(rows)+23)//24):
        canvas=np.full((6*300,4*360,3),255,np.uint8)
        for i,r in enumerate(rows[page*24:(page+1)*24]):
            x=i%4*360;y=i//4*300;canvas[y:y+270,x:x+360]=cv2.imread(str(args.output/r['file']))
            cv2.putText(canvas,f"#{r['index']}  +{r['header_s']-t0:.1f}s",(x+5,y+290),cv2.FONT_HERSHEY_SIMPLEX,.6,(0,0,0),1)
        cv2.imwrite(str(args.output/f'contact_{page:02d}.jpg'),canvas)
    (args.output/'index.json').write_text(json.dumps({'scope':'Input images only; no trajectory/error inspection','topic':args.topic,'step_s':args.step,'first_header_s':t0,'samples':rows},indent=2)+'\n')
    print(args.output,len(rows),flush=True)


if __name__=='__main__':main()
