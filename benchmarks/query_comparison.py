"""Query-model comparison with an LLM judge.

Runs a fixed set of questions through each candidate query model (full
agent loop), then has a judge model score each answer on correctness,
citation use, completeness, and whether it actually answered.

Usage:
    DEV_MODE=1 .venv/bin/python benchmarks/query_comparison.py \
        --models "deepseek-v4-flash:0731-cloud,phi4-mini:3.8b,phi4:14b" \
        --judge "deepseek-v4-pro:cloud" \
        --questions "How do I create a character?|What is a goblin's AC?"
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.agent.loop import AgentLoop
from app.agent.tools import ToolBox
from app.config import load_config
from app.gateway.ollama import OllamaGateway
from app.storage.shared_db import init_shared_db, list_users
from app.storage.user_db import init_user_db, list_collections

DEFAULT_QUESTIONS = [
    "How do I create a character?",
    "What is a goblin's armor class?",
    "How does combat work?",
    "What character classes are available?",
    "What spells can a sorcerer cast?",
]

JUDGE_PROMPT = """You are evaluating answers from an RPG rules assistant. The assistant searches a
user's RPG manual collection and answers questions with citations.

Score the answer 0-10 on each axis:
- correctness: is the answer factually right per the cited sources?
- citation_use: does it cite sources with paths/pages?
- completeness: does it fully answer the question?
- answered: did it actually answer, or did it give up / claim it couldn't?

Return ONLY JSON: {{"correctness": N, "citation_use": N, "completeness": N, "answered": 0|1, "reason": "one line"}}

Question: {question}
Answer: {answer}
Citations: {cites}
"""


async def run_question(model_name: str, question: str, cfg, toolbox, num_ctx: int) -> dict:
    models = {**cfg.models, "query": model_name}
    gw = OllamaGateway(cfg.ollama_host, models, num_ctx=num_ctx)
    try:
        loop = AgentLoop(gw, toolbox, max_iterations=12)
        t0 = time.time()
        try:
            result = await asyncio.wait_for(loop.run([], question), timeout=180)
        except asyncio.TimeoutError:
            result = {"answer": "TIMEOUT", "cites": [], "iterations": 0, "done_called": False}
        elapsed = time.time() - t0
        return {
            "model": model_name,
            "question": question,
            "answer": result.get("answer", ""),
            "cites": result.get("cites", []),
            "iterations": result.get("iterations", 0),
            "done_called": result.get("done_called", False),
            "elapsed": round(elapsed, 1),
        }
    finally:
        await gw.close()


async def judge_answer(judge_gw, question: str, result: dict) -> dict:
    cites = json.dumps(result.get("cites", []))[:500]
    prompt = JUDGE_PROMPT.format(
        question=question,
        answer=(result.get("answer") or "")[:2000],
        cites=cites,
    )
    try:
        resp = await judge_gw.call("query", prompt)
        raw = resp.get("message", {}).get("content", "")
        # Extract JSON from the response.
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            return json.loads(raw[start:end + 1])
    except Exception as e:
        return {"error": str(e)}
    return {"error": "no JSON in judge response"}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", type=str, default="deepseek-v4-flash:0731-cloud,phi4-mini:3.8b")
    parser.add_argument("--judge", type=str, default="deepseek-v4-pro:cloud")
    parser.add_argument("--questions", type=str, default=None)
    parser.add_argument("--num-ctx", type=int, default=16384)
    args = parser.parse_args()

    questions = args.questions.split("|") if args.questions else DEFAULT_QUESTIONS
    models = [m.strip() for m in args.models.split(",")]

    cfg = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    sconn = init_shared_db(cfg.db_dir)
    users = list_users(sconn)
    sconn.close()
    if not users:
        print("no users")
        return
    uid = users[0]["user_id"]
    uconn = init_user_db(cfg.db_dir, uid)
    cols = list_collections(uconn)
    uconn.close()
    if not cols:
        print("no collections")
        return
    cid = cols[0]["collection_id"]
    print(f"collection: {cols[0]['name']}")

    toolbox = ToolBox(cfg.data_dir, uid, cfg.db_dir, cid)
    judge_gw = OllamaGateway(cfg.ollama_host, {"query": args.judge}, num_ctx=8192)

    all_results = {}
    for model in models:
        print(f"\n=== MODEL: {model} ===")
        model_results = []
        for q in questions:
            print(f"  Q: {q[:60]}")
            r = await run_question(model, q, cfg, toolbox, args.num_ctx)
            print(f"    iters={r['iterations']} cites={len(r['cites'])} time={r['elapsed']}s done={r['done_called']}")
            print(f"    A: {(r['answer'] or '')[:120]}")
            score = await judge_answer(judge_gw, q, r)
            r["judge"] = score
            model_results.append(r)
            print(f"    judge: {score}")
        all_results[model] = model_results

    await judge_gw.close()

    # Summary table
    print(f"\n{'='*100}")
    print("QUERY MODEL COMPARISON (LLM-judged)")
    print(f"{'='*100}")
    print(f"{'Model':<28} {'Q':<40} {'Corr':>5} {'Cite':>5} {'Comp':>5} {'Ans':>4} {'Iters':>6} {'Time':>6}")
    print("-" * 100)
    for model, results in all_results.items():
        for r in results:
            j = r.get("judge", {})
            corr = j.get("correctness", "-")
            cite = j.get("citation_use", "-")
            comp = j.get("completeness", "-")
            ans = j.get("answered", "-")
            print(f"{model:<28} {r['question'][:38]:<40} {corr:>5} {cite:>5} {comp:>5} {ans:>4} "
                  f"{r['iterations']:>6} {r['elapsed']:>5.1f}s")
        print()

    # Averages
    print(f"\n{'='*100}")
    print("AVERAGES")
    print(f"{'='*100}")
    print(f"{'Model':<28} {'Corr':>6} {'Cite':>6} {'Comp':>6} {'Ans%':>6} {'Iters':>6} {'Time':>6}")
    for model, results in all_results.items():
        n = len(results)
        corr = sum(r["judge"].get("correctness", 0) for r in results if "correctness" in r["judge"]) / n
        cite = sum(r["judge"].get("citation_use", 0) for r in results if "citation_use" in r["judge"]) / n
        comp = sum(r["judge"].get("completeness", 0) for r in results if "completeness" in r["judge"]) / n
        ans = sum(1 for r in results if r["judge"].get("answered") == 1) / n * 100
        iters = sum(r["iterations"] for r in results) / n
        elapsed = sum(r["elapsed"] for r in results) / n
        print(f"{model:<28} {corr:>6.1f} {cite:>6.1f} {comp:>6.1f} {ans:>5.0f}% {iters:>6.1f} {elapsed:>5.1f}s")

    out = "/tmp/query_comparison_results.json"
    with open(out, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nFull results: {out}")


if __name__ == "__main__":
    asyncio.run(main())
