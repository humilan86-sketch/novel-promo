# 小说推文自动化（TikTok 解压视频版）

贴一段小说原文 → 自动产出 **3-10 分钟竖屏成品视频 + 发布文案包**。

**默认面向海外版（英文）**：英文改写、英文配音、英文字幕、英文话题标签；切回中文只需改 `project.language: zh`。

一条视频的流水线：

```
小说原文
  → ① 剧本改写（3 版黄金三秒 + 章回钩子 + 语言化字数/词数预算 + 时长收敛）
  → ② 配音（edge-tts 默认，可切火山 / MiniMax）
  → ③ 时间轴（以音频真实时长为唯一真源，毫秒级）
  → ④ 字幕（PIL 逐句渲染透明 PNG，自动折行/淡入淡出）
  → ⑤ 素材（素材库选片 + 冷却期 + 防撞车参数）
  → ⑥ 渲染（分段渲染 Ken Burns/调色/字幕 overlay/混音 → 拼接）
  → ⑦ 物料（封面 + 标题 3 选 + 话题 + 置顶评论 + 上传清单）
  → ⑧ 质检（时长/分辨率/响度/黑帧/静音/音画同步/素材复用/钩子密度）
```

---

## 〇、在 AI 里直接用（技能 `/novel-promo`）

这套流水线已经封装成 DSH 技能，装上后在对话里直接说需求就行，不用自己敲命令：

```
/novel-promo 用 ~/Desktop/story.txt 做一条 4 分钟、悬疑风、女声的视频
/novel-promo 把我上次那个项目换个开头重新出片
/novel-promo 素材库现在够跑多久？帮我入库 ~/Desktop/new_clips
```

安装位置（两份都已装好）：

| 位置 | 范围 |
|---|---|
| `~/.dsh/skills/novel-promo/` | **全局**，任何工作区都能用 |
| `<本项目>/.dsh/skills/novel-promo/` | 当前工作区 |

技能入口：`bash ~/.dsh/skills/novel-promo/scripts/novelauto.sh <子命令> --json`
（脚本自动定位 novel-auto 项目；项目搬家就 `export NOVELAUTO_HOME=/新路径/novel-auto`）

技能里的标准流程：`doctor 自检 → prepare 出 3 个开头并让你选 → render 出片 → 报告质检结论`。
所有命令都支持 `--json`，AI 拿结构化结果，不用解析进度条。

## 一、最快上手：双击启动（不用碰命令行）

项目目录里有三个双击就能用的启动器（首次双击如果被系统拦，右键点它 →选「打开」）：

| 文件 | 作用 |
|---|---|
| **小说推文控制台.app** | 双击即开控制台（不弹终端窗口），适合拖进程序坞当常驻入口 |
| **启动控制台.command** | 同上，但会显示终端窗口和日志（排查问题用） |
| **生成视频.command** | 直接生成一条：弹出选文件 → 问时长/项目名 → 跑完自动打开成品目录（也可以把 .txt 拖到这个图标上） |
| **素材入库.command** | 选素材文件夹 → 自动扫描入库（指纹去重、时长、分类） |
| **停止控制台.command** | 停止后台运行的控制台服务（用 .app 启动的没有终端窗口可关） |

想把它们放到桌面或程序坞：按住 `⌘` 拖到桌面/Dock 即可，之后从那里双击。
关掉终端窗口 = 停止服务。

> 第一次双击若提示「无法打开，因为来自身份不明的开发者」：右键 → 打开 → 仍要打开。

### 控制台里做什么（现在是两步式）

1. 顶部选语言：**English（海外版）** 或 中文（默认英文）
2. 贴原文（英文 600 词以上最好；中文 3000 字以上）→ 点 **① 解析原文**
3. 十几秒后出 **3 个不同打法的黄金三秒**（Suspense / Conflict / Flashback 或 悬念 / 冲突 / 倒叙）：
   - 点一个选中，或在「自己改开头」里直接写一句
   - 想微调正文就点「展开分段稿编辑」，一行一句随便删改
4. 选 **素材目录**（点「选择…」弹 Finder）→ 点「扫描入库」
5. 选 **导出目录**（可选，成片会另存一份过去）、选音色（英文默认 Andrew 男声）
6. **风格参数**在 ① 卡片里：风格（悬疑/情感/复仇/甜宠/家庭伦理）、人称（第一/第三人称）、钩子强度（自然/强/极强）
7. **语速**在 ③ 生成设置里：滑杆 -30% ~ +30%，慢了更解压/悬疑，快了信息密度更高
8. 点 **② 生成视频**，看进度条（配音 → 分章 → 字幕 → 素材 → 渲染 → 物料 → 质检）
6. 完成后右栏直接预览、下载 `final.mp4` / `cover.jpg` / `publish.txt` / `qa_report.txt`，
   点「复制文案」粘到 TikTok；质检项逐条显示（黑帧/静音/响度/音画同步/素材复用/钩子密度）

想换开头重做：改选另一个版本再点生成即可，配音有缓存，很快。

### 接上大模型（强烈建议，决定文案质量）

打开项目里的 `.env`（没有就把 `.env.example` 复制改名），填：

```ini
LLM_API_KEY=sk-你的key
```

存盘后重启控制台即可。不填也能跑，但改写是离线模板，只是流程验证。

---

## 二、安装

需要 macOS + ffmpeg（本机已装 8.1.1）。

```bash
cd novel-auto
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env        # 填 API Key（可选）
./.venv/bin/python -m novelauto doctor
```

`doctor` 全绿就可以用了。没有大模型 Key 也能跑，会进入「离线改写」模式（流程全通，文案质量仅供验证）。

---

## 三、第一次跑通（没有素材也能跑）

```bash
# 1. 造 3 条测试素材（合成渐变/纹理，用来验证渲染链路）
./.venv/bin/python -m novelauto assets synth --count 3 --seconds 120
./.venv/bin/python -m novelauto ingest

# 2. 跑一条
./.venv/bin/python -m novelauto run --input samples/sample_story.txt --minutes 6

# 3. 看成品
open output/*/            # final.mp4 / cover.jpg / publish.txt / qa_report.txt
```

## 四、用你自己的素材

```bash
# 把解压素材（mp4/mov/mkv 都行，长素材最好）放进 assets/clips/
./.venv/bin/python -m novelauto ingest          # 扫描 + 指纹去重 + 入库
./.venv/bin/python -m novelauto assets stats    # 看素材池够不够用
```

素材量参考（5-8 分钟成片、每天 3 条、冷却 7 天）：

- 长素材随机取段：**20-30 条长素材（15-30 小时）** 就够跑一周；
- 短片段拼接：需要 700-1000 段。

素材来源优先级：自己拍/录屏 > 免版权站（Pexels/Pixabay/Mixkit）> 付费商用包（Envato/Artgrid）> ❌ 不要直接搬运别人的解压视频。

## 五、Web 控制台

```bash
./.venv/bin/python -m novelauto web            # 自动打开 http://127.0.0.1:8765
```

贴原文 → 点「开始生成」→ 看进度 → 预览成品 / 下载 / 一键复制文案。素材库面板可以「扫描入库」和「生成测试素材」。

---

## 六、接大模型（让改写真正有质量）

在 `.env` 里填：

```ini
LLM_API_KEY=sk-xxxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

任何 OpenAI 兼容接口都行（DeepSeek / 通义 / 火山方舟 / 硅基流动 / 本地 Ollama 的 `/v1`）。

改写分 3 段：**分块转述（每块 700 字，共享上下文）→ 钩子包（黄金三秒 / 3 条续航钩 / 断点结尾 / 发布文案）→ 机器质检 + 时长收敛**。

## 七、语速（影响时长与字数预算）

控制台 ③ 生成设置里有滑杆（-30% ~ +30%）。命令行：

```bash
./.venv/bin/python -m novelauto prepare -i 原文.txt --rate=-20%
./.venv/bin/python -m novelauto render --job-id <job_id> --rate=-20%
# 注意命令行里负值要写成 --rate=-20%（带等号），否则会被当成参数
```

- 语速同时影响**文案字数预算**：-20% 时同样的目标时长会少写约 20% 的词
- 改进语音速会**重新合成**（缓存按语速区分），第一次会慢一点，之后复用
- 每条视频的语速会记进项目，重新打开项目自动恢复

## 八、风格参数（决定文案调性）

```bash
./.venv/bin/python -m novelauto prepare -i 原文.txt --language en \
  --style thriller --person first --hook-strength max
```

| 参数 | 可选值 | 作用 |
|---|---|---|
| `--style` | auto / thriller / romance / revenge / sweet / drama | 悬疑 / 情感 / 复仇 / 甜宠 / 家庭伦理；直接写进改写提示词 |
| `--person` | auto / first / third | 第一人称（更有代入）或第三人称 |
| `--hook-strength` | normal / strong / max | 开头钩子强度：自然 / 强 / 极强（零铺垫从危机中段开场） |

网页控制台在 ① 卡片里有对应下拉框，随「解析原文」一起生效。

## 九、语言与时长

```bash
# 切换语言（en 英文海外版 / zh 中文）
./.venv/bin/python -m novelauto run -i 原文.txt --language en

# 时长范围在 config.yaml：
#   target.minutes_min: 3     # 至少 3 分钟
#   target.minutes_max: 10    # TikTok 上限 10 分钟
# 不传 --minutes 时按原文长度自动落在 3-10 分钟之间
```

语言切换会自动换掉：改写提示词、词数/字数预算、TTS 音色、字幕字体与换行规则（英文按像素宽度按词换行）、话题标签与发布文案模板。

## 十、换配音

```bash
# 换音色（edge 音色列表：edge-tts --list-voices | grep zh-CN）
./.venv/bin/python -m novelauto run -i 原文.txt --voice zh-CN-YunjianNeural

# 切火山引擎（先 .env 填 VOLC_APPID/VOLC_TOKEN/VOLC_CLUSTER）
./.venv/bin/python -m novelauto run -i 原文.txt --provider volc

# 切 MiniMax（先填 MINIMAX_API_KEY/MINIMAX_GROUP_ID）
./.venv/bin/python -m novelauto run -i 原文.txt --provider minimax

# 完全离线（没网时兜底，音质差）
./.venv/bin/python -m novelauto run -i 原文.txt --provider say
```

配音按句合成并缓存（`.cache/tts/`），**改文案只重合成改动的那几句**，不会重复计费。

---

## 十一、目录结构

```
novel-auto/
├── config.yaml              # 所有参数（时长/音色/字幕样式/防撞车强度…）
├── .env                     # 密钥（不进版本库）
├── assets/clips/            # 你的素材
├── assets/assets.db         # 素材索引（sqlite，指纹/冷却记录）
├── samples/sample_story.txt # 测试原文
├── daily/                   # 每日自动生成（inbox 放原文 / done 归档 / logs 日志+status.json）
├── scripts/                 # install_daily.sh（定时任务）、daily_run.command（终端入口）
├── work/<job_id>/           # 中间产物：script.json / timeline.json / master.wav
│   └── render/chXX/         # 分章节的 subs.ass / audio.wav / chapter.mp4
└── output/<job_id>/         # 交付物
    ├── final.mp4
    ├── cover.jpg
    ├── publish.txt          # 标题 3 选 + 话题 + 置顶评论 + 上传清单
    ├── qa_report.txt        # 质检（时长/响度/钩子分布/素材使用/警告）
    ├── script.json
    └── meta.json
```

## 十二、怎么改

| 想改的东西 | 改哪里 |
|---|---|
| 成片时长 | `config.yaml` → `target.minutes_min/max` |
| 字幕字号/位置 | `target` 不动，改 `subtitle.font_size` / `subtitle.margin_v` |
| 语速、音色 | `tts.rate` / `tts.voice` |
| 防撞车强度 | `render.grain`、`render.kb_strength`、`assets.cooldown_days`，以及 `assets.py` 里 `pick()` 的随机参数 |
| 钩子写法/密度 | `scriptwriter.py` 的提示词常量（`SYSTEM_HOOK`、`_prompt_hook_pack`） |
| 章节长度 | `pipeline.chapter_min_sec/max_sec` |

## 十三、实测结果（本机 macOS）

**接入 DeepSeek 后（2026-11-02 实测）**：成片 6:18 / 7 章 / 1819 字 / -16.3 LUFS / 116MB，
续航钩由模型按剧情生成（「婆婆每天端来的汤，是补身体还是封口？」），标题/置顶评论/封面文案均为模型产出，
单条全流程约 283 秒（含 3 轮时长收敛）。

离线模式（无 Key 兜底）：

| 指标 | 结果 |
|---|---|
| 成片时长 | 5:47（目标 6 分钟，收敛后 5:43-5:50 之间） |
| 规格 | 1080x1920 / 30fps / h264+aac / 100.9 MB |
| 响度 | -16.3 LUFS |
| 钩子分布 | 0% 黄金三秒 → 35% 续航钩 → 69% 续航钩 |
| 单条全流程耗时 | 约 128 秒（其中渲染 118 秒，5 个章节） |
| 单条现金成本 | ¥0（离线）/ 约 ¥0.05-0.15（接 LLM + 商 TTS） |

跑一条真实素材视频大约 2-3 分钟；渲染时间可以用 `render.preset: veryfast` 再压一半。

## 十四、项目制（刷新/重启不丢）

每个任务在 `work/<job_id>/project.json` 里存一份记录：标题、日期、语言、风格、3 个开头、分段稿、发布文案、音色、成片信息。

- 控制台左侧顶部是**项目列表**，点一下就能继续：3 个开头、分段稿、成品视频都会恢复
- 关掉浏览器、重启服务、重启电脑都不会丢（记录在磁盘）
- 音色是**按项目**生效的：下拉选了哪个，生成就用哪个，并记进项目记录（不再只依赖「保存为默认」）

## 十四半、删除项目

**网页**：项目列表每条右侧的 `✕`，确认后删除；勾选标题栏「删项目时保留成片」就只清中间文件（配音/字幕/分章），成品留在 `output/<job_id>/`。

删除当前正在生成的项目会被拒绝（提示「任务正在生成中」），等它跑完再删。删除当前选中的项目后，右侧成品区会自动清空，避免点开一个已经不存在的文件。

```bash
./.venv/bin/python -m novelauto projects --json                          # 先看有哪些项目
./.venv/bin/python -m novelauto delete --job-id <id> --yes               # 中间文件 + 成片一起删
./.venv/bin/python -m novelauto delete --job-id <id> --keep-output --yes # 只清中间文件，保留成片
```

技能 `novel-promo` 也已补上 `delete` 用法，并要求先问用户确认再删。

> 只删 `work/<job_id>` 和 `output/<job_id>`。记录里指向外部自定义导出目录的，只提示不删，避免误删桌面上的其他文件。

## 十四·五、每天自动生成（定时任务）

想「晚上丢原文，早上起床就有成片」：把原文扔进 `daily/inbox/`，定时任务到点自动跑完整条流水线。

### 装一次

双击 **`设置每日自动生成.command`**。它会：

1. 写入 launchd 定时任务 `~/Library/LaunchAgents/com.huqi.novel-promo.daily.plist`（默认每天 **08:00**）
2. 问你要不要顺便设置「07:55 自动唤醒」（需要一次开机密码，`sudo pmset repeat wakeorpoweron MTWRFSU 07:55:00`）

> **为什么到点会弹出一个终端窗口？**
> macOS 的隐私保护不允许「后台任务」读取 `~/Documents`（测试确认：launchd 直接跑会报 `PermissionError: Operation not permitted`）。所以默认让**终端**来启动——终端有这个权限，你也能顺便看到跑到哪一步。跑完窗口留着就行，关掉不影响成片。
>
> 想完全不弹窗（静默模式）：先到「系统设置 → 隐私与安全性 → 完全磁盘访问权限」把 `.venv/bin/python` 加进去，再执行 `./scripts/install_daily.sh --silent` 重装一次。

取消：双击 **`开启自动唤醒.command`** 可单独设唤醒；双击 **`关闭每日自动生成.command`** 卸载定时任务（不动已生成的成片）。

### 每天怎么用

```
daily/inbox/001-我的书名.txt      ← 丢这里就会排队
daily/inbox/002-下一本.md
```

- 文件名前缀 `001-` 只用于排序，标题会自动去掉数字前缀
- 也可以在文件第一行写 `title: 我的书名`
- 英文建议 600 词以上，中文 1300 字以上（3 分钟成片的底线）
- 每次默认跑 **1 篇**（`daily.max_per_run`），收件箱空着就只记日志 + 通知，不会报错

### 跑完长什么样

- 成片：`output/<job_id>/final.mp4` + `cover.jpg` + `publish.txt` + `qa_report.txt`
- 原文归档：`daily/done/<时间戳>_001-我的书名.txt`
- 结果记录：同目录 `….result.json`（job_id、时长、开头序号、成品路径、质检状态）
- 失败：原文进 `daily/failed/`，附 `….error.txt`（完整堆栈），并弹系统通知；自动重试 1 次
- 总览：`daily/logs/status.json`；日志 `daily/logs/launchd.out.log`、`launchd.err.log`

### 常用命令 / 调试

```bash
./.venv/bin/python -m novelauto daily --dry-run   # 今天会处理哪些文件（不真跑）
./.venv/bin/python -m novelauto daily             # 立刻手动跑一次
./.venv/bin/python -m novelauto daily --max 3     # 一次处理 3 篇
./.venv/bin/python -m novelauto daily --no-notify # 不弹通知
pmset -g sched                                    # 看自动唤醒有没有设上
launchctl print gui/$UID/com.huqi.novel-promo.daily | head -20   # 看任务状态
```

### 可调参数（`config.yaml` → `daily:`）

| 键 | 默认 | 说明 |
|---|---|---|
| `time` | `08:00` | 每天几点跑（重装脚本后生效） |
| `max_per_run` | `1` | 每次最多几篇 |
| `retry` / `retry_wait` | `1` / `90` | 失败重试次数与等待秒数 |
| `hook` | `1` | 用第几个开头；`auto` = 每天轮换 1/2/3 |
| `minutes` | `0` | `0` = 按原文长度自动落在 3-10 分钟；也可固定如 `4.5` |
| `voice` / `rate` / `language` | 空 | 留空用主配置；填了就覆盖当天 |
| `style` / `person` / `hook_strength` | `auto` | 同网页上的三个下拉 |
| `ingest` / `ingest_dir` | `false` / 空 | 每天先扫素材目录入库（素材会越用越少，建议开） |
| `keep_awake` | `true` | 跑的时候用 `caffeinate` 阻止休眠 |
| `network_timeout` | `240` | 刚唤醒时等网络就绪的最长秒数 |

### 注意

- **关机不能自动开机**（硬件限制）：只能从「睡眠」唤醒。笔记本建议插电、别合盖断网
- 每天跑之前素材要够：素材库可用时长 ≈ 1.2 × 成片时长。不够会在 `render` 阶段报错并通知 → 记得往素材目录补片、或开 `daily.ingest`
- 大模型额度：每条成片 3-6 次 LLM 调用（改写+钩子），DeepSeek 余额不足会在日志里报错
- 定时任务和手动跑互斥（`daily/logs/.lock`），撞上会直接提示「另一个任务正在运行」

## 十五、常见问题

**Q：点「扫描入库」提示「素材目录必须位于项目目录内」？**
A：那是旧版本的安全限制（防止网页接口扫描系统目录）。现在的版本已放开：**桌面、Movies、移动硬盘、外接盘都能选**，只拦 `/System`、`/Library` 等系统目录。更新后重启控制台即可。

**Q：视频中间有一段不是我自己的素材？**
A：以前库里混着 4 条测试渐变素材，选片是随机混挑的。现在默认**只用自己的素材**（`assets.exclude_categories: ["_synthetic"]`），控制台里还有「清理测试素材」按钮可以彻底删掉。另外素材比音频短时，末段会用末帧补齐（不再黑屏）。

**Q：素材库放哪？**
A：放哪都行，用控制台的「选择…」按钮挑文件夹，路径会记住。也可以把文件夹拖进项目内的 `assets/clips/`（更省事，备份项目就一起备份了）。
注意：扫描时会自动跳过 `output/`、`work/` 等成品目录，不会把成片当素材。

**Q：素材扫描很慢？**
A：首次入库要对每个视频抽 3 帧算指纹（去重用），素材多时几分钟正常；之后再入库只处理新文件。

## 十六、代码审查后的修复记录（2026-11-01 第二轮）

用只读审查跑了一遍，修掉的问题（都已回归验证）：

**严重**
- `_split_chunks(source, ~700)` 的 `~700` 是 Python 按位取反 = -701 → LLM 分支永远退化为离线模式。已改 700，并用假 LLM 验证 rewrite() 真的走 LLM 路径。
- 原文太短（如 339 字）只产出 1 分钟残片 → 现在按 `minutes_min × 60 × 字/秒 × 0.82` 卡下限（默认约 1279 字）直接报错。
- 超长原文（2 万字）没有截断，会合成上百分钟语音 → 现在按目标字数截断到句子边界。
- Web `/files/../config.yaml` 可读任意文件（含 `.env` 里的 Key）→ 改为文件名白名单 + `resolve()` + `relative_to()`，实测穿越全部 404。
- 单句 TTS 失败会终止整条任务 → 现在收集失败句，自动降级 macOS `say` 补上。
- 素材凑不够章节时长会黑屏/定帧 → 现在按 `max_segment` 切段、必要时换起点复用，凑不够直接报错。

**一般**
- 章节边界吃掉句间停顿 → 章节 end 延伸到下一章开头（concat 期望值也改为章节时长之和）。
- 末章超过 `chapter_max_sec` → 分章算法重写，实测 6 章 56-60s。
- Ken Burns 15 秒就到顶 → 改为按片段时长归一（`on/total_frames`）。
- 字幕 PNG 缓存 key 缺样式参数、超 32 字丢字 → key 补全，超 2 行自动缩字号。
- TTS 缓存 key 不含 say/volc/minimax 音色 → 按 provider 分别并入。
- TTS 临时文件并发互写 → uuid 临时名 + `os.replace`。
- LLM 返回非 dict 时 `.get` 崩溃 → 结构校验 + 兜底。
- 素材 `mark_used` 从未调用（冷却期失效）→ 已调用；sqlite 加 WAL + busy_timeout。
- 时长收敛窗口不跟随 target → 改为 `target ± tolerance` 且不越过 5-8 分钟。
- 封面失败会丢整条成品 → try/except 兜底；底部条 alpha 改用 RGBA 合成。
- Web 输入无上限、ingest 可扫任意目录 → 加长度/范围校验 + 目录限定项目内。

**仍未做**：Web 任务队列（并发多条会抢 CPU，建议一次跑一条）、多任务共享 JOBS（重启丢失，但产物在 output/ 里不丢）。

## 十七、已知限制 / 注意

- **自动发帖不做**：TikTok 个人号拿不到官方发帖 API，模拟点击封号风险高。这里做的是「成品 + 文案包」，你手动上传 30 秒。
- **AIGC 标注**：AI 配音/画面按平台要求勾选；`publish.txt` 里有提醒。
- **小说授权**：推文需对应平台/渠道的授权，未经授权的书不要推。
- **渲染耗时**：5-8 分钟成片单条 6-12 分钟（分段渲染）；想快就调 `render.preset: veryfast`。
- **字幕字体**：字幕是 PIL 渲染 PNG，直接用 `config.yaml` 的 `project.font_path`（macOS 自带的 Hiragino Sans GB 即可，中文不会缺字）。
- **ffmpeg 构建差异**：本机 ffmpeg 未编译 libass/freetype，所以不用 ASS/drawtext，改走 PNG overlay，换任何 ffmpeg 构建都能跑。
- **edge-tts 依赖网络**：断网自动降级需手动 `--provider say`。
