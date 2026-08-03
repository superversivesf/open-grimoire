"""Compare models on the same questions. Run after uploading + processing a book.

Usage:
    .venv/bin/python tests/model_comparison.py [--questions "q1|q2|q3"]

Tests each model: time, iterations, answer quality, whether done was called.
"""
import asyncio
import sys
import json
import time
import argparse

sys.path.insert(0, "/home/jason/Repos/rpg-master")

from app.agent.tools import ToolBox
from app.agent.loop import AgentLoop
from app.gateway.ollama import OllamaGateway
from app.config import load_config
from app.storage.shared_db import init_shared_db, list_users
from app.storage.user_db import init_user_db, list_collections


DEFAULT_QUESTIONS = [
    "What security clearances are available?",
    "How does combat work?",
    "What happens when a character dies?",
]


async def test_model(model_name: str, questions: list[str], cfg, toolbox, num_ctx=32768):
    """Run a set of questions through a specific model and return results."""
    models = {**cfg.models, "query": model_name}
    gw = OllamaGateway(cfg.ollama_host, models, num_ctx=num_ctx)

    results = []
    for q in questions:
        print(f"  Q: {q}")
        start = time.time()
        loop = AgentLoop(gw, toolbox, max_iterations=12)
        try:
            result = await loop.run([], q)
        except Exception as e:
            result = {"answer": f"ERROR: {e}", "cites": [], "iterations": 0}
        elapsed = time.time() - start

        answer = result["answer"][:200]
        cites = len(result.get("cites", []))
        iters = result.get("iterations", 0)

        print(f"  A: {answer}")
        print(f"     iters={iters} cites={cites} time={elapsed:.1f}s")
        print()

        results.append({
            "question": q,
            "answer": result["answer"],
            "cites": result.get("cites", []),
            "iterations": iters,
            "elapsed": round(elapsed, 1),
            "model": model_name,
        })

    await gw.close()
    return results


async def main():
    parser = argparse.ArgumentParser(description="Compare LLM models for RPG query agent")
    parser.add_argument("--questions", type=str, default=None, help="Pipe-separated questions")
    parser.add_argument("--models", type=str, default=None, help="Comma-separated model names")
    parser.add_argument("--num-ctx", type=int, default=32768, help="Context window size")
    args = parser.parse_args()

    questions = args.questions.split("|") if args.questions else DEFAULT_QUESTIONS

    cfg = load_config("/home/jason/Repos/rpg-master/config.yaml")

    sconn = init_shared_db(cfg.db_dir)
    users = list_users(sconn)
    sconn.close()
    if not users:
        print("No users found. Create one first: .venv/bin/python -m app.cli create --username admin --password admin --admin")
        return
    uid = users[0]["user_id"]

    uconn = init_user_db(cfg.db_dir, uid)
    cols = list_collections(uconn)
    uconn.close()
    if not cols:
        print("No collections found. Upload a book first.")
        return
    cid = cols[0]["collection_id"]
    print(f"Using collection: {cols[0]['name']}")

    toolbox = ToolBox(cfg.data_dir, uid, cfg.db_dir, cid)

    if args.models:
        model_list = args.models.split(",")
    else:
        # Auto-detect available local models with tool support
        import subprocess
        result = subprocess.run(["curl", "-s", "http://localhost:11434/api/tags"], capture_output=True, text=True)
        data = json.loads(result.stdout)
        model_list = [m["name"] for m in data["models"]
                      if "tools" in m.get("capabilities", []) and not m["name"].endswith(":cloud")]
        print(f"Testing local models with tool support: {model_list}")

    all_results = {}
    for model_name in model_list:
        print(f"\n{'='*70}")
        print(f"MODEL: {model_name} (num_ctx={args.num_ctx})")
        print(f"{'='*70}")
        results = await test_model(model_name, questions, cfg, toolbox, args.num_ctx)
        all_results[model_name] = results

    # Summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    print(f"{'Model':<30} {'Q':<45} {'Iters':>6} {'Cites':>6} {'Time':>6}")
    print("-" * 95)
    for model_name, results in all_results.items():
        for r in results:
            q = r["question"][:43]
            print(f"{model_name:<30} {q:<45} {r['iterations']:>6} {len(r['cites']):>6} {r['elapsed']:>5.1f}s")
        print()

    # Save full results
    output_path = "/tmp/model_comparison_results.json"
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"Full results saved to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())