#!/usr/bin/env python3
"""Push vector embeddings from CSV to Cloudflare Vectorize."""
import csv, json, os, sys, time, urllib.request

def main():
    account_id = os.environ.get("CF_ACCOUNT_ID") or sys.argv[1]
    api_token = os.environ.get("CF_API_TOKEN") or sys.argv[2]
    index_name = os.environ.get("CF_INDEX_NAME", "magazine-articles")
    csv_path = os.environ.get("CSV_PATH", "content/database/chunk_embeddings.csv")

    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/vectorize/v2/indexes/{index_name}/upsert"

    print(f"Reading {csv_path}...")
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    total = len(rows)
    print(f"Total vectors: {total}")

    batch_size = 500
    pushed = 0
    t0 = time.time()

    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]
        vectors = []
        for row in batch:
            # Parse PostgreSQL array string "[0.1,0.2,...]" into list of floats
            emb_str = row["embedding"].strip("{}")
            emb_list = [float(x) for x in emb_str.split(",")]
            vectors.append({
                "id": f"chunk_{row['chunk_id']}",
                "values": emb_list,
                "metadata": {
                    "doc_id": int(float(row.get("doc_id", 0))),
                    "title": (row.get("title", "") or "")[:100],
                    "doc_type": row.get("doc_type", "") or "",
                    "snippet": (row.get("snippet", "") or "")[:200],
                },
            })

        body = json.dumps({"vectors": vectors}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"},
            method="POST",
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    result = json.loads(resp.read())
                    if result.get("success"):
                        pushed += len(vectors)
                        elapsed = time.time() - t0
                        print(f"  [{pushed}/{total}] {elapsed:.0f}s, {pushed/elapsed:.0f} vec/s")
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

    print(f"\nDone! {pushed}/{total} vectors pushed in {time.time()-t0:.0f}s")

if __name__ == "__main__":
    main()