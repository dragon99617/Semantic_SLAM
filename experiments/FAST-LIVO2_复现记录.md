# FAST-LIVO2 ROS 2 复现记录

开始时间：2026-10-15，Asia/Shanghai。

## 目标

在 Ubuntu 22.04 / WSL2 上编译社区 Humble 移植版，并用官方录制序列完成运行验证。编译成功、无数据启动、合成消息检查与真实序列复现分别记录，不互相替代。

## 环境与当前状态

- Ubuntu 22.04.5 LTS，WSL2，x86_64。
- 前次检查：内存约 7.6 GiB，swap 2 GiB，逻辑 CPU 20；构建限制为 2 个编译进程。
- 开始时没有 ROS 2 / colcon / CMake / GCC 工具链。
- `sudo -n true` 返回 `sudo: a password is required`。代理无法替用户输入本地 Linux 密码。
- 上一轮回复已保存到 `../FAST-LIVO2_ROS2_复现方案.md`。

## 系统安装

已准备 `../scripts/01_install_system.sh`，按 ROS 官方 Humble 文档配置官方 apt 源，安装 ROS、构建工具与系统依赖。脚本将日志保存到本目录的 `logs/`。

需要用户在自己的 WSL 终端运行：

```bash
bash /home/hxy/SLAM_project/scripts/01_install_system.sh
```

执行时在终端输入 Linux 的 sudo 密码，不要把密码发送到聊天。

系统安装等待期间继续准备源码、固定版本、检查兼容问题与数据入口。

## 用户确认的第一阶段范围

2026-10-15：完成一条官方序列的基础复现，保存轨迹、地图和运行记录。暂不接入语义地图、CLIP、LLM 或进行后续研究实验。

## 已完成的源码准备

- 已拉取 FAST-LIVO2 的 `humble` 分支及 Vikit、livox_ros_driver2、Sophus 1.22.10、Livox-SDK2。
- 完整提交号见 `source_versions.json`；源码补丁见 `patches/`。
- 主项目提交：`837b7bbc1431cb04cf936528e52c83c835efba8e`。
- 修复主程序对空节点构造 ImageTransport；改为使用 LIVMapper 拥有的有效节点。
- 把 Vikit common 的构建改为 ament 包，导出库及依赖；移除 FAST-LIVO2 的 Vikit 绝对安装布局假设，使用包导出的库。
- 统一使用 Sophus 1.22.10 的 CMake target。Sophus 与 SDK 将安装在项目目录，后续构建不需要 sudo。
- 为 launch 添加压缩图像解码开关：保留原默认行为；当前镜像已含 raw Image，运行脚本关闭解码节点。
- 以上修复已于 2026-10-15 通过 C++ 构建；完整运行验收见后续记录。

## 数据准备与验证

- 原 README 的 SharePoint 下载入口实际请求返回 HTTP 404，结果见 `dataset_page_check.txt`。
- 已从 DeepRoboticsLab 仓库链接的公开 Google Drive 镜像下载 `Retail_Street.db3`（1,995,526,144 字节）及元数据。该文件是社区转换后的 ROS 2 版本，没有原始 ROS 1 bag 的上游校验值；来源与本地 SHA-256 保留在 `../datasets/Retail_Street_mirror/`。
- SQLite `PRAGMA quick_check` 通过。
- 共 30,157 条消息，约 135.47 秒：27,447 条 IMU（约 202.6 Hz），1,355 条 LiDAR（约 10 Hz），1,355 条图像（约 10 Hz）。
- 镜像元数据声称使用 driver2 类型，但 SQLite 实际仍是 `livox_ros_driver/msg/CustomMsg`，不能只相信 YAML。
- 原版与 driver2 的 CustomMsg/CustomPoint 字段序列相同（将 Header 展开为 std_msgs/Header 后比较）。数据库内嵌的旧类型定义也已核对。
- 首尾输入消息的 CDR 数据结构检查通过；图像为 640×512、rgb8，正好匹配默认相机 1280×1024、scale=0.5 的实际工作尺寸，无需再缩放内参。
- 已生成 `../datasets/Retail_Street_humble/`：使用 Humble SQLite schema 3 与 metadata version 5，统一 Livox 类型名；原镜像不变。
- 对所有 30,157 条消息逐条通过 SQL 比较确认：消息负载、记录时间戳、topic ID 与消息 ID 完全不变。容器及类型标识的处理见该目录 `preparation.json`。
- 检查报告见 `retail_street_inspection.json` 与 `retail_street_humble_inspection.json`。
- 安装 ROS 后，`ros2 bag info` 已正确读取完整 30,157 条消息。数据完整性检查与 SLAM 运行验收分开记录。

## 已准备的执行脚本

1. `../scripts/01_install_system.sh`：用户终端执行，需要本地 sudo 密码。
2. `../scripts/02_build.sh`：构建 Sophus、SDK 及 ROS 工作空间，最多两个编译进程。
3. `../scripts/04_prepare_retail_bag.py`：已执行，生成兼容数据副本。
4. `../scripts/03_run.sh`：启动算法，默认不启用 RViz；设置 `RUN_RVIZ=True` 可打开 RViz。
5. `../scripts/05_play.sh`：播放准备好的序列，默认半速。

输入源相机标定与时间偏移沿用移植版默认配置；`config/retail_street.yaml` 开启轨迹与 PCD 保存。算法正常退出后点云保存到源码 `Log/pcd/`，轨迹保存到 `Log/result/Retail_Street.txt`。正式运行后再归档到实验目录。

## 初始检查与已解除的安装阻塞

- Shell 脚本语法、包 XML、launch Python 语法与 git diff 空白检查通过。
- 已实际尝试 `02_build.sh`，因 `/opt/ros/humble/setup.bash` 尚不存在而停止；日志见 `logs/02_build_20261015_132811.log`。
- 上述最初尝试没有产生构建或运行成功结果。
- 用户随后完成系统安装，当前已不受 sudo 阻塞。

## 2026-10-15 安装与构建验收

- 系统安装完成，日志：`logs/01_install_system_20261015_135023.log`。实际版本见 `system_versions.txt`。
- ROS 2 Humble、RViz、colcon、GCC 11.4、CMake 3.22.1 均可使用。
- `02_build.sh` 完成 Sophus 1.22.10 与 Livox SDK 的本地安装，并构建四个 ROS 包：livox_ros_driver2、vikit_common、vikit_ros、fast_livo。
- 四个 ROS 包构建耗时约 3 分 33 秒；日志：`logs/02_build_20261015_142534.log`。
- 构建有上游警告（RapidJSON pragma、unused variable、未使用的 CMake 变量、PCL CMake policy），没有编译或链接错误。
- `ros2 pkg executables fast_livo` 返回 `fast_livo fastlivo_mapping`。
- `ldd` 未发现缺失运行库，Vikit 与驱动消息库均从工作空间加载。
- Python driver2 消息、cv_bridge / OpenCV、NumPy、Matplotlib 可导入。

## 完整序列运行与退出问题排查

- 使用 `scripts/run_reproduction.py` 自动启动算法、回放整段数据、观察输入和输出、正常关闭并归档结果。
- 首次半速回放目录：`runs/20261015_143029/`。
- 已确认 IMU 初始化、视觉特征检索和更新、位姿、点云及 RGB 输出持续出现，未观察到运行 ERROR。
- 发现移植版 ROS Odometry / TF 使用节点系统时间，输入消息和原生保存的 TUM 轨迹使用传感器时间。验收脚本最初直接比较两者会产生错误失败判定，已改为按保存轨迹检查尾部覆盖；不会据此修改算法估计或消息负载。
- 时间基准差异作为已知接口限制保留；后续做传感器对齐时应另行处理。当前阶段采用原生传感器时间轨迹评估运行覆盖。

### 已确认的退出崩溃及修复

- 前三次完整序列均完成回放与轨迹/点云保存，但节点在退出时发生段错误，因此没有作为最终第一阶段通过结果。
- 最初看到主线程 `pipe_write`，曾推测为日志阻塞；进一步检查 `/proc/sys/kernel/core_pattern` 为 WSL 的 `wsl-capture-crash` 管道，说明该等待是崩溃捕获过程。此推测已纠正。
- 使用项目本地解包的 GDB（无需新增系统 sudo 安装）运行短数据片段，堆栈明确落在 `free → pcl::PointCloud<pcl::PointXYZINormal> → LIVMapper::~LIVMapper`。
- `pcl_alignment_probe.cpp` 对相同系统 PCL 分别用 `-march=native` 与基线 x86 编译：前者 Eigen alignment=32 / malloc_already_aligned=0，并在释放滤波结果时 SIGSEGV；后者 alignment=16 / malloc_already_aligned=1，正常退出。
- 最小实验日志：`shutdown_debug/pcl_probe_native.log`、`shutdown_debug/pcl_probe_portable.log`。
- 修复：删除主项目 x86 Release 编译选项中的 `-march=native`，保留 `-O3 -mtune=native -funroll-loops`，与 Ubuntu 预编译 PCL 的 Eigen 分配 ABI 保持一致。
- 自动运行脚本会检查映射进程的真实清洁退出，单独保存 `mapper_clean_exit`；仅仅 launch 返回 0 不视为映射节点退出成功。

## 最终验收：第一阶段完成

- 2026-10-15：修复后 GDB 短数据片段测试正常退出，证据 `shutdown_debug/gdb_after_alignment_fix.log`。
- 最终构建脚本完整返回 0，增量构建四个包均通过；日志 `logs/rebuild_alignment_final_console.log`。
- 运行目录 `runs/20261015_145245/`：原速回放完整 Retail_Street，player / launch 返回 0，映射节点保存点云后正常退出，`mapper_clean_exit=true`。
- 保存 1,348 个原生传感器时间位姿，时间跨度 134.700 秒；输入初始化间隔约 0.700 秒，LiDAR 传感器时间覆盖约 99.48%。
- 视觉模块完成 1,346 次带检索点的处理；降采样彩色地图 100,604 点，原始累计地图 18,851,419 点。
- 已检查轨迹有限值、时间严格递增、四元数归一化、序列覆盖、地图非空与坐标有限值；预览图已生成并目视检查。
- 输入观察器可能因 DDS 发现时机漏记初始消息，其计数不替代输入数据库总数和轨迹时间覆盖检查。
- 完整输出、实际参数、日志、指标、复现命令见 [阶段一结果](阶段一_结果.md)。没有进行 ATE/RPE 真值对照。
- 后续阶段未启动。

## 2026-10-20：补充单序列精度核验

已下载并校验 NTU 官方 eee_01 数据，保持传感器数据与时间不变转换为 Humble bag，沿用现有算法与 NTU 配置完成半速全段回放，保存 3,983 个位姿，映射节点正常退出。作者示例口径 RMSE 为本地 2.50 厘米、作者参考复算 2.71 厘米；使用官方教程真值插值方法交叉核对，为 2.98、3.10 厘米。

图像日志全部匹配；26 条未匹配 IMU 位于启动门控阶段，中途及尾部没有匹配缺口。参数服务关闭时的 SIGINT 退出码 -2 单独记录，不影响已完成的映射结果。完整结论、覆盖限制和复现命令见 [精度核验报告](阶段一_精度核验_eee01_20261020.md)。本次未修复 Retail Street 的既有问题，也未启动语义建图。

## 参考

- https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debs.html
- https://github.com/ros-infrastructure/ros-apt-source
- https://github.com/Robotic-Developer-Road/FAST-LIVO2/tree/humble
- https://github.com/Robotic-Developer-Road/rpg_vikit
- https://github.com/Livox-SDK/livox_ros_driver2
- https://github.com/Livox-SDK/Livox-SDK2
