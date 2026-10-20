# NTU-VIRAL eee_01 精度核验资料

本目录记录社区 FAST-LIVO2 ROS 2 Humble 移植版的单序列精度核验。里程计只订阅图像、激光与 IMU；测量真值仅用于离线评测。

## 数据与参考

- [NTU 官方数据页](https://ntu-aris.github.io/ntu_viral_dataset/)，当前文件 API：`https://researchdata.ntu.edu.sg/api/access/datafile/68133`。
- 下载包实际大小 4,533,480,279 字节，官方 MD5：`5def8677063a4fbbf3e6c116bb8e685c`；已核对相同，并经 ZIP 解压 CRC 验证。
- 原始 ZIP、ROS 1 bag 与标定文件在 `datasets/NTU_VIRAL_original/`；原始文件保持不变。
- 派生 ROS 2 Humble bag 在 `datasets/NTU_VIRAL_eee01_humble/`，只保留左相机、水平 Ouster、外置 IMU 和 Leica 真值四个标准消息话题；不重采样、不重新标定、不修改传感器时间。
- `preparation.json` 记录消息数量、图像和点云字段及内容核对；所有图像/点云数据缓冲区及全部选定消息的传感器时间都经过转换前后比较。
- Leica 测量提供棱镜位置，单位四元数仅为格式占位。本次不评测姿态误差。
- 作者参考文件来自原仓库提交 `0d2c0346107b75b59934975adec9a6eeeb913c64` 的 `Log/result/ntu_viral/`。
- `ground_truth_crosscheck.json` 核查原始 bag 真值与作者提供文件：同为 6,616 条，差异仅为文本舍入。

## 评测口径

1. 使用源码中的 `config/NTU_VIRAL.yaml` 及 `camera_NTU_VIRAL.yaml`，只更改轨迹输出名称。每次运行保留源配置、实际参数转储、源码差异和二进制校验值。
2. 使用程序原生保存的传感器时间 TUM 轨迹。ROS Odometry 的系统时间不参与匹配。
3. 按官方标定补偿旋转中的 IMU→PRISM 杠杆臂：`p_prism = p_imu + R_imu * [-0.293656, -0.012288, -0.273095]`，单位米。
4. 主对照复用作者 README：`evo_ape tum GT EST -a --t_max_diff 0.01`。使用 SE(3) 对齐，尺度固定为 1，时间偏移为 0。作者轨迹和本地轨迹各自用相同真值及规则评价。
5. 同时按[数据集官方教程](https://ntu-aris.github.io/ntu_viral_dataset/evaluation_tutorial.html) 做交叉核对：将原始测量位置线性插值到估计时间，要求左右相邻真值均不超过 0.1 秒，再进行固定尺度刚体对齐。两套规则匹配点数不同，数值分别报告。
6. 保存匹配数量、时间范围、逐点误差 CSV、误差曲线、标准 evo ZIP 和 JSON 指标。回放完成与匹配覆盖分别检查；没有测量真值的时段不计算精度。
7. IMU 和图像按算法回调日志与 bag 传感器时间逐条匹配，容差 2 微秒；PointCloud2 回调没有逐帧日志，因此不能用此方法证明每条 LiDAR 消息都被消费。

## 复现命令

评测 Python 环境单独位于 `third_party/accuracy_venv`，不修改 ROS Python 环境。若重建：

```bash
cd /home/hxy/SLAM_project
python3 -m venv third_party/accuracy_venv
third_party/accuracy_venv/bin/python -m pip install -r experiments/accuracy/eee_01/requirements-accuracy.txt
```

仅在派生目录不存在时转换原始数据：

```bash
third_party/accuracy_venv/bin/python scripts/prepare_ntu_accuracy_bag.py \
  datasets/NTU_VIRAL_original/extracted/eee_01/eee_01.bag \
  datasets/NTU_VIRAL_eee01_humble
```

数据已准备好时，直接运行：

```bash
source scripts/env.sh
export ROS_DOMAIN_ID=71
python3 scripts/run_ntu_accuracy.py --rate 0.5
```

在程序打印的运行目录上评测：

```bash
third_party/accuracy_venv/bin/python scripts/evaluate_ntu_accuracy.py \
  experiments/accuracy/eee_01/runs/运行时间目录
```

完整运行记录位于 `runs/`。只有 `summary.json` 的状态为 `playback_completed` 且正常退出的完整运行才能进入精度评价。半速用于降低处理积压影响，结果不用于宣称端到端实时性能。
