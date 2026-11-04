# 每日自动生成 · 使用说明

## 它做什么

每天 **08:00**（可改）自动检查 `daily/inbox/`：

1. 有 `.txt` / `.md` 就按文件名顺序取一篇（默认一次 1 篇）
2. 跑完整流水线：改写 → 3 版开头 → 配音 → 字幕 → 素材剪辑 → 成片 → 封面/文案 → 质检
3. 成片进 `output/<job_id>/`，原文归档到 `daily/done/`，结果存 `daily/done/<名>.result.json`
4. 失败自动重试 1 次，仍失败移到 `daily/failed/`，并弹系统通知
5. 收件箱空着就只记日志 + 通知「今天没有新原文」，不会报错

## 怎么放原文

```
daily/inbox/001-我的书名.txt
daily/inbox/002-下一本.txt
```

- 文件名前缀 `001-`、`002-` 用来控制顺序，会自动去掉再当标题
- 也可以在文件第一行写 `title: 我的书名` 指定标题
- 英文内容建议 600 词以上，中文 1300 字以上（3 分钟成片的底线）

## 相关命令

```bash
./.venv/bin/python -m novelauto daily --dry-run   # 看看今天会处理什么
./.venv/bin/python -m novelauto daily             # 立刻手动跑一次
./.venv/bin/python -m novelauto daily --max 3     # 一次处理 3 篇
./.venv/bin/python -m novelauto daily --no-notify # 不弹通知
```

## 为什么到点会弹终端窗口

macOS 不允许后台任务读取 `~/Documents`（实测 launchd 直接跑会 `Operation not permitted`），所以定时任务通过「终端」启动——终端有权限，还能顺便看进度。跑完窗口留着就行。

不想弹窗：把 `.venv/bin/python` 加进「系统设置 → 隐私与安全性 → 完全磁盘访问权限」，再 `./scripts/install_daily.sh --silent` 重装。

## 定时与唤醒

- 定时任务：`scripts/install_daily.sh`（或双击「设置每日自动生成.command」）写入
  `~/Library/LaunchAgents/com.huqi.novel-promo.daily.plist`
- 自动唤醒：`sudo pmset repeat wakeorpoweron MTWRFSU 07:55:00`
  - 只会把**睡眠**中的 Mac 叫醒；**关机**状态无法自动开机（硬件限制）
  - 笔记本合盖时建议插电，并对电池供电时同样生效
- 查看/取消：`pmset -g sched`、`sudo pmset repeat cancel`
- 卸载定时任务：双击「关闭每日自动生成.command」

## 小贴士

- 跑的时候会自动 `caffeinate`，避免生成到一半睡着
- 每天第一次跑会等网络就绪（最多 4 分钟）
- 想每天轮换不同开头打法：`config.yaml` 里 `daily.hook: auto`
- 素材不够时会直接报错并通知，记得先 `ingest` 新素材
