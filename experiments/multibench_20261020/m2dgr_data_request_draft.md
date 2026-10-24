# 致 M2DGR 作者的数据请求草稿

> 用途：GitHub issue（中文，简短分点，公开）+ 邮件（中文，私信）。
> 替换占位符：`[NAME]`、`[AFFILIATION]`、`[GITHUB_HANDLE]`、`[DATE]`。
> 建议附上仓库里的失败记录：`experiments/multibench_20261020/m2dgr_access_final.json`、
> `m2dgr_access_20261020_throttle.json`（可贴成 gist）。

---

## 版本 A：GitHub issue（中文，精简）

**标题：** 请求 door_01 / door_02 的替代下载渠道（官方链接返回 429 activityLimitReached）

各位作者好，

我们在做室内外转场场景下的语义 SLAM 评测（FAST-LIVO2 的 ROS 2 移植版），只需要两条序列及其 Leica 真值：

- `door_01`（461 s，约 35.5 GB，outdoor→indoor→outdoor）
- `door_02`（127 s，约 10.5 GB，outdoor→indoor）

M2DGR 里只有这两条带明确的室内外转场标注，其他序列替代不了。不需要完整的 1.2 TB。

**现象**（多天、两个不同网络一致，M2DGR-plus 的链接表现相同）：

1. 浏览器打开 → 302 跳转到 `PolicyViolationThrottle.htm?fabDeleteSchdDate=2026-11-15`
2. 加 `&download=1` → 429，`Retry-After: 300`，body 为 `429 TOO MANY REQUESTS`
3. 走 shares API（`/_api/v2.1/shares/u!<token>/driveItem`）→ 429，`activityLimitReached / forbiddenQuota`

按 5–10 分钟退避重试数小时无效，看起来是账号级配额，而不是单条链接失效。

**能否提供任一替代方式：**

1. 阿里云盘链接（M3DGR 用的就是它，我们验证可用）；百度网盘或 123 云盘也行
2. 重新分享一次现有 OneDrive 链接，临时有效即可
3. 若只能通过校园网下载，请告知，我们去找有交大网络的同学

拿到数据后我们会回传 SHA-256 与 `rosbag info`；数据仅用于研究，不转载，按 MIT 与 RA-L 论文引用。

谢谢。

---

## 版本 B：邮件（中文）

**主题：** 关于 M2DGR door_01 / door_02 下载渠道的请求

殷杰老师，您好：

我们在做室内外转场场景下的语义 SLAM 评测，用的是 FAST-LIVO2 的 ROS 2 移植版本，
需要连续跨越室内外边界的序列做实验。M2DGR 里只有 `door_01`（461 s，约 35.5 GB，
outdoor→indoor→outdoor）和 `door_02`（127 s，约 10.5 GB，outdoor→indoor）带明确的
转场标注，其他序列无法替代，因此我们只申请这两条及其 Leica 真值 txt，不需要完整的 1.2 TB。

目前这两条链接在我们这里持续失败（多天、两个不同网络都一样）：

- 浏览器打开：302 跳转到 `PolicyViolationThrottle.htm?fabDeleteSchdDate=2026-11-15`
- 直接下载：429，`Retry-After: 300`，返回 `429 TOO MANY REQUESTS`
- 走 shares API：429 `activityLimitReached / forbiddenQuota`

我们按 5–10 分钟退避重试了几个小时，M2DGR-plus 的链接也是同样表现，判断是账号级别的
配额限制，而不是单条链接失效。

能否麻烦您提供一个替代渠道？以下任一方式都可以：

1. **阿里云盘**链接（M3DGR 就是这种方式，我们用起来没问题）；百度网盘、123 云盘也行
2. 重新分享一次现有的 OneDrive 链接，临时有效也可以
3. 如果只能靠校园网下载，请告诉我们，我们去找有交大网络的同学

拿到数据后我们可以回传 SHA-256 和 `rosbag info` 结果，也可以帮您确认从其他网络是否
可访问；数据仅用于研究，不对外转载，会按 MIT/学术用途引用 RA-L 论文。

感谢您维护这个数据集。

此致
[NAME]
[AFFILIATION]
[DATE]