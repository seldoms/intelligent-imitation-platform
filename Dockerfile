# 杂志写作客户端 —— 容器镜像
# 基于 Python 3.13-slim，FastAPI + uvicorn 提供页面与流式生成接口
FROM python:3.13-slim

# 减少镜像体积 / 避免交互提示
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖（利用层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷应用代码
COPY backend.py .
COPY static ./static

# 打包进镜像的写作技能（构建前已拷入 ./skills；如需最新可改挂载 volumes）
COPY skills ./skills

# 服务监听容器内所有网卡；端口可在运行时用 -e PORT=xxxx 覆盖
ENV SKILLS_ROOT=/app/skills
EXPOSE 8000

# 注意：DeepSeek API Key 只存在用户浏览器 localStorage，服务端不保存。
# 语料库代理（MAGAZINE_PROXY）默认空（直连 workers.dev），可 -e 指定。
CMD ["sh", "-c", "uvicorn backend:app --host 0.0.0.0 --port ${PORT:-8000}"]
