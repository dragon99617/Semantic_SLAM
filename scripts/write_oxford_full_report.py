#!/usr/bin/env python3
"""Combine full engineering acceptance and explicitly pending semantic labels."""
import datetime
import json
from pathlib import Path

from score_oxford_transition_annotations import score
from verify_phase1_handoff import sha

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'experiments/phase2_20261024/full'


def main():
    audits = [json.loads((BASE/'runs'/seq/'full_audit.json').read_text()) for seq in ('oxford02', 'oxford05')]
    package = BASE/'annotations/oxford_transitions_v1'
    labels = score(package, [BASE/'runs'/seq/'transition_predictions' for seq in ('oxford02', 'oxford05')])
    (BASE/'semantic_evaluation.json').write_text(json.dumps(labels, ensure_ascii=False, indent=2)+'\n')
    checks = dict(both_full_runs_pass=all(a['engineering_passed'] for a in audits),
                  read_only_monitor_validated=json.loads((BASE/'instrumentation_check.json').read_text())['passed'],
                  all_13_transitions_audited=sum(len(a['events']) for a in audits) == 13)
    frozen = json.loads((BASE/'source_freeze_before_full_ovo.json').read_text())['files']
    corrections = BASE/'non_algorithmic_report_fix.json'
    if corrections.exists():
        frozen.update(json.loads(corrections.read_text())['revised_files'])
    checks['full_source_freeze_matches'] = all(sha(ROOT/path) == digest for path, digest in frozen.items())
    phase1_check = BASE/'phase1_recheck_after_full.json'
    if phase1_check.exists():
        checks['phase1_freeze_still_matches'] = json.loads(phase1_check.read_text())['passed']
    scripts = ['capture_livo_ovo.py', 'prepare_livo_ovo.py', 'livo_ovo_geometry.py', 'run_livo_ovo.py',
               'sparse_depth_filter.py', 'run_oxford_ovo_full.py', 'inspect_livo_ovo.py', 'run_oxford_full_batch.py',
               'prepare_oxford_transition_annotations.py', 'score_oxford_transition_annotations.py',
               'audit_oxford_full_run.py', 'write_oxford_full_report.py']
    result = dict(updated_at=datetime.datetime.now().astimezone().isoformat(),
                  status='engineering_passed_semantic_annotations_pending' if all(checks.values()) else 'engineering_checks_failed',
                  engineering_passed=all(checks.values()), semantic_accuracy_accepted=False,
                  semantic_accuracy_status=labels['status'], checks=checks, runs=audits,
                  ontology_sha256=sha(package/'ontology.json'), annotation_manifest_sha256=sha(package/'manifest.json'),
                  source_sha256={f'scripts/{f}': sha(ROOT/'scripts'/f) for f in scripts},
                  limits=['No independent reviewed semantic labels are available yet',
                          'Causal map renders preserve the original delayed descriptor queue',
                          'No instance-ID accuracy claim or full-scene 3D semantic ground truth',
                          'Fixed 15-class query vocabulary for future sampled 2D scoring; not an open-set recognition benchmark'])
    (BASE/'final_acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    lines = ['# Oxford 全序列及转场验收', '',
             f"更新：{result['updated_at']}。", '',
             '**两条完整序列的工程验收通过；转场语义精度等待独立标注，尚未判定通过。**', '',
             '第一阶段 238 个冻结文件、源码和模型版本在本次运行后再次复核一致，见 [phase1_recheck_after_full.json](phase1_recheck_after_full.json)。', '',
             '运行覆盖当前已准备的 Oxford Christ Church 02、05 全部输入，分别约 592 s、800 s；不是 Oxford 全部 24 条序列。冻结估计器、相机与回放速率 0.5 沿用上一阶段。', '',
             '| 序列 | 原始 RGB / 接入 RGB-D | IMU 位姿 | 地图点 | 对象 | 已赋对象 ID 的点 | 最近邻 APE RMSE m | 插值 APE RMSE m |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for a in audits:
        lines.append(f"| {a['sequence']} | {a['input_images']} / {a['accepted_frames']} | {a['pose_count']} | {a['map_points']:,} | {a['objects']} | {a['semantic_points']:,} | {a['nearest_ape_rmse_m']:.5f} | {a['interpolated_ape_rmse_m']:.5f} |")
    lines += ['', '所有输入回调多重集合、同刻同步、投影数值、原生/ROS 位姿、完整 OVO 帧遍历与重载地图均检查。地图多帧扩展；各转场中心窗口均有语义更新。未接入帧及截断/重叠窗口有单独记录，不能用输入接收完整替代算法更新覆盖。', '',
              'APE/RPE 只使用真实 6DoF base 参考与一次全程 SE(3) 对齐，尺度固定 1，不搜索时移，不逐窗口重配准。Oxford02 最后部分无参考轨迹的限制继续保留；几何误差不等于语义精度。', '',
              '| 事件 | 类型 | 前 / 中 / 后接入 RGB-D 帧 | 前 / 中 / 后语义更新 | 语义精度 |',
              '|---|---|---:|---:|---|']
    for a in audits:
        for event in a['events']:
            windows = [event['windows'][k] for k in ('before', 'transition', 'after')]
            counts = ' / '.join(str(w['accepted_rgbd']) for w in windows)
            updates = ' / '.join(str(w['semantic_updates']) for w in windows)
            lines.append(f"| {event['event_id']} | {event['from_environment']} → {event['to_environment']} | {counts} | {updates} | 待独立标注 |")
    lines += ['', '完整稀疏语义地图复用全程轨迹对齐后，与 5 cm TLS 参考逐点比较，不做额外地图 ICP，也不裁掉大距离点：', '']
    for a in audits:
        geo = a['integrated_map_tls_check']
        lines.append(f"- {a['sequence']}：中位距离 {geo['median_m']:.4f} m，95 分位 {geo['p95_m']:.4f} m，{geo['fraction_within_0_2_m']:.2%} 的输出点距参考≤0.2 m。")
    lines += ['', '这些是观测地图到参考表面的几何距离，受参考覆盖、遮挡和动态物体影响，不代表全场景完整性或语义准确率。']
    lines += ['', '## 独立标注与计分', '',
              '[标注包 ZIP](annotations/oxford_transitions_v1.zip) 含 114 张独立抽样、去重后的去畸变图像，覆盖 13 个事件和前/中/后固定窗口。117 个请求锚点中有 2 个被序列尾部截断，另有 1 个重复采样图像已合并。包含 15 类定义、有效区域、空模板和 tasks.csv；没有把预测或空模板当作真值。', '',
              '请按[标注说明](annotations/oxford_transitions_v1/README.md)完成独立标注与复核。预测存放在 runs/*/transition_predictions，未放入盲标注包。渲染只使用采样时刻之前的地图及当时已有描述符，不用全序列末尾的地图覆盖过去；保留原 OVO 的延迟队列。没有完成的描述符或位姿时输出未知，不用未来信息补齐。稀疏渲染不能解决未采样遮挡。', '',
              '两条序列均保留 segment_every=10、kf_queue_delay=10；它们实际输入频率不同，因此真实时间上的语义更新频率和描述符延迟不同。队列参数按关键帧计，不按固定秒数解释，也未为改善转场结果调参。', '',
              '标注完成后：', '', '```bash', 'python3 scripts/score_oxford_transition_annotations.py \\',
              '  experiments/phase2_20261024/full/annotations/oxford_transitions_v1 \\',
              '  --predictions experiments/phase2_20261024/full/runs/oxford02/transition_predictions \\',
              '                experiments/phase2_20261024/full/runs/oxford05/transition_predictions \\',
              '  --output experiments/phase2_20261024/full/semantic_evaluation.json --require-complete', '```', '',
              '计分输出逐类 IoU、mIoU、像素准确率、未知预测比例、条件准确率及每事件/每窗口结果。未知预测计入漏检，汇总按唯一图像去重；重叠事件不能视为独立样本。还没有约定准确率通过阈值，因此即使之后有标注，脚本也只报告测量，不擅自给出质量通过结论。', '',
              '## 产物与边界', '']
    for seq in ('oxford02', 'oxford05'):
        lines += [f'- {seq}：[全程审计](runs/{seq}/full_audit.json)、[地图检查](runs/{seq}/map_checks.json)、[进度与资源](runs/{seq}/resources.json)、[完整几何评分](captures/{seq}_full_r1/accuracy/metrics.json)。',
                  f'![{seq} 地图](runs/{seq}/semantic_map.png)', '']
    lines += ['公开标注查找结果见 [annotation_search.md](annotation_search.md)，原始运行、数据帧和检查点保留本机。全序列工程验收、当前可见语义覆盖、转场语义准确率分别报告。', '',
              '本次只新增只读监测与因果预测导出；姿态比较支持 q 与 −q 等价，避免完整旋转时误报。没有改变估计器、语义阈值、模型权重或上一轮验证的两项稀疏掩膜/FP32 几何处理。只读导出前后的短段结果一致，见 [instrumentation_check.json](instrumentation_check.json)。', '',
              '首轮控制器在写审计 JSON 时遇到 NumPy 布尔值序列化错误，修复报告写入后恢复控制器，复用已经成功的建图与采集。保留原异常日志与控制器状态；未重跑或改变 Oxford02 语义地图。报告/控制器的非算法修订见 [non_algorithmic_report_fix.json](non_algorithmic_report_fix.json)。', '',
              '机器可读状态：[final_acceptance.json](final_acceptance.json)、[semantic_evaluation.json](semantic_evaluation.json)。']
    (BASE/'README.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(dict(engineering_passed=result['engineering_passed'], semantic_accuracy_status=labels['status']), indent=2))
    if not result['engineering_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
