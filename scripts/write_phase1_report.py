#!/usr/bin/env python3
"""Publish an evidence-based phase-one status/report; completion is gated by checks."""
import datetime,hashlib,json
from pathlib import Path
from summarize_phase1 import build,ROOT,BASE


def render(r):
    complete=r['status']=='completed_available_dataset_scope';rows=r['geometry'];done=[x for x in rows if x.get('evaluated')];primary=[x for x in done if x['repeat']==1]
    name='阶段一_执行与结果_20261023.md';p=ROOT/'experiments'/name
    lines=['# 阶段一执行与结果','',f"更新：{r['updated_at']}。",'',('**已完成选定可用数据的原方法基线与验收。**' if complete else f'**阶段一仍在执行：已完成评测 {len(done)}/12 个几何运行，不能视为全部完成。**')+' 六条可用几何序列之外，M2DGR door01/02 的官方入口仍返回 HTTP 429，未运行，作为明确的可用性例外保留。','', 'FAST-LIVO2 与 OVO 分别建立基线。未实现 FAST-LIVO2→OVO 接口，没有把参考位姿语义成绩当作融合系统结果。','', '## 几何结果','', '| 序列 / 参考点 | 位姿数 | 最近邻 RMSE (m) | 最近邻匹配率 | 插值 RMSE (m) | 插值匹配率 | mapper 峰值 RSS GiB |','|---|---:|---:|---:|---:|---:|---:|']
    if r['status']=='paused_user_requested':
        lines[4]=f'**按用户要求已保存并停止：完成评测 {len(done)}/12 个几何运行，六条序列首轮全部完成。** 剩余四次完整重复运行；中断轮次及日志已归档，恢复时从该轮开头重跑。M2DGR door01/02 官方入口 HTTP 429，未运行。'
    for x in primary:
        for variant,v in (x['position_metrics'] or {'无可核验 GT':None}).items():
            score=f"{v['nearest_rmse_m']:.5f} | {v['nearest_matched_fraction']:.1%} | {v['interpolated_rmse_m']:.5f} | {v['interpolated_matched_fraction']:.1%}" if v else '— | — | — | —'
            lines.append(f"| {x['sequence']} / {variant} | {x['poses']} | {score} | {x['peak_mapper_rss_GiB']:.2f} |")
    oxford=[x for x in primary if x.get('RPE')]
    if oxford:
        lines+=['','| Oxford 1秒 RPE | 平移 RMSE (m) | 旋转 RMSE (deg) | 位姿对数 |','|---|---:|---:|---:|']
        for x in oxford:
            rp=x['RPE'];lines.append(f"| {x['sequence']} | {rp['translation_m']['stats']['rmse']:.5f} | {rp['rotation_deg']['stats']['rmse']:.5f} | {rp['translation_m']['pairs']} |")
        lines+=['','当前 evo 不直接支持按秒筛选 RPE 位姿对，因此按参考时刻选取 +1 s 最近邻（±0.05 s），连续对的下一起点为上一终点，再使用 evo 的标准 TUM 相对 SE(3) 误差公式。逐对时刻与误差已导出；Oxford02 的独立 NumPy/SciPy 复核通过。']
    lines+=['','全程只做一次 SE(3) 对齐，比例固定为 1，额外时间偏移固定为 0。最近邻容差 0.01 s；插值要求前后参考点各距估计时刻≤0.1 s，两种匹配口径分别报告。每次运行的标准 evo 结果、完整匹配 CSV、地图预览、参数与轨迹均在 [运行汇总](phase1_20261022/results.md) 中索引。','', 'FPV2 的参考点尚未被发布资料唯一说明，保留 IMU 直接位置与标定棱镜两种解释，不按分数择优。00/01 位置参考分别存在最长约 70 s / 37.8 s 的缺口；缺测窗口不跨长间断插值。作者工具的 01 示例时刻与下载 GT 相差 +0.06 s，本批次主指标仍固定采用下载 GT 的原始时刻，不搜索测试时移。','', 'NTU 与 FPV2 的 GT 四元数是占位值，不计姿态误差和标准全位姿 RPE。Oxford 的 GT 为 LiDAR 对 Leica TLS 配准得到的 base 位姿，已将 IMU 估计完整变换到 base，支持 1 s RPE；它不属于独立动作捕捉真值。Oxford02 末尾约 30 s 缺发布 GT，运行覆盖与真值覆盖分别保留。','', '## 重复性','', '| 核心序列 | 已完成次数 | 参考点 | 最近邻 RMSE 均值 ± 样本标准差 (m) | 插值 RMSE 均值 ± 样本标准差 (m) |','|---|---:|---|---|---|']
    for seq,g in r['repeatability'].items():
        for variant,v in (g['position_metrics'] or {'待运行':None}).items():
            def fmt(d):return f"{d['mean']:.5f} ± {d['sample_std']:.5f}" if d['sample_std'] is not None else f"{d['mean']:.5f}（单次）"
            lines.append(f"| {seq} | {g['completed_repeats']} / 3 | {variant} | {fmt(v['nearest_rmse_m']) if v else '—'} | {fmt(v['interpolated_rmse_m']) if v else '—'} |")
    lines+=['','这是同数据、同估计器版本的初始重复性检查，不是跨场景统计显著性证明。历史旧补丁运行不计入本批次重复次数。','', '## 转场与地图诊断','', '从四条序列的输入图像独立标出 17 个边界区间，区分封闭室内、室外和半开放回廊。原图接触表、索引及冻结区间在 [input_annotations](phase1_20261022/input_annotations/)；按固定的前 [-30,-10]、中 [-10,10]、后 [10,30] s 窗口复用全程对齐。边界并非精确物理门槛真值，重叠、截断和参考缺测均标出。','', '逐次 `diagnostics.json` 保存窗口位置误差、参考覆盖、灰度/饱和/黑像素比例、视觉检索点数、外观地图点更新数与位姿间隔。正检索不等于独立核验的 EKF 接受；固定 0.5 m、连续 5 s 的返回阈值仅是探索性诊断，不代表实现了重定位或恢复机制。','', '| 运行 | 标注边界数 | 位姿间隔 >0.5s 次数 | 回调接收完整 | 原生视觉日志对应核验 |','|---|---:|---:|---|---|']
    for x in primary:lines.append(f"| {x['id']} | {x.get('transition_events',0)} | {x.get('pose_gaps_over_0_5_s','—')} | {x['input_callbacks_exact']} | {x.get('visual_trace_valid',False)} |")
    lines+=['','每条首轮收集观测到的 registered 点流，按 10 cm 体素保存累计输出地图。它不是内部完整地图或全部原始点的导出。Oxford 另以全程轨迹对齐变换检查输出点到 5 cm TLS 参考的距离，受参考覆盖、遮挡和动态物体影响；不宣称完整地图完整性。','', '## 语义原方法基线','', '| 范围 | 完整运行数 | 场景平均 mIoU |','|---|---:|---:|','| Replica office0 / office1 / room0 | 3 | 25.34% |','| OSMa 发布数据三个布局 baseline | 3 | 27.21% |','| OSMa 本地三个布局 × 四光照 | 12 | baseline 27.69%，dynamic 24.04% |','', '共 18 组、23,340 RGB 帧，沿用作者 OVO、SAM2.1 large、SigLIP-384 与发布融合权重。代码、资源适配补丁、三个权重和紧凑结果产物已重新核对，逐点混淆矩阵的既有独立复核记录仍有效。FP16/分批设置已披露，未证明与作者其他数值精度设置等价。六组发布数据与十二组本地光照实验分开报告；seed=0 单次，属于输入条件鲁棒性实验，未构成新模块消融。','', '## 版本、传输与失败记录','', '本机已独立保存冻结 mapper 与项目库，见 [frozen_runtime_manifest.json](phase1_20261022/frozen_runtime_manifest.json)。冻结估计器、标定和模型见 [baseline_freeze.json](phase1_20261022/baseline_freeze.json)、[协议哈希](phase1_20261022/protocol_hashes.json)。ROS2 兼容补丁涉及构建、BGR8 图像所有权、鱼眼参数加载/服务发现、传感器时间输出与精确回调日志，未改变状态估计公式。实际 ROS 参数已与请求值核对，未暴露 YAML 项的源码固定值/参数别名也有逐次记录。','', '回放名义速率 0.5，输入可靠传输并在启动发现后恢复；积压超过阈值时暂停、排空后恢复，动作逐次记录。NTU/Retail 首轮未达到新增节流阈值且输入接收精确匹配，因此保留。原生编译宏 MP_PROC_NUM=4；OMP_NUM_THREADS=2 不是强制两线程上限。运行墙钟耗时不作为实时性或论文速度排名。','', 'RX 日志只证明消息进入接收回调，不能证明每个点都进入有效更新。原生 TUM 与 ROS Odometry 时间已统一；它们均为 LIO 更新后、同帧 VIO 反馈前的状态，视觉反馈仍影响后续状态。','', '保留的失败/中断完整尝试：']
    for f in r['retained_failed_full_attempts']:lines.append(f"- [{Path(f['run']).name}]({Path(f['run']).relative_to('experiments')}/controller_note.json)：{f['status']}。{f['controller_note'].get('reason','') if f['controller_note'] else f['error']}")
    lines+=['','启动诊断失败、被控制器停止的短段也保留在 diagnostics/，不算正式成功运行或算法精度失败。','', '## 验收与下一阶段','']
    lines.extend(f"- {'通过' if passed else '待完成'}：`{check}`。" for check,passed in r['checks'].items())
    lines+=['', '当前阶段完成后可开展 FAST-LIVO2→OVO 的同步、坐标变换、深度投影、估计位姿输入和配对评测，具体输入契约见 [第二阶段交接](phase1_20261022/phase2_handoff.md)。真实转场语义/实例精度仍需合适标注，新增可靠性/恢复/复核模块及正式组件消融另行实现。','', '复现入口：[批次说明及命令](phase1_20261022/README.md)、[完整机器可读结果](phase1_20261022/results.json)。数据、模型、大地图与原始点流保留在本机，未随 Git 分发。','']
    p.write_text('\n'.join(lines))
    inventory={'recorded_date':'2026-10-23','phase1_status':r['status'],'report':str(p.relative_to(ROOT)),'counts':{'completed_geometry_runs':len(done),'planned_geometry_runs':12,'completed_geometry_sequences':len(primary),'geometry_sequences_with_position_reference':sum(bool(x['position_metrics']) for x in primary),'semantic_runs':18,'semantic_rgb_frames':23340},'geometry':rows,'repeatability':r['repeatability'],'semantics':r['semantics'],'checks':r['checks'],'baseline_freeze':'experiments/phase1_20261022/baseline_freeze.json','initial_audit_snapshot':'experiments/phase1_20261022/initial_inventory_snapshot.json','retained_failed_attempts':r['retained_failed_full_attempts'],'optional_unavailable':{'door01':'experiments/phase1_20261022/m2dgr_availability.json','door02':'experiments/phase1_20261022/m2dgr_door02_availability.json'},'phase2_pending':['FAST-LIVO2 to OVO interface and estimated-pose semantics'],'later_research_pending':['New reliability/recovery/review mechanisms, component ablations, semantic multi-seed and wider benchmark scope']}
    (ROOT/'experiments/phase1_baseline_inventory_20261022.json').write_text(json.dumps(inventory,indent=2)+'\n')
    readme=ROOT/'README.md';s=readme.read_text();start=s.index('按当前“');end=s.index('\n\n## 已完成与待完成',start)
    text=f"按当前“多数据集原方法基线，为后续消融准备”的阶段目标，{'**已完成选定可用数据的第一阶段基线**（M2DGR 因 429 未运行）' if complete else f'**第一阶段正在执行，几何评测已完成 {len(done)}/12 次**'}。详见[本次执行与结果]({p.relative_to(ROOT)})、[逐次汇总](experiments/phase1_20261022/results.md)和[结构化状态](experiments/phase1_baseline_inventory_20261022.json)；[最初盘点](experiments/阶段一_基线盘点与后续安排_20261022.md)保留为执行前快照。"
    if r['status']=='paused_user_requested':
        text=text.replace(f'**第一阶段正在执行，几何评测已完成 {len(done)}/12 次**',f'**第一阶段已按用户要求保存并停止，几何评测完成 {len(done)}/12 次**')
    s=s[:start]+text+s[end:];head=s.index('| 工作 | 当前状态 |');tail=s.index('\n\n语义部分共完成',head)
    table=['| 工作 | 当前状态 |','|---|---|']
    for job in [x for x in rows if x['repeat']==1]:
        vals=job.get('position_metrics',{});scores='；'.join(k+' 最近邻/插值 RMSE '+f"{v['nearest_rmse_m']:.4f}/{v['interpolated_rmse_m']:.4f} m" for k,v in vals.items());status=(f"完整运行与评测通过，{job['poses']} 位姿；"+(scores or '无可核验 GT，仅工程回归')) if job.get('evaluated') else '待完成完整运行与评测'
        table.append(f"| {job.get('dataset',job['id'].rsplit('_',1)[0])} {job.get('sequence','')} | {status} |")
    repeat_status='NTU、Oxford02、FPV2_00 各完成三次，均值与样本标准差已汇总' if complete else 'NTU、Oxford02、FPV2_00 各计划三次，详见逐次汇总'
    table+=['| OVO 参考位姿语义基线 | 6 组发布数据 +12 组本地配对条件，23,340 RGB 帧；独立复核保留 |',f'| 核心几何重复性 | {repeat_status} |','| M2DGR door01 / door02 | 官方入口 HTTP429，无可用本地数据，未评测 |','| FAST-LIVO2 → OVO 接口 | 第二阶段，尚未实现 |']
    readme.write_text(s[:head]+'\n'.join(table)+s[tail:])
    return p


if __name__=='__main__':
    r=build();p=render(r);print(r['status'],p)
