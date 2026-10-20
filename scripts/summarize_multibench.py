#!/usr/bin/env python3
"""Summarize only completed, actually scored scenes; retain pending datasets."""
import csv
import json
from pathlib import Path

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'experiments/multibench_20261020'


def main():
    rows=[]
    for path in sorted((BASE/'runs').glob('*/*/metrics.json')):
        data=json.loads(path.read_text())
        runtime=json.loads((path.parent/'runtime.json').read_text())
        assert runtime['completed'] and runtime['full_scene']
        data['frames']=runtime['processed_frames']
        data['wall_s']=runtime['total_wall_seconds']
        data['rgb_fps_including_initialization']=data['frames']/data['wall_s']
        data['peak_gpu_gib']=runtime['peak_cuda_allocated_bytes']/2**30
        data['run']=str(path.parent.relative_to(ROOT))
        rows.append(data)
    aggregates={}
    for dataset in ['Replica','OSMa']:
        selected=[r for r in rows if r['dataset']==dataset]
        if not selected:continue
        all_ids=set()
        matrices=[]
        for r in selected:
            run=ROOT/r['run']
            ids=np.load(run/'instance_classes.npz')['class_ids'].tolist()+[-1]
            all_ids.update(ids)
            matrices.append((ids,np.load(run/'confusion.npy')))
        all_ids=sorted(all_ids-{ -1})+[-1]
        index={x:i for i,x in enumerate(all_ids)}
        cm=np.zeros((len(all_ids),len(all_ids)),dtype=np.int64)
        for ids,mat in matrices:
            ix=[index[x] for x in ids]
            cm[np.ix_(ix,ix)]+=mat
        tp=np.diag(cm)[:-1].astype(float)
        support=cm.sum(1)[:-1]
        union=support+cm.sum(0)[:-1]-tp
        valid=(union>0) if dataset=='Replica' else (support>0)
        aggregates[dataset]=dict(
            scenes=len(selected),mean_scene_miou=float(np.mean([r['miou'] for r in selected])),
            mean_scene_macc=float(np.mean([r['macc'] for r in selected])),
            pooled_miou=float(np.mean(tp[valid]/union[valid])),
            pooled_macc=float(np.mean(tp[valid]/np.maximum(support[valid],1))),
            pooled_overall_accuracy=float(tp.sum()/support.sum()))
        np.savez(BASE/f'{dataset}_pooled_confusion.npz',confusion=cm,class_ids=all_ids)
    (BASE/'summary.json').write_text(json.dumps(dict(scenes=rows,aggregates=aggregates,M2DGR='not_evaluated_download_blocked'),indent=2))
    with (BASE/'summary.csv').open('w') as f:
        w=csv.writer(f);w.writerow(['dataset','scene','frames','miou','macc','overall_accuracy','coverage_5cm','strict_5cm_miou','wall_s','rgb_fps_including_initialization','peak_gpu_gib'])
        for r in rows:w.writerow([r['dataset'],r['scene'],r['frames'],r['miou'],r['macc'],r['overall_accuracy'],r['coverage']['0.05'],r['strict_5cm']['miou'],r['wall_s'],r['rgb_fps_including_initialization'],r['peak_gpu_gib']])
    lines=['# M2DGR、Replica 与 OSMa-Bench 评测报告（2026-10-20）','',
           f'已完成实际计分的场景：{len(rows)}/6。M2DGR 尚未评测：官方入口多次 HTTP 429，用户确认没有本地副本。ScanNet 已按用户要求替换为 Replica。','',
           '## 实测结果','',
           '| 数据集 / 场景 | 完整 RGB 帧 | mIoU | mAcc | 点准确率 | 5 cm 覆盖 | 5 cm 拒识 mIoU | 总耗时 s | 峰值 GPU GiB |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['dataset']} / {r['scene']} | {r['frames']} | {r['miou']:.2%} | {r['macc']:.2%} | {r['overall_accuracy']:.2%} | {r['coverage']['0.05']:.2%} | {r['strict_5cm']['miou']:.2%} | {r['wall_s']:.1f} | {r['peak_gpu_gib']:.2f} |")
    lines+=['','## 汇总口径','',
            '| 数据集 | 场景数 | 场景平均 mIoU / mAcc | 合并混淆矩阵 mIoU / mAcc |',
            '|---|---:|---:|---:|']
    for d,a in aggregates.items():
        lines.append(f"| {d} | {a['scenes']} | {a['mean_scene_miou']:.2%} / {a['mean_scene_macc']:.2%} | {a['pooled_miou']:.2%} / {a['pooled_macc']:.2%} |")
    lines+=['','## 可解释的范围与限制','',
        '- 本次采用 OVO 官方代码的 SAM2.1 large + SigLIP-384 + 作者学习融合权重，固定参考位姿构建语义地图。没有在 RGB-D 数据集上运行 FAST-LIVO2，也没有用这些结果证明端到端定位精度或尚未实现的新方法提升。',
        '- Replica 固定 room0、office0、office1，官方 reduced-51 标签；5-NN 对实例 ID 投票后分类，忽略无效 GT 标签。三个场景的子集结果不能直接等同于论文完整八场景结果。',
        '- OSMa 固定三个 ReplicaCAD 独立布局 v3_sc0/1/2_staging_00，发布元数据均为 baseline 光照；真值重建 stride=5、end=-1、有效点全局每 10 点抽 1 点，scene_label_set 排除 0，1-NN，提示 an image of {}。本表只汇报发布数据；同场景配对光照变化已另以本地重放实验评测。',
        '- 公开包另外含八个 HM3D 场景，标记 no_lights/no_lights_1/no_lights_2；没有与选定 ReplicaCAD 布局对应的成对条件，未将它们混成明暗对照。完整审计在 multibench_20261020/osma_inventory.json。',
        '- 因 8 GiB 显存限制，编码器与学习融合权重固定 FP16、CLIP 图像 batch=8、SAM 点提示 batch=16，关闭 debug 保存。其余匹配/采样频率保留官方默认。配置及源码补丁随结果留档。',
        '- 每个场景运行一次、固定 seed=0；场景平均反映这组场景的差异，不是多随机种子重复实验的置信区间。',
        '- 原始最近邻指标允许从远处外推类别，因此同时报告 2/5/10 cm 覆盖，并把超过 5 cm 的匹配判未知重新计分。拒识既增加部分漏检，也减少部分误报，因此该 mIoU 可能上升，不是原始指标的数学下界；两种指标分别保存。',
        '- OSMa 使用该基线自带的 SigLIP 嵌入，不套用其他方法 ViT-H-14 的权重或论文成绩。没有实例 GT 精度、导航成功率或可靠性机制收益的虚构结果。',
        '- 耗时包含模型初始化、映射和保存，吞吐按全部 RGB 帧计算；峰值 GPU 指 PyTorch 分配量，不含桌面和驱动。未用 OVO 内部“语义关键帧 FPS”冒充 RGB 实时帧率。',
        '- 部分运行与 CPU 数据渲染/准备并发，耗时只作本机工程记录，不能据此作论文速度排名。',
        '', '## 复核入口','',
        '- 逐场景：multibench_20261020/runs/<dataset>/<scene>/ 的 config.yaml、audit.json、runtime.json、mapping.log、evaluation.log、metrics.json、per_class.csv、confusion.npy、point_predictions.npy、nearest_distances_m.npy 和 ovo_map.ckpt。',
        '- 汇总：multibench_20261020/summary.json、summary.csv、各数据集合并混淆矩阵。',
        '- 最终复核：18 组保存的逐点预测与真值重新生成的混淆矩阵精确一致，官方指标函数复算一致；Replica 抽样 5-NN 实例投票与官方实现一致。详情见 multibench_20261020/final_result_checks.json。',
        '- 数据、模型、依赖与源码：source_versions.json、model_versions.json、semantic_requirements.lock、OVO_memory_compat.patch；数据成员校验见 datasets/Replica_original/selected_provenance.json。',
        '- 运行：third_party/semantic_venv/bin/python scripts/run_multibench_batch.py；重建汇总：third_party/semantic_venv/bin/python scripts/summarize_multibench.py。',
        '- 本地四光照配对实验独立记录在 [OSMa 配对光照评测](OSMa配对光照评测_20261020.md)，不与上表发布数据的场景成绩混合。',
        '- 主要来源：[OVO 官方仓库](https://github.com/tberriel/OVO)、[OSMa-Bench 官方仓库](https://github.com/be2rlab/OSMa-Bench)、[M2DGR 官方仓库](https://github.com/SJTU-ViSYS/M2DGR)。','']
    report=ROOT/'experiments/多数据集评测报告_20261020.md'
    report.write_text('\n'.join(lines))
    if rows:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig,axes=plt.subplots(1,2,figsize=(11,4),layout='constrained')
        for ax,dataset in zip(axes,['Replica','OSMa']):
            subset=[r for r in rows if r['dataset']==dataset]
            x=np.arange(len(subset))
            ax.bar(x-.18,[100*r['miou'] for r in subset],.36,label='mIoU')
            ax.bar(x+.18,[100*r['macc'] for r in subset],.36,label='mAcc')
            ax.set_xticks(x,[r['scene'].replace('v3_','').replace('_staging_00','') for r in subset])
            ax.set_title(dataset+' (reference poses)');ax.set_ylabel('Percent');ax.set_ylim(0,100);ax.legend()
        fig.savefig(BASE/'semantic_scores.png',dpi=180)
        plt.close(fig)
    print(report)


if __name__=='__main__':
    main()
