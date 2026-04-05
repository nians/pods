# 🎙 podcast-notes

小宇宙播客自动转录 + GitHub 存储 + Claude 交互分析

## 它解决什么问题

听完一期两小时的播客，想回顾某个观点却找不到在哪。这个工具把播客变成可搜索、可提问的文本笔记：一条命令完成转录，存到 GitHub，随时在 Claude 里贴个链接就能深度分析。

## 架构

```
┌─────────────┐    ┌──────────────────┐    ┌─────────────┐    ┌────────────┐
│ 小宇宙 URL  │───→│ GitHub Actions   │───→│ Groq Whisper │───→│ transcripts│
│ 或 RSS 订阅 │    │ (自动/手动触发)   │    │ API 转录     │    │ /*.md      │
└─────────────┘    └──────────────────┘    └─────────────┘    └─────┬──────┘
                                                                    │ git push
                                                                    ▼
                   ┌──────────────────┐    ┌─────────────┐    ┌────────────┐
                   │  结构化笔记       │◀───│ Claude 分析  │◀───│ raw URL    │
                   │  投资策略提取     │    │ web_fetch   │    │ 贴到对话    │
                   └──────────────────┘    └─────────────┘    └────────────┘
```

## 两种使用方式

| | GitHub Actions（推荐） | 本地运行 |
|---|---|---|
| 转录引擎 | Groq / OpenAI Whisper API | 本地 faster-whisper |
| 硬件要求 | 无 | GPU（或 CPU 慢跑） |
| 速度 | 122 分钟 ≈ 1-2 分钟 | GPU ~15 分钟 / CPU ~3 小时 |
| 费用 | 免费层 ~50h/周；超出 $0.111/h | 免费（电费） |
| 自动化 | ✅ 支持定时 RSS 监控 | 需自行 cron |
| 存储 | 自动 commit 到 repo | 本地或手动上传 |

---

## 快速开始：GitHub Actions 方式

### 第一步：创建 repo

```bash
# 克隆模板
git clone https://github.com/你的用户名/podcast-notes.git
cd podcast-notes

# 或者从零开始
mkdir podcast-notes && cd podcast-notes
# 把本项目文件复制进来
git init && git add -A && git commit -m "init"
gh repo create podcast-notes --private --push
```

### 第二步：配置 Groq API Key

1. 注册 [console.groq.com](https://console.groq.com)（免费，无需信用卡）
2. 创建 API Key
3. 添加到 repo：Settings → Secrets and variables → Actions → New repository secret
   - Name: `GROQ_API_KEY`
   - Value: `gsk_xxxxxxxxxxxx`

### 第三步：运行转录

1. 进入 repo → **Actions** 标签
2. 左侧选择 **Podcast Transcribe**
3. 点击 **Run workflow**
4. 填入小宇宙单集 URL，例如：
   ```
   https://www.xiaoyuzhoufm.com/episode/69cc68b2b977fb2c47c86d94
   ```
5. 点击绿色按钮运行

### 第四步：在 Claude 中使用

Actions 完成后，在运行的 **Summary** 页面会显示 Raw URL。

到 [claude.ai](https://claude.ai) 中直接说：

```
请帮我分析这个播客转录，提取核心观点和投资策略：
https://raw.githubusercontent.com/你的用户名/podcast-notes/main/transcripts/2026-04-01_Vol61_xxx.md
```

Claude 会自动读取全文，然后你可以持续追问。

---

## 快速开始：本地方式

### 安装

```bash
pip install requests beautifulsoup4 faster-whisper pydub
```

### 使用本地 Whisper 转录

```bash
# 默认使用 large-v3 模型（最准，需要 GPU）
python podcast2note.py https://www.xiaoyuzhoufm.com/episode/69cc68b2b977fb2c47c86d94

# 使用较小模型（CPU 友好）
python podcast2note.py --model medium https://www.xiaoyuzhoufm.com/episode/xxx

# 转录本地音频
python podcast2note.py --audio downloaded_podcast.mp3
```

### 使用云端 API 转录（更快）

```bash
export GROQ_API_KEY=gsk_xxxxxxxxxxxx
python podcast2note.py --api groq https://www.xiaoyuzhoufm.com/episode/xxx
```

### 转录并上传 GitHub

```bash
export GITHUB_TOKEN=ghp_xxxxxxxxxxxx

# 上传到 Gist（简单）
python podcast2note.py --upload gist https://www.xiaoyuzhoufm.com/episode/xxx

# 上传到 Repo（适合长期管理）
python podcast2note.py --upload repo:你的用户名/podcast-notes https://www.xiaoyuzhoufm.com/episode/xxx
```

---

## 自动订阅新集（RSS 模式）

不想每次手动触发？可以让 GitHub Actions 每天自动检查你关注的播客是否有新集。

### 配置

1. 获取播客的 RSS feed URL
   - 在小宇宙 app 中：我的 → 订阅 → 右上角分享 → 导出 OPML → 用记事本打开找到 feed URL
   - 或通过 [getrssfeed.com](https://getrssfeed.com) 从 Apple Podcasts 链接提取

2. 在 repo 中添加 Variable：
   Settings → Secrets and variables → Actions → Variables → New repository variable
   - Name: `RSS_FEEDS`
   - Value: RSS feed URL，多个用逗号分隔
   ```
   https://feed.xyzfm.space/xxxx,https://feed.xyzfm.space/yyyy
   ```

3. 取消 `.github/workflows/transcribe.yml` 中 schedule 部分的注释：
   ```yaml
   schedule:
     - cron: '0 8 * * *'  # UTC 8:00 = 北京时间 16:00
   ```

4. 提交推送，之后每天自动检查并转录新集。

---

## 费用估算

### Groq Whisper API

| 用量 | 免费层 | 付费 | 付费 + Batch |
|------|--------|------|-------------|
| 5 小时/周 | ✅ 免费 | $2.2/月 | $1.1/月 |
| 20 小时/周 | ✅ 免费 | $8.9/月 | $4.4/月 |
| 50 小时/周 | ⚠️ 接近上限 | $22/月 | $11/月 |
| 100 小时/周 | ❌ 超限 | $44/月 | $22/月 |

免费层限制：每小时最多处理 7,200 秒（2 小时）音频，每天 2,000 个请求。

### GitHub Actions

GitHub 免费账户每月 2,000 分钟 Actions 时间。单次转录约消耗 2-5 分钟（主要是下载），每月可运行 400-1,000 次，完全够用。

---

## 在 Claude 中的提问技巧

贴上转录 URL 后，可以这样提问：

**内容分析类**
- "总结核心观点，按重要性排序"
- "提取所有数据和具体数字"
- "嘉宾和主播的观点有哪些分歧？"
- "这期节目的论证逻辑链是什么？"

**投资相关**
- "提取所有投资建议，按资产类别分类"
- "嘉宾对美元和黄金的判断分别是什么？"
- "哪些观点有具体数据支撑，哪些是推测？"

**笔记整理类**
- "生成一份结构化学习笔记，带时间戳引用"
- "用 bullet points 列出可操作的行动建议"
- "做一张思维导图大纲"

**深度追问类**
- "XX 观点的依据是什么？在转录中找到原文"
- "对比这个观点和达利欧的看法"
- "基于这期内容，推荐我后续阅读什么？"

---

## 文件结构

```
podcast-notes/
├── .github/workflows/
│   └── transcribe.yml          # GitHub Actions 工作流
├── scripts/
│   └── transcribe_ci.py        # CI 环境转录脚本（Groq/OpenAI API）
├── transcripts/                 # 转录输出（自动生成）
│   ├── 2026-04-01_Vol61_时代切换的十字路口.md
│   └── ...
├── state/
│   └── processed.json           # RSS 模式的已处理记录
├── podcast2note.py              # 本地运行脚本
├── requirements.txt
└── README.md
```

## 输出格式

每个转录文件包含：

- 元数据表（来源、时长、字数、转录时间）
- Show Notes（可折叠）
- 按 5 分钟分段的转录全文，带时间戳标题
- 同时生成 `.txt` 纯文本版（本地模式）

---

## 常见问题

**Q: 无法自动提取音频 URL？**

小宇宙的前端结构可能更新。备选方案：
1. 在 app 中分享到微信"文件传输助手" → 电脑 Chrome 打开 → 右键下载音频
2. 使用 mitmproxy 抓包
3. 下载后用 `--audio` 参数直接转录

**Q: 转录质量不好？**

Groq 使用的是 Whisper large-v3，中文识别准确率很高。如果效果不佳：
- 确认 `--lang zh` 参数正确
- 音频质量差时可尝试先降噪
- 本地模式可加 `--model large-v3` 并关闭 VAD

**Q: 文件超过 25MB？**

脚本会自动切分为多段分别转录再合并，无需手动处理。

**Q: GitHub Actions 运行失败？**

检查 Secrets 是否正确配置。进入 Actions → 失败的运行 → 查看日志定位问题。

---

## Token 创建指引

### Groq API Key

1. 访问 [console.groq.com](https://console.groq.com)
2. 注册账号（Google 登录即可，无需信用卡）
3. 左侧菜单 → API Keys → Create API Key
4. 复制保存

### GitHub Token（本地上传用）

1. 访问 [github.com/settings/tokens](https://github.com/settings/tokens)
2. Generate new token (classic)
3. 勾选权限：`gist`（Gist 模式）或 `repo`（Repo 模式）
4. 复制保存，设置环境变量 `export GITHUB_TOKEN=ghp_xxx`

### OpenAI API Key（备选）

1. 访问 [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
2. Create new secret key
3. 复制保存
