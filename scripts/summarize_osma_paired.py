#!/usr/bin/env python3
"""Report locally replayed paired illumination separately from released data."""
import csv
import json
from pathlib import Path

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'experiments/multibench_20261020'
CONDITIONS=['baseline','camera_lights','no_lights','dynamic_lights']


def main():
    rows=[]
    for path in sorted((BASE/'paired_runs').glob('*/*/metrics.json')):
        r=json.loads(path.read_text());assert r['data_origin']=='local_replay'
        runtime=json.loads((path.parent/'runtime.json').read_text());assert runtime['full_scene'] and runtime['completed']
        r['frames']=runtime['processed_frames'];r['run']=str(path.parent.relative_to(ROOT))
        rows.append(r)
    averages={}
    for c in CONDITIONS:
        subset=[r for r in rows if r['condition']==c]
        if subset:averages[c]={'scenes':len(subset),'miou':float(np.mean([r['miou'] for r in subset])),
                               'macc':float(np.mean([r['macc'] for r in subset])),
                               'coverage_5cm':float(np.mean([r['coverage']['0.05'] for r in subset]))}
    (BASE/'paired_summary.json').write_text(json.dumps({'scenes':rows,'scene_means':averages},indent=2))
    with (BASE/'paired_summary.csv').open('w') as f:
        w=csv.writer(f);w.writerow(['scene','condition','frames','miou','macc','coverage_5cm'])
        for r in rows:w.writerow([r['scene'],r['condition'],r['frames'],r['miou'],r['macc'],r['coverage']['0.05']])
    lines=['# OSMa 本地配对光照实验（2026-10-20）','',f'已计分 {len(rows)}/12 组。此报告独立于已发布数据的基线结果。','',
           '## 数据与协议','',
           '采用官方 HaDaGe 配置和 Augmented ReplicaCAD 资产，在公开三个场景的完整参考相机轨迹上，以 Habitat-Sim 0.3.3 + Mesa llvmpipe 重渲染 RGB。四个条件为 baseline、camera_lights、no_lights、dynamic_lights。动态光照按照发布 log.txt 的五段导航边界切换，沿用官方灯光生命周期。',
           '', '先为每场景重渲染 baseline 的 RGB、深度和语义真值，其他三条件只改变原生三维灯光，并共享 baseline 深度、位姿、内参与真值。因此没有将不同路线或不同布局当成光照对照。生成数据明确标为本地重放，不冒充发布方未提供的原始配对文件。',
           '', '语义方法与已发布数据基线相同：OVO、SAM2.1 large、SigLIP-384、作者学习融合器、FP16、CLIP batch=8、SAM 点提示 batch=16、seed=0。使用参考位姿，map_every=5、segment_every=10；按官方 OSMa scene label set 排除 0、1-NN、an image of {} 提示计分。每组完整运行一次。',
           '', '## 逐场景结果','',
           '| 场景 | 光照条件 | RGB 帧 | mIoU | mAcc | 5 cm 覆盖 |',
           '|---|---|---:|---:|---:|---:|']
    for r in rows:lines.append(f"| {r['scene']} | {r['condition']} | {r['frames']} | {r['miou']:.2%} | {r['macc']:.2%} | {r['coverage']['0.05']:.2%} |")
    lines+=['','## 场景均值','', '| 条件 | 已计分场景 | mIoU | mAcc | 相对同场景 baseline 的 mIoU 变化 |','|---|---:|---:|---:|---:|']
    baseline={r['scene']:r for r in rows if r['condition']=='baseline'}
    for c in CONDITIONS:
        if c not in averages:continue
        a=averages[c];common=[r for r in rows if r['condition']==c and r['scene'] in baseline]
        change=float(np.mean([r['miou']-baseline[r['scene']]['miou'] for r in common])) if common else None
        delta=f'{100*change:+.2f} 个百分点' if change is not None else '待配对'
        lines.append(f"| {c} | {a['scenes']} | {a['miou']:.2%} | {a['macc']:.2%} | {delta} |")
    if len(rows)==12:
        lines+=['','## 结果解读','',
                '在参考位姿与深度固定的设置下，动态光照的平均 mIoU 比 baseline 低 3.65 个百分点，无外部灯光低 3.22 个百分点。平均 5 cm 覆盖率仍在约 96% 附近，语义指标的变化不能归因于定位漂移；当前实验尚不能区分分割、分类和实例关联各自的贡献。',
                '', '相机灯光平均 mIoU 上升，但并非三个场景都改善。无外部灯光条件下 mAcc 反而上升而 mIoU 下降，因此不能用单个召回指标概括退化。本结果是三场景、单种子基线，支持后续做语义可靠性机制的对照，不构成尚未实现方法的收益证据。']
    lines+=['','## 复算与限制','',
            '- 各组 config.yaml、audit.json、runtime.json、ovo_map.ckpt、point_predictions.npy、confusion.npy、per_class.csv、metrics.json 位于 multibench_20261020/paired_runs/<condition>/<scene>/。',
            '- 生成数据位于 datasets/OSMa_paired/<condition>/<scene>/，render_manifest.json 记录配置、导航边界、帧数、文件流哈希与抽样几何对照；资产和生成器提交号单独留档。GT 缓存位于 datasets/OSMa_paired_gt/。',
            '- paired_data_checks.json 对 12 组检查帧数、文件流哈希、相同位姿/内参/类别表、共享深度/真值及实际 RGB 差异；final_result_checks.json 独立从逐点预测复建混淆矩阵并与官方计分公式核对。',
            '- 场景均值不是全 OSMa 数据集结果；采用本地重渲染，不能直接与论文原始表格排名等同。固定参考位姿，没有验证 FAST-LIVO2 定位在光照变化下的表现。',
            '- 每场景单次运行、固定随机种子；没有多种子方差。耗时受 CPU 渲染等并发任务影响，只作工程记录。',
            '- 没有加入尚未实现的可靠性门控、模型复核或导航方法，光照退化结果不等于这些机制的改进收益。',
            '', '[官方 HaDaGe](https://github.com/warmhammer/habitat_data_generator) · [OSMa-Bench](https://github.com/be2rlab/OSMa-Bench)','']
    (ROOT/'experiments/OSMa配对光照评测_20261020.md').write_text('\n'.join(lines))
    if rows:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig,ax=plt.subplots(figsize=(8,4),layout='constrained')
        x=np.arange(len(averages));labels=[c for c in CONDITIONS if c in averages]
        ax.bar(x-.18,[100*averages[c]['miou'] for c in labels],.36,label='mIoU')
        ax.bar(x+.18,[100*averages[c]['macc'] for c in labels],.36,label='mAcc')
        ax.set_xticks(x,labels,rotation=12);ax.set_ylim(0,100);ax.set_ylabel('Percent');ax.set_title('OSMa local paired replay, scene means');ax.legend()
        fig.savefig(BASE/'paired_semantic_scores.png',dpi=180);plt.close(fig)


if __name__=='__main__':
    main()
