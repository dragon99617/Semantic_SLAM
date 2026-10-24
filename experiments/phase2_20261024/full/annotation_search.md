# Oxford 语义真值公开检索

检索日期：2026-10-24。目标：当前 Christ Church 02（2024-03-18）和 05（2024-03-20）的独立语义/实例标注。

**在核对的官方发布、衍生版本与相关语义研究中，未找到可直接用于这两条序列语义精度验收的公开独立标签。** 这不是对所有未检索资源不存在的证明。

| 来源 | 核对结果 | 适用范围 |
|---|---|---|
| [Oxford 官方项目页](https://dynamic.robots.ox.ac.uk/datasets/oxford-spires/)及 [Ground Truth 文档](https://github.com/ori-drs/oxford_spires_dataset/wiki/Ground-Truth) | 公布 TLS 几何地图与配准得到的 base 轨迹；没有列出语义/实例标签 | 几何验收可用 |
| [Voxel51 衍生数据集说明](https://huggingface.co/datasets/Voxel51/oxford-spires-multimodal) | 发布者明确说明没有物体检测、分割或分类标注，该封装也未新增此类真值 | 不是带语义真值的替代版 |
| [Aazeus/oxford-spires-part 文件清单](https://huggingface.co/datasets/Aazeus/oxford-spires-part/tree/main) | Hub API 返回的文件只有 README、gitattributes 和 calibration-sequences-2024-06.zip | 标定子集，不是这两条 Christ Church 序列的语义标注 |
| [Neural-MMGS 方法正文](https://arxiv.org/html/2509.17762v1) III-A | 使用 SAM 分割和 CLIP 特征生成语义训练监督 | 模型生成监督，不是独立人工语义真值 |
| [Incremental Semantics-Aided Meshing](https://arxiv.org/html/2604.09478v1) §5.2 | 使用 Christ Church sequence 2；定量报告几何重建准确度、完整度与 F1 | 不能把几何指标当作语义 mIoU |

还检查了官方数据组织、相关 GitHub 搜索，以及精确序列名与 semantic/labels/annotations 的组合检索。搜索中其他带 semantic 字样的数据集或几何结果没有作为 Oxford02/05 的标注复用。

Hugging Face 在线 API 搜索 spires 返回的三个数据仓库均已核对；紧凑清单保存在 [hub_search_spires.json](hub_search_spires.json)，标定子集的固定版本和文件清单见 [hub_oxford_part_info.json](hub_oxford_part_info.json)。没有为查标签下载整个大体积标定压缩包。

用户已选择“暂无标注，先生成转场标注包”。因此将语义精度验收保持为 awaiting_independent_annotation，独立标注、复核并冻结后才计算语义指标。未发送邮件或联系作者。
