# 小说推文自动化 · TikTok 解压视频

输入一段小说原文，自动产出 **3-10 分钟竖屏成品视频 + 发布文案 + 质检报告**。

```
原文 → 改写（3 版黄金三秒 + 章回钩子）→ 自动配音 → 字幕 → 素材剪辑 → 渲染合成 → 发布文案 → 质检
```

- 英文海外版默认（可切中文）、1080x1920、3-10 分钟
- 配音 edge-tts（可换火山 / MiniMax，支持音色与语速）
- 素材自动选片、冷却防撞车，成片带 Ken Burns 动效与淡入淡出字幕
- 交付物：`final.mp4` + `cover.jpg` + `publish.txt` + `qa_report.txt`

## 目录

| 路径 | 内容 |
|---|---|
| `novel-auto/` | 可运行的完整工程（Python + FastAPI 控制台 + 双击启动器），使用文档见 [novel-auto/README.md](novel-auto/README.md) |
| `小说推文自动化方案-v2.md` | 整体方案：流水线设计、长视频章回钩子、时长收敛、质检、成本 |
| `参考方案对比与借鉴清单.md` | 与另一份需求文档的对比与借鉴项 |
| `.dsh/skills/novel-promo/` | DSH 技能：在对话里直接说需求即可跑流水线 |

## 快速开始

```bash
cd novel-auto
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # 填 LLM_API_KEY（DeepSeek）
./.venv/bin/python -m novelauto doctor          # 自检 ffmpeg / Key / 字体
./.venv/bin/python -m novelauto web             # 打开控制台
```

macOS 也可以直接双击 `novel-auto/小说推文控制台.app`。

**首次使用没素材？** 用测试素材跑通流程：

```bash
./.venv/bin/python -m novelauto assets synth --count 3 --seconds 90
```

## 不会上传进仓库的东西

`.venv/`、`work/`、`output/`、`.cache/`、`assets/clips/`（你的视频素材）、`.env`（API Key）都已被 `.gitignore` 排除，仓库里只有代码、文档和示例文本。

## 版权与使用

- 代码以 MIT 许可发布（见 `LICENSE`）。
- 小说原文、配音音色、视频素材的版权归各自权利人；商用前请确认授权，本项目不附带任何素材内容。
