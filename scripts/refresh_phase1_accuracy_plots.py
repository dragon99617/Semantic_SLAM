#!/usr/bin/env python3
"""Redraw phase-one APE figures from fixed CSVs with accurate reference labels.
Does not rerun alignment, association, metrics, or the mapper.
"""
import hashlib,json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT=Path(__file__).resolve().parent.parent
BASE=ROOT/'experiments/phase1_20261022'


def main():
    rows=[]
    for path in sorted((BASE/'runs').glob('*/accuracy/metrics.json')):
        m=json.loads(path.read_text());out=path.parent
        label={'OxfordSpires':'LiDAR-to-TLS base reference','NTU_VIRAL':'Leica prism reference'}.get(m['dataset'],'Published position reference')
        for name,c in m['comparisons'].items():
            for interp in (False,True):
                csv=out/f"{name}_{'interpolated' if interp else 'matched'}.csv"
                digest=hashlib.sha256(csv.read_bytes()).hexdigest();d=np.loadtxt(csv,delimiter=',',skiprows=1,ndmin=2)
                fig=plt.figure(figsize=(12,4.5) if interp else (13,5))
                if interp:
                    ax=fig.add_subplot(121);ax.plot(*d[:,1:3].T,color='black',lw=1,label=label+' (interpolated)');ax.plot(*d[:,4:6].T,lw=.6,label=name);ax.set(aspect='equal',xlabel='x [m]',ylabel='y [m]',title='Matched trajectory, XY view')
                else:
                    ax=fig.add_subplot(121,projection='3d');ax.scatter(*d[:,1:4].T,s=3,c='black',label=label);ax.scatter(*d[:,4:7].T,s=2,label=name);ax.set(xlabel='x [m]',ylabel='y [m]',zlabel='z [m]')
                ax.legend();ax=fig.add_subplot(122);ax.scatter(d[:,0]-d[0,0],d[:,7]*100,s=2);rmse=float(np.sqrt(np.mean(d[:,7]**2)))
                ax.set(xlabel='Time from first matched pose [s]',ylabel='Translation APE [cm]',title=f'RMSE {rmse*100:.3f} cm; {len(d)} matches');ax.grid(alpha=.3);fig.tight_layout()
                png=out/f"{name}_{'interpolated_accuracy' if interp else 'accuracy'}.png";fig.savefig(png,dpi=160);plt.close(fig)
                assert hashlib.sha256(csv.read_bytes()).hexdigest()==digest
                rows.append({'source_csv':str(csv.relative_to(ROOT)),'sha256':digest,'plot':str(png.relative_to(ROOT)),'reference_label':label})
    (BASE/'plot_reference_labels.json').write_text(json.dumps({'scope':'Correct reference descriptions; figures redrawn only from unchanged, already globally aligned scored CSVs','plots':rows},indent=2)+'\n');print('Redrawn',len(rows),'plots from unchanged CSVs')


if __name__=='__main__':main()
