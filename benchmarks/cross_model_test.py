"""Test whether enrichment model choice affects search quality.

Takes the same book, enriches it with two different models, then
runs the same set of questions against both and compares results.
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
from app.storage.user_db import init_user_db, list_collections, list_docs
from app.agent.tools import ToolBox
from app.agent.loop import AgentLoop
from app.pipeline.enrich import Enricher
from app.pipeline.index import index_document


def strip_frontmatter(text):
    if text.startswith("---"):
        parts = text.split("---", 2)
        return parts[2].strip() if len(parts) > 2 else text
    return text


def get_frontmatter(text):
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) > 2:
            fm_text = parts[1]
            fm = {}
            for line in fm_text.splitlines():
                if ":" in line:
                    key, _, val = line.partition(":")
                    fm[key.strip()] = val.strip()
            return fm
    return {}


async def enrich_with_model(model_name, leaf_paths, udata, page_map, cfg, num_ctx=4096):
    """Re-enrich all leaf files with a specific model, return new front-matter."""
    models = {"enrich": model_name, "query": model_name}
    gw = OllamaGateway(cfg.ollama_host, models, num_ctx=num_ctx)
    enricher = Enricher(gw)

    # Backup original content (strip old front-matter)
    originals = {}
    for p in leaf_paths:
        full = udata / p
        content = full.read_text()
        raw = strip_frontmatter(content)
        fm = get_frontmatter(content)
        originals[p] = (raw, fm.get("page"))

    # Re-enrich each file
    enriched = {}
    for i, p in enumerate(leaf_paths):
        full = udata / p
        raw_content, page = originals[p]
        # Write raw content back (strip old front-matter)
        full.write_text(raw_content)
        try:
            result = await asyncio.wait_for(
                enricher.enrich_leaf(full, page),
                timeout=120
            )
            enriched[p] = result
            print(f"  [{i+1}/{len(leaf_paths)}] {p.split('/')[-1][:40]:<42} "
                  f"summary={result.get('summary', '')[:50]}...", flush=True)
        except asyncio.TimeoutError:
            print(f"  [{i+1}/{len(leaf_paths)}] TIMEOUT", flush=True)
            enriched[p] = {"summary": "", "keywords": []}

    await gw.close()
    return enriched


async def run_questions(model_name, questions, cfg, toolbox, num_ctx=8192):
    """Run Q&A with a specific query model."""
    models = {"query": model_name, "enrich": model_name}
    gw = OllamaGateway(cfg.ollama_host, models, num_ctx=num_ctx)

    results = []
    for q in questions:
        t0 = time.time()
        loop = AgentLoop(gw, toolbox, max_iterations=12)
        try:
            result = await asyncio.wait_for(loop.run([], q), timeout=120)
        except asyncio.TimeoutError:
            result = {"answer": "TIMEOUT", "cites": [], "iterations": 0}
        elapsed = time.time() - t0

        results.append({
            "question": q,
            "answer": result.get("answer", ""),
            "cites": len(result.get("cites", [])),
            "iterations": result.get("iterations", 0),
            "elapsed": round(elapsed, 1),
        })
        print(f"  Q: {q[:50]}", flush=True)
        print(f"  A: {result.get('answer', '')[:80]}...", flush=True)
        print(f"     cites={len(result.get('cites', []))} iters={result.get('iterations', 0)} "
              f"time={elapsed:.1f}s", flush=True)
        print(flush=True)

    await gw.close()
    return results


async def main():
    cfg = load_config(str(Path(__file__).parent.parent / "config.yaml"))

    sconn = init_shared_db(cfg.db_dir)
    users = list_users(sconn)
    sconn.close()
    uid = users[0]["user_id"]
    uconn = init_user_db(cfg.db_dir, uid)
    cols = list_collections(uconn)
    docs = list_docs(uconn, cols[0]["collection_id"])
    uconn.close()

    cid = cols[0]["collection_id"]
    print(f"User: {uid}")
    print(f"Collection: {cols[0]['name']}")
    print(f"Docs: {len(docs)}")

    from app.storage.user_db import get_doc
    uconn = init_user_db(cfg.db_dir, uid)
    doc = get_doc(uconn, docs[0]["doc_id"])
    uconn.close()
    doc_id = docs[0]["doc_id"]
    doc_dir = cfg.data_dir / uid / doc_id

    # Get all leaf markdown files
    leaf_files = sorted([f for f in doc_dir.rglob("*.md") if f.name != "index.md"])
    # Limit to first 20 for speed
    leaf_files = leaf_files[:20]
    leaf_paths = [str(f.relative_to(cfg.data_dir / uid)) for f in leaf_files]
    udata = cfg.data_dir / uid

    print(f"Testing with {len(leaf_paths)} sections from '{doc['title']}'")
    print(f"Sections: {[p.split('/')[-1][:30] for p in leaf_paths[:5]]}...")

    questions = [
        "What is a goblin's armor class?",
        "How does combat work?",
        "What character classes are available?",
        "What spells can a sorcerer cast?",
        "How do I create a new character?",
    ]

    # Models to test
    enrich_models = ["phi4-mini:3.8b", "deepseek-v4-flash:0731-cloud"]
    query_model = "deepseek-v4-flash:0731-cloud"

    all_results = {}

    for enrich_model in enrich_models:
        print(f"\n{'='*80}")
        print(f"ENRICH WITH: {enrich_model}")
        print(f"{'='*80}")

        # Re-enrich
        enriched = await enrich_with_model(enrich_model, leaf_paths, udata, {}, cfg)

        # Rebuild FTS index with new enrichment
        uconn = init_user_db(cfg.db_dir, uid)
        index_document(uconn, leaf_paths, udata, doc_id)
        fts_count = uconn.execute("SELECT count(*) FROM documents_fts").fetchone()[0]
        uconn.close()
        print(f"FTS index rebuilt: {fts_count} rows")

        # Run FTS search test
        toolbox = ToolBox(cfg.data_dir, uid, cfg.db_dir, cid)
        print(f"\nFTS search tests:")
        fts_results = {}
        for q in questions:
            # Extract key terms for FTS
            terms = q.lower().replace("'s", "").replace("?", "")
            results = toolbox.fts_search(terms)
            fts_results[q] = len(results)
            print(f"  '{q[:40]}': {len(results)} results")

        # Run Q&A tests with the query model
        print(f"\nQ&A with {query_model}:")
        qa_results = await run_questions(query_model, questions, cfg, toolbox)

        all_results[enrich_model] = {
            "enrichment": {k: v for k, v in enriched.items()},
            "fts_results": fts_results,
            "qa_results": qa_results,
        }

    # Compare
    print(f"\n{'='*80}")
    print("COMPARISON: Does enrichment model affect search + Q&A?")
    print(f"{'='*80}")
    print(f"\n{'Question':<45} {'phi4-mini FTS':>14} {'deepseek FTS':>14} {'phi4 Q&A iters':>15} {'deepseek Q&A iters':>19}")
    print("-" * 110)

    for q in questions:
        phi_fts = all_results["phi4-mini:3.8b"]["fts_results"].get(q, 0)
        ds_fts = all_results["deepseek-v4-flash:0731-cloud"]["fts_results"].get(q, 0)
        phi_qa = [r for r in all_results["phi4-mini:3.8b"]["qa_results"] if r["question"] == q]
        ds_qa = [r for r in all_results["deepseek-v4-flash:0731-cloud"]["qa_results"] if r["question"] == q]
        phi_iters = phi_qa[0]["iterations"] if phi_qa else "?"
        ds_iters = ds_qa[0]["iterations"] if ds_qa else "?"
        phi_cites = phi_qa[0]["cites"] if phi_qa else "?"
        ds_cites = ds_qa[0]["cites"] if ds_qa else "?"

        print(f"{q[:43]:<45} {phi_fts:>14} {ds_fts:>14} "
              f"{phi_iters:>7} cites={phi_cites:<3} {ds_iters:>8} cites={ds_cites}")

    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    for model_name in enrich_models:
        r = all_results[model_name]
        avg_fts = sum(r["fts_results"].values()) / len(r["fts_results"])
        qa = r["qa_results"]
        avg_iters = sum(q["iterations"] for q in qa) / len(qa) if qa else 0
        avg_cites = sum(q["cites"] for q in qa) / len(qa) if qa else 0
        avg_time = sum(q["elapsed"] for q in qa) / len(qa) if qa else 0
        print(f"\n  Enriched with {model_name}:")
        print(f"    Avg FTS results per question: {avg_fts:.1f}")
        print(f"    Avg Q&A iterations: {avg_iters:.1f}")
        print(f"    Avg citations: {avg_cites:.1f}")
        print(f"    Avg Q&A time: {avg_time:.1f}s")

    # Save
    with open("/tmp/cross_model_test.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nResults saved to /tmp/cross_model_test.json")


if __name__ == "__main__":
    asyncio.run(main())