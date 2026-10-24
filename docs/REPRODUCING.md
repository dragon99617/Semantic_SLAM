# 复现与仓库产物说明

## 当前边界

FAST-LIVO2 → OVO 离线连接已完成，并通过 Oxford02/05 全序列工程验收；转场语义精度尚未完成独立评测。第一阶段定位成绩来自 FAST-LIVO2 ROS 2，Replica/OSMa 语义成绩来自使用参考位姿及 RGB-D 的独立 OVO 基线，不能将两组成绩组合成融合系统的端到端语义效果。评测口径与下一步见[语义评测说明](SEMANTIC_EVALUATION.md)。

## 1. 固定源码

```bash
python3 scripts/bootstrap_sources.py --profile all --dry-run
python3 scripts/bootstrap_sources.py --profile all
```

脚本按提交恢复上游源码，并检查/应用已记录补丁；不会 reset 已有工作目录。仅需要一部分时使用 `--profile ros2` 或 `--profile semantic`。额外下载 M2DGR 资料仓库可加 `--include-reference-repos`，这不会取得 rosbag。

## 2. FAST-LIVO2 ROS 2

Ubuntu 22.04 下执行：

```bash
bash scripts/01_install_system.sh
bash scripts/02_build.sh
source scripts/env.sh
```

Retail Street 的下载、转换、回放脚本分别见 `scripts/download_retail_mirror.py`、`04_prepare_retail_bag.py`、`run_reproduction.py`。数据来源与实际命令以[工程记录](../experiments/FAST-LIVO2_复现记录.md)为准。

NTU-VIRAL 的下载来源、MD5、ROS 1→ROS 2 转换和评测命令见[精度核验说明](../experiments/accuracy/eee_01/README.md)。结果保存在 `experiments/accuracy/eee_01/runs/20261020_114001/accuracy/`。Leica 棱镜位置真值只支持本次平移精度口径；该 NTU 核验不报告姿态精度；Oxford 的真实六自由度参考支持的 RPE 另见统一阶段一批次。

## 3. OVO 与模型

```bash
bash scripts/setup_semantic_env.sh
```

历史环境精确列表为 `experiments/multibench_20261020/semantic_requirements.lock`。`setup_semantic_env.sh` 固定核心包版本并构建不含可选 CUDA 后处理扩展的 SAM2；它不是操作系统或数据下载器。

按 `model_versions.json` 中的来源和校验准备：

- SAM2.1 large → `third_party/OVO/data/input/sam_ckpts/sam2.1_hiera_large.pt`。
- OVO 作者融合权重 → `third_party/OVO/data/input/weights_predictor/base/model.pt`。发布文件本身是 PyTorch ZIP 格式 checkpoint，不应当作普通数据 ZIP 解压。
- SigLIP `timm/ViT-SO400M-14-SigLIP-384`，固定 revision `ac16108d567c4389e6cd2b11c9b8585f7474435b`，模型/分词配置缓存到 `third_party/model_cache/`。可在已安装 huggingface_hub 的环境使用 `snapshot_download`，设置 `HF_HOME` 为该目录；下载 `open_clip_model.safetensors` 和 JSON 配置。

语义运行脚本默认 `HF_HUB_OFFLINE=1`，完整准备缓存后再运行。历史设置为 FP16、CLIP image batch=8、SAM points batch=16、seed=0；不是最新 TextRegion/Perception Encoder 配置。

## 4. 发布数据的六组语义基线

- Replica：`python3 scripts/download_replica_selected.py` 按官方 ZIP 的 Range 获取 room0、office0、office1，逐成员 CRC 验证并写 SHA-256 清单。脚本需要 requests。局部 sparse ZIP 不是完整官方压缩包。
- OSMa：按[官方说明](https://github.com/be2rlab/OSMa-Bench)取得 compressed 分支 data.zip，解压到 `datasets/OSMa-Bench/`，使场景位于 `datasets/OSMa-Bench/data/replica_cad/<scene>/`。期望 SHA-256 见 `experiments/data_sources/`。
- 本次公开包只提供每布局一个条件；没有将其目录名称当作四种原始光照文件均已取得。

```bash
third_party/semantic_venv/bin/python scripts/verify_semantic_protocol.py
third_party/semantic_venv/bin/python scripts/run_multibench_batch.py
third_party/semantic_venv/bin/python scripts/summarize_multibench.py
```

如复用历史提交的结果目录，运行器会识别 `runtime.json` 和 `metrics.json` 并跳过已完成项。**重新推理前应选择新的输出目录**，或将已有的历史证据目录移到另一个路径后再运行。单场景入口支持 `--output`：

```bash
third_party/semantic_venv/bin/python scripts/run_semantic_benchmark.py \
  --dataset Replica --scene office0 --output experiments/new_run/office0
third_party/semantic_venv/bin/python scripts/evaluate_semantic_benchmark.py \
  --dataset Replica --scene office0 --run experiments/new_run/office0
```

正数 `--frame-limit` 仅用于短段检查，计分脚本拒绝将其认作全场景结果。

## 5. 本地配对光照

单独建立 Habitat-Sim **0.3.3**、Python **3.9**、headless + withbullet 环境；历史 Conda 包 URL 记录在 `habitat_explicit.lock`，其中本机 prefix 只是历史路径。将 `warmhammer/Augmented_ReplicaCAD_dataset` 固定 revision `dec0c80cd0b0d3dfc135cea83afe06b6ced13429` 下载到 `datasets/OSMa_sim/replica_cad/`。

```bash
LP_NUM_THREADS=2 LIBGL_ALWAYS_SOFTWARE=1 EGL_PLATFORM=surfaceless \
  third_party/habitat_env/bin/python scripts/probe_osma_replay.py
LP_NUM_THREADS=2 LIBGL_ALWAYS_SOFTWARE=1 EGL_PLATFORM=surfaceless \
  third_party/habitat_env/bin/python scripts/render_osma_paired.py
python3 scripts/audit_osma_pairs.py --require-complete
third_party/semantic_venv/bin/python scripts/run_osma_paired_batch.py
third_party/semantic_venv/bin/python scripts/summarize_osma_paired.py
```

配对运行器等待发布数据基线完成后才使用 GPU。生成器使用 Mesa llvmpipe 重放完整参考轨迹及官方灯光生命周期；四条件共享本地重渲染 baseline 的深度、真值和位姿。详情见[独立报告](../experiments/OSMa配对光照评测_20261020.md)。

## 6. GitHub 与本地的区别

GitHub 保留了小型指标、配置、混淆矩阵、图表、源码版本、补丁、校验记录和语义运行日志。原始数据、模型、ROS 大日志、地图 checkpoint、逐点预测、距离数组和相机数组未随仓库分发。

因此 `verify_multibench_results.py` 需要在拥有完整本地产物的目录运行；克隆仓库后只能先检查已发布的小型结果，或准备输入并重新运行。`final_result_checks.json` 是原本机对全部 18 组的审计记录，不表示未分发的原始产物能在空目录中重新计算。

原机路径 `/home/hxy/SLAM_project` 保留在历史配置与日志中。核心运行脚本从自身位置推导工程根目录；重建历史 shell 命令、Habitat 环境或直接读取旧 YAML 时需改为新的工程路径。

## 7. FusionPortableV2 ugv_transition00

从官方入口取得完整 `ugv_transition00.7z`，解压并通过 CRC 核验后，将 bag 保存在 `datasets/FusionPortableV2/original/extracted/ugv_transition00/ugv_transition00.bag`。官方 `20230426_calib/calib` 的三个标定文件及版本见 `experiments/fusionportable_v2/config/`；工作数据目录中的标定副本用于配置生成。下载受配额限制时，`download_fusionportable.py` 只能重试取数，不能保证成功。

```bash
third_party/accuracy_venv/bin/python scripts/prepare_fusionportable_bag.py \
  datasets/FusionPortableV2/original/extracted/ugv_transition00/ugv_transition00.bag \
  datasets/FusionPortableV2/ugv_transition00_humble
third_party/accuracy_venv/bin/python scripts/prepare_fusionportable_config.py \
  --image-scale 1.0 --sliding-half-map-voxels 60 --sliding-threshold-m 2.0
source scripts/env.sh
ROS_DOMAIN_ID=74 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 \
  python3 scripts/run_fusionportable.py --rate 0.5
```

转换器将点云头时间由扫描末尾按逐帧 `max(t)` 修正为扫描起点，保留所有点数据；JPEG 解码为 BGR8。配置的 LiDAR 时间偏移为 0，不能再叠加旧候选配置的 -0.1 秒。完整且正常退出的运行可用 `evaluate_fusionportable.py <run>` 比较发布位置真值；测量参考点的两种解释分别报告，不能按更小误差选择。详情与当前执行状态见 FusionPortableV2 执行记录。

BGR8 原始图像需要最新版项目补丁中的图像 ownership 修复：回调缓存使用 `toCvCopy`，不能直接保留临时 `toCvShare` 的裸 Mat。原分辨率实验使用约 15 GiB RAM 主机；滑动地图半尺寸 `60` 按 0.5 米体素计，实际半边长约 30 米。

## 9. 统一阶段一基线批次

最终版本的配置、二进制/库哈希、传感器适配协议、输入接收审计、转场标注和独立运行队列见 [phase1_20261022](../experiments/phase1_20261022/README.md)。进度与逐次结果见 [汇总](../experiments/phase1_20261022/results.md)，完成状态以该批次的 `results.json` 验收检查为准。

Oxford 原始 ROS2 分包与官方标定/GT 按下载清单准备后，在 ROS 环境中执行：

```bash
source scripts/env.sh
python3 scripts/prepare_oxford_livo.py 2024-03-18-christ-church-02 \
  datasets/OxfordSpires/christ_church02_livo_half --image-scale .5
python3 scripts/prepare_oxford_livo.py 2024-03-20-christ-church-05 \
  datasets/OxfordSpires/christ_church05_livo_half --image-scale .5
python3 scripts/run_phase1_batch.py
```

新准备的 Oxford 包自带消息时间戳缓存。对于只含标准 Image/Imu/PointCloud2 的旧 ROS2 转换包，可先在 Python≥3.11、已安装 NumPy/PyYAML 的环境执行 `scripts/cache_ros2_headers.py <bag目录>`；它只读 12 字节 CDR 前缀，在数据库外生成缓存，避免首次索引加载完整点云/图像。完整 ROS2 回放仍使用系统 Python/ROS 环境。

运行后的 `summary.json` 是最终运行状态，`progress.json` 是运行中快照。参数实际值保存在 ROS parameter dump；`audit_livo_parameters.py` 还检查上游未暴露的 YAML 项：偏置协方差为源码固定值 0.0001，LIO 迭代上限实际读取名为 `lio.min_iterations` 的 ROS2 参数（默认 5）。本批次请求值与实际值一致，不修改估计器。

Oxford 图像缩到半分辨率并同比缩放鱼眼内参；所有主运行仍启用完整 LIVO。最近邻与插值分别计分；插值要求前后邻点各距查询时刻≤0.1 s，而非两邻点总间隔≤0.1 s。只有 Oxford 的真实姿态 GT 用于全位姿 RPE；NTU 和 FPV2 的占位四元数不能用来计算姿态误差。

## 10. FAST-LIVO2 → OVO 离线连接

阶段一数据、ROS 环境与语义环境均已准备后，从项目根目录执行：

```bash
bash scripts/run_livo_ovo_pipeline.sh \
  experiments/phase1_20261022/oxford02.json \
  experiments/phase2_20261024/new_oxford_test 16
```

输出目录必须不存在。16 表示从包开头取 16 秒，保留估计器初始化；0 表示全包。脚本依次核对第一阶段冻结产物，回放并采集原始 RGB/估计位姿/世界点云，同刻匹配并投影稀疏深度，运行原 OVO 模型，最后重载地图检查对象描述符与文本查询。ROS 与 CUDA/GPU 访问都需要环境允许。

相机支持当前冻结标定的 Pinhole 与 EquidistantCamera，先把 RGB 统一去畸变到固定针孔 K，再投影世界点云生成以米为单位的 z 深度与有效掩膜。未同步或无有效深度的帧在 `rejected_frames.json` 中列明。不会补全深度，也不把 GT 交给估计器。

集成运行显式采用 `--depth-policy valid_mask --mapping-policy valid_mask --geometry-precision float32`：对稀疏缺测做两项掩膜兼容处理，并把几何关联与神经网络的 BF16 autocast 分开。模型、语义融合和阈值沿用冻结设置。逐阶段脚本、发现的问题、原设置诊断、验收边界与实际结果见 [第二阶段记录](../experiments/phase2_20261024/README.md)。短段接通不代表完整序列或语义精度通过。

## 11. Oxford 全序列与转场标注

Oxford02/05 已完成当前准备输入的全序列工程验收，见[完整结果](../experiments/phase2_20261024/full/README.md)。这不覆盖 Oxford 的全部 24 条序列；转场语义精度仍待独立标注。

完整回放可将上节最后一个参数设为 0，或逐阶段使用 `capture_livo_ovo.py --seconds 0`、`prepare_livo_ovo.py`、`run_oxford_ovo_full.py`、`inspect_livo_ovo.py`。所有输出目录必须是新目录，失败/中断尝试不覆盖。

要生成独立盲标注包，在已 source ROS 的系统 Python 环境运行：

```bash
python3 scripts/prepare_oxford_transition_annotations.py --output /absolute/new_annotation_package
```

`run_oxford_ovo_full.py <prepared_input> --output <new_run> --annotation-package <package>` 沿用原 OVO 资源与融合配置，增加进度、资源记录与只读因果预测导出。它在处理采样时刻之前的地图上渲染，保留原始描述符队列延迟，不使用最终地图回填过去。

标注包内含 15 类定义、图像有效区域、空模板、独立标注/复核记录；空模板不能计分。独立标注后使用 `score_oxford_transition_annotations.py`，完整命令见全序列报告。未知预测计入漏检，同时报告整幅图像、投影几何支持内的指标与覆盖率；没有语义真值时不输出准确率，也不凭几何检查判定语义精度通过。
