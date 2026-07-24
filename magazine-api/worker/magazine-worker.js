/**
 * 杂志语料库 API Worker
 *
 * Binding 配置（在 wrangler.jsonc 或 Dashboard 中配置）：
 *   AI            → Workers AI（用于 embedding）
 *   VECTORIZE     → magazine-articles 索引
 *   ARTICLE_KV    → magazine-article-texts 命名空间
 */

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+/g, '/');

    // CORS
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type',
        },
      });
    }

    const headers = { 'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json' };
    const json = (data, status = 200) => new Response(JSON.stringify(data, null, 2), { status, headers });

    try {
      if (path === '/health') return json({ status: 'ok', time: Date.now() });

      if (path === '/search') return handleSearch(url, env, json);
      if (path === '/tags') return handleTagSearch(url, env, json);
      if (path.startsWith('/article/')) return handleGetArticle(path, env, json);
      if (path === '/articles') return handleListArticles(url, env, json);
      if (path === '/random') return handleRandom(url, env, json);
      if (path === '/satirical') return handleSatirical(url, env, json);

      return json({ error: 'Not Found. Try /search?q=, /article/:id, /articles, /random, /satirical' }, 404);
    } catch (err) {
      console.error(err.stack);
      return json({ error: err.message }, 500);
    }
  },
};

// ── 语义搜索（支持按标签过滤） ──
async function handleSearch(url, env, json) {
  const query = url.searchParams.get('q');
  if (!query) return json({ error: '?q= is required' }, 400);

  const topK = Math.min(parseInt(url.searchParams.get('top_k') || '10'), 50);
  const docType = url.searchParams.get('type');
  const requestedTags = url.searchParams.getAll('tag');

  // 1) 用 Workers AI bge-m3 把 query 转成向量
  const aiResp = await env.AI.run('@cf/baai/bge-m3', { text: [query] });
  const queryVector = aiResp.data[0];

  // 2) Vectorize 查询（多取一些，给后面标签过滤和去重留空间）
  const results = await env.VECTORIZE.query(queryVector, {
    topK: topK * 5,
    returnMetadata: 'all',
  });

  // 3) 加载标签数据（如果有标签过滤）
  let tagIndex = null;
  if (requestedTags.length > 0) {
    const tagsJson = await env.ARTICLE_KV.get('_article_tags');
    if (tagsJson) {
      tagIndex = JSON.parse(tagsJson);
    }
  }

  // 4) 按 doc_id 去重 + 标签过滤
  const seen = new Map();
  for (const match of results.matches) {
    const docId = match.metadata?.doc_id;
    if (!docId) continue;
    if (seen.has(docId) && seen.get(docId).score >= match.score) continue;

    // 标签过滤
    if (requestedTags.length > 0 && tagIndex) {
      const tagEntry = tagIndex.tags.find(t => t.article_id === parseInt(docId));
      if (!tagEntry) continue;

      let matched = true;
      for (const rt of requestedTags) {
        const allTagValues = [
          tagEntry.开头方式 || '',
          tagEntry.展开方式 || '',
          tagEntry.结尾技巧 || '',
          ...(tagEntry.修辞手法 || []),
          tagEntry.叙事视角 || '',
          tagEntry.情感基调 || '',
          ...(tagEntry.主题标签 || []),
        ];
        if (!allTagValues.some(v => v.includes(rt))) {
          matched = false;
          break;
        }
      }
      if (!matched) continue;
    }

    seen.set(docId, {
      id: parseInt(docId),
      title: match.metadata?.title || '',
      doc_type: match.metadata?.doc_type || '',
      score: match.score,
      snippet: match.metadata?.snippet || '',
    });
  }

  let matches = Array.from(seen.values())
    .sort((a, b) => b.score - a.score)
    .slice(0, topK);

  return json({ query, total: matches.length, results: matches });
}

// ── 按标签搜索（不依赖向量，纯标签检索） ──
async function handleTagSearch(url, env, json) {
  const requestedTags = url.searchParams.getAll('tag');
  const page = parseInt(url.searchParams.get('page') || '1');
  const perPage = Math.min(parseInt(url.searchParams.get('per_page') || '20'), 50);

  if (requestedTags.length === 0) return json({ error: '?tag= is required' }, 400);

  const tagsJson = await env.ARTICLE_KV.get('_article_tags');
  if (!tagsJson) return json({ error: 'Tag index not found' }, 404);

  const tagIndex = JSON.parse(tagsJson);
  const matched = [];

  for (const entry of tagIndex.tags) {
    const allTagValues = [
      entry.开头方式 || '',
      entry.展开方式 || '',
      entry.结尾技巧 || '',
      ...(entry.修辞手法 || []),
      entry.叙事视角 || '',
      entry.情感基调 || '',
      ...(entry.主题标签 || []),
    ];
    let allMatch = true;
    for (const rt of requestedTags) {
      if (!allTagValues.some(v => v.includes(rt))) {
        allMatch = false;
        break;
      }
    }
    if (allMatch) matched.push(entry.article_id);
  }

  const total = matched.length;
  const start = (page - 1) * perPage;
  const pageIds = matched.slice(start, start + perPage);

  const articles = [];
  for (const id of pageIds) {
    try {
      const data = JSON.parse(await env.ARTICLE_KV.get(`article:${id}`));
      articles.push({
        id: data.id,
        title: data.title,
        document_type: data.document_type,
        char_len: data.content_text?.length || 0,
      });
    } catch (e) { /* skip */ }
  }

  return json({ tags: requestedTags, total, page, per_page: perPage, articles });
}

// ── 获取文章全文 ──
async function handleGetArticle(path, env, json) {
  const id = path.split('/')[2];
  if (!id || !/^\d+$/.test(id)) return json({ error: 'Invalid article ID' }, 400);

  const data = await env.ARTICLE_KV.get(`article:${id}`);
  if (!data) return json({ error: 'Article not found' }, 404);

  return new Response(data, {
    headers: { 'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json' },
  });
}

// ── 文章列表 ──
async function handleListArticles(url, env, json) {
  const page = parseInt(url.searchParams.get('page') || '1');
  const perPage = Math.min(parseInt(url.searchParams.get('per_page') || '20'), 100);
  const docType = url.searchParams.get('type');

  const listResult = await env.ARTICLE_KV.list({ prefix: 'article:', limit: 1000 });
  let keys = listResult.keys;

  // 如果还有更多，分页
  let allKeys = keys;
  let cursor = listResult.cursor;
  while (cursor) {
    const next = await env.ARTICLE_KV.list({ prefix: 'article:', limit: 1000, cursor });
    allKeys = allKeys.concat(next.keys);
    cursor = next.cursor;
  }

  const total = allKeys.length;
  const start = (page - 1) * perPage;
  const pageKeys = allKeys.slice(start, start + perPage);

  const articles = [];
  for (const key of pageKeys) {
    try {
      const item = JSON.parse(await env.ARTICLE_KV.get(key.name));
      if (docType && item.document_type !== docType) continue;
      articles.push({
        id: item.id,
        title: item.title,
        document_type: item.document_type,
        confidence: item.confidence,
        char_len: item.content_text?.length || 0,
      });
    } catch (e) { /* skip corrupt entries */ }
  }

  return json({ total, page, per_page: perPage, articles });
}

// ── 随机文章 ──
async function handleRandom(url, env, json) {
  const docType = url.searchParams.get('type');
  const listResult = await env.ARTICLE_KV.list({ prefix: 'article:', limit: 1000 });
  const keys = listResult.keys;

  // 如果没有类型筛选，直接随机
  if (!docType) {
    const key = keys[Math.floor(Math.random() * keys.length)];
    const data = JSON.parse(await env.ARTICLE_KV.get(key.name));
    return json({
      id: data.id,
      title: data.title,
      document_type: data.document_type,
      content_text: data.content_text,
      char_len: data.content_text?.length || 0,
    });
  }

  // 有类型筛选，遍历过滤
  const filtered = [];
  for (const key of keys) {
    const data = JSON.parse(await env.ARTICLE_KV.get(key.name));
    if (data.document_type === docType) filtered.push(data);
  }
  if (filtered.length === 0) return json({ error: 'No matching articles' }, 404);

  const item = filtered[Math.floor(Math.random() * filtered.length)];
  return json({
    id: item.id,
    title: item.title,
    document_type: item.document_type,
    content_text: item.content_text,
    char_len: item.content_text?.length || 0,
  });
}

// ── 暗渡陈仓类文章 ──
async function handleSatirical(url, env, json) {
  const page = parseInt(url.searchParams.get('page') || '1');
  const perPage = Math.min(parseInt(url.searchParams.get('per_page') || '50'), 200);

  // 从 KV 读取暗渡文章 ID 列表
  const idsJson = await env.ARTICLE_KV.get('_satirical_ids');
  if (!idsJson) return json({ error: 'Satirical article list not found' }, 404);

  const ids = JSON.parse(idsJson);
  const total = ids.length;
  const start = (page - 1) * perPage;
  const pageIds = ids.slice(start, start + perPage);

  const articles = [];
  for (const id of pageIds) {
    try {
      const data = JSON.parse(await env.ARTICLE_KV.get(`article:${id}`));
      articles.push({
        id: data.id,
        title: data.title,
        document_type: data.document_type,
        char_len: data.content_text?.length || 0,
      });
    } catch (e) { /* skip */ }
  }

  return json({ total, page, per_page: perPage, articles });
}