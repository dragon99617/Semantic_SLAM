"""Bounded-memory voxel selection for recorded outputs and binary TLS PCD."""
from pathlib import Path
import numpy as np


def voxel_unique(xyz, voxel):
    cells=np.floor(xyz/voxel).astype(np.int64)
    return np.unique(cells,axis=0,return_index=True)[1]


def reduce_points(points,voxel=.1,block=1000000):
    pieces=[];colors=[]
    for start in range(0,len(points),block):
        part=points[start:start+block]
        xyz=np.column_stack([part[k] for k in ['x','y','z']])
        finite=np.isfinite(xyz).all(axis=1)
        xyz=xyz[finite]
        idx=voxel_unique(xyz,voxel)
        pieces.append(xyz[idx])
        if 'rgb' in part.dtype.names:colors.append(part['rgb'][finite][idx])
    xyz=np.concatenate(pieces)
    idx=voxel_unique(xyz,voxel)
    rgb=np.concatenate(colors)[idx] if colors else np.zeros(len(idx),dtype=np.uint32)
    return xyz[idx],rgb


def reduce_recorded(path,output,voxel=.1):
    path=Path(path)
    if path.stat().st_size%16:raise ValueError('Incomplete registered point record')
    points=np.memmap(path,dtype=[('x','<f4'),('y','<f4'),('z','<f4'),('rgb','<u4')],mode='r')
    xyz,rgb=reduce_points(points,voxel)
    np.savez_compressed(output,xyz=xyz,rgb=rgb)
    return {'captured_points':len(points),'voxel_points':len(xyz),'voxel_m':voxel,
            'scope':'Voxelized union of every point in the observed registered-output stream; not an export of every raw input point or internal local map.'}


def pcd_memmap(path):
    path=Path(path);header={}
    with path.open('rb') as f:
        while True:
            line=f.readline().decode('ascii').strip()
            if not line:continue
            if line.startswith('#'):continue
            k,*v=line.split();header[k]=v
            if k=='DATA':break
        offset=f.tell()
    if header['DATA']!=['binary']:raise ValueError('Requires binary PCD')
    names=header['FIELDS'];sizes=list(map(int,header['SIZE']));counts=list(map(int,header.get('COUNT',['1']*len(names))))
    offsets=[];stride=0
    for size,count in zip(sizes,counts):offsets.append(stride);stride+=size*count
    if any(sizes[names.index(k)]!=4 for k in ['x','y','z']):raise ValueError('Requires float32 XYZ')
    dtype=np.dtype({'names':['x','y','z'],'formats':['<f4']*3,'offsets':[offsets[names.index(k)] for k in ['x','y','z']],'itemsize':stride})
    n=int(header['POINTS'][0])
    if path.stat().st_size-offset!=n*stride:raise ValueError('PCD header/size mismatch')
    return np.memmap(path,dtype=dtype,mode='r',offset=offset,shape=(n,))
