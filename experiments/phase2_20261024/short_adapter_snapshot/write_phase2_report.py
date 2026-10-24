#!/usr/bin/env python3
"""Assemble auditable short-segment integration results, without claiming phase 2 complete."""
import datetime
import json
from pathlib import Path

from verify_phase1_handoff import sha

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'experiments/phase2_20261024'


def read(path):
    return json.loads(path.read_text())


def main():
    rows = []
    for name, capture, data, run in [
        ('Oxford02 / fisheye', OUT/'captures/oxford02_16s_r2', OUT/'inputs/oxford02_16s', OUT/'runs/oxford02_sparse_fp32'),
        ('FPV2 00 / pinhole', OUT/'pipeline_fpv2_00_r2/capture', OUT/'pipeline_fpv2_00_r2/input', OUT/'runs/fpv2_00_sparse_fp32'),
    ]:
        c, m, r, a, v = [read(p) for p in (capture/'capture_summary.json', data/'manifest.json',
                            run/'runtime.json', run/'audit.json', run/'map_checks.json')]
        row = dict(name=name, capture=str(capture.relative_to(ROOT)), input=str(data.relative_to(ROOT)),
                   run=str(run.relative_to(ROOT)), input_images=m['input_images'], accepted_frames=len(m['frames']),
                   rejected_frames=m['rejected'], max_sync_residual_ns=m['max_sync_residual_ns'],
                   mean_valid_depth_fraction=m['mean_valid_depth_fraction'],
                   map_points=r['map_points'], objects=r['objects'], assigned_points=r['assigned_points'],
                   assigned_fraction=r['assigned_points']/r['map_points'],
                   mapping_frames_with_new_points=r['mapping_frames_with_new_points'],
                   peak_cuda_allocated_bytes=r['peak_cuda_allocated_bytes'],
                   peak_cuda_reserved_bytes=r['peak_cuda_reserved_bytes'],
                   checks=dict(capture_complete=c['completed'], all_sensor_callback_multisets_exact=all(t['exact'] for t in c['input_audit'].values()),
                               all_accepted_frames_traversed=r['completed'] and r['processed_frames'] == len(m['frames']),
                               projection_crosscheck=m['projection_crosscheck']['passed'],
                               native_ros_pose_values_match=m['projection_crosscheck']['native_ros_pose_values_match'],
                               original_semantic_settings_unchanged=a['original_semantic_config_unchanged'],
                               run_source_snapshots_match=all(sha(ROOT/'scripts'/name) == digest for name, digest in a['adapter_source_hashes'].items()),
                               geometry_association_float32=r['geometry_precision'] == 'float32',
                               map_checks_pass=v['passed'], map_grows=r['mapping_frames_with_new_points'] > 1))
        rows.append(row)
    source_files = ['livo_ovo_geometry.py', 'capture_livo_ovo.py', 'prepare_livo_ovo.py', 'run_livo_ovo.py',
                    'sparse_depth_filter.py', 'inspect_livo_ovo.py', 'run_livo_ovo_pipeline.sh',
                    'verify_phase1_handoff.py', 'test_livo_ovo_geometry.py', 'test_sparse_depth_filter.py',
                    'verify_livo_ovo_precision.py', 'write_phase2_report.py']
    checks = dict(phase1_recheck=read(OUT/'phase1_recheck_final.json')['passed'],
                  geometry_precision_probe=read(OUT/'geometry_precision_check.json')['passed'],
                  contract_test_transcripts_pass=all('Ran 6 tests' in (OUT/f).read_text() and '\nOK\n' in (OUT/f).read_text() for f in ('geometry_tests.txt', 'sparse_tests.txt')),
                  both_real_segments_verified=all(all(r['checks'].values()) for r in rows))
    evidence = {}
    for row in rows:
        for base, names in [(row['capture'], ['capture_summary.json', 'capture_index.json', 'mapper.yaml', 'camera.yaml', 'estimate_imu.tum']),
                            (row['input'], ['manifest.json', 'frame_audit.csv', 'rejected_frames.json']),
                            (row['run'], ['runtime.json', 'audit.json', 'config.yaml', 'map_checks.json', 'mapping_trace.json', 'ovo_map.ckpt'])]:
            for name in names:
                path = ROOT/base/name
                evidence[str(path.relative_to(ROOT))] = sha(path)
    report = dict(created_at=datetime.datetime.now().astimezone().isoformat(),
                  status='short_segment_integration_verified' if all(checks.values()) else 'checks_failed',
                  passed=all(checks.values()), phase2_complete=False,
                  checks=checks, runs=rows, evidence_sha256=evidence,
                  adapter_source_sha256={f'scripts/{f}': sha(ROOT/'scripts'/f) for f in source_files},
                  limits=['Offline sequential connection, not a live real-time semantic node',
                          'Two 16s moving segments, not full-sequence or transition acceptance',
                          'No verified semantic/instance GT; no accuracy claim',
                          'No reference/estimated geometry paired experiment yet',
                          'Semantic point coverage remains limited; object counts and assigned fractions are not accuracy',
                          'Same-pixel z-buffer on sparse image-filtered registered clouds; unsampled occlusion is unresolved'])
    (OUT/'final_acceptance.json').write_text(json.dumps(report, indent=2)+'\n')
    lines = ['# 第二阶段：FAST-LIVO2 → OVO 首步集成验证', '',
             f"生成时间：{report['created_at']}。", '',
             '**第一阶段复核通过，第二阶段的离线短段连接已验证。第二阶段整体尚未验收。**', '',
             '第一阶段 238 个冻结文件、源码提交及 OVO 补丁再次核对一致，12 次几何运行的紧凑产物与 30 份评分 CSV 保持原样。M2DGR 下载不可用的例外继续保留。见 [第一阶段复核](phase1_recheck_final.json)。', '',
             '| 真实片段（各 16 s） | 原始 / 接入 RGB 帧 | 最大同步残差 ns | 平均有效深度 | 地图点 | 对象 | 已赋对象 ID 的点 | 有新点的建图更新 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['name']} | {r['input_images']} / {r['accepted_frames']} | {r['max_sync_residual_ns']} | {r['mean_valid_depth_fraction']:.2%} | {r['map_points']:,} | {r['objects']} | {r['assigned_points']:,} ({r['assigned_fraction']:.2%}) | {r['mapping_frames_with_new_points']} |")
    lines += ['', '两段都精确接收了切片内全部原始传感器消息，mapper 正常退出；未接入图像逐帧记录原因，主要是没有同刻非空前视点云，另有初始化/尾部无位姿的帧。没有用邻帧点云或插值位姿填补。整数 ns 同步只允许 2 µs 的浮点时间输出量化容差，FPV2 固定使用相机→IMU 时移 −13,618,217 ns。', '',
              '`T_cam_imu = T_cam_lidar · inverse(T_imu_lidar)`，`T_world_cam = T_world_imu · inverse(T_cam_imu)`。使用 LIO 更新后、同帧 VIO 更新前的 IMU 估计位姿。冻结 mapper 的点坐标也在该状态下生成，后续 VIO 发布做视场过滤和着色；适配器使用原始图像，不使用叠画 `/rgb_img`。鱼眼/针孔 RGB 先统一去畸变到固定新 K；深度为同一相机坐标系的 z 值（米），同像素取最近点，零为无观测，保留有效掩膜。', '',
              '逐点用独立齐次逆变换和 OpenCV 投影复核，最近像素误差≤0.5 px，z 数值误差<2 µm。这是数值接口检查，不代表实际标定或三维空间精度达到微米。原生 TUM、ROS 位姿与 OVO 保存的 c2w 均已比对。', '',
              '## 稀疏输入兼容', '',
              '沿用 SAM2.1 large、SigLIP-384、发布融合权重、FP16/分批资源设置与全部语义阈值。第三方源码与冻结估计器未修改。仅在本项目适配器中显式选择两个 `valid_mask` 策略：', '',
              '1. 原深度边缘滤波把缺测零值参与高斯平滑，抽查帧的有效深度全部被剔除。适配为按有效支持归一化平滑；无效像素仍为零，不补深度，核大小、sigma 和原阈值不变。',
              '2. 原建图的重复点邻域抑制把无深度像素也视作已匹配区域，导致地图停在首帧。适配为只围绕实际有深度且匹配已有地图的像素抑制新点；核大小和建图频率不变。', '',
              '另外，作者在语义推理外层使用 BF16 autocast，连带世界点→相机投影及 5 cm 深度匹配也被降精度；真实片段首帧自匹配中，FPV2 的 1096 个 FP32 匹配在 BF16 下只剩 228 个，Oxford 为 632→239。适配器仅将对象几何关联固定为 FP32，模型推理精度保留。见 [精度复核](geometry_precision_check.json)。这项数值适配也单独披露，不归因于新语义算法。', '',
              '两项策略在完整正深度输入上均与原实现逐元素一致；空洞、非有限值、边缘和稀疏新点行为另有测试。6 项几何测试与 6 项稀疏兼容测试均通过。诊断过程及未通过的尝试见 [诊断记录](diagnostics.md)。这些是已披露的输入兼容改动，不能把集成结果称为完全未改动的原方法分数。', '',
              '## 可审阅产物', '']
    for row in rows:
        rel_run = (ROOT/row['run']).relative_to(OUT)
        rel_data = (ROOT/row['input']).relative_to(OUT)
        lines += [f"- {row['name']}：[运行结果]({rel_run}/runtime.json)、[逐帧新增点]({rel_run}/mapping_trace.json)、[地图重载及文本查询]({rel_run}/map_checks.json)、[投影帧审计]({rel_data}/frame_audit.csv)。",
                  f'![{row["name"]} 地图]({rel_run}/semantic_map.png)', '']
    lines += ['检查点重载后，所有地图点、对象描述符和文本查询分数均为有限值，对象 ID 可解析，地图在多帧上持续扩展。文本查询只证明功能可用，其候选词排名不作为语义准确率。', '',
              '语义覆盖仍有限：表中赋对象 ID 的比例只针对本次输出点云，既不是整个场景的覆盖率，也不证明对象 ID 正确。尤其 FPV2 的稀疏输入与保留的原关联阈值限制了可赋语义的点；后续需在冻结条件下继续诊断，不能通过本次短段检查宣称语义质量达标。', '',
              '## 复跑与下一步', '',
              '```bash', 'bash scripts/run_livo_ovo_pipeline.sh \\', '  experiments/phase1_20261022/oxford02.json \\', '  experiments/phase2_20261024/new_oxford_test 16', '```', '',
              '输出目录必须不存在；最后一个参数为从包开头截取的秒数，0 表示全包。系统 ROS Python 负责采集/投影，隔离 semantic_venv 负责 OVO。原始数据、NPZ 帧和检查点保留本机，紧凑审计与预览随项目保存。', '',
              '下一步是冻结本适配器，在完整序列和已标注转场窗口验收覆盖与资源占用，再做 Oxford 可用参考几何/估计几何配对。参考与估计几何必须使用同样的掩膜/FP32 适配，第一阶段未适配的 OVO 分数不能直接作为这批集成结果的公平对照。当前仅完成两种相机的短段接口验证，尚未完成全序列、转场、实时在线节点或语义/实例精度验收；未加入可靠性门控、恢复或正式消融模块。', '',
              '机器可读验收与源码/产物哈希见 [final_acceptance.json](final_acceptance.json)。']
    (OUT/'README.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k: v for k, v in report.items() if k not in ('evidence_sha256', 'adapter_source_sha256', 'runs')}, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
