# 阶段一运行汇总

状态：`completed_available_dataset_scope`。更新：2026-10-23T16:45:04.421587+08:00。

| 运行 | 状态 | 位姿 | 最近邻位置 RMSE (m) | 插值 RMSE (m) | 峰值 GiB | 输入回调完整 |
|---|---|---:|---|---|---:|---|
| [ntu_eee01_r1](runs/ntu_eee01_r1/summary.json) | playback_completed | 3983 | prism: 0.02512 | prism: 0.03182 | 4.27 | True |
| [retail_r1](runs/retail_r1/summary.json) | playback_completed | 1351 | — | — | 0.77 | True |
| [fpv2_01_r1](runs/fpv2_01_r1/summary.json) | playback_completed | 7486 | imu_direct: 0.16883; prism_compensated: 0.16892 | imu_direct: 0.19880; prism_compensated: 0.19890 | 8.02 | True |
| [oxford02_r1](runs/oxford02_r1/summary.json) | playback_completed | 11763 | base: 0.29705 | base: 0.29703 | 5.56 | True |
| [oxford05_r1](runs/oxford05_r1/summary.json) | playback_completed | 3959 | base: 0.14723 | base: 0.14577 | 2.33 | True |
| [fpv2_00_r1](runs/fpv2_00_r1/summary.json) | playback_completed | 9834 | imu_direct: 0.16858; prism_compensated: 0.16847 | imu_direct: 0.17513; prism_compensated: 0.17501 | 10.66 | True |
| [ntu_eee01_r2](runs/ntu_eee01_r2/summary.json) | playback_completed | 3983 | prism: 0.02493 | prism: 0.03155 | 4.32 | True |
| [ntu_eee01_r3](runs/ntu_eee01_r3/summary.json) | playback_completed | 3983 | prism: 0.02504 | prism: 0.03157 | 4.32 | True |
| [oxford02_r2](runs/oxford02_r2/summary.json) | playback_completed | 11763 | base: 0.29513 | base: 0.29511 | 5.51 | True |
| [oxford02_r3](runs/oxford02_r3/summary.json) | playback_completed | 11763 | base: 0.29698 | base: 0.29697 | 5.56 | True |
| [fpv2_00_r2](runs/fpv2_00_r2/summary.json) | playback_completed | 9834 | imu_direct: 0.16778; prism_compensated: 0.16770 | imu_direct: 0.17350; prism_compensated: 0.17340 | 10.63 | True |
| [fpv2_00_r3](runs/fpv2_00_r3/summary.json) | playback_completed | 9832 | imu_direct: 0.16479; prism_compensated: 0.16478 | imu_direct: 0.16843; prism_compensated: 0.16833 | 10.60 | True |

Retail 无可核验 GT，不能给出定位精度。FPV2 的 IMU / 棱镜解释并列保留。最近邻与插值具有不同匹配覆盖，不能互相替代。

复现范围、限制与协议见 [本目录说明](README.md)；完整数值、重复性与验收检查见 [results.json](results.json)。
