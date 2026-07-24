#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
杂志写作客户端后端
流水线：意图分析 -> 选技能 -> 拉语料库范文 -> 组装提示词 -> 调 DeepSeek(流式) 生成
通过 SSE 把每一步进度实时推给前端，向用户展示创作过程与工作量。

运行：
    python backend.py
默认监听 http://127.0.0.1:8000
"""
import os
import re
import json
import requests
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

SKILLS_ROOT = os.environ.get("SKILLS_ROOT", os.path.expanduser("~/.workbuddy/skills"))
EXCLUDE_SKILLS = {"magazine-writing-router", "magazine-writing-deploy-magazine-api"}
# 语料库 API 地址：从环境变量读取，默认空。
# 需自行在 Cloudflare 部署 magazine-api（见 magazine-api/DEPLOY.md），
# 然后用 -e MAGAZINE_BASE=https://<你的子域名>.workers.dev 传入。
# 留空时跳过范文检索，仅凭写作技能生成（范文只是增强项）。
MAGAZINE_BASE = os.environ.get("MAGAZINE_BASE", "").rstrip("/")
# 语料库代理：默认空（直连）。若你的网络无法直连语料库地址，可在界面填写本地代理。
PROXY = os.environ.get("MAGAZINE_PROXY", "")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = FastAPI(title="杂志写作客户端")


# ---------- 技能加载 ----------
def parse_frontmatter(text: str):
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    head = text[3:end].strip("\n")
    body = text[end + 4:]
    meta = {}
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, body


def load_skills():
    skills = []
    if not os.path.isdir(SKILLS_ROOT):
        return skills
    for name in sorted(os.listdir(SKILLS_ROOT)):
        if not name.startswith("magazine-writing-") or name in EXCLUDE_SKILLS:
            continue
        skill_md = os.path.join(SKILLS_ROOT, name, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue
        with open(skill_md, encoding="utf-8") as fh:
            text = fh.read()
        meta, body = parse_frontmatter(text)
        skills.append({
            "id": name,
            "name": meta.get("name", name),
            "description": meta.get("description", ""),
            "instructions": text,  # 完整 SKILL.md，用于喂给模型
        })
    return skills


SKILLS = load_skills()
SKILL_BY_ID = {s["id"]: s for s in SKILLS}


# ---------- 自动意图路由（关键词表，来自 magazine-writing-router） ----------
ROUTE_RULES = [
    ("praise-china", ["褒中", "贬西", "贬美", "外国人", "西方", "外媒", "china"]),
    ("quote-expand", ["名言", "金句", "诗句", "扩写", "引用"]),
    ("material-rewrite", ["素材", "新闻", "改写", "二次"]),
    ("character-story", ["人物", "特稿", "某人", "经历", "生平", "传记"]),
    ("emotional-opinion", ["观点", "立场", "情感观点", "看法", "议论"]),
    ("micro-story", ["故事", "情节", "矛盾", "转折", "短篇", "小说"]),
    ("narrative-prose", ["叙事", "生活", "故事", "夹叙夹议", "画面", "回忆"]),
    ("lyrical-prose", ["抒情", "借景", "情感", "流淌", "物件", "意境", "散文"]),
    ("philosophical-prose", ["哲理", "人生", "智慧", "思辨", "以小见大", "思考", "感悟"]),
]


def auto_route(topic: str):
    t = (topic or "").lower()
    for sid, kws in ROUTE_RULES:
        hit = [kw for kw in kws if kw.lower() in t]
        if hit:
            return sid, hit
    return "magazine-writing-philosophical-prose", ["(默认)"]


# ---------- 输入形态识别 ----------
def classify_input(text: str) -> str:
    """识别用户输入是『短题目（发散）』还是『成篇素材/小作文（扩写转写）』。

    - ≤20 字且无句末标点  -> title  （题目，发散模式）
    - 其余（含长文本/有断句）-> essay  （素材，提炼→扩写/转写模式）
    """
    t = (text or "").strip()
    if not t:
        return "title"
    has_sentence_punct = bool(re.search(r"[。！？!?；;\n]", t))
    if len(t) <= 20 and not has_sentence_punct:
        return "title"
    return "essay"


# ---------- 语料库范文检索（走代理） ----------
def fetch_references(topic: str, top_k: int = 5, proxy: str = ""):
    """拉语料库范文。proxy 为空则直连；代理不通/失败则快速降级（范文仅为增强项）。"""
    refs = []
    if not MAGAZINE_BASE:
        return [], "未配置语料库地址（MAGAZINE_BASE）。请参考 magazine-api/DEPLOY.md 自行部署后配置；（范文仅为增强项，跳过也能正常生成）"
    proxies = {"https": proxy, "http": proxy} if proxy else None
    try:
        r = requests.get(f"{MAGAZINE_BASE}/search",
                         params={"q": topic, "top_k": top_k},
                         proxies=proxies, timeout=(4, 15))
        data = r.json()
        results = data.get("results", [])
        for it in results[:3]:  # 只取前 3 篇拉全文，省调用
            aid = it.get("id")
            try:
                ar = requests.get(f"{MAGAZINE_BASE}/article/{aid}",
                                  proxies=proxies, timeout=(4, 15)).json()
                refs.append({
                    "id": aid,
                    "title": it.get("title", ""),
                    "content": ar.get("content_text", "")[:2000],
                })
            except Exception:
                continue
    except Exception as e:
        hint = ""
        if not proxy:
            hint = "若你所处网络无法直接访问语料库地址，请在本页『代理地址』填写本地代理（例如 http://127.0.0.1:7897）后重试。"
        return [], f"语料库检索失败：{e}。{hint}（范文仅为增强项，跳过也能正常生成）"
    return refs, None


# ---------- 提示词组装 ----------
def build_system_prompt(skill, refs, length):
    parts = []
    parts.append(
        "你是一位深谙《读者》《意林》风格的华语写作助手。"
        "你的任务：严格遵循下面给定的『写作技能』的结构与文风要求，"
        "结合『参考范文』的技法，写出一篇高质量中文文章。"
    )
    parts.append("【写作技能要求】（必须严格遵循其结构、技法、开头/结尾方式与行文禁忌）\n" + skill["instructions"])
    if refs:
        ref_text = "\n\n".join(
            f"·《{r['title']}》(ID {r['id']})\n{r['content']}" for r in refs
        )
        parts.append("【参考范文】（来自已上线的杂志语料库，学习其结构/技法/语言节奏，不是抄袭内容）\n" + ref_text)
    else:
        parts.append("【参考范文】本次未能从语料库取到范文，请仅依据写作技能要求完成。")
    parts.append(
        "【去 AI 味硬约束】\n"
        "1. 禁止使用『首先/其次/最后/总而言之/我们应该/值得注意的是』等套话与总结词。\n"
        "2. 必须用具体人名、具体场景、口语化短句，避免空泛说教。\n"
        "3. 不替读者下结论式训诫；结尾留有余韵（引而不发）。\n"
        f"4. 目标篇幅约 {length} 字（汉字+中文标点）。\n"
        "5. 只输出文章正文（含一个标题行，结尾用『——文/（占位署名）』），不要输出任何解释、元说明或 JSON。"
    )
    return "\n\n".join(parts)


def count_cjk(text: str) -> int:
    n = 0
    for c in text:
        o = ord(c)
        if (0x4E00 <= o <= 0x9FFF) or (0x3000 <= o <= 0x303F) or \
           (0xFF00 <= o <= 0xFFEF) or (0x3400 <= o <= 0x4DBF):
            n += 1
    return n


# ---------- SSE 工具 ----------
def sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ---------- 路由 ----------
@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    # 把技能列表直接注入页面，避免前端再发一次 /api/skills（同源/CORS/file:// 都可能失败）
    skills_json = json.dumps(
        [{"id": s["id"], "name": s["name"], "description": s["description"]} for s in SKILLS],
        ensure_ascii=False,
    )
    html = html.replace(
        "<!--SKILLS_JSON-->",
        f'<script id="skills-data" type="application/json">{skills_json}</script>',
    )
    return HTMLResponse(html)


@app.get("/api/skills")
def api_skills():
    return JSONResponse([
        {"id": s["id"], "name": s["name"], "description": s["description"]}
        for s in SKILLS
    ])


@app.post("/api/generate")
async def api_generate(request: Request):
    """SSE 流式生成。前端用 fetch + ReadableStream 解析进度与正文。

    注意：必须在创建 StreamingResponse 之前解析好请求体，
    否则流式生成器内部的 await request.json() 会与响应流形成死锁。
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "无法解析请求体"}, status_code=400)

    topic = (body.get("topic") or "").strip()
    skill_id = (body.get("skill") or "auto").strip()
    length = int(body.get("length") or 2000)
    api_key = (body.get("apiKey") or "").strip()
    proxy = (body.get("proxy") or "").strip()
    top_k = int(body.get("top_k") or 5)
    input_mode_req = (body.get("inputMode") or "auto").strip().lower()

    def generate():
        # 纯同步生成器（无 await），直接复用上面已解析好的局部变量
        if not topic:
            yield sse("error", {"message": "请填写题目"})
            return
        if not api_key:
            yield sse("error", {"message": "请填写 DeepSeek API Key"})
            return

        # —— 第 1 步：意图分析（含输入形态识别）——
        if input_mode_req in ("title", "essay"):
            input_mode = input_mode_req
            mode_src = "用户指定"
        else:
            input_mode = classify_input(topic)
            mode_src = "自动识别"
        mode_label = "题目（发散）" if input_mode == "title" else "素材（提炼→扩写/转写）"
        yield sse("step", {"n": 1, "label": "意图分析", "status": "running",
                           "detail": f"输入类型：{mode_label}（{mode_src}）· 目标 {length} 字"})

        # —— 第 2 步：选技能 ——
        if skill_id == "auto" or skill_id not in SKILL_BY_ID:
            sid, hit = auto_route(topic)
            auto_picked = True
        else:
            sid, hit = skill_id, []
            auto_picked = False
        skill = SKILL_BY_ID.get(sid) or SKILLS[0]
        yield sse("step", {"n": 2, "label": "选择写作技能", "status": "running",
                           "detail": f"{skill['name']}（{'自动路由命中关键词：' + '、'.join(hit) if auto_picked else '用户手动指定'}）"})

        # —— 第 3 步：检索语料库范文 ——
        # 素材模式时检索词过长会稀释召回，截断到前 60 字
        search_q = topic if input_mode == "title" else topic[:60]
        yield sse("step", {"n": 3, "label": "检索语料库范文", "status": "running",
                           "detail": ("直连中…" if not proxy else f"经代理 {proxy} 检索中…")})
        refs, warn = fetch_references(search_q, top_k, proxy)
        if refs:
            ref_summary = "；".join(f"《{r['title']}》(#{r['id']})" for r in refs)
            yield sse("step", {"n": 3, "label": "检索语料库范文", "status": "done",
                               "detail": f"取回 {len(refs)} 篇范文：{ref_summary}"})
        else:
            yield sse("step", {"n": 3, "label": "检索语料库范文", "status": "warn",
                               "detail": (warn or "未取到范文，跳过（将仅依据技能要求生成）")})

        # —— 第 4 步：组装提示词（按输入形态分支）——
        system_prompt = build_system_prompt(skill, refs, length)
        if input_mode == "title":
            user_prompt = (
                f"题目：{topic}\n"
                f"请围绕这个题目发散构思，结合上面的写作技能与参考范文，"
                f"写一篇约 {length} 字的中文文章。"
            )
        else:
            user_prompt = (
                f"以下是一段原始素材/草稿（可能是一篇不成形的小作文、零散笔记或他人文字）：\n"
                f"『{topic}』\n\n"
                f"请先提炼其中的核心观点与关键情节（2-4 个），保留原有的神态与细节，"
                f"再以『{skill['name']}』的写法将其扩写/转写为一篇结构完整、约 {length} 字的中文文章。\n"
                f"要求：不是简单复述或翻译，要提升文采、理顺结构、去 AI 味；"
                f"若原素材信息单薄，可围绕其主旨合理生发，但不要凭空编造与原意矛盾的事实。"
            )
        skill_tokens = len(system_prompt)
        ref_tokens = sum(len(r["content"]) for r in refs)
        yield sse("step", {"n": 4, "label": "组装提示词", "status": "done",
                           "detail": f"技能 SKILL.md（{skill_tokens} 字）+ 范文（{ref_tokens} 字）+ 去 AI 味硬约束 → 已就绪，准备调用 {DEEPSEEK_MODEL}"})

        # —— 第 5 步：调用 DeepSeek（流式出文）——
        yield sse("step", {"n": 5, "label": "调用 DeepSeek 生成", "status": "running",
                           "detail": f"模型 {DEEPSEEK_MODEL} 流式输出中…"})
        article_parts = []
        try:
            resp = requests.post(
                DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.85,
                    "max_tokens": min(8192, int(length * 1.8) + 300),
                    "stream": True,
                },
                timeout=120,
                stream=True,
            )
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                line = line.decode("utf-8") if isinstance(line, bytes) else line
                if not line.startswith("data:"):
                    continue
                data_str = line[len("data:"):].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk["choices"][0]["delta"].get("content", "")
                except Exception:
                    continue
                if delta:
                    article_parts.append(delta)
                    yield sse("token", {"text": delta})
        except Exception as e:
            yield sse("error", {"message": f"DeepSeek 调用失败：{e}"})
            return

        article = "".join(article_parts).strip()
        cnt = count_cjk(article)

        # —— 第 6 步：完成 ——
        yield sse("step", {"n": 6, "label": "完成", "status": "done",
                           "detail": f"已生成全文，约 {cnt} 字（汉字+中文标点）"})
        yield sse("done", {
            "article": article,
            "skill_id": skill["id"],
            "skill_name": skill["name"],
            "auto_picked": auto_picked,
            "input_mode": input_mode,
            "input_mode_label": mode_label,
            "matched_keywords": hit,
            "references": [{"id": r["id"], "title": r["title"]} for r in refs],
            "warn": warn,
            "char_count": cnt,
        })

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    import uvicorn
    HOST = os.environ.get("HOST", "0.0.0.0")   # Docker/服务器需监听 0.0.0.0；本地可设 127.0.0.1
    PORT = int(os.environ.get("PORT", "8000"))
    print(f"已加载 {len(SKILLS)} 个写作技能；语料库代理={'未设置(直连)' if not PROXY else PROXY}")
    print(f"监听 {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
