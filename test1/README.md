# 三张 CVAT 试标

用户提供的三张 Oxford02 原图与 CVAT 1.1 掩膜标注，保存为初步试标资料，尚未完成修正与独立复核。

`annotations.xml` 从本机原始 `test1.zip` 按字节提取，未修改标注。XML SHA-256：`e89b889e4978c7e5e6efea04b018783d69afd63f931bb9b2d3d1b1ff180e6569`。ZIP 和 Windows Zone.Identifier 元数据不随仓库分发。

三张图像的尺寸均为 720×540，哈希与原转场抽样包一致。逐图结构检查、语义问题和可视化见[检查报告](../experiments/phase2_20261024/full/annotation_review/test1_r1/README.md)。当前不将这些标注视为已验收真值，不据此报告正式 mAP 或 mIoU。
