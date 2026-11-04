# TikTok 素材 Agent · 框架设计 v1.1

> 目标：把「找素材 → 下载 → 去重 → 入库 → 按推文脚本匹配画面/配音/字幕 → 出片」串成一条可自动跑的流水线。
>
> 定位：不是重写 `novel-auto`，而是在它上面加三个新模块（采集、去重、匹配），把现在的「随机选片 + 冷却」升级成「语义匹配 + 多层去重」。
>
> **v1.1 变更（2026-11-04）**：确认「去重处理工具箱」只放在渲染层做成片差异化；推文库新增「本地素材库 + 飞书表格」双来源适配器。

---

## 0. 先说结论

这件事拆成 **5 个可独立验收的部分**，不要一上来做一个“全自动大 Agent”：

1. **采集器**：按 URL/博主/话题/关键词把 TikTok 原片（无水印、最高码率）拉下来，落元数据。
2. **素材湖 + 去重**：文件级 → 画面级 → 音频级 → 使用级，四层去重，疑似重复进隔离区而不是直接删。
3. **推文库**：把书/剧本/分集/授权状态做成结构化数据，剧本自动拆成“节拍（beat）”。
4. **匹配器**：每个 beat → 画面描述 → 检索素材 → 重排 → 分配时间窗；同时生成配音和字幕。
5. **编排层**：状态机 + 审批点 + 定时任务，把上面四步串起来；LLM 只负责“改写/拆解/描述”，下载、去重、渲染用确定性代码。

**建议顺序**：先做「采集 + 去重」的最小闭环（1 周内可验收），再做「推文库 + 匹配」（1-2 周）。原因：没有干净、唯一的素材池，匹配做得再花哨也是在垃圾上做检索；而采集和去重是可以独立验证对错的。

另外，你之前用 Codex 做的视频下载器和去重扫描器可以直接复用，能省掉 M0/M1 的一部分工作，接入方式见 §13。

**最大的风险不在技术**：TikTok 反爬 + 素材版权/平台判重。框架里会把「来源白名单 / 授权状态 / 隔离区 / 人工审批」做成硬约束，不默认全自动搬运。

### 0.1 本版已确认的两个方向

1. **「去重处理工具箱」用在渲染层，不做库内去重。** 只在选中的片段上做轻量画面差异化（缩放/裁切/镜像/调色/颗粒），在字幕叠加和混音之前完成；不生成 `_processed` 文件回写素材湖。
2. **推文库支持双来源。** 本地目录（`txt/md/docx/csv`）和飞书表格/多维表格（链接或导出文件）都走同一套 `ScriptSource` 适配器；表格里的链接按类型分流到「下载队列 / 文案抓取 / 音频字幕附件」，不把链接一律当视频。

这两个决定会改变 §2 的架构、§5 的推文库、§6 的渲染编排和 §13 的复用方案，下面按 v1.1 写。

---

## 1. 需求拆解

| # | 你的需求 | 输入 | 输出 | 关键难点 | 现有基础 |
|---|---|---|---|---|---|
| 1 | 无损下载素材 | 关键词 / 博主 / 话题 / URL 清单 | 无水印原片 + 元数据 + 指纹 | 选流、去水印、反爬、断点续传 | 无，需新建 |
| 2 | 素材去重 | 下载后的素材池 | 唯一素材库 + 隔离区 + 使用记录 | 裁剪/镜像/改色/变速/换音后的判定 | 只有单帧 dHash |
| 3 | 按推文库匹配视频/音频/字幕 | 剧本（本地/飞书）+ 素材库 | 配音、字幕、画面计划、成片 | 语义匹配、时长约束、画面多样性 | TTS/字幕/随机选片已有 |

### 1.1 三个必须先对齐的定义

**① “无损”**
TikTok 上传时平台已经转过码，所以“无损”只能定义为：**拿到平台当前提供的最高码率、无水印源文件，下载后不做任何二次转码**。它不是拍摄原片，也不可能比平台源文件更清晰。
落地标准：保留原始容器/编码/音频轨；`sha256` 入库；`ffprobe` 记录分辨率/码率/帧率；渲染阶段才转码。

**② “去重”**
要分四层，不要混为一谈：

- **文件级**：同一个视频重复下载（同 `aweme_id`、同 `sha256`）。
- **画面级**：同一条内容被重新上传/裁剪/镜像/改色/变速/加字，画面高度相似。
- **音频级**：同一条音频/BGM 被换到不同画面，或换配音后重复。
- **使用级**：同一条素材在近期成片里被重复使用（冷却期、同段起点复用）。
- 另外还有 **成片差异化**：渲染时的裁切/镜像/调色/颗粒，属于“发布层”，不是“库内去重”。

**③ “匹配”**
有两种可能，默认按 A 做，B 做成可选：

- **A 生成式（默认）**：推文库给剧本 → 生成配音/字幕 → 从素材库检索画面 → 合成。
- **B 检索式**：推文库里已经有配好的音频/字幕/成片 → 建立索引，按新剧本检索复用。
- 如果你的“推文库”里本来就带音频和字幕，告诉我，B 的权重要调高。

---

## 2. 总体架构

```
        ┌──────────────────────────────────────┐
        │ 推文库                                │
        │ 本地目录 / 飞书表格 / 飞书多维表格     │
        │ 文案 · 素材链接 · 音频 · 字幕          │
        └──────────────────┬───────────────────┘
                           │ ScriptSource 适配器
                           ▼
                  统一 books / scripts 模型
                           │
                           ▼
                   剧本结构化（LLM 拆 beat）
                                  │
                 ┌────────────────┼────────────────┐
                 ▼                ▼                ▼
            画面描述/标签      TTS 配音         字幕时间轴
                 │                │                │
                 ▼                ▼                ▼
        ┌──────────────────────────────────────────────┐
        │ 匹配器：硬过滤 → 向量召回 → 重排 → 时间窗分配 │
        └──────────────────────▲───────────────────────┘
                               │ match_plan
                               ▼
        ┌──────────────────────────────────────────────┐
        │ 渲染：片段差异化 → 字幕 → 混音 → 拼接 → QC    │
        └──────────────────────▲───────────────────────┘
                               │
        ┌──────────────────────┴───────────────────────┐
        │ 素材库（唯一 canonical + 变体 + 使用记录）    │
        └──────────────────────▲───────────────────────┘
                               │
        ┌──────────────────────┴───────────────────────┐
        │ 去重引擎：文件 → 画面 → 音频 → 使用           │
        └──────────────────────▲───────────────────────┘
                               │
        用户需求 → 需求解析 → 查询词/硬过滤 ─┐
                                             ▼
        ┌──────────────────────────────────────────────┐
        │ 采集器：发现 → 选流 → 下载 → 校验 → 元数据    │
        └──────────────────────▲───────────────────────┘
                               │
                          TikTok / 白名单来源

  横切：Agent 编排（状态机 / 审批 / 调度 / 日志 / 控制台）
```

数据流一句话：**采集只写 raw；去重只做索引和状态；推文库适配器只做归一化；匹配只读 canonical；渲染只读匹配计划 + 音频/字幕**。每层单向依赖，方便单独重跑；渲染层差异化只改输出，不回写素材湖。

---

## 3. 模块 A：TikTok 无损采集

### A1. 发现（三种模式，按稳定性排序）

| 模式 | 输入 | 稳定性 | 适用 |
|---|---|---|---|
| A. 清单导入 | URL / 博主主页 / 话题页清单（txt/csv） | 高 | MVP 首选；你知道要谁的内容 |
| B. 关键词搜索 | 关键词 + 数量 + 时长/品类过滤 | 中 | 需要“自动找新素材”时 |
| C. 趋势/榜单 | TikTok Creative Center、第三方榜单 | 中 | 选题参考，不一定能批量下 |

TikTok 网页搜索接口有签名（`X-Bogus`/`msToken`），自己写爬虫很容易挂。建议：

- **MVP 只做 A**：给 URL 清单，稳定、可控、可解释。
- 需要 B 时接第三方（TikAPI / Apify actor / tikwm 之类），或 Playwright 兜底；把 provider 做成可替换适配器，不要绑死一家。
- 无论哪种，最终都归一成 `source` 记录（`aweme_id` 是主键级别的去重键）。

### A1.5 需求解析（自然语言 → 采集参数）

这是「根据我提供的需求自动找」的核心。不要让 LLM 直接决定下载哪条，而是把它变成 **可审计的查询计划**。

用户输入：

> 找 30 条厨房解压素材，暖色调，有切皂/洗杯子，不要露脸，20 秒以上。

解析为：

```json
{
  "queries": ["satisfying kitchen", "soap cutting asmr", "kitchen cleaning asmr"],
  "must": {"scene": ["kitchen"], "action": ["cutting", "washing"],
           "duration_min": 20, "no_face": true},
  "prefer": {"mood": ["warm", "cozy"], "resolution_min": 1080},
  "count": 30
}
```

执行顺序：

1. 查询词 + 硬过滤 → 搜索/清单拿到候选（元数据 + 封面）；
2. 封面用 CLIP 与查询词做粗排，必要时用 VLM 检查「是否露脸 / 是否真厨房」；
3. 只下载 Top-N 全片；下载后 `ffprobe` 再校验硬条件；
4. 数量不够时按「放宽 prefer → 放宽 must 的非关键项 → 报告缺口」降级，不静默凑数。

这样“需求”是可解释、可复现的；出了偏差能定位是查询词、粗排还是硬过滤的问题。

### A2. 下载（关键在“选对流”）

```
1. 展开短链，解析 aweme_id
2. 查 source 表：已存在 → 跳过（避免重复下载）
3. 取详情 JSON：`yt-dlp -J` 优先（签名/地区由它处理）；拿不到无水印流时，
   用 aweme_id 调移动端 feed API 补 `play_addr`
4. 选流：
   - 优先 `bitrateInfo` 里码率最高、且 `play_addr`（无水印）的源
   - 其次 `play_addr` / `playAddr`
   - 禁用 `download_addr`（通常带水印）
   - yt-dlp 的格式里排除 format_id 含 `download` 的；以 `-F` 实测为准
5. 下载到 .part → 校验大小/完整性 → 改名
6. ffprobe + sha256 + 首尾帧解码抽检
7. 写 source / asset 记录，状态 = raw
```

几个硬规则：

- **下载后不转码、不裁切、不压码率**；原始文件放 `assets/raw/`，只读。
- **一条素材只下载一次**：`aweme_id` 和 `sha256` 双键去重。
- **断点续传**：`.part` + HTTP Range；失败重试 3 次，进失败队列。
- **并发 2-3、请求间隔 1-3 秒 + 抖动**；不要开 20 并发，封 IP 得不偿失。
- 需要登录态/年龄限制的内容，用 `--cookies-from-browser` 或导出的 cookie；代理做成可选配置。

### A3. 元数据（每条素材必须落库）

```json
{
  "aweme_id": "...",
  "source_url": "...",
  "author": {"id": "...", "name": "...", "url": "..."},
  "desc": "...",
  "hashtags": ["..."],
  "music": {"title": "...", "author": "..."},
  "stats": {"play": 0, "like": 0, "comment": 0},
  "create_time": "...",
  "download_time": "...",
  "duration": 0.0,
  "width": 0, "height": 0, "fps": 0.0,
  "bitrate": 0, "codec": "h264", "has_audio": true,
  "sha256": "...",
  "watermark": false,
  "license_status": "authorized | cc | unknown",
  "raw_path": "assets/raw/..."
}
```

`license_status` 不是形式主义：`unknown` 的素材默认只能进隔离区/测试池，不能进生产素材库。这是后面避免版权和平台判重风险的第一道闸。

### A4. 稳定性设计

- 详情 JSON 缓存 7 天，重跑不重复请求。
- 失败分类：网络 / 404 / 地区限制 / 需要登录 / 解析失败；分别重试或转人工。
- 下载完成做“可解码”校验（首帧 + 尾帧），防止拿到 HTML 错误页或半截文件。
- 所有请求带 UA、Referer、Cookie（可选）；记录响应码，方便判断是不是被风控。
- 采集任务本身进 `job` 表，支持中断续跑。

### A5. 合规边界（必须内置）

- 只对 **白名单博主 / 明确授权 / CC0 来源** 做批量采集；`unknown` 默认隔离。
- 保留 `source_url` 和抓取时间，方便下架/追溯。
- 不绕过付费/私密/年龄门槛；不提供“批量搬运再发布”的默认开关。
- 频率上做“像人”的限制，不做压力测试式抓取。

### A6. 验收标准（M0）

给 10 条 URL + 1 条自然语言需求：
1. 10 条全部下载成功，均为无水印、最高可用码率；
2. `ffprobe` 信息与平台一致，`sha256` 唯一；
3. 重复跑同一清单，0 重复下载；
4. 断网/中断后重跑可续传；
5. 每条都有完整元数据，`license_status` 可查；
6. 自然语言需求能产出查询计划；候选封面粗排 Top-10 里，人工看至少 7 条符合硬条件。

---

## 4. 模块 B：素材湖与去重

### B1. 素材状态机

```
raw（原始下载，只读）
  → indexed（已抽帧/指纹/元数据）
  → canonical（唯一保留，进生产库）
  → variant（疑似重复，关联到 canonical）
  → quarantine（低质/未知授权/待人工确认）
  → archived（确认不用，保留记录）
```

**永远不物理删除**，只改状态和移动目录；删除必须人工确认。这样误判可恢复。

### B2. 指纹体系（4 个主层 + 2 个增强层）

| 层级 | 方法 | 能抓住什么 | 成本 | 阶段 |
|---|---|---|---|---|
| L0 文件 | `sha256` | 完全相同的文件 | 极低 | M0 |
| L1 画面 | 5 帧 dHash/pHash（10/30/50/70/90%） | 重编码、调色、简单裁剪 | 低 | M0 |
| L2 语义 | CLIP/SigLIP 帧嵌入（均值/注意力池化） | 裁剪、镜像、加字、滤镜、翻拍 | 中 | M1 |
| L3 时序 | 1fps 帧序列 + DTW，或 vPDQ/TMK+PDQF | 变速、剪辑、片段重组 | 高 | M2 可选 |
| L4 音频 | chromaprint（fpcalc） | 同音频换画面、同 BGM | 低 | M1 |
| L5 文本 | OCR 角标/字幕 + ASR 转写 | 改字、换配音、口播重复 | 中 | 可选 |

L2 的几个细节，直接决定召回：

- **多裁剪**：原图、中心 80%、中心 60% 各算一次，取最大相似度，抗裁剪。
- **镜像**：翻转后再算一次，抗左右翻转。
- **抽帧**：每条 5-8 帧，避开纯黑/纯色/转场帧（方差过滤）。
- **归一化**：L2 用余弦相似度；L1 用 Hamming 距离。

### B3. 判定与合并

不要用单一阈值拍脑袋，用“融合分 + 多信号”：

```
score = 0.40 * clip_sim
      + 0.20 * (1 - phash_dist / 64)
      + 0.25 * audio_sim
      + 0.15 * meta_sim（时长/分辨率/作者/文案）
```

初始策略（需要用标注集校准）：

- `sha256` 相同 → 直接 variant。
- `score ≥ 0.92` 且至少 2 个信号命中 → 自动 variant。
- `0.80 ≤ score < 0.92` → `quarantine`，人工复核。
- 其余 → canonical。

**canonical 选择规则**（同组里留哪条）：

1. 分辨率/码率更高；
2. 无水印优先；
3. 时长更完整；
4. 更早入库（来源更可信）；
5. 文件更小（同质量下省空间）。

保留 `variant_of`、`match_score`、`matched_signals`，方便解释“为什么判重”。

### B4. 增量索引与性能

- 入库时只和 **候选集** 比：时长 ±20%、分辨率相近、同作者优先；不要全库 O(n²)。
- 向量索引用 FAISS HNSW（`faiss-cpu` 就够）；< 5 万条也可以先 numpy 暴力算，简单可控。
- 每晚批量重建/合并索引；白天只做增量插入。
- 抽帧临时文件即用即删；缩略图持久化，供控制台复核。

### B5. 使用级去重（现有能力的升级）

现有 `assets.db` 已有 `last_used_at` / `uses` / 7 天冷却，保留并扩展：

- **同一条成片内**：同一 `asset_id` 不重复（现有逻辑）。
- **跨成片**：冷却期内降权；同一素材的 **使用区间** 也记录，避免最近用过同一段起点。
- **按账号隔离**（未来）：不同账号的素材使用记录分开，降低“同素材多号”关联风险。
- **素材健康度**：总可用时长 / 当前产量 = 可撑天数；低于 7 天报警。

### B6. 评估与验收（M1）

先造一个 **标注测试集**：100 条唯一素材 + 30 条人为变体（重编码、裁 10%、镜像、变速 1.1x、加角标、换 BGM、截前 80%）。

验收：
1. 变体召回率 ≥ 95%（30 条里至少 28 条被拦）；
2. 自动合并的误杀率 ≤ 1%（不能把不同素材误判成重复）；
3. 边界样本进隔离区，可人工一键改判；
4. 1000 条入库耗时、索引大小可接受（目标：单条 < 3 秒，不含下载）。

---

## 5. 模块 C：推文库与多模态匹配

### C1. 推文库数据模型与双来源

数据模型在 v1 基础上加来源字段，保证同一条剧本从本地或飞书进来能被识别、更新、追溯：

```sql
script_sources(
  id, kind(local/feishu_bitable/feishu_sheet/csv), name,
  config_json, enabled, last_sync_at
)

books(
  id, source_id, external_id, title, author, genre, language,
  rights_status, channel, cover, raw_json, synced_at
)

scripts(
  id, book_id, source_id, external_id, episode_no, title, text,
  content_hash, language, target_sec, style, hook_type,
  status(draft/approved/published/archived), created_at, synced_at
)

script_assets(
  id, script_id, kind(tiktok_url/audio/subtitle/cover/doc),
  url, local_path, status, meta_json
)

import_runs(
  id, source_id, started_at, ended_at, stats_json, error
)
```

`script_segments`、`audio_tracks`、`subtitles`、`match_plans` 等表不变。关键约定：

- `external_id` 是来源侧稳定 ID（飞书 `record_id` / 本地相对路径），同一来源重复导入按它 upsert。
- `content_hash = sha256(规范化文本)`；文本变了就新版本，旧版本标 `archived`，不物理删。
- `script_assets` 把「文案」和「链接」分开存；一条剧本可以带多个 TikTok 链接、音频、字幕。

#### C1.1 统一适配器接口

```python
class ScriptSource:
    def scan(self, since=None) -> Iterable[ScriptRecord]: ...
    def fetch_binary(self, ref: str, dest: Path) -> Path: ...

@dataclass
class ScriptRecord:
    external_id: str
    book_title: str
    author: str = ""
    episode_no: int = 1
    title: str = ""
    text: str = ""
    language: str = "zh"
    rights_status: str = "unknown"
    assets: list[ScriptAsset] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
```

适配器只做「读 + 归一化 + 下载附件」，不做拆 beat、不做匹配；失败写 `import_runs.error`，不中断整批。

#### C1.2 本地素材库

推荐目录（兼容现有 `novel-auto`）：

```
scripts/
  books.csv
  <book>/<ep>.md
  <book>/<ep>.json        # 可选，字段更全
  audio/<book>/<ep>.mp3
  subtitles/<book>/<ep>.srt
  imports/feishu/*.csv    # 飞书导出兜底
```

`books.csv` 列：`book_title, author, genre, language, rights_status, channel`。
`<ep>.md` 用 YAML frontmatter：

```yaml
---
book: 示例书名
episode: 1
title: 第1集
language: zh
rights_status: authorized
assets:
  - kind: tiktok_url
    url: https://www.tiktok.com/@xxx/video/123
  - kind: audio
    path: audio/示例书名/001.mp3
  - kind: subtitle
    path: subtitles/示例书名/001.srt
---
正文……
```

`txt/docx` 没有 frontmatter 时，用同目录 `<ep>.meta.json` 补字段；`csv` 按列映射。本地文件必须校验路径在工作区内，防止 `../` 越界。

#### C1.3 飞书表格 / 多维表格

支持三种接入，按稳定性排序：

| 方式 | 适用 | 优点 | 代价 |
|---|---|---|---|
| A. 多维表格 API（Bitable） | 表格是「多维表格」 | 可增量、字段类型清楚、可拿 record_id | 要建应用、配权限 |
| B. 电子表格 API（Sheet） | 普通飞书表格 | 直接读区域值 | 合并单元格/多 sheet 要额外处理 |
| C. 导出 CSV/XLSX | 不想配 API | 零权限、最快 | 手动导出，不能自动增量 |

配置放 `config/feishu.yaml`，密钥只从环境变量/keychain 读，不写进仓库：

```yaml
source: bitable
app_token: env:FEISHU_APP_TOKEN
table_id: env:FEISHU_TABLE_ID
app_id: env:FEISHU_APP_ID
app_secret: env:FEISHU_APP_SECRET
field_map:
  external_id: record_id
  book_title: 书名
  episode_no: 集数
  title: 标题
  text: 正文
  tiktok_urls: 素材链接
  audio: 音频
  subtitle: 字幕
  rights_status: 授权状态
  status: 状态
```

字段映射规则：

- 文本字段：直接取值；富文本取 `text` 片段拼接。
- URL 字段：取 `link` + `text`；纯文本里的 URL 用正则提取。
- 附件字段：拿 `file_token`，调 Drive 下载到 `scripts/imports/feishu/<record_id>/`。
- 关联字段：只取一层，避免递归；关联记录 ID 存 `raw_json`。
- 单选/多选：转字符串/列表；未知选项保留原值并告警。

同步逻辑：

1. `scan()` 按 `last_modified_time` 或全量拉取，按 `(source_id, external_id)` upsert。
2. 文本 `content_hash` 变化 → 新 `scripts` 版本，旧版 `archived`；已有 `match_plans` 不回溯改。
3. 来源里消失的记录 → 标 `archived`，不删除；避免历史 job 断链。
4. 本地与飞书同书同集冲突 → 按 `source_priority`（默认 `feishu > local`）或进复核队列；不静默覆盖。
5. 每批写 `import_runs`（新增/更新/跳过/失败数），失败可重试。

#### C1.4 链接分类（关键）

飞书表里的「链接」不一定都是视频。导入时逐个分类，不能一律塞进下载器：

| 链接/字段 | 判定 | 去向 |
|---|---|---|
| `tiktok.com/@.../video/...`、`douyin.com/video/...`、`youtube.com/shorts/...` | 视频源 | `script_assets(kind=tiktok_url)` → 采集队列 |
| `feishu.cn/docx/...`、`/wiki/...` | 飞书文档 | 拉 `raw_content` 当文案；失败则保留链接人工处理 |
| `feishu.cn/file/...`、Drive 附件 | 文件 | 下载；按扩展名归 audio/subtitle/cover |
| `.mp3/.wav/.m4a` | 音频 | `kind=audio` |
| `.srt/.ass/.vtt` | 字幕 | `kind=subtitle` |
| 本地绝对/相对路径 | 本地文件 | 校验存在且在工作区内 |
| 其他 http(s) | 未知 | 存 `raw_json` + 告警，不自动下载 |

下载器只接收第一类；音频/字幕走附件下载，不经过 TikTok 解析。这样避免「把文档链接丢给 yt-dlp」这类错误。

#### C1.5 权限与降级

- 飞书应用需要被加为表格/文档的协作者；权限最小化：多维表格只读、Drive 只读、文档只读。
- 拿不到 API 权限时，降级为「导出 CSV/XLSX 到 `scripts/imports/feishu/`」，功能不阻塞。
- 不在日志里打印 `app_secret`/token；不把飞书 cookie 拿来做网页爬取。
- 限流：按飞书返回的 429 退避；批量拉取分页，默认 500 条/页。

**需要你给一个样例**：飞书表是「多维表格」还是普通表格？表里是纯文案，还是文案 + TikTok 链接 + 音频/字幕混在一起？发我一个导出 CSV（或截图表头）即可，我按真实字段映射，不猜。

### C2. 剧本结构化（LLM 的关键位置）

不要直接把整篇剧本丢给匹配器。先拆成 beat，每个 beat 带“可检索的画面描述”：

```json
{
  "seq": 3,
  "text": "她把汤端到婆婆面前，手一直在抖。",
  "role": "escalation",
  "mood": "tense",
  "scene": "indoor_kitchen",
  "keywords": ["汤", "手抖", "婆媳"],
  "visual_desc": "close-up of trembling hands holding a bowl of soup, dim kitchen, tense mood",
  "est_sec": 12
}
```

要点：

- `text` 保持原文，给 TTS 和字幕用；`visual_desc` 是给 CLIP 检索用的“画面提示词”，不是给人看的。
- `role` 控制节奏和钩子位置；`mood` 控制素材色调；`est_sec` 由字数和语速估算，后面用真实音频时长修正。
- 一次拆 20-40 个 beat（对应 3-10 分钟），每个 beat 6-15 秒，和现有 `assets.max_segment` 对齐。
- 拆完做一次“时长预算”校验：总 `est_sec` 与目标时长偏差 > 10% 就回炉调整。

### C3. 音频与字幕

**人声（默认生成式）**

- 沿用现有 edge-tts / 火山 / MiniMax Provider，逐 beat 合成。
- 升级点：拿 **词级时间戳（WordBoundary）**，而不是只有句级；字幕按词对齐，长句断行更准。
- 保留现有缓存机制（同句复用），改文案不重复计费。

**已有音频（检索式，可选）**

- 如果推文库自带配音，用 `faster-whisper` / WhisperX 做词级转写 + 强制对齐到剧本；
- 对齐置信度低的句子标记人工复核，不要硬烧字幕。

**已有字幕（检索式，可选）**

- 若推文库自带 `.srt/.ass`，解析成时间轴并与音频做重叠校验；整体偏移 > 200ms 就平移，局部错位标记复核；没有就按 TTS 词级时间戳生成。

**BGM / 环境音**

- 建一个小型音频库（雨声、翻书、键盘、低频鼓点），按 `mood` 匹配；
- 人声优先，BGM 建议 -28 ~ -30 dB，侧链压缩；响度统一 -16 LUFS（现有 QC 已有）。

**字幕**

- 沿用现有 ASS/PIL 渲染管线；增加：关键词强调、每行最大字数、底部安全区（避开 TikTok UI）。
- 输出 `subtitles.srt` 和 `subtitles.ass` 两份，方便人工改。

### C4. 素材标注（让素材“可被检索”）

每条 canonical 素材入库时自动算：

| 维度 | 方法 | 用途 |
|---|---|---|
| 技术 | ffprobe | 时长/分辨率/帧率/码率/音轨 |
| 画质 | Laplacian 清晰度、亮度、抖动 | 过滤糊/暗/抖 |
| 镜头 | PySceneDetect 分镜 + 关键帧 | 按镜头而不是整条检索 |
| 语义 | CLIP/SigLIP 帧嵌入 | 以图搜图、文本搜图 |
| 标签 | CLIP zero-shot（场景/动作/情绪/物体） | 硬过滤、可解释 |
| 运动 | 帧差/光流强度 | 匹配“静态/动态”段落 |
| 文本 | OCR（可选） | 避开带大字/水印的片段 |

**冷启动策略**（很重要，别一上来追求全自动标签）：

1. 技术指标 + CLIP 嵌入全自动，零人工；
2. zero-shot 标签先用一个 30 词左右的小词表（场景 6、动作 6、情绪 6、物体 6、光线 3、节奏 3），够用再扩；
3. 只对“高频使用/高价值”的素材做人工分类和情绪标注；
4. VLM 打标（Qwen2.5-VL 等）作为可选项，按条计费，先跑 100 条看效果再决定。

### C5. 匹配算法（四步）

**Step 1 硬过滤**

- 时长 ≥ beat 需求 + 1s 余量；
- 方向/分辨率可满足竖屏裁切（横屏可裁，但优先级降低）；
- 不在本片已用集合；
- 不在冷却期（或降权）；
- `license_status = authorized/cc`；
- 排除黑帧/静音/低清晰度。

**Step 2 召回**

- 标签倒排（`scene`/`mood`/`action`）+ 向量 Top-K（每 beat 取 50 条）；
- 文本侧：`visual_desc` 用 CLIP text encoder 编码。**文本模型和描述语言要匹配**：英文描述用 OpenCLIP/SigLIP（开放域更强），中文描述用 Chinese-CLIP；建议统一写英文短描述。
- 长剧本注意：CLIP 对长句不敏感，`visual_desc` 控制在 1-2 句，具体名词 + 动作 + 光线。

**Step 3 重排**

```
final = 0.40 * clip_sim
      + 0.20 * mood_match
      + 0.15 * quality
      + 0.10 * freshness（冷却越久越高）
      + 0.15 * diversity（与已选片段的视觉差异，MMR）
```

- 用 MMR 做多样性：避免 10 个 beat 全选同一种“白墙+手”；
- 同一素材的不同镜头可以分别候选，但同一 `asset_id` 默认只用一次（素材池足够时）；
- 分数和理由写进 `clip_plan.json`，方便回查“为什么选它”。

**Step 4 时间窗分配**

- 长素材（30 分钟-3 小时）随机取 8-15 秒窗口；优先选运动/清晰度高的区间；
- 避开片头片尾、黑场、静止过长段；
- 同一素材跨 beat 复用时，窗口尽量不重叠（池子不足时降级并告警）；
- 全局做一次“覆盖校验”：总时长必须覆盖音频，否则报错（现有逻辑保留）。

**降级顺序**：精确标签 → 放宽 mood → 放宽 scene → 任意新鲜素材 → 复用但换窗口（警告）。

### C6. 质量与反馈

- 自动指标：覆盖率 100%、单素材复用次数、平均相似度、字幕对齐误差、黑帧/静音、响度。
- 人工抽检：每 20 个 beat 看 5 个，Top-3 里有没有“明显不搭”的。
- 记录用户“换素材/换开头/换音色”的操作，作为偏好信号；初期只做统计，不做自动学习。
- 发布数据（完播/互动）回流后，再考虑调权重；不要在冷启动阶段过拟合。

### C7. 验收标准（M2/M3）

1. 一篇 3-5 分钟剧本 → 输出完整 `clip_plan.json`，每个 beat 都有画面，覆盖率 100%；
2. 同一成片内无重复素材；跨成片遵守冷却；
3. 人工抽检 20 个 beat，Top-3 里至少 16 个“可用/不违和”（80%）；
4. 字幕与语音对齐误差 < 100ms；无错行/溢出；
5. 渲染成片通过现有 QC（黑帧/静音/响度/时长）；
6. 匹配耗时：单条 5 分钟成片 < 30 秒（不含渲染）。

---

## 6. 模块 D：Agent 编排

### D1. Agent 的形态

建议做 **“确定性流水线 + LLM 节点 + 人工审批”**，而不是让模型自由发挥的 autonomous agent：

- LLM 负责：剧本改写/拆 beat/画面描述/发布文案。
- 代码负责：下载、校验、指纹、去重、检索、分配、渲染、QC。
- 人负责：采集清单审批、边界重复复核、开头选择、发布。

这样可控、可复现、出错能定位；也更符合你现在的 `novelauto` CLI + Web 控制台形态。

### D2. 状态机

```
REQUESTED
  → COLLECTING（采集）
  → INDEXING（入库/指纹）
  → DEDUPED（去重完成）
  → SCRIPT_READY（剧本结构化）
  → MATCHED（画面/音频/字幕计划）
  → RENDERING（渲染 + 可选差异化）
  → QC
  → DELIVERED
```

每个状态可重入；失败保留现场（raw/中间文件），支持从任意阶段续跑。现有 `work/<job_id>` 的分阶段产物可以复用。
渲染层差异化是 `RENDERING` 的子步骤，不新增状态；某个片段差异化失败时，回退到 `off` 档重跑该片段，不阻塞整条成片。
采集/入库/去重属于“素材准备”阶段：素材库已就绪时可以跳过，直接从 `SCRIPT_READY` 开始；批量生产通常复用同一批素材。

### D3. 工具清单（Agent 可调用）

| 工具 | 作用 | 关键参数 |
|---|---|---|
| `collect.plan` | 自然语言需求 → 查询计划 | text, count |
| `collect.urls` | 按清单下载 | file, workers, cookies |
| `collect.creator` | 按博主主页采集 | url, limit, date_range |
| `collect.search` | 关键词搜索（可选 provider） | keyword, limit, filters |
| `library.ingest` | 入库/抽帧/指纹 | dir, license |
| `library.dedup` | 批量去重/合并 | thresholds, dry_run |
| `library.stats` | 素材健康度 | — |
| `script.import` | 导入推文库 | file/format |
| `script.segment` | 拆 beat + 画面描述 | script_id, target_sec |
| `match.plan` | 生成匹配计划 | script_id, constraints |
| `render.run` | 渲染（含可选差异化） | job_id, hook, voice, diff_profile |
| `qc.run` | 质检 | job_id |

### D4. 审批点（Human-in-the-loop）

1. **采集前**：展示候选数量、预计体积、来源/授权状态 → 确认再下。
2. **去重后**：只把 `quarantine` 的边界样本推到复核队列；一键“保留/合并/隔离”。
3. **剧本/开头**：沿用现有「3 个开头让用户选」。
4. **发布前**：只产出成品 + 文案，上传仍由你手动完成（TikTok 个人号没有官方发帖 API）。

### D5. 调度与可观测

- `launchd`/cron：每天低峰期采集、每晚重建索引、按需批量渲染。
- 控制台新增面板：素材健康度（总时长/可撑天数/重复率/分类缺口）、采集任务、复核队列、匹配计划预览。
- 日志按 job 落盘；关键指标（下载成功率、重复率、匹配耗时、QC 通过率）做周报。

### D6. 渲染层差异化（去重处理工具箱）

已确认：工具箱只做「成片差异化」，不参与库内查重。接入点放在 `render.py::_clip_filter`，对每个已选片段在拼接/字幕/混音之前做一次，避免二次转码和字幕被裁。

**配置档位**（沿用工具箱预设，可自定义）：

| 档位 | 缩放 | 裁切（上/下/左/右） | 镜像 | 亮度/对比/饱和 | 色相 | 噪点 | 变速/抽帧 |
|---|---|---|---|---|---|---|---|
| off | 100% | 0 | 否 | 1.0 | 0 | 0 | 关 |
| subtle | 102.5% | 0.5/0.3/0.5/0.4% | 否 | 1.01 | +1.5° | 2 | 关 |
| medium | 105% | 1/1/1.5/1% | 否 | 1.03 | +3° | 4 | 默认关 |
| strong | 108% | 3/2.5/3/2.5% | 是* | 1.05/1.06/1.05 | +5° | 6 | 默认关 |

\* 镜像只对 OCR 未检测到文字/角标的片段启用；否则降级为不镜像。

**顺序（重要）**：

```
片段解码 → 裁切/缩放 → 镜像 → 调色/色相 → 噪点 → Ken Burns → fps/trim
        → 章节 concat → 字幕 overlay → 人声/BGM 混音 → 章节拼接 → QC
```

- 差异化必须在字幕 overlay 之前；否则字幕会被裁切或镜像。
- 音频不参与滤镜；人声/BGM 在之后混音，避免变速/抽帧造成音画错位。
- 每个片段参数由 `(job_id, asset_id, start)` 派生随机种子，保证同一 job 重跑结果一致，且不修改素材源文件。

**安全边界**：

- 裁切上限 3%，缩放上限 108%；超过容易切到人脸/主体，QC 直接判失败。
- `speed` 默认 100%；只有显式 `allow_retime=true` 才启用，并按「输入时长 = 输出时长 × speed」重新取源、重算时间窗。
- `frameDrop/frameDuplicate` 默认关闭；它会让动作变卡，且对平台判重的收益不如裁切/调色稳定。
- 成片输出仍按现有 1080×1920、30fps、-16 LUFS；差异化不改变规格。

**工具箱怎么“用”**：

- 默认：把它的预设和参数搬进 `render.py` 的 filter graph，流水线内自动执行，不额外交给 Electron。
- 备用：保留 `.app` 做人工发布前处理；流水线输出 `publish/master.mp4` + `diff_profile.json`，你手动跑工具箱时按同一档位，不重复自动处理。
- 不建议把整片丢给工具箱再回传：会二次转码、可能裁掉字幕、且无法自动 QC。

---

## 7. 数据与目录结构

在现有 `novel-auto/` 上扩展，尽量不破坏已有路径：

```
novel-auto/
  assets/
    raw/                 # 原始下载，只读，按作者/日期分目录
      tiktok/<author>/<aweme_id>.mp4
    clips/               # canonical 生产素材（现有 ingest 入口保留）
    quarantine/          # 疑似重复/低质/未知授权
    thumbnails/          # 抽帧缩略图，供复核
    assets.db            # 现有库，扩表
    index/               # FAISS/numpy 索引
  scripts/               # 推文库（本地源）
    books.csv
    <book>/<ep>.md
    audio/
    subtitles/
    imports/feishu/      # 飞书导出兜底
  config/feishu.yaml     # 字段映射；密钥走 env/keychain
  work/<job_id>/         # 现有中间产物
    diff/                # 每个片段的差异化参数快照
  output/<job_id>/       # 现有成片
  publish/<job_id>/      # 可选：未差异化母版 + diff_profile.json
```

数据库在同一 SQLite 里加表，避免过早引入 Postgres；等素材 > 10 万条或并发写入再考虑迁移。

新增/扩展表（示意）：

```sql
sources(id, platform, aweme_id, url, author_id, author_name, desc,
        hashtags, music, stats_json, create_time, fetched_at, license_status)

assets(id, source_id, raw_path, sha256, duration, width, height, fps,
       bitrate, codec, has_audio, status, canonical_id, quality_json,
       added_at, last_used_at, uses)

fingerprints(asset_id, kind, value, meta_json)
asset_tags(asset_id, key, value, confidence, source)
asset_shots(asset_id, shot_idx, start, end, embedding_path, quality_json)
usage(asset_id, job_id, start, duration, used_at)

script_sources(id, kind, name, config_json, enabled, last_sync_at)
books(id, source_id, external_id, title, author, genre, language,
      rights_status, channel, raw_json, synced_at)
scripts(id, book_id, source_id, external_id, episode_no, title, text,
        content_hash, language, target_sec, style, hook_type,
        status, raw_json, synced_at)
script_assets(id, script_id, kind, url, local_path, status, meta_json)
import_runs(id, source_id, started_at, ended_at, stats_json, error)
script_segments(id, script_id, seq, text, role, mood, scene, keywords,
                visual_desc, est_sec)
audio_tracks(id, script_id, segment_id, path, voice, rate, duration, word_ts_json)
subtitles(id, audio_id, path, format, language)
match_plans(id, job_id, script_id, plan_json, score, created_at)
jobs(id, type, status, input_json, output_json, error, started_at, ended_at)
```

---

## 8. 技术选型

| 能力 | 首选 | 理由 | 备选 |
|---|---|---|---|
| 下载 | `yt-dlp` + 移动端详情 API | 成熟、可兜底、支持 cookies | TikAPI / Apify / tikwm |
| 媒体处理 | `ffmpeg` / `ffprobe` | 现有管线已在用 | — |
| 数据库 | SQLite（现有） | 单机、零运维、够用 | Postgres（>10 万条） |
| 向量索引 | FAISS HNSW / numpy | Mac 可跑、无 GPU 也行 | sqlite-vec、Milvus |
| 视觉嵌入 | Chinese-CLIP / SigLIP / OpenCLIP ViT-B | 文本搜图、去重都用它 | BLIP-2（更重） |
| 镜头检测 | PySceneDetect | 成熟 | ffmpeg 场景检测 |
| 音频指纹 | chromaprint (`fpcalc`) | 轻、准 | audfprint |
| ASR/对齐 | faster-whisper / WhisperX | 已有音频时用 | 云 ASR |
| OCR | RapidOCR / PaddleOCR | 可选，查角标/字幕 | 云 OCR |
| 推文库接入 | 飞书开放平台（Bitable / Sheet / Drive） | 官方 API、可增量 | CSV/XLSX 导出兜底 |
| 渲染差异化 | 现有 FFmpeg filter + 工具箱预设 | 不二次转码、可自动 QC | 短视频去重工具箱（人工） |
| 编排 | Python 状态机 + SQLite job 表 | 简单、可调试 | Prefect/Dagster（过度） |
| 控制台 | 现有 FastAPI + Jinja | 复用 | — |
| 定时 | launchd / cron | macOS 原生 | — |

原则：**能在 Mac 本地跑、不引入重型服务**。CLIP/Whisper 模型按需加载，不常驻。

---

## 9. 实施路线

| 阶段 | 范围 | 交付物 | 验收 | 估算 |
|---|---|---|---|---|
| **M0 采集闭环** | sources/assets 表、需求解析（简版）、URL 清单采集、选流、校验、精确+dHash 去重、CLI | `collect` 命令 + 元数据 + 原始库 | 10 条 URL 全成功、无水印、0 重复下载 | 2-3 天 |
| **M1 去重闭环** | CLIP 嵌入、音频指纹、融合判定、隔离区、健康度面板 | `dedup` + 评估报告 + 复核 UI | 变体召回 ≥95%、误杀 ≤1% | 2-3 天 |
| **M2 推文库 + 基础匹配** | 本地库 + 飞书 CSV/API 导入、beat 拆分、素材标注、硬过滤+向量召回+分配、接入 `pick()` | `script` / `match` 命令 + `clip_plan.json` | 1 篇剧本全覆盖、无重复、抽检 80% 可用 | 5-6 天（仅 CSV 为 4-5 天） |
| **M3 音字与重排** | 词级 TTS 时间戳、ASS 升级、BGM 情绪匹配、MMR 重排、渲染层差异化、QC 扩展 | 成片 + 字幕 + 匹配评分 + `diff_profile.json` | 字幕误差 <100ms、成片 QC 通过、差异化可开关 | 4-5 天 |
| **M4 Agent 编排** | 状态机、审批点、定时任务、控制台面板、批量队列 | 一键跑 + 审批 + 看板 | 10 篇剧本无人值守批量出片 | 3-4 天 |

**MVP（建议先做这个）**：M0 + 一个极简匹配器（只用标签和时长，不做 CLIP），推文库先用本地目录或飞书导出的 CSV，跑通「20 条 URL → 去重 → 1 篇剧本 → 1 条成片」；渲染时用 `subtle` 档验证差异化开关，不默认 `strong`。等这条链路顺了，再上 CLIP 重排、音频指纹和飞书 API 增量同步。

---

## 10. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| TikTok 反爬/风控 | 采集失败、IP 被封 | 低并发+间隔、cookies、缓存、第三方 provider 兜底；不追求大规模 |
| 素材版权/平台判重 | 下架、限流、侵权 | 白名单+授权字段；unknown 隔离；成片差异化；不默认搬运 |
| “无损”预期错位 | 以为能拿到拍摄原片 | 明确“平台最高码率源文件”，验收按 ffprobe |
| 去重误杀 | 好素材被隔离 | 多信号融合、边界进隔离区、永不物理删除 |
| 去重漏判 | 重复素材进成片 | 时序/音频指纹补强；发布前抽检 |
| 匹配“语义不搭” | 画面出戏 | 短画面描述、中文 CLIP、MMR 多样性、人工抽检反馈 |
| 素材池不足 | 反复用同几条 | 健康度面板、采集计划、降级策略明确告警 |
| 存储增长 | 硬盘吃紧 | raw 只存一份、缩略图、归档策略、定期清理 quarantine |
| 模型/算力 | Mac 跑 CLIP 慢 | 批量编码、MPS、按需加载；Whisper 只对已有音频用 |

---

## 11. 需要你拍板的 6 个问题

**已确认，不再问：**

- 「去重处理工具箱」放在渲染层做差异化，不参与库内去重；默认 `subtle`，需要更强再切 `medium/strong`。
- 推文库支持本地素材库 + 飞书表格/多维表格双来源；表格里的链接按类型分流，不一律当视频。

剩下 6 个是落地细节：

1. **推文库样例与权限**：本地库现在是「目录 + 文件」，还是已有 JSON/CSV/数据库？飞书是「多维表格」还是普通表格？表里是纯文案，还是文案 + TikTok 链接 + 音频/字幕混排？能否发我 1 行导出样例或截图表头？你愿意建一个只读飞书自建应用，还是先用 CSV 导出兜底？
2. **采集入口**：你手上有明确的 URL/博主/话题清单，还是要按关键词自动搜？每天大概多少条、什么品类？有没有可用的 cookie/代理？
3. **素材来源与授权**：这些 TikTok 素材是自有/授权/CC，还是从别人账号下载？如果是后者，是否接受「只对白名单采集 + unknown 进隔离区」的硬约束？
4. **去重策略**：严格只留一份，还是保留“变体”用于成片差异化？边界样本你要人工复核，还是全自动？
5. **匹配优先级**：语义贴合 > 画面多样性 > 出片速度，还是反过来？目标时长仍是 3-10 分钟？
6. **运行环境**：只在 Mac 本地跑吗？素材盘大概能留多少 GB？是否接受装 CLIP/Whisper 模型（各 1-2GB）？

每个问题我都给了默认建议（见正文），你也可以直接说“按默认开工”，我就按：**URL 清单采集、unknown 隔离、严格去重但保留变体记录、语义优先、Mac 本地、3-10 分钟、渲染差异化默认 subtle** 来搭。

---

## 12. 我建议的第一步

先做一个 **7 天可验收的 MVP**，不做关键词爬虫、不做全自动 Agent：

1. 你给 1 条自然语言需求 + 20-30 条 TikTok URL（或 3-5 个博主主页）；
2. 我实现 `collect`：需求解析、下载、校验、元数据、精确+dHash 去重；
3. 你给 1 篇推文剧本 + 现有素材目录；
4. 我实现极简 `match`：按 beat 时长 + 标签/分类选片，复用现有渲染；
5. 产出 1 条 3-5 分钟成片 + `clip_plan.json` + 素材健康度报告。

验收通过后，再决定是否上 CLIP 语义匹配、音频指纹、词级字幕和定时采集。这样每一步都能看到东西，不会憋两周做出一个“看起来很强但不好用”的大系统。

---

## 13. 复用已有的 Codex 产出

本机找到了三套相关代码。结论：**下载器和去重扫描器可以直接复用；「去重处理工具箱」不能当库内去重，只能放到渲染/发布层。**

### 13.1 三个项目

| 项目 | 位置 | 实际能力 | 复用判断 |
|---|---|---|---|
| 视频下载器 | `~/Documents/Codex/2026-06-13/video-downloader` | PySide6 + yt-dlp；抖音单个下载，TikTok/YouTube 主页批量；格式列表、Cookies、队列、限速 | **模块 A 可直接复用引擎**，不要连 GUI 一起搬 |
| TikTok 去重扫描器 | `~/Documents/Codex/2026-06-05/tiktok/tiktok_dedupe.py` | 精确 SHA-256 + 抽帧 dHash + 音频包络 + 时长；输出 JSON/CSV；只报告，不删除 | **模块 B 的 MVP 可直接复用** |
| 短视频去重工具箱 | `~/Documents/Codex/去重/files-mentioned-by-the-user-video-2`、`/Applications/短视频去重工具箱.app` | Electron；画面处理/批量处理（裁剪、缩放、镜像、调色、噪声、变速、抽帧），另有单对文件检测页；**没有下载** | 属于「成片差异化/发布层」，**不要拿它做库内去重** |

另外 `~/Documents/Codex/2026-06-13/video-downloader` 的下载输出默认在 `~/Downloads/VideoDownloader`，文件名模板是 `%(title).100s [%(id)s].%(ext)s`；`~/.video-downloader/config.json` 里是并发 2、间隔 2 秒、Chrome Cookies 这些设置。

### 13.2 为什么不能把「去重工具箱」当库内去重

它的“去重”是**把一条视频改得和原来不一样**（裁一点、镜像、调色、加噪、变速、抽帧），用来降低发布时的画面指纹相似度。这会让同一条素材变成多条“变体”，如果放在入库前去跑，素材湖会被自己制造出来的变体污染。

正确的位置是：**匹配完成后、渲染阶段**，对选中的片段做轻量差异化；而且这属于“发布层”，不是“库内去重”。库内去重仍然用 `tiktok_dedupe.py` 那套“检测 + 隔离”。

### 13.3 接入方式

```
video-downloader/DownloadEngine
        ↓ 抽成无 Qt 依赖的 provider
novelauto/collect/ytdlp_provider.py → sources / raw
        ↓
tiktok_dedupe.py 的算法
        ↓ 抽成可 import 的扫描器
novelauto/dedup/scanner.py → fingerprints / matches / quarantine
        ↓
novel-auto 现有 match / render
        ↓
（可选）短视频去重工具箱的 FFmpeg 滤镜 → 发布层差异化
```

**A. 下载器：复用引擎，不复用 GUI**

- 抽 `DownloadEngine`、`ProfileScraper`、`FormatInfo`、`url_parser.py`，去掉 PySide6/Qt 依赖，做成 `collect` 的 provider。
- 保留现有 GUI 作为人工工具；流水线只调 CLI，不 import Qt。
- 需要补：批量 URL 清单、元数据 JSON、`aweme_id`/`sha256` 双键去重、稳定输出目录、失败分类与重试、`license_status`、选流策略（禁用 `download_addr`、优先最高码率 `play_addr`）。
- 现在的 `best_format_id()` 优先“最高分辨率的视频+音频”，不等于“最高码率且无水印”；接入时要按我们的选流规则覆盖，不能直接沿用。

**B. 去重扫描器：复用算法，补数据库和分块**

- 把 `sha256_file`、`frame_hashes`、`audio_hash`、`combined_score`、`compare_pair`、`build_groups` 抽成 `novelauto/dedup/` 下的库，保留 JSON/CSV 导出。
- 补：`fingerprints`/`matches` 表、候选分块（时长 ±20%、分辨率相近）、`canonical/variant/quarantine` 状态、canonical 选择规则、复核队列。
- 当前是 O(n²) 全量两两比较，素材上千后会慢；MVP 可接受，M1 必须加候选分块或向量索引。
- 音频是 64 位能量包络（前 60 秒），只能抓“很像”的音频；后续换 chromaprint。
- 抽帧是 17×16 dHash、默认每 2 秒 1 帧、最多 16 帧；比现在 `novel-auto` 的“首帧 Hamming≤4”强很多，足够做 MVP。

**C. 去重工具箱：只借滤镜，不借流程**

- 只保留它的 FFmpeg 参数预设（轻微/中等/强力）和“随机参数”思路，接到渲染阶段。
- 不在入库前跑，不生成 `_processed` 文件进素材湖。
- 它的单对文件检测页可以当人工复核工具，但不承担全库扫描。

### 13.4 能省多少时间

| 阶段 | 原估算 | 复用后 | 省下来的工作 |
|---|---|---|---|
| M0 采集闭环 | 2-3 天 | 1-2 天 | URL 解析、yt-dlp 封装、格式列表、Cookies、队列逻辑 |
| M1 去重闭环 | 2-3 天 | 1-2 天 | SHA-256、抽帧 dHash、音频包络、融合分、JSON/CSV 报告 |

仍然要自己写：数据库与状态机、元数据、选流/授权策略、候选分块、复核 UI。**不建议直接复用它的 GUI、配置和数据库**，否则会把两个项目的生命周期绑死。

### 13.5 风险

- **Qt 耦合**：下载器引擎目前和 UI 在同一工程里，直接 import 会把 PySide6 带进流水线；必须先抽 adapter。
- **选流不等于无损**：现有格式排序按分辨率和综合分，不保证最高码率/无水印；接入时重写选择逻辑。
- **扫描器性能**：全量两两比较，素材量上来会卡；MVP 后必须加分块。
- **音频指纹弱**：64 位包络对变速、换调、片段截取不稳；先接受，M1 后再换。
- **授权缺失**：两个工具都没有 `license_status`；接入时必须补，否则 unknown 素材会直接进生产库。
- **维护方式**：建议“复制 + 改造”进 `novel-auto`，不要做 git submodule；代码量不大，耦合更低。

### 13.6 需要你确认

1. 你说的“应用”是上面哪一套？如果是另一套，把路径发我。
2. GUI 要保留吗？建议保留（人工下载/复核用），流水线走 CLI。
3. 去重工具箱只放在渲染层做差异化，可以接受吗？
