"""Run enrichment benchmark for a single model. Outputs JSON to stdout.

Usage: python tests/single_model_bench.py <model_name> [num_samples]
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
from tests.enrich_comparison import (
    collect_enrich_samples, score_enrichment,
    check_topic_match, EnrichResult
)


async def main():
    model_name = sys.argv[1]
    num_samples = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    cfg = load_config(str(Path(__file__).parent.parent / "config.yaml"))

    sconn = init_shared_db(cfg.db_dir)
    users = list_users(sconn)
    sconn.close()
    uid = users[0]["user_id"]
    uconn = init_user_db(cfg.db_dir, uid)
    cols = list_collections(uconn)
    uconn.close()

    samples = collect_enrich_samples(cfg.data_dir, uid, num_samples)

    models = {"enrich": model_name, "query": model_name}
    gw = OllamaGateway(cfg.ollama_host, models, num_ctx=4096)

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
            resp = await asyncio.wait_for(gw.call("enrich", prompt), timeout=120)
            raw = resp.get("message", {}).get("content", "")
        except asyncio.TimeoutError:
            raw = "TIMEOUT"
        except Exception as e:
            raw = f"ERROR: {e}"
        elapsed = time.time() - t0

        parsed = None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
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
        results.append(asdict(result))

        print(f"  [{i+1}/{len(samples)}] score={result.quality_score:.2f} {result.elapsed:.1f}s", flush=True)

    await gw.close()

    # Output JSON to stdout
    print(json.dumps(results), flush=True)


if __name__ == "__main__":
    asyncio.run(main())