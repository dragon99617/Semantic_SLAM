# Oxford 转场独立语义标注包 v1

本包只包含原始输入去畸变图像与冻结转场区间，不含 OVO 预测。**当前没有任何已完成语义标注。**

- 每个转场前/中/后各固定抽 3 帧（−25/−20/−15、−5/0/+5、+15/+20/+25 秒），不按模型表现择样。序列截断/缺帧见 manifest.json；重叠窗口不视为独立事件。
- 在 images/ 的原尺寸 PNG 上标注。使用 ontology.json 的 0–14 类；无法辨识、严重过曝/欠曝、遮挡边界及 valid_masks/ 外区域设为 255。
- 以单通道 uint8 PNG 保存到 labels/<sample_id>.png。禁止缩放、调色板 RGB 或 JPEG 标签。label_templates/ 全为 255，仅是空模板，不能当作完成标注。
- 更新 tasks.csv：annotation_status 改为 reviewed，填写不同的 annotator 与 reviewer；若整帧确实无法标注，填写 unscorable 并在 notes.json 说明原因，不能静默删帧。
- 只使用图像及相邻输入帧帮助判读，不查看 OVO 预测后修标签。一般优先专门类别（门、窗、柱、楼梯），剩余墙体归 wall_building；玻璃/门洞后的背景按可辨对象标，无法判读则 255。
- 标注冻结后应报告：每事件/每窗口像素准确率、mIoU、逐类 IoU、未预测比例；未知预测应计入假阴性，不可只挑已赋语义点。另报稀疏几何支持范围内的条件指标与覆盖率。实例 ID 保持率需要额外独立对象标注，本包不据此判定。

在独立标注完成前，语义精度验收状态始终为 awaiting_independent_annotation。
