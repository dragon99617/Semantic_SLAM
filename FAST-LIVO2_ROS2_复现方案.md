# FAST-LIVO2 ROS 2 复现方案

保存时间：2026-10-15。以下保存上一轮回复内容；实际执行记录见 `experiments/FAST-LIVO2_复现记录.md`。

建议按 **“安装环境 → 编译移植版 → 跑官方数据 → 保存结果并验收”** 来做，先完成一条录制序列的复现。

我检查了你的 WSL：**Ubuntu 22.04，内存约 7.6 GiB，目前没有 ROS 2、colcon、CMake 和 C++ 编译工具**。下面是针对这台机器的实施顺序；当时尚未执行安装或编译。

## 1. 选定仓库并固定版本

建议以社区仓库 [Robotic-Developer-Road/FAST-LIVO2 的 humble 分支](https://github.com/Robotic-Developer-Road/FAST-LIVO2/tree/humble)为起点。原作者仓库目前提供的是 ROS 1 的构建流程，两者的安装说明要分别看。[原版说明](https://github.com/hku-mars/FAST-LIVO2)

工作目录建议放在 Linux 文件系统内：

```text
/home/hxy/SLAM_project/
├── fast_livo2_ws/       # ROS 2 工作空间
│   └── src/
├── third_party/        # 需要源码构建的依赖
├── datasets/           # 原始及转换后的数据
└── experiments/        # 配置、日志、轨迹和点云
```

拉取主项目时明确选择分支：

```bash
mkdir -p ~/SLAM_project/fast_livo2_ws/src
cd ~/SLAM_project/fast_livo2_ws/src

git clone --branch humble \
  https://github.com/Robotic-Developer-Road/FAST-LIVO2.git

git -C FAST-LIVO2 rev-parse HEAD
```

将提交号记录下来，后续修复也保留补丁。

## 2. 安装 ROS 2 Humble 和依赖

Ubuntu 22.04 对应 Humble。按照 [ROS 官方安装说明](https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debs.html)配置软件源，再安装 Desktop 版，以便使用 RViz。

需要准备的依赖包括：

| 类别 | 依赖 |
|---|---|
| 编译工具 | GCC/G++、CMake、colcon、rosdep |
| 数学及视觉库 | Eigen、PCL、OpenCV、Sophus、fmt |
| ROS 组件 | cv_bridge、image_transport、压缩图像插件、pcl_ros、TF |
| 配套源码 | ROS 2 版 Vikit、livox_ros_driver2、Livox-SDK2 |

主项目直接依赖 `livox_ros_driver2`，即使回放数据，也需要能构建其消息接口；不需要连接真实雷达。[构建依赖](https://github.com/Robotic-Developer-Road/FAST-LIVO2/blob/humble/CMakeLists.txt)

## 3. 处理兼容问题，再编译

这里需要实际调试，不能保证照抄 README 就通过。我核对代码后，发现两处需要优先处理：

- FAST-LIVO2 的 CMake 硬编码了 Vikit 库的安装路径。
- 配套 Vikit 当前把 `vikit_common` 作为普通 CMake 库构建，安装方式与主项目的路径假设需要统一。

因此要统一 **Sophus 版本、Vikit 安装位置和 CMake 链接方式**。[主项目 CMake](https://github.com/Robotic-Developer-Road/FAST-LIVO2/blob/humble/CMakeLists.txt)、[Vikit 说明](https://github.com/Robotic-Developer-Road/rpg_vikit)

依赖准备好后，限制编译并行度，先控制内存占用：

```bash
source /opt/ros/humble/setup.bash
cd ~/SLAM_project/fast_livo2_ws

MAKEFLAGS="-j2 -l2" CMAKE_BUILD_PARALLEL_LEVEL=2 \
  colcon build --symlink-install --executor sequential

source install/setup.bash
ros2 pkg executables fast_livo
```

验收点是能找到 `fastlivo_mapping`，所有必要依赖构建成功。

## 4. 用官方数据跑通完整 LIVO

第一条建议选择移植版 README 使用的 `Retail_Street.bag`，从原项目提供的 FAST-LIVO2 数据入口获取。它最贴近该移植版已有的演示流程。[数据及示例说明](https://github.com/Robotic-Developer-Road/FAST-LIVO2/blob/humble/README.md)

原始 ROS 1 bag 可以用 `rosbags` 转换：

```bash
rosbags-convert \
  --src datasets/Retail_Street.bag \
  --dst datasets/Retail_Street_ros2
```

该工具支持 ROS 1 与 ROS 2 bag 转换。[转换工具文档](https://ternaris.gitlab.io/rosbags/topics/convert.html)

**转换后还要检查 Livox 自定义消息类型**：原数据可能是 `livox_ros_driver/CustomMsg`，算法订阅的是 `livox_ros_driver2/msg/CustomMsg`。处理时应核对消息字段，并保持 bag 数据库与元数据一致，不能只看到转换成功就直接播放。

运行前还要核对图像、LiDAR、IMU 话题，以及相机内参、外参和时间偏移。该 launch 已包含压缩图像解码节点。[启动配置](https://github.com/Robotic-Developer-Road/FAST-LIVO2/blob/humble/launch/mapping_aviz.launch.py)

准备完成后，两个终端分别启动：

```bash
# 终端 1：加载 ROS 和工作空间环境后
ros2 launch fast_livo mapping_aviz.launch.py use_rviz:=True
```

```bash
# 终端 2：加载相同环境后
ros2 bag play ~/SLAM_project/datasets/Retail_Street_ros2 --rate 0.5
```

先半速排查同步和处理能力，再尝试原速。

## 5. 明确“复现完成”的标准

第一阶段应交付：

- 一条完整序列运行结束，节点没有崩溃。
- 确认图像、LiDAR 和 IMU 都参与处理。
- 持续输出位姿、轨迹和地图，保存轨迹与点云。
- 记录提交号、依赖版本、配置、运行命令和日志。
- 数据有匹配真值时评估 ATE/RPE；没有真值时，明确结果属于运行复现。

当前第一步就是**装好 Humble 和编译工具，再完成主项目与配套依赖的构建**。官方序列跑通后，再接我们关心的室内外转场数据。
