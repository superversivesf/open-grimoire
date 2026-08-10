"""Full matrix benchmark — test all available models for enrichment quality.

Runs enrichment on 10 sample sections from the user's processed books,
scores each model, and writes results to /tmp/full_benchmark.json.
"""
import asyncio
import sys
import json
import time
import re
from pathlib import Path
from dataclasses import asdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.gateway.ollama import OllamaGateway
from app.config import load_config
from app.storage.shared_db import init_shared_db, list_users
from app.storage.user_db import init_user_db, list_collections
from benchmarks.enrich_comparison import (
    collect_enrich_samples, score_enrichment,
    check_topic_match, EnrichResult, print_enrich_summary
)


async def test_enrich_model_with_timeout(model_name, samples, cfg, num_ctx=4096, timeout_per_call=120):
    """Run enrichment with a per-call timeout. Skips models that hang."""
    models = {"enrich": model_name, "query": model_name}
    gw = OllamaGateway(cfg.ollama_host, models, num_ctx=num_ctx)

    # Warmup: send a tiny prompt to load the model into GPU memory
    try:
        print(f"  warming up model...")
        await asyncio.wait_for(
            gw.call("enrich", "Say hello."),
            timeout=timeout_per_call
        )
    except asyncio.TimeoutError:
        print(f"  WARMUP TIMEOUT — model takes too long to load, skipping")
        await gw.close()
        return [EnrichResult(
            model=model_name, section_path="", section_title="",
            raw_response="WARMUP TIMEOUT", parsed=None,
        )]

    results = []
    for i, sample in enumerate(samples):
        t0 = time.time()
        prompt = (
            "Read this RPG manual section and produce a JSON object with "
            "a 1-2 sentence 'summary' and a list of 3-8 'keywords' (lowercase). "
            "Return ONLY valid JSON, no prose.\n\n"
            f"{sample['content']}"
        )
        try:
            resp = await asyncio.wait_for(
                gw.call("enrich", prompt),
                timeout=timeout_per_call
            )
            raw = resp.get("message", {}).get("content", "")
        except asyncio.TimeoutError:
            print(f"  [{i+1}/{len(samples)}] TIMEOUT ({timeout_per_call}s) — skipping remaining sections")
            # Fill remaining with timeout errors
            for j in range(i, len(samples)):
                results.append(EnrichResult(
                    model=model_name,
                    section_path=samples[j]["path"],
                    section_title=samples[j]["title"],
                    raw_response="TIMEOUT",
                    parsed=None,
                    elapsed=timeout_per_call,
                ))
            await gw.close()
            return results
        except Exception as e:
            raw = f"ERROR: {e}"
        elapsed = time.time() - t0

        # Parse
        parsed = None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            import re
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass

        summary = (parsed or {}).get("summary", "") if parsed else ""
        keywords = (parsed or {}).get("keywords", []) if parsed else []

        result = EnrichResult(
            model=model_name,
            section_path=sample["path"],
            section_title=sample["title"],
            raw_response=raw[:500],
            parsed=parsed,
            summary=summary,
            keywords=keywords if isinstance(keywords, list) else [],
            elapsed=round(elapsed, 2),
            json_valid=parsed is not None,
            summary_words=len(summary.split()),
            keyword_count=len(keywords) if isinstance(keywords, list) else 0,
            topic_match=check_topic_match(summary, sample["title"]) if summary else False,
        )
        result.quality_score = score_enrichment(result, sample)
        results.append(result)

        status = "OK" if result.json_valid else "FAIL"
        print(f"  [{i+1}/{len(samples)}] {status} {sample['title'][:40]:<42} "
              f"score={result.quality_score:.2f} {result.elapsed:.1f}s "
              f"words={result.summary_words} kw={result.keyword_count}")

    await gw.close()
    return results


async def main():
    cfg = load_config(str(Path(__file__).parent.parent / "config.yaml"))

    # Get all available models (skip embedding, kimi, and >12GB local)
    import urllib.request
    data = json.loads(urllib.request.urlopen(f"{cfg.ollama_host}/api/tags").read())
    all_models = []
    for m in data["models"]:
        name = m["name"]
        caps = m.get("capabilities", [])
        size_mb = m.get("size", 0) // 1024 // 1024
        is_cloud = ":cloud" in name
        # Skip kimi (402 error)
        if "kimi" in name:
            continue
        # Skip embedding models
        if "embedding" in name or "embed" in name:
            continue
        # Skip models too large for 12GB GPU
        if not is_cloud and size_mb > 11000:
            continue
        # Skip models without completion capability
        if "completion" not in caps and "tools" not in caps:
            continue
        all_models.append(name)

    print(f"Testing {len(all_models)} models: {all_models}")

    # Get user and collection
    sconn = init_shared_db(cfg.db_dir)
    users = list_users(sconn)
    sconn.close()
    uid = users[0]["user_id"]
    uconn = init_user_db(cfg.db_dir, uid)
    cols = list_collections(uconn)
    uconn.close()
    cid = cols[0]["collection_id"]
    print(f"Using collection: {cols[0]['name']}")

    # Collect samples
    samples = collect_enrich_samples(cfg.data_dir, uid, 10)
    print(f"Collected {len(samples)} sections")

    all_results = {}
    for i, model_name in enumerate(all_models):
        print(f"\n{'='*70}")
        print(f"[{i+1}/{len(all_models)}] ENRICHMENT: {model_name}")
        print(f"{'='*70}")
        try:
            results = await test_enrich_model_with_timeout(model_name, samples, cfg, num_ctx=4096, timeout_per_call=60)
            all_results[model_name] = [asdict(r) for r in results]
        except Exception as e:
            print(f"  FAILED: {e}")
            all_results[model_name] = []

        # Save intermediate results after each model
        with open("/tmp/full_benchmark.json", "w") as f:
            json.dump({
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "models_tested": list(all_results.keys()),
                "enrichment": all_results,
            }, f, indent=2, default=str)

    # Print summary
    print(f"\n{'='*90}")
    print("FULL ENRICHMENT MODEL COMPARISON")
    print(f"{'='*90}")
    print(f"{'Model':<35} {'JSON%':>6} {'Score':>6} {'Time':>6} {'Words':>6} {'KW':>5} {'Topic%':>7}")
    print("-" * 90)

    ranked = []
    for model_name, results in all_results.items():
        if not results:
            print(f"{model_name:<35}  --- FAILED ---")
            continue
        n = len(results)
        json_pct = sum(1 for r in results if r["json_valid"]) / n * 100
        avg_score = sum(r["quality_score"] for r in results) / n
        avg_time = sum(r["elapsed"] for r in results) / n
        avg_words = sum(r["summary_words"] for r in results) / n
        avg_kw = sum(r["keyword_count"] for r in results) / n
        topic_pct = sum(1 for r in results if r["topic_match"]) / n * 100
        is_cloud = ":cloud" in model_name
        cost = "$" if is_cloud else "free"

        print(f"{model_name:<35} {json_pct:>5.0f}% {avg_score:>5.2f} "
              f"{avg_time:>5.1f}s {avg_words:>5.0f} {avg_kw:>4.1f} {topic_pct:>6.0f}% [{cost}]")
        ranked.append((model_name, avg_score, avg_time, cost))

    # Final ranking
    print(f"\n{'='*90}")
    print("FINAL RANKING")
    print(f"{'='*90}")
    ranked.sort(key=lambda x: x[1], reverse=True)
    for rank, (model_name, score, avg_time, cost) in enumerate(ranked, 1):
        # Value score: quality / time (higher is better)
        value = score / avg_time if avg_time > 0 else 0
        print(f"  {rank:>2}. {model_name:<35} score={score:.2f} time={avg_time:.1f}s "
              f"value={value:.3f} [{cost}]")

    # Recommended for deletion
    print(f"\n{'='*90}")
    print("MODELS SAFE TO DELETE (score < 0.70 or JSON% < 80%)")
    print(f"{'='*90}")
    for model_name, results in all_results.items():
        if not results:
            print(f"  {model_name} — FAILED/ERROR")
            continue
        n = len(results)
        avg_score = sum(r["quality_score"] for r in results) / n
        json_pct = sum(1 for r in results if r["json_valid"]) / n * 100
        if avg_score < 0.70 or json_pct < 80:
            print(f"  {model_name} — score={avg_score:.2f} json={json_pct:.0f}%")

    print(f"\nResults saved to /tmp/full_benchmark.json")


if __name__ == "__main__":
    asyncio.run(main())