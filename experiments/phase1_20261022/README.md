# 阶段一执行与冻结记录

本目录用于完成“选定数据上的原方法基线，为后续接入与消融做准备”。运行进度由 `batch_status.json` 记录；只有全部必要产物与检查完成后，汇总才能标为完成。

- 几何：FAST-LIVO2 社区 ROS 2 移植，保留原估计器，披露构建、图像所有权、鱼眼参数加载与输出时间修复。配置/补丁/实际二进制记录于 `baseline_freeze.json`、`protocol_hashes.json`、每次运行目录。
- 六条几何序列：Retail Street、NTU-VIRAL eee_01、FPV2 transition00/01、Oxford Christ Church 02/05。
- NTU、FPV2 transition00、Oxford02 各重复三次，其余各一次，共 12 个完整运行。旧版结果另存，不能算作本冻结版本的重复。
- 语义：沿用已完成的 18 组 OVO 参考位姿运行与独立混淆矩阵核验；`semantic_baseline_freeze_check.json` 再核对未改变的源码、资源适配补丁与模型。语义侧单次/seed=0 的限制继续保留。
- Oxford 图像显式缩到 720×540，鱼眼内参同比缩放，原始图像保留；30 m 半边长滑动地图与 0.5 倍回放属于资源设置。它不是论文全分辨率/实时速度复现。
- 官方/原 bag 的 GT 只用于离线评测，不交给估计器。固定比例 1、不搜索时间偏移、不按分数选择标定或 FPV2 参考点。FPV2 同时报告 IMU/棱镜两种解释；NTU/FPV2 的占位 GT 四元数不能用于姿态 RPE。
- 回放名义速率 0.5；观察到图像相对原生状态积压超过 2 s 时通过 ROS bag 服务暂停，排空到 0.25 s 内且至少暂停 2 s 后恢复。时间戳、输入内容和估计器不变，动作与额外墙钟耗时记录于 `pacing_events.json`。首轮 FPV2_01 的传输失败保留在 `failed_full_attempts/`；NTU/Retail 已通过精确接收检查，且日志证明不会达到新增暂停阈值（`pre_pacing_compatibility.json`）。
- 最近邻 0.01 s 与插值前后邻点各距查询时刻不超过 0.1 s（两邻点总间隔可达 0.2 s） 分别全程对齐后计分，不混为同一口径。
- native TUM 和 ROS Odometry 都保存 LIO 更新后的状态；同帧视觉反馈影响后续状态，属于上游 LIVO 导出协议。接入 OVO 时应明确选择位姿输出阶段。
- 每个输入消息的回调接收通过整数纳秒多重集合检查；这不意味着每个原始点都进入有效更新。输出地图由观测到的 registered cloud 累积；不是内部完整地图导出或完整性评分。Oxford 与 TLS 的距离检查还受参考覆盖、遮挡和动态物体影响。
- `input_annotations/` 的原图接触表及区间标注独立于误差。转场窗口固定为前 [-30,-10]、中 [-10,10]、后 [10,30] 秒；标注不确定性、窗口重叠/截断、参考覆盖一并记录。图像标注保存原始 header 时刻，窗口分析加上冻结相机标定的 img_time_offset 后再与位姿比较。
- `OMP_NUM_THREADS=2` 只是环境设置；冻结二进制的上游 OpenMP 路径调用 `omp_set_num_threads(MP_PROC_NUM)`，编译宏为 4，详见 `build_flags.txt`。不能将本次运行描述为强制限制到两个线程。
- FPV2 发布位置真值有长间断（00 最大约 70 s，01 最大约 37.8 s）。作者工具示例还存在固定时间平移（01 例子相对发布 GT +0.06 s）；本批次主指标使用下载的发布 GT 原始时刻、额外偏移 0，不按评分搜索偏移。报告窗口匹配覆盖，缺测处不插值跨越长间断。
- M2DGR 可选补充源重新访问仍 429，见 `m2dgr_availability.json`。FAST-LIVO2→OVO 集成属于第二阶段。

完整输入准备完成后，已构建本地环境的重放命令：

```bash
source scripts/env.sh
python3 -u scripts/run_phase1_batch.py
```

脚本串行运行原生 mapper，成功的已有任务会复用；失败尝试保留，需查明原因后创建新尝试，不会静默覆盖。单个新目录可用：

```bash
source scripts/env.sh
ROS_DOMAIN_ID=82 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 \
  python3 scripts/run_livo_baseline.py experiments/phase1_20261022/oxford02.json \
  --output /absolute/path/to/new_run --rate .5 --record-clouds
third_party/accuracy_venv/bin/python scripts/evaluate_livo_baseline.py /absolute/path/to/new_run
third_party/accuracy_venv/bin/python scripts/analyze_livo_transitions.py /absolute/path/to/new_run
```

`datasets/`、原生构建、输出点流/大地图、逐帧详细矩阵与语义检查点保留在本机，不随 Git 分发。紧凑的轨迹、配置、汇总、接触表和评测图可以审阅。

NTU 描述符的参考文件路径已改为转换器生成的 dataset 路径，与最初历史运行目录中的文件 SHA-256 完全相同；见 `reference_path_update_20261023.json`。旧运行描述符保留原样。

冻结二进制和项目库已另存于本机 `frozen_runtime/`，校验与来源见 `frozen_runtime_manifest.json`。该快照仍依赖记录的 ROS/系统库和编译路径，不随 Git 分发。

本批次最终完成：12/12 次几何运行，全部汇总验收项通过。额外逐文件核验见 [final_acceptance.json](final_acceptance.json)，脚本版本见 [script_hashes.json](script_hashes.json)。30 份已对齐评分 CSV 的行数、逐点误差和 RMSE 已独立重算核对；图表只从这些固定 CSV 重绘，不重新配准或改分数。
