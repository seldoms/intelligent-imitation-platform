# 智能仿写平台

基于已有杂志写作技能（magazine-writing-*）的本地 Web 客户端：输入题目或素材，由系统做意图分析、自动/手动选技能、检索在线语料库范文、组装提示词，再调 DeepSeek 流式生成《读者》《意林》风格的中文文章。创作过程（意图分析 → 选技能 → 检索范文 → 组装提示词 → 调用 DeepSeek → 完成）全程可视化。

## 特性

- **左右分栏、单页显示**：左侧所有配置，右侧结果与创作过程；长文在框内滚动，不撑破页面。
- **9 个写作技能**：默认「自动路由」按题目关键词匹配；也可手动指定（人物故事 / 情感观点 / 抒情散文 / 素材改写 / 微故事 / 叙事散文 / 哲理散文 / 褒中贬美 / 金句扩写）。
- **API Key 本地保存**：仅存于浏览器 `localStorage`，服务端不保存，不同用户各自填写，互不可见。
- **说明即悬浮提示**：配置项的说明文字改为鼠标悬停（ⓘ）显示，页面更紧凑。
- **设置折叠**：DeepSeek Key 与代理地址收进「⚙ 设置」面板，默认隐藏。

## 本地运行

```bash
pip install -r requirements.txt
python backend.py            # 监听 http://127.0.0.1:8000
```

浏览器打开 `http://127.0.0.1:8000`，填写题目与 DeepSeek API Key 即可生成。
（技能目录默认读 `~/.workbuddy/skills`，可用环境变量 `SKILLS_ROOT` 覆盖。）

## Docker 部署（Ubuntu）

```bash
docker build -t magazine-writer .
docker run -d --name magazine-writer -p 8000:8000 --restart unless-stopped magazine-writer
# 或用 compose：
docker compose up -d
```

启动后访问 `http://<服务器IP>:8000`。

- 镜像已内置 `skills/` 写作技能快照；如需用服务器最新技能，可在 `docker-compose.yml` 中挂载
  `~/.workbuddy/skills:/app/skills:ro` 覆盖。
- 若服务器无法直连 `workers.dev`（语料库），运行时加 `-e MAGAZINE_PROXY=http://...`，
  或在页面「代理地址」里临时填写。
- 语料库检索失败不影响生成，范文仅为增强项。

## 目录结构

```
backend.py            FastAPI 服务（页面 + /api/skills + /api/generate 流式接口）
static/index.html     单页前端
skills/               打包进镜像的写作技能快照
requirements.txt      依赖
Dockerfile            镜像构建
docker-compose.yml    一键编排
```

## 隐私说明

DeepSeek API Key 仅保存在访问者各自的浏览器，不经过服务端持久化，也不在用户间共享。
