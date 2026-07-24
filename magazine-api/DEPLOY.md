# 语料库后端（magazine-api）部署指南

写作客户端在生成文章时，会去一个「云端语料库接口」检索同类范文，学习其结构与技法。
这个接口是一个跑在 **Cloudflare** 上的 Worker（`magazine-api`），本目录提供从零复现它所需的全部代码。

> 范文只是**增强项**：不部署语料库、客户端也能仅凭写作技能正常生成文章。
> 如果你想要「带范文参考」的完整效果，就按本文部署一套属于你自己的接口。

---

## 架构一览

```
用户题目
   │
写作客户端 backend.py ──HTTP──▶  magazine-api（Cloudflare Worker）
                                    ├── Workers AI (bge-m3)  查询时把关键词编码成 1024 维向量
                                    ├── Vectorize            向量检索，找出最相似的范文
                                    └── Workers KV           按 id 取文章全文
```

三个 Cloudflare 组件都在**免费额度**内即可支撑个人使用（详见文末）。

---

## 一、准备数据（向量库导出文件）

语料库已从本地 pgvector 导出为 3 个 CSV（约 320MB，**因体积过大未随仓库分发**）：

| 文件 | 内容 | 大小 |
|---|---|---|
| `chunk_embeddings.csv` | 每个文本块的 1024 维向量 | ~256MB |
| `cleaned_documents.csv` | 清洗后的文章全文 | ~48MB |
| `document_chunks.csv` | 文章切块映射 | ~22MB |

获取方式（任选其一）：
1. 向仓库维护者索取导出包 `magazine-data.tar.gz`（含以上 3 个 CSV）；
2. 或按最上游流程自建（OCR → 清洗 → bge-m3 向量化 → 导出 CSV）。

把 CSV 放到本目录下的 `content/database/`（脚本默认从这里读）：

```
magazine-api/
  content/database/
    chunk_embeddings.csv
    cleaned_documents.csv
```

---

## 二、创建 Cloudflare 资源

先准备两样东西：
- **Account ID**：Cloudflare 控制台右侧「账户 ID」。
- **API Token**：控制台 → 我的个人资料 → API 令牌 → 创建令牌，授予
  `Workers Scripts:Edit`、`Vectorize:Edit`、`Workers KV Storage:Edit`。

```bash
export CF_ACCOUNT_ID="你的AccountID"
export CF_API_TOKEN="你的APIToken"
```

### 2.1 创建 Vectorize 索引（维度必须 1024，度量 cosine）

```bash
curl -X POST "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/vectorize/v2/indexes" \
  -H "Authorization: Bearer $CF_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"magazine-articles","config":{"dimensions":1024,"metric":"cosine"}}'
```

### 2.2 创建 KV 命名空间（记下返回的 id）

```bash
curl -X POST "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/storage/kv/namespaces" \
  -H "Authorization: Bearer $CF_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"magazine-article-texts"}'
```

---

## 三、灌数据

```bash
# 推送向量到 Vectorize（每批 500 条）
CSV_PATH=content/database/chunk_embeddings.csv \
python3 scripts/push_to_cf_vectorize.py

# 推送文章全文到 KV（每批 5000 条，用 2.2 返回的 KV id）
export CF_KV_NAMESPACE="上一步返回的KV_id"
CSV_PATH=content/database/cleaned_documents.csv \
python3 scripts/push_to_cf_kv.py
```

两个脚本都从环境变量读取 `CF_ACCOUNT_ID` / `CF_API_TOKEN`，不含任何硬编码凭据。

---

## 四、部署 Worker，拿到你的接口地址

```bash
npm install -g wrangler          # 或 npx wrangler
cd worker
cp wrangler.toml.example wrangler.toml
# 编辑 wrangler.toml：把 [[kv_namespaces]] 的 id 填成 2.2 返回的 KV id
wrangler login
wrangler deploy
```

部署成功后，命令行会打印**你自己的公开地址**，形如：

```
https://magazine-api.<你的账号子域名>.workers.dev
```

> 这就是「**云端接口地址**」。它由 Cloudflare 按你的账号子域名自动生成，
> 每个账号各不相同。也可在控制台 **Workers & Pages → magazine-api → 触发器/域名** 里查看。

验证：

```bash
curl https://magazine-api.<你的子域名>.workers.dev/health
curl "https://magazine-api.<你的子域名>.workers.dev/search?q=母爱&top_k=3"
```

---

## 五、把接口地址接入写作客户端

拿到地址后，通过环境变量 `MAGAZINE_BASE` 传给客户端即可（客户端默认留空 = 跳过范文检索）：

```bash
docker run -d --name intelligent-imitation-platform \
  -p 28080:8000 --restart unless-stopped \
  -e MAGAZINE_BASE=https://magazine-api.<你的子域名>.workers.dev \
  intelligent-imitation-platform:latest
```

如果客户端所在网络无法直连该地址，可在页面「⚙ 设置 → 代理地址」临时填写本地代理，
或运行时加 `-e MAGAZINE_PROXY=http://host:port`。

---

## 接口一览

| 路径 | 功能 |
|---|---|
| `GET /health` | 健康检查 |
| `GET /search?q=母爱&top_k=5` | 语义搜索（返回相似范文列表） |
| `GET /article/:id` | 获取文章全文 |
| `GET /articles?page=1` | 文章列表 |
| `GET /random` | 随机文章 |
| `GET /satirical?page=1` | 特定类别列表 |

## 免费额度（个人使用足够）

| 服务 | 免费额度 |
|---|---|
| Workers AI (bge-m3) | 10,000 neurons/天（约每次搜索 10 neurons） |
| Vectorize | 500 万向量（本库仅约 1.9 万） |
| Workers KV | 1000 万读/天 |
| Worker 请求 | 10 万/天 |
