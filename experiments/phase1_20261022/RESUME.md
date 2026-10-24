# 保存与恢复

当前更新：2026-10-23 已完成恢复后的全部四轮，12/12 几何运行与评测完成，全部结果检查通过。当前无需恢复计算；以下为暂停时的历史记录和复现说明。

2026-10-23 按用户要求保存并停止，相关批次、回放、mapper 和报告监控进程均已退出。

- 已完成：六条序列首轮，NTU eee_01 额外两次重复，共 8/12 次几何运行及评测；18 组既有 OVO 基线保留。
- 待完成：Oxford02 r2/r3、FPV2_00 r2/r3。
- Oxford02 r2 的部分轨迹、日志和正常退出记录保存在 failed_full_attempts/oxford02_r2_user_paused_20261023/，不计为算法失败或完整基线。内部状态未保存，恢复后从此轮开头重跑。
- Oxford02 r1 的 RPE 评测已修复，最终 metrics 有效；暂停状态已核对，原控制器状态保留在 batch_status_before_user_pause_20261023.json。

在项目根目录执行：

```bash
source scripts/env.sh
python3 -u scripts/run_phase1_batch.py
```

控制器复用已有完整结果，只运行剩余四轮。完成后再执行：

```bash
third_party/accuracy_venv/bin/python scripts/refresh_phase1_accuracy_plots.py
third_party/accuracy_venv/bin/python scripts/write_phase1_report.py
```

最终验收以 results.json 全部 checks 为 true 和 completed_available_dataset_scope 为准。M2DGR 官方 429 的可用性例外保留；本阶段不接入 FAST-LIVO2→OVO。
