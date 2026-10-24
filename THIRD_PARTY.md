# 第三方源码与数据

本仓库不把第三方算法视为原创实现；修复补丁属于对应上游代码的修改，应遵守相应上游许可证。没有为所有第三方内容统一授予新的许可证。

固定提交、原始远程地址及本机路径见：

- `experiments/source_versions.json`：FAST-LIVO2 ROS 2 移植版、rpg_vikit、Livox 驱动、Sophus、Livox-SDK2。
- `experiments/multibench_20261020/source_versions.json`：OVO、SAM2、OSMa-Bench、HaDaGe、M2DGR 资料与 SigLIP 模型。
- `experiments/local_patches.json`、`experiments/patches/`、`experiments/multibench_20261020/OVO_memory_compat.patch`：本项目使用的修复与适配。

主要来源：

- [FAST-LIVO2 原项目](https://github.com/hku-mars/FAST-LIVO2)、[ROS 2 移植版](https://github.com/Robotic-Developer-Road/FAST-LIVO2)
- [OVO](https://github.com/tberriel/OVO)、[SAM2](https://github.com/facebookresearch/sam2)
- [OSMa-Bench](https://github.com/be2rlab/OSMa-Bench)、[HaDaGe](https://github.com/warmhammer/habitat_data_generator)
- [NTU-VIRAL](https://ntu-aris.github.io/ntu_viral_dataset/)、[M2DGR](https://github.com/SJTU-ViSYS/M2DGR)
- [Oxford Spires](https://dynamic.robots.ox.ac.uk/datasets/oxford-spires/)、[作者数据仓库](https://huggingface.co/datasets/ori-drs/oxford_spires_dataset)：所选版本与逐文件哈希见 `experiments/oxford_spires/download_manifest.json`。
- [Replica 预处理数据](https://cvg-data.inf.ethz.ch/nice-slam/data/Replica.zip)、[OVO 数据说明](https://github.com/tberriel/OVO/blob/main/data/input/ReadMe.md)
- [OSMa 数据](https://huggingface.co/datasets/warmhammer/OSMa-Bench_dataset)、[Augmented ReplicaCAD](https://huggingface.co/datasets/warmhammer/Augmented_ReplicaCAD_dataset)

数据与模型由各自发布方提供。本仓库仅提供来源、校验记录和使用方法，不重新分发原始数据、模型权重或论文 PDF。ScanNet 已按用户要求换为 Replica，不包含 ScanNet 数据。
