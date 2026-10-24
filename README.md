# Semantic_SLAM：阶段性复现与评测

**FAST-LIVO2 → OVO 离线链路已通过 Oxford02/05 全序列工程验收。** 原始 RGB、估计相机位姿与点云投影深度经同步后交给 OVO；第一阶段的独立基线继续保留。见[短段集成记录](experiments/phase2_20261024/README.md)和[Oxford 全序列报告](experiments/phase2_20261024/full/README.md)。13 个转场窗口已核对，114 张图像的独立标注包已准备；转场语义精度仍待独立标注，第二阶段整体尚未完成。

按当前“多数据集原方法基线，为后续消融准备”的阶段目标，**已完成选定可用数据的第一阶段基线**（M2DGR 因 429 未运行）。详见[本次执行与结果](experiments/阶段一_执行与结果_20261023.md)、[逐次汇总](experiments/phase1_20261022/results.md)和[结构化状态](experiments/phase1_baseline_inventory_20261022.json)；[最初盘点](experiments/阶段一_基线盘点与后续安排_20261022.md)保留为执行前快照。

## 已完成与待完成

| 工作 | 当前状态 |
|---|---|
| NTU_VIRAL eee_01 | 完整运行与评测通过，3983 位姿；prism 最近邻/插值 RMSE 0.0251/0.0318 m |
| Retail_Street Retail_Street | 完整运行与评测通过，1351 位姿；无可核验 GT，仅工程回归 |
| FusionPortableV2 ugv_transition01 | 完整运行与评测通过，7486 位姿；imu_direct 最近邻/插值 RMSE 0.1688/0.1988 m；prism_compensated 最近邻/插值 RMSE 0.1689/0.1989 m |
| OxfordSpires 2024-03-18-christ-church-02 | 完整运行与评测通过，11763 位姿；base 最近邻/插值 RMSE 0.2970/0.2970 m |
| OxfordSpires 2024-03-20-christ-church-05 | 完整运行与评测通过，3959 位姿；base 最近邻/插值 RMSE 0.1472/0.1458 m |
| FusionPortableV2 ugv_transition00 | 完整运行与评测通过，9834 位姿；imu_direct 最近邻/插值 RMSE 0.1686/0.1751 m；prism_compensated 最近邻/插值 RMSE 0.1685/0.1750 m |
| OVO 参考位姿语义基线 | 6 组发布数据 +12 组本地配对条件，23,340 RGB 帧；独立复核保留 |
| 核心几何重复性 | NTU、Oxford02、FPV2_00 各完成三次，均值与样本标准差已汇总 |
| M2DGR door01 / door02 | 官方入口 HTTP429，无可用本地数据，未评测 |
| FAST-LIVO2 → OVO 接口 | 两种相机短段与 Oxford02/05 全序列工程验收通过；13 个转场窗口已核对，独立语义标注及精度结论待完成 |

语义部分共完成 18 组完整运行（6 组发布数据、12 组配对条件），累计遍历 23,340 RGB 帧。已从保存的逐点预测独立重建混淆矩阵，核对官方指标公式，并检查配对数据的帧数、哈希与共享真值。三场景子集与单种子结果不代表完整数据集或统计显著性。

## 报告与证据

- [第一阶段再次复核](experiments/phase2_20261024/phase1_recheck_final.json)、[第二阶段集成与复跑](experiments/phase2_20261024/README.md)、[集成诊断](experiments/phase2_20261024/diagnostics.md)
- [Oxford 全序列及转场工程验收](experiments/phase2_20261024/full/README.md)、[公开语义标注检索](experiments/phase2_20261024/full/annotation_search.md)、[独立标注说明](experiments/phase2_20261024/full/annotations/oxford_transitions_v1/README.md)
- [语义评测口径与待完成项](docs/SEMANTIC_EVALUATION.md)、[三张 CVAT 试标](test1/README.md)、[试标检查报告](experiments/phase2_20261024/full/annotation_review/test1_r1/README.md)
- [阶段一复现方案](FAST-LIVO2_ROS2_复现方案.md)、[工程复现记录](experiments/FAST-LIVO2_复现记录.md)
- [当前阶段目标与基线盘点](experiments/阶段一_基线盘点与后续安排_20261022.md)、[结构化状态清单](experiments/phase1_baseline_inventory_20261022.json)
- [NTU-VIRAL 精度核验](experiments/阶段一_精度核验_eee01_20261020.md)
- [多数据集评测报告](experiments/多数据集评测报告_20261020.md)
- [OSMa 配对光照报告](experiments/OSMa配对光照评测_20261020.md)
- [18 组结果独立核验](experiments/multibench_20261020/final_result_checks.json)、[12 组配对数据核验](experiments/multibench_20261020/paired_data_checks.json)
- [原研究计划与综述](综述报告.md)、[数据集选择依据](experiments/项目最小数据集评测建议_20261020.md)

![语义场景评测](experiments/multibench_20261020/semantic_scores.png)

## 恢复源码与复现

适用环境为 Ubuntu 22.04 / ROS 2 Humble。历史语义环境为 Python 3.11、PyTorch 2.5.1 + CUDA 12.4、RTX 4060 Laptop 8 GiB；完整环境、模型和源码版本记录在 `experiments/` 中。

```bash
git clone https://github.com/dragon99617/Semantic_SLAM.git
cd Semantic_SLAM

# 下载固定提交的第三方源码，检查并应用本项目补丁；不下载数据或模型。
python3 scripts/bootstrap_sources.py --profile all

# ROS 2 依赖安装需要在自己的终端输入 sudo 密码。
bash scripts/01_install_system.sh
bash scripts/02_build.sh
```

语义环境与 ROS 环境隔离：

```bash
bash scripts/setup_semantic_env.sh
```

随后按[复现指南](docs/REPRODUCING.md)准备数据与模型，再运行对应脚本。**这些步骤不是一键完成所有数据授权、下载或模型准备。** 语义脚本默认离线读取已准备好的模型缓存；历史配置内的 `/home/hxy/SLAM_project` 路径仅记录当时环境。

## 仓库包含范围

包含项目脚本、固定版本清单、第三方修复补丁、配置、报告、图表、逐类别指标与小型混淆矩阵。第三方源码通过固定提交恢复，不直接复制到本仓库。

数据集、论文 PDF、模型权重、Python/Conda 环境、ROS 构建目录、大体积地图/检查点及逐点预测留在原本机目录。报告中提到这些本地产物，不代表 GitHub 中附带它们。参见[第三方与数据来源](THIRD_PARTY.md)及[复现指南](docs/REPRODUCING.md)。

114 张图像的完整标注包 ZIP、原始图像与有效掩膜保留本机；仓库保存抽样清单、哈希、标注说明和联系表预览。`test1/` 额外保存用户已试标的三张图像及原始 CVAT XML；这些标注尚未完成修正与独立复核，不能当作已验收真值。
