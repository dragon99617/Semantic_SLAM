# 复现与仓库产物说明

## 当前边界

FAST-LIVO2 和 OVO 尚未连接。定位成绩来自 FAST-LIVO2 ROS 2；Replica/OSMa 的语义成绩来自使用参考位姿及 RGB-D 的 OVO。不能将两组成绩组合成融合系统的端到端效果。

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

NTU-VIRAL 的下载来源、MD5、ROS 1→ROS 2 转换和评测命令见[精度核验说明](../experiments/accuracy/eee_01/README.md)。结果保存在 `experiments/accuracy/eee_01/runs/20261020_114001/accuracy/`。Leica 棱镜位置真值只支持本次平移精度口径；本仓库不报告姿态精度。

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
