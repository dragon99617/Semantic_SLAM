# Oxford Spires 室内外转场数据准备

**已完成：41 个文件、41.948 GB 全部下载并通过官方哈希校验，三个 ROS 2 分包的内容检查通过。尚未运行 SLAM。**

任务：为当前 FAST-LIVO2 与后续彩色语义建图选取室内外转场数据。本次完成数据准备，不报告算法精度。

## 下载范围

从作者的 [Hugging Face 仓库](https://huggingface.co/datasets/ori-drs/oxford_spires_dataset) 下载，固定版本为 `03f4382308333aa70c3253f12acd3fbf0c7c4a15`。

两条序列在[官方场景页面](https://dynamic.robots.ox.ac.uk/datasets/oxford-spires/)均标记为 Outdoor + Indoor (proper)，并实际发布了 `gt-tum.txt`：

| 序列 | ROS 2 数据 | 记录跨度 | IMU 条数 | LiDAR 帧数 | 前向相机帧数 |
|---|---:|---:|---:|---:|---:|
| `2024-03-18-christ-church-02` | 两个完整分包，共 24.196 GB | 592.207 秒 | 236,606 | 5,928 | 11,775 |
| `2024-03-20-christ-church-05` | 一个完整包，15.011 GB | 799.989 秒 | 319,608 | 8,007 | 3,961 |

还包含：

- 相机内参、相机—LiDAR—IMU 外参、IMU 参数，以及各日期标定参数文件；不下载原始标定录像压缩包。
- 对应 `gt-tum.txt` 和体积很小的作者参考轨迹。`vilens-slam`、`hba`、`colmap` 轨迹是参考方法输出，不能替代 GT。
- 两条序列共用的 Christ Church TLS 真值地图 `merged-cloud-1cm.pcd`（2.736 GB）与测量报告，可供后续几何地图评测。
- 官方说明、许可和轨迹示意图。

实际下载 **41 个文件、41,948,348,476 字节（41.948 GB，约 39.067 GiB）**。未重复下载 ROS 1 bag、独立图像/点云压缩包、COLMAP 数据库或 NeRF 模型。

## 本地位置与下载状态

数据根目录：`/home/hxy/SLAM_project/datasets/OxfordSpires/`。

[download_status.json](oxford_spires/download_status.json) 已记录 `status=completed`、`verified_bytes=41948348476`；41 个文件均通过上游哈希验证。清单、状态与本地逐文件字节数一致，下载分片和临时文件均已清理。

- [固定下载清单](oxford_spires/download_manifest.json)：逐文件路径、字节数及上游 Git/LFS 哈希。
- [元数据与真值摘要](oxford_spires/metadata_and_gt_summary.json)。
- [首个完成分包的传感器解码记录](oxford_spires/first_completed_bag_samples.json)。
- [SHA256SUMS](oxford_spires/SHA256SUMS)：全部 41 个文件的本地 SHA-256。
- [数据检查结果](oxford_spires/data_checks.json)：`status=passed`，包含每个分包的实际消息数与传感器解码样例。

下载使用严格 HTTP Range 校验、分片续传和重试。大文件与官方 LFS SHA-256 比较，小文件与官方 Git blob SHA-1 比较，同时记录本地 SHA-256。下载中断后运行同一命令即可继续：

```bash
cd /home/hxy/SLAM_project
python3 scripts/download_oxford_spires.py --workers 64
```

不要同时启动两个相同的下载任务，以免写入相同分片。

## 完整性与可用性核验

已执行，以下命令可复核：

```bash
source scripts/env.sh
python3 scripts/check_oxford_spires.py --sample-messages
```

核验覆盖：SQLite `quick_check`、各 topic 实际消息数与作者元数据一致、必要 LiDAR/IMU/图像 topic 存在、GT 数值与四元数有效且时间严格递增、数据与 GT 时间范围重叠。抽样反序列化各传感器消息，并实际解码三路 JPEG。

三个数据库的 `quick_check` 均返回 `ok`，全部 topic 实际计数与官方元数据一致：`christ-church-02` 共 **277,757** 条，`christ-church-05` 共 **339,442** 条，合计 **617,199** 条。逐包抽检五类 topic，共反序列化 **15** 条传感器消息，解码 **9** 张 JPEG，全部通过。此检查不是对每条传感器载荷逐点进行有效性评测。

已经验证 Humble 可以读取原始 version 8 元数据。首个完成分包的三路图像解码尺寸均为 **1440×1080×3**；LiDAR 消息含 `x/y/z/intensity/timestamp/ring`，其中 `timestamp` 为 FLOAT64，`ring` 为 UINT16。

## 后续运行需要注意的实际数据特征

1. 两个序列都有三路彩色鱼眼相机、Hesai QT64 LiDAR、IMU。鱼眼模型为 equidistant/OpenCV fisheye，不能把其四个畸变系数直接当作普通针孔径向畸变系数。
2. `christ-church-02` 两个分包之间约 7.056 ms，需按时间连续回放并保持同一个算法进程。不要把只跑完一个分包称为完整序列运行。
3. `christ-church-05` 发布 bag 的前向图像平均约 **4.95 Hz**；`christ-church-02` 约 **19.88 Hz**。传感器标称频率不能代替发布文件实际消息数。
4. `christ-church-02` 真值共有 14,379 个位姿，时间区间约 `1710754089.975849–1710754650.797929`，未覆盖记录末尾约 30.42 秒；`christ-church-05` 真值有 11,503 个位姿。评测必须报告实际匹配覆盖率。
5. GT 是 LiDAR 与 TLS 地图配准得到的轨迹，位姿对应 **base frame**；不是独立动作捕捉真值，也不是默认 IMU frame。后续应按标定变换测量参考点后再比较。
6. 作者 `calibration/README.md` 明确建议 March 序列用根目录默认标定：相机/LiDAR 标定来自 March，IMU 相关参数来自最近的 June 标定。另存的 [sensor_config.yaml](oxford_spires/sensor_config.yaml) 来自固定作者代码版本，包含 base frame 定义。
7. 本次没有复用 FusionPortableV2 的点时间偏移、Ouster 字段解析或相机参数；接入 FAST-LIVO2 前需要独立核查时间语义、Hesai 点格式与鱼眼去畸变。

官方代码来源版本见 [official_source_commit.json](oxford_spires/official_source_commit.json)。许可随数据保存为 `datasets/OxfordSpires/LICENSE.md`。
