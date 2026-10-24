# 集成诊断与保留尝试

这些记录用于解释适配器的最终行为，未通过的尝试不计入短段验收。

| 尝试 | 发现 / 结果 | 处置 |
|---|---|---|
| `captures/oxford02_16s` | 初始沙箱禁止 ROS 2 `getifaddrs`/UDP 套接字，在正式回放前停止 | 保留切片与 controller_note；获准访问后用新目录采集 |
| `runs/oxford02_author_probe` | 作者滤波把抽查帧 2678/2684 个有效深度全部剔除；保存的地图无对象。运行后检查代码误以为上游仍保留 GPU backbone，但 `run()` 已主动删除它，导致检查器 AttributeError | 保留原 runtime 的失败状态；修正为重载保存的检查点，后续运行验证。该目录不算成功集成 |
| `runs/oxford02_valid_mask` | 只适配深度滤波，158 帧正常遍历；最终只有 638 地图点、1 个对象，地图停在首帧 | 定位原重复点抑制会腐蚀缺测邻域；添加仅针对真实匹配像素的邻域抑制。该诊断不满足多帧建图验收 |
| `pipeline_fpv2_00` | shell 的 `set -u` 导致 ROS Humble setup.bash 访问未定义环境变量时报错，尚未采集 | 改为 source ROS 环境后再启用 nounset，用 `pipeline_fpv2_00_r2` 完整重跑 |
| `runs/oxford02_sparse`、`pipeline_fpv2_00_r2/ovo` | 两项掩膜适配后均能持续建图，但保留作者 BF16 几何关联时，已赋对象 ID 的点仅 4443/40984 与 124/41544 | 同帧投影自匹配复核发现 BF16 数值误差会破坏 5 cm 匹配。仅几何关联固定 FP32，复用相同输入运行 `runs/*_sparse_fp32` 作为最终短段验证 |

最终选用 `depth_policy=valid_mask`、`mapping_policy=valid_mask`、`geometry_precision=float32`，两项掩膜处理都不创建缺测深度。模型权重、神经网络推理精度、语义融合方式、关联阈值、建图/分割频率与第一阶段冻结配置相同；三维几何关联单独关闭 BF16 autocast。基线源码、补丁、二进制和旧结果均保留。

上游日志中的 `Avg t_seg: nan` 等来自没有填充的统计列表；它们不用于验收或性能比较。最终地图、对象描述符、文本查询分数另行检查为有限值。
