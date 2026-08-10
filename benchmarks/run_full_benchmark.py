"""Run full benchmark matrix using subprocess for each model.

Each model runs as a separate process with a hard timeout.
This avoids asyncio cancellation issues with hanging models.
"""
import asyncio
import sys
import json
import time
import os
import signal
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import load_config
from app.storage.shared_db import init_shared_db, list_users
from app.storage.user_db import init_user_db, list_collections


def get_all_models(cfg):
    """Get all testable model names."""
    import urllib.request
    data = json.loads(urllib.request.urlopen(f"{cfg.ollama_host}/api/tags").read())
    models = []
    for m in data["models"]:
        name = m["name"]
        caps = m.get("capabilities", [])
        size_mb = m.get("size", 0) // 1024 // 1024
        is_cloud = ":cloud" in name
        if "kimi" in name:
            continue
        if "embedding" in name or "embed" in name:
            continue
        if not is_cloud and size_mb > 11000:
            continue
        if "completion" not in caps and "tools" not in caps:
            continue
        models.append(name)
    return models


async def main():
    cfg = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    models = get_all_models(cfg)
    print(f"Testing {len(models)} models: {models}")

    results_file = Path("/tmp/full_benchmark.json")
    all_results = {}

    for i, model_name in enumerate(models):
        print(f"\n[{i+1}/{len(models)}] {model_name}", flush=True)

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(Path(__file__).parent / "single_model_bench.py"),
                model_name,
                "10",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(Path(__file__).parent.parent),
                start_new_session=True,
            )

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
                stdout = stdout.decode()
                stderr = stderr.decode()
            except asyncio.TimeoutError:
                # Kill the entire process group
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
                print(f"  TIMEOUT (180s) — skipping", flush=True)
                all_results[model_name] = []
                # Save intermediate results
                with open(results_file, "w") as f:
                    json.dump({
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "models_tested": list(all_results.keys()),
                        "enrichment": all_results,
                    }, f, indent=2, default=str)
                continue

            if proc.returncode != 0:
                print(f"  ERROR (exit {proc.returncode}): {stderr[:200]}", flush=True)
                all_results[model_name] = []
                continue

            # Last line of stdout should be the JSON array
            lines = stdout.strip().split("\n")
            json_line = lines[-1]
            try:
                model_results = json.loads(json_line)
                all_results[model_name] = model_results
                n = len(model_results)
                if n > 0:
                    valid = [r for r in model_results if r.get("json_valid")]
                    avg_score = sum(r["quality_score"] for r in model_results) / n
                    avg_time = sum(r["elapsed"] for r in model_results) / n
                    print(f"  score={avg_score:.2f} time={avg_time:.1f}s json={len(valid)}/{n}", flush=True)
                else:
                    print(f"  no results", flush=True)
            except json.JSONDecodeError:
                print(f"  JSON parse error: {json_line[:100]}", flush=True)
                all_results[model_name] = []

        except Exception as e:
            print(f"  EXCEPTION: {e}", flush=True)
            all_results[model_name] = []

        # Save intermediate results
        with open(results_file, "w") as f:
            json.dump({
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "models_tested": list(all_results.keys()),
                "enrichment": all_results,
            }, f, indent=2, default=str)

    # Final summary
    print(f"\n{'='*90}")
    print("FULL ENRICHMENT MODEL COMPARISON")
    print(f"{'='*90}")
    print(f"{'Model':<35} {'JSON%':>6} {'Score':>6} {'Time':>6} {'Words':>6} {'KW':>5} {'Topic%':>7} {'Cost':>5}")
    print("-" * 90)

    ranked = []
    for model_name, results in all_results.items():
        if not results:
            print(f"{model_name:<35}  --- FAILED ---")
            continue
        n = len(results)
        json_pct = sum(1 for r in results if r.get("json_valid")) / n * 100
        avg_score = sum(r["quality_score"] for r in results) / n
        avg_time = sum(r["elapsed"] for r in results) / n
        avg_words = sum(r["summary_words"] for r in results) / n
        avg_kw = sum(r["keyword_count"] for r in results) / n
        topic_pct = sum(1 for r in results if r.get("topic_match")) / n * 100
        is_cloud = ":cloud" in model_name
        cost = "$" if is_cloud else "free"

        print(f"{model_name:<35} {json_pct:>5.0f}% {avg_score:>5.2f} "
              f"{avg_time:>5.1f}s {avg_words:>5.0f} {avg_kw:>4.1f} {topic_pct:>6.0f}% {cost:>5}")
        ranked.append((model_name, avg_score, avg_time, cost))

    # Final ranking
    print(f"\n{'='*90}")
    print("FINAL RANKING (by quality score)")
    print(f"{'='*90}")
    ranked.sort(key=lambda x: x[1], reverse=True)
    for rank, (model_name, score, avg_time, cost) in enumerate(ranked, 1):
        value = score / avg_time if avg_time > 0 else 0
        print(f"  {rank:>2}. {model_name:<35} score={score:.2f} time={avg_time:.1f}s "
              f"value={value:.3f} [{cost}]")

    # Models safe to delete
    print(f"\n{'='*90}")
    print("MODELS SAFE TO DELETE (score < 0.70 or JSON% < 80%)")
    print(f"{'='*90}")
    for model_name, results in all_results.items():
        if not results:
            print(f"  {model_name} — FAILED/ERROR")
            continue
        n = len(results)
        avg_score = sum(r["quality_score"] for r in results) / n
        json_pct = sum(1 for r in results if r.get("json_valid")) / n * 100
        if avg_score < 0.70 or json_pct < 80:
            print(f"  {model_name} — score={avg_score:.2f} json={json_pct:.0f}%")

    # Models worth keeping
    print(f"\n{'='*90}")
    print("MODELS WORTH KEEPING (score >= 0.80)")
    print(f"{'='*90}")
    for model_name, score, avg_time, cost in ranked:
        if score >= 0.80:
            print(f"  {model_name:<35} score={score:.2f} time={avg_time:.1f}s [{cost}]")

    print(f"\nResults saved to {results_file}")


if __name__ == "__main__":
    asyncio.run(main())