---
name: novel-promo
description: 生成或重做小说推文长视频（3-10 分钟，默认英文海外版）：原文改写（3 版黄金三秒）→ 配音 → 字幕 → 解压素材剪辑 → 竖屏成片 + 发布文案 + 质检。支持换开头、换音色/语速/风格、按项目续跑。
whenToUse: 用户要做小说推文/故事类竖屏视频、批量出片、改已有项目的开头或音色/语速、查素材库或历史项目时。
---

# 小说推文自动化（novel-promo）

本地视频生产流水线，跑在这台 Mac 上。本技能只负责**调用**，不要重写项目代码。

## 入口

```bash
S=~/.dsh/skills/novel-promo/scripts/novelauto.sh        # 全局安装
# 或项目级：$WS/.dsh/skills/novel-promo/scripts/novelauto.sh
bash "$S" <子命令> [参数] --json
```

入口脚本会自动定位 novel-auto 项目（已知路径 / 工作区 / `NOVELAUTO_HOME` 环境变量），
**项目如果搬家，就 export NOVELAUTO_HOME=/新路径/novel-auto**。

- 项目根目录：`$WS/novel-auto`
- 入口脚本会自动 `cd` 到项目根目录并使用项目 venv；不要自己 `cd` 或换 Python
- 所有创建/查询命令都支持 `--json`，**优先用 `--json` 拿结构化结果**，不要靠解析进度条
- 进度条只打到 stderr，不影响 JSON 解析

## 开跑前自检（每次新任务先做一次）

```bash
bash "$S" doctor --json
```

看三个字段即可：`ok`（整体）、`checks[]` 里 `llm_key`、`assets`。缺 `llm_key` 会退化成离线模板（能出片但文案差），要主动告诉用户。

## 标准工作流

### 1. 读原文并判断长度

原文来自用户提供的文件或消息正文。最低要求：
- 英文：**约 450 词**起步（3 分钟成片），做 5 分钟约 720 词
- 中文：约 1300 字起步

不够就直接告诉用户补原文，不要硬跑（会报"原文太短"）。太长会自动按目标时长截断，不用手动切。

### 2. 解析原文 → 出 3 个开头（约 20-30 秒）

```bash
bash "$S" prepare --input /abs/path/story.txt --minutes 4 \
  --language en --style thriller --person first --hook-strength strong \
  --rate=-6% --json
```

返回 JSON 关键字段：`job_id`、`hooks[3]`（每个有 `type` 和 `text`）、`segments`、`publish`、`target_sec`。

**必须把 3 个开头原样展示给用户挑选**，除非用户明确说"你定"。格式：

```
1) [Suspense] ...
2) [Conflict] ...
3) [Flashback] ...
```

### 3. 用户选好后渲染（约 2-5 分钟）

```bash
bash "$S" render --job-id <job_id> --hook 2 --json
```

可选覆盖：`--voice en-US-JennyNeural`、`--rate=-15%`、`--out-dir /some/dir`。

返回 JSON：`out_dir`、`final`、`duration_hms`、`size_mb`、`lufs`、`chapters`、`qc{status,checks}`。

### 4. 验收

- `qc.status` = `pass` 可直接交付；`warn` 可交付但要在回复里提一句警告项；`error` 要读 `out_dir/qa_report.txt`，先修再交付
- 交付物：`final.mp4`（竖屏成片）、`cover.jpg`（封面）、`publish.txt`（标题 3 选 + 话题 + 置顶评论 + 上传清单）
- 用 `present` 展示 `final.mp4` 和 `publish.txt`，并把 `publish.txt` 里"采用"的标题和话题贴到回复里，方便用户直接复制

### 5. 改需求（不用重跑整条）

- 换开头：同一个 `job_id` 再 `render --hook 1|2|3`（配音有缓存，很快）
- 换音色/语速：同上，加 `--voice` / `--rate`
- 改文案某句：`sed` 改 `work/<job_id>/script.json` 的 `segments` 后重新 `render`（改完先备份）
- 看历史项目：`bash "$S" projects --json`；继续某个：直接 `render --job-id` 或 `prepare --job-id`（复用已解析稿）
- 删项目：`bash "$S" delete --job-id <id> --yes --json`（中间文件 + 成片一起删）；只想清中间文件保留成片加 `--keep-output`。**删除前必须先问用户确认**，不要自己删

## 参数

| 参数 | 取值 | 默认 | 说明 |
|---|---|---|---|
| `--language` | en / zh | en | 海外版用 en；原文语言要和它一致 |
| `--minutes` | 3-10 | 按原文长度自动 | 不传则由程序决定，成片自动收敛 ±8% |
| `--style` | auto / thriller / romance / revenge / sweet / drama | auto | 文案调性 |
| `--person` | auto / first / third | auto | 人称 |
| `--hook-strength` | normal / strong / max | normal | 开头钩子强度 |
| `--rate` | -30% ~ +30% | -6% | 语速。**命令行负值必须写 `--rate=-15%`（带等号）** |
| `--voice` | 见下 | en-US-AndrewNeural | 英文女声：`en-US-JennyNeural` / `en-US-AriaNeural` / `en-GB-SoniaNeural`；中文：`zh-CN-YunxiNeural` / `zh-CN-XiaoxiaoNeural` |
| `--hook` | 1/2/3 | 1 | 用第几个开头 |
| `--out-dir` | 绝对路径 | 项目 output/ | 额外导出一份成片 |

## 素材

```bash
bash "$S" assets stats --json                      # count / total_seconds / usable_*
bash "$S" ingest --dir /Users/xxx/素材 --json      # 扫描入库（只读，不会动原文件）
```

- `usable_*` 是排除测试素材后的真实可用量
- **经验值：可用素材时长 ≥ 成片时长的 2 倍**才不容易重复；低于 1.2 倍要提醒用户补素材
- 素材不足会在 `render` 阶段直接报错，不要试图绕过

## 硬性规则

1. **绝不自动发布**：只产出成品 + 文案，上传由用户手动完成（TikTok 个人号没有官方发帖 API）
2. **不编造素材/不删用户文件**：素材只读，`ingest` 只写数据库索引；删项目必须先得到用户明确同意
3. **一次只跑一个渲染**：同一时间跑两个会抢 CPU；渲染期间不要重复提交
4. **时长边界**：3-10 分钟，超出要明确拒绝并解释
5. **原文语言与 `--language` 要匹配**：用户给中文却要英文视频时，先翻译成英文原文再跑，并告知用户
6. **没有 LLM key 时**成片可用但文案是模板级，必须明确告知用户质量受限
7. 不要改项目代码、不要动 `.venv`、不要换 Python 解释器

## 常见报错

| 报错 | 原因 | 处理 |
|---|---|---|
| `原文太短：X words/字` | 原文不够 3 分钟 | 让用户补原文，或把 `target.minutes_min` 调到 3 以下（不推荐） |
| `素材总时长不足` | 可用素材太少 | 让用户加素材或缩短目标时长 |
| `TTS 合成失败` | edge-tts 网络问题 | 重试一次；仍失败可 `--provider say` 用本机语音兜底（音质差） |
| `目录不存在` / 入库被拦 | 素材路径不对或系统目录 | 让用户确认绝对路径 |
| `qc.status = error` | 时长/黑帧/静音不达标 | 看 `qa_report.txt` 具体项，必要时重跑 |

## 网页控制台（用户想要界面时）

```bash
cd $WS/novel-auto
./.venv/bin/python -m novelauto web --port 8765
```

或让用户双击 `小说推文控制台.app`。**不要自己另起一个端口**；已经有服务在跑就直接给用户 URL。

## 汇报模板

```
已生成：4:12 · 1080x1920 · 116MB · 质检 warn（素材复用）
成片：<绝对路径>/final.mp4
封面：cover.jpg
标题：<采用的那条>
话题：#xxx #yyy
置顶评论：...
提醒：素材池只有 4.6 分钟，单条素材被复用了 4 次，建议补素材
```
