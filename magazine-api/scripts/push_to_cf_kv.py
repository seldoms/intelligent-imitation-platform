#!/usr/bin/env python3
"""Push article texts from CSV to Cloudflare Workers KV."""
import csv, json, os, sys, time, urllib.request

def main():
    account_id = os.environ.get("CF_ACCOUNT_ID") or sys.argv[1]
    api_token = os.environ.get("CF_API_TOKEN") or sys.argv[2]
    kv_namespace = os.environ.get("CF_KV_NAMESPACE") or sys.argv[3]
    csv_path = os.environ.get("CSV_PATH", "content/database/cleaned_documents.csv")

    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/storage/kv/namespaces/{kv_namespace}/bulk"

    print(f"Reading {csv_path}...")
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    total = len(rows)
    print(f"Total articles: {total}")

    batch_size = 5000
    pushed = 0
    t0 = time.time()

    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]
        pairs = []
        for row in batch:
            key = f"article:{row['id']}"
            value = json.dumps({
                "id": int(row["id"]),
                "title": row.get("title", ""),
                "document_type": row.get("document_type", ""),
                "status": row.get("status", "published"),
                "confidence": float(row.get("confidence", 0)),
                "content_text": row.get("content_text", ""),
            }, ensure_ascii=False)
            pairs.append({"key": key, "value": value})

        body = json.dumps(pairs, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"},
            method="PUT",
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    result = json.loads(resp.read())
                    if result.get("success"):
                        pushed += len(pairs)
                        elapsed = time.time() - t0
                        print(f"  [{pushed}/{total}] {elapsed:.0f}s, {pushed/elapsed:.0f} keys/s")
                        break
                    print(f"  Error: {result.get('errors', 'unknown')}")
                    break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    print("  Rate limited, waiting 10s...")
                    time.sleep(10)
                    continue
                print(f"  HTTP {e.code}: {e.read().decode()[:200]}")
                break

    print(f"\nDone! {pushed}/{total} articles pushed in {time.time()-t0:.0f}s")

if __name__ == "__main__":
    main()