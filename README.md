# AI 校园版《败犬女主太多了！》OP · 制作过程开源

把《败犬女主太多了！》的无字幕 OP（NCOP，90.97 秒）整支重做：18 位出场角色全部换成各家大模型的拟人 AI 娘，片头 staff 表换成 AI 圈的研究者和公司。原曲、剪辑节奏和分镜全部保留。

成片：[B 站 BV1Nfag6xEHQ](https://www.bilibili.com/video/BV1Nfag6xEHQ/)

这个仓库公开的是**做法**：分镜计划、每个镜头最终通过的提示词、18 位 AI 娘的人设图和生成提示词、完整演员表，以及整条流水线的代码和 ComfyUI 工作流。**不包含**任何原作画面、生成视频或音乐。

## 流程

| 步骤 | 做什么 | 文档 |
|---|---|---|
| 1. 切分镜 | 按原片剪辑点切成 81 个镜头，确定每镜的人物、字幕和替换方式 | [docs/01-分镜切分.md](docs/01-分镜切分.md) |
| 2. 逐镜人物替换 | 本地 MiniMax H3 做视频编辑：原镜头 + AI 娘人设图 → 换人换字的新镜头；人工逐镜验收，不行就改提示词重跑 | [docs/02-人物替换.md](docs/02-人物替换.md) |
| 3. 拼接 | 每个镜头取最终通过的版本，按原时间轴拼回，铺原音轨 | [docs/03-拼接.md](docs/03-拼接.md) |
| 4. 超分 | 1024×576 → 1080p；对比了 SeedVR2、AnimeJaNai、Real-CUGAN、Topaz，最终用 Topaz Astra 2 | [docs/04-超分.md](docs/04-超分.md) |

## 数字

- 81 个原镜头，合并成 73 个验收单元；2181 帧，24000/1001 fps
- 生成分辨率 1024×576；59 个单元由 H3 生成（2014 帧），9 个直接用原片，4 张字卡/道具用生图，1 个 CPU 排版
- 硬件：单张 RTX 5090（32 GB）；每个 56 帧镜头约 2.5 分钟

## 目录

```
cast/            演员表 + 18 位 AI 娘人设图和生图提示词
prompts/         每个验收单元最终通过的 H3 提示词；index.csv 列出版本、种子、参考图顺序
workflows/       ComfyUI 工作流模板：H3 视频编辑（Ref2VA）、H3 首帧图生视频、SeedVR2 超分
data/            分镜计划 full_plan.json、署名决定 credit_decisions.json
pipelines/       全部代码（见下方说明）
docs/            四个步骤的详细说明和踩坑记录
```

## 代码说明

`pipelines/anime_op/` 是实际跑通整片的研究代码，没有整理成通用工具，但每个环节都能对照着读：

- `plan_school_full_op.py` / `prepare_school_full_op.py`：切镜头、生成计划
- `render_school_full_op.py`：批量生成主程序（提交 ComfyUI、GPU 温度保护、帧数和音轨校验）
- `overnight_queue.py` / `revise_school_shot_prompt.py`：按新提示词重跑单个镜头或合并单元，自动找回真实剪辑点
- `review_school_op.py` + `.html`：本地逐镜验收页面（通过 / 打回 / 写备注）
- `revise_041_043_i2v.py`：视频编辑换不掉人时，改用首帧图生视频 + 后期溶解的例子
- `assemble_full_op_native.py` / `build_topaz_master.py`：拼接全片、准备超分母带
- `upscale_4k_test.py` / `upscale_4k_seedvr2_7b.py`：开源超分对比
- `export_opensource.py`：生成本仓库

运行需要：Windows、Python 3.12、ffmpeg（默认路径 `C:\Program Files\ffmpeg\bin`）、ComfyUI + MiniMax H3 模型。代码默认的数据目录是 `assets/anime_op/school_full_op_v1/`，原片需自备。

## 提示词写法

本地 ComfyUI 没有官方的提示词改写器，必须严格按 MiniMax 官方格式写，随手写的短提示词会直接照抄原片。官方写法见 MiniMax 的 [h3-prompt-writing skill](https://github.com/MiniMax-AI/MiniMax-H3)（本仓库不转载），我们的用法和经验见 [docs/02-人物替换.md](docs/02-人物替换.md)。

## 版权与许可

- 本作是粉丝二创。《败犬女主太多了！》原作：雨森たきび（小学馆 GAGAGA 文库），动画制作：A-1 Pictures；OP《つよがるガール》：ぼっちぼろまる feat. もっさ（ネクライトーキー）。原作画面与音乐版权归原作方所有，本仓库不包含任何原作素材。
- staff 表里的研究者姓名和公司均为致敬玩梗，AI 娘形象为二创拟人，与相关人士及公司无关。
- 18 位 AI 娘中，DeepSeek、Gemini、Claude、Kimi、MiniMax、Qwen 的基础形象参考了社区流行的 AI 娘设计（例如 DeepSeek 娘由社区共同完成设计）；GPT 和其余 11 位为本项目原创设计。本仓库里全部 18 张校服人设图都由 GPT 生图生成，生图提示词见 [cast/designs/](cast/designs/)。
- 代码：MIT（见 [LICENSE](LICENSE)）。人设图、提示词和文档：CC BY-NC 4.0。
- 生成模型 MiniMax H3 的社区许可对生成内容的发布地区有限制，复用前请自行阅读其许可。
