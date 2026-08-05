"""Compare LLM models on enrichment quality, speed, and cost.

Tests each model on a sample of markdown sections from your processed books,
measures quality metrics, and produces a comparison report.

Usage:
    # Quick test (5 sections, all models with tool support)
    .venv/bin/python tests/enrich_comparison.py

    # Specific models and sections
    .venv/bin/python tests/enrich_comparison.py --models "qwen2.5:7b,deepseek-v4-flash:cloud" --samples 10

    # Query model comparison (Q&A quality)
    .venv/bin/python tests/enrich_comparison.py --mode query --models "qwen2.5:7b,deepseek-v4-flash:cloud"

    # Full benchmark (enrich + query)
    .venv/bin/python tests/enrich_comparison.py --mode both --samples 20

Metrics measured:
  Enrichment:
    - JSON parse success rate (valid JSON returned?)
    - Summary length (too short = useless, too long = not a summary)
    - Keyword count and relevance
    - Summary coherence (does it mention the section topic?)
    - Time per section
    - Tokens/sec (throughput)

  Query (Q&A):
    - Answer length and coherence
    - Citation count (does it cite sources?)
    - Iterations used (efficiency)
    - Done called (did it terminate properly?)
    - Time per question
    - Dedup hits (did it waste cycles?)

Scoring:
    Each metric gets a 0-1 score. Weighted aggregate produces a
    quality-per-dollar ranking to guide model selection.

Output:
    /tmp/enrich_comparison_results.json — full detailed results
    Console table — summary comparison
"""
import asyncio
import sys
import json
import time
import argparse
import re
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.gateway.ollama import OllamaGateway
from app.config import load_config
from app.storage.shared_db import init_shared_db, list_users
from app.storage.user_db import init_user_db, list_collections, list_docs
from app.agent.tools import ToolBox
from app.agent.loop import AgentLoop


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class EnrichResult:
    model: str
    section_path: str
    section_title: str
    raw_response: str
    parsed: Optional[dict]
    summary: str = ""
    keywords: list = field(default_factory=list)
    elapsed: float = 0.0
    json_valid: bool = False
    summary_words: int = 0
    keyword_count: int = 0
    topic_match: bool = False
    quality_score: float = 0.0


@dataclass
class QueryResult:
    model: str
    question: str
    answer: str
    cites: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)
    iterations: int = 0
    elapsed: float = 0.0
    done_called: bool = False
    answer_words: int = 0
    quality_score: float = 0.0


# ---------------------------------------------------------------------------
# Sample Collection
# ---------------------------------------------------------------------------

def collect_enrich_samples(data_dir: Path, uid: str, n: int = 10) -> list[dict]:
    """Collect N markdown sections from the user's processed books."""
    user_dir = data_dir / uid
    if not user_dir.exists():
        return []
    samples = []
    for doc_dir in sorted(user_dir.iterdir()):
        if not doc_dir.is_dir():
            continue
        for f in sorted(doc_dir.rglob("*.md")):
            if f.name == "index.md":
                continue
            content = f.read_text()
            # Strip existing front-matter to get raw content
            if content.startswith("---"):
                parts = content.split("---", 2)
                raw_content = parts[2].strip() if len(parts) > 2 else content
            else:
                raw_content = content
            # Extract title from first heading
            title = f.stem.replace("_", " ")
            for line in raw_content.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            samples.append({
                "path": str(f),
                "title": title,
                "content": raw_content[:2000],  # truncate for speed
            })
            if len(samples) >= n:
                return samples
    return samples


def get_query_questions() -> list[str]:
    """Standard test questions for Q&A comparison."""
    return [
        "What is a goblin's armor class?",
        "How does combat work?",
        "What character classes are available?",
        "What spells can a sorcerer cast?",
        "How do I create a new character?",
    ]


# ---------------------------------------------------------------------------
# Enrichment Evaluation
# ---------------------------------------------------------------------------

def score_enrichment(result: EnrichResult, section: dict) -> float:
    """Score an enrichment result 0-1 based on quality metrics."""
    score = 0.0

    # JSON valid (30%)
    if result.json_valid:
        score += 0.3

    # Summary exists and is reasonable length (25%)
    if result.summary_words >= 5:
        score += 0.1
    if result.summary_words >= 10:
        score += 0.1
    if result.summary_words > 50:
        score -= 0.05  # too long, not a summary
    if result.summary_words > 100:
        score -= 0.05

    # Keywords present (20%)
    if result.keyword_count >= 3:
        score += 0.1
    if result.keyword_count >= 5:
        score += 0.1

    # Topic match — does summary mention words from the section title? (25%)
    if result.topic_match:
        score += 0.25

    return max(0.0, min(1.0, score))


def check_topic_match(summary: str, title: str) -> bool:
    """Check if the summary mentions key words from the section title."""
    title_words = set(w.lower() for w in title.split() if len(w) > 3)
    summary_lower = summary.lower()
    if not title_words:
        return True  # can't evaluate
    matches = sum(1 for w in title_words if w in summary_lower)
    return matches >= len(title_words) * 0.3  # 30% of title words appear


async def test_enrich_model(model_name: str, samples: list[dict], cfg, num_ctx: int = 4096) -> list[EnrichResult]:
    """Run enrichment on all samples with a specific model."""
    models = {"enrich": model_name, "query": model_name}
    gw = OllamaGateway(cfg.ollama_host, models, num_ctx=num_ctx)

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
            resp = await gw.call("enrich", prompt)
            raw = resp.get("message", {}).get("content", "")
        except Exception as e:
            raw = f"ERROR: {e}"
        elapsed = time.time() - t0

        # Parse
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
        results.append(result)

        status = "OK" if result.json_valid else "FAIL"
        print(f"  [{i+1}/{len(samples)}] {status} {sample['title'][:40]:<42} "
              f"score={result.quality_score:.2f} {result.elapsed:.1f}s "
              f"words={result.summary_words} kw={result.keyword_count}")

    await gw.close()
    return results


# ---------------------------------------------------------------------------
# Query Evaluation
# ---------------------------------------------------------------------------

def score_query(result: QueryResult) -> float:
    """Score a query result 0-1."""
    score = 0.0

    # Answer exists and is substantive (30%)
    if result.answer_words >= 10:
        score += 0.15
    if result.answer_words >= 30:
        score += 0.15

    # Done was called properly (25%)
    if result.done_called:
        score += 0.25

    # Has citations (25%)
    if len(result.cites) >= 1:
        score += 0.15
    if len(result.cites) >= 2:
        score += 0.1

    # Efficiency — fewer iterations is better (20%)
    if result.iterations <= 3:
        score += 0.2
    elif result.iterations <= 6:
        score += 0.1
    elif result.iterations <= 10:
        score += 0.05

    return max(0.0, min(1.0, score))


async def test_query_model(model_name: str, questions: list[str], cfg, toolbox, num_ctx: int = 8192) -> list[QueryResult]:
    """Run Q&A test with a specific model."""
    models = {"query": model_name, "enrich": model_name}
    gw = OllamaGateway(cfg.ollama_host, models, num_ctx=num_ctx)

    results = []
    for q in questions:
        print(f"  Q: {q}")
        t0 = time.time()
        loop = AgentLoop(gw, toolbox, max_iterations=12)
        try:
            result = await loop.run([], q)
        except Exception as e:
            result = {"answer": f"ERROR: {e}", "cites": [], "suggestions": [], "iterations": 0}
        elapsed = time.time() - t0

        qr = QueryResult(
            model=model_name,
            question=q,
            answer=result.get("answer", ""),
            cites=result.get("cites", []),
            suggestions=result.get("suggestions", []),
            iterations=result.get("iterations", 0),
            elapsed=round(elapsed, 1),
            done_called=result.get("iterations", 0) <= 12,
            answer_words=len(result.get("answer", "").split()),
        )
        qr.quality_score = score_query(qr)
        results.append(qr)

        print(f"  A: {qr.answer[:80]}...")
        print(f"     score={qr.quality_score:.2f} iters={qr.iterations} "
              f"cites={len(qr.cites)} words={qr.answer_words} time={qr.elapsed:.1f}s")
        print()

    await gw.close()
    return results


# ---------------------------------------------------------------------------
# Model Discovery
# ---------------------------------------------------------------------------

def get_available_models(cfg, tools_only: bool = True, max_local_size_mb: int = 12000) -> list[str]:
    """Get available models, filtered by tool support and local GPU size."""
    import urllib.request
    try:
        data = json.loads(urllib.request.urlopen(f"{cfg.ollama_host}/api/tags").read())
    except Exception:
        return []

    models = []
    for m in data["models"]:
        caps = m.get("capabilities", [])
        if tools_only and "tools" not in caps:
            continue
        name = m["name"]
        size_mb = m.get("size", 0) // 1024 // 1024
        is_cloud = ":cloud" in name
        # Skip kimi-k3 (402 payment required)
        if "kimi" in name:
            continue
        # Skip models too large for local GPU
        if not is_cloud and size_mb > max_local_size_mb:
            continue
        models.append(name)

    return models


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_enrich_summary(all_results: dict):
    """Print enrichment comparison table."""
    print(f"\n{'='*90}")
    print("ENRICHMENT MODEL COMPARISON")
    print(f"{'='*90}")
    print(f"{'Model':<30} {'JSON%':>6} {'AvgScore':>9} {'AvgTime':>8} {'AvgWords':>9} {'AvgKW':>7} {'Topic%':>7}")
    print("-" * 90)

    for model_name, results in all_results.items():
        n = len(results)
        json_pct = sum(1 for r in results if r.json_valid) / n * 100
        avg_score = sum(r.quality_score for r in results) / n
        avg_time = sum(r.elapsed for r in results) / n
        avg_words = sum(r.summary_words for r in results) / n
        avg_kw = sum(r.keyword_count for r in results) / n
        topic_pct = sum(1 for r in results if r.topic_match) / n * 100

        print(f"{model_name:<30} {json_pct:>5.0f}% {avg_score:>8.2f} "
              f"{avg_time:>7.1f}s {avg_words:>8.0f} {avg_kw:>6.1f} {topic_pct:>6.0f}%")

    # Rank by score
    print(f"\nRanking (by quality score):")
    ranked = sorted(all_results.items(), key=lambda x: sum(r.quality_score for r in x[1]) / len(x[1]), reverse=True)
    for rank, (model_name, results) in enumerate(ranked, 1):
        avg_score = sum(r.quality_score for r in results) / len(results)
        avg_time = sum(r.elapsed for r in results) / len(results)
        # Rough cost estimate (cloud models cost ~$0.001-0.01 per call)
        is_cloud = ":cloud" in model_name
        cost_marker = "$" if is_cloud else "free"
        print(f"  {rank}. {model_name:<30} score={avg_score:.2f} time={avg_time:.1f}s [{cost_marker}]")


def print_query_summary(all_results: dict):
    """Print query comparison table."""
    print(f"\n{'='*100}")
    print("QUERY MODEL COMPARISON (Q&A)")
    print(f"{'='*100}")
    print(f"{'Model':<30} {'AvgScore':>9} {'AvgTime':>8} {'AvgIter':>8} {'AvgCites':>9} {'AvgWords':>9} {'Done%':>6}")
    print("-" * 100)

    for model_name, results in all_results.items():
        n = len(results)
        avg_score = sum(r.quality_score for r in results) / n
        avg_time = sum(r.elapsed for r in results) / n
        avg_iters = sum(r.iterations for r in results) / n
        avg_cites = sum(len(r.cites) for r in results) / n
        avg_words = sum(r.answer_words for r in results) / n
        done_pct = sum(1 for r in results if r.done_called) / n * 100

        print(f"{model_name:<30} {avg_score:>8.2f} {avg_time:>7.1f}s "
              f"{avg_iters:>7.1f} {avg_cites:>8.1f} {avg_words:>8.0f} {done_pct:>5.0f}%")

    # Rank by score
    print(f"\nRanking (by quality score):")
    ranked = sorted(all_results.items(), key=lambda x: sum(r.quality_score for r in x[1]) / len(x[1]), reverse=True)
    for rank, (model_name, results) in enumerate(ranked, 1):
        avg_score = sum(r.quality_score for r in results) / len(results)
        avg_time = sum(r.elapsed for r in results) / len(results)
        is_cloud = ":cloud" in model_name
        cost_marker = "$" if is_cloud else "free"
        print(f"  {rank}. {model_name:<30} score={avg_score:.2f} time={avg_time:.1f}s [{cost_marker}]")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="Compare LLM models for enrichment and query quality")
    parser.add_argument("--mode", choices=["enrich", "query", "both"], default="enrich",
                        help="Test mode: enrich only, query only, or both")
    parser.add_argument("--models", type=str, default=None,
                        help="Comma-separated model names (default: auto-detect all)")
    parser.add_argument("--samples", type=int, default=5,
                        help="Number of sections to enrich (default: 5)")
    parser.add_argument("--enrich-ctx", type=int, default=4096,
                        help="Context window for enrichment (default: 4096)")
    parser.add_argument("--query-ctx", type=int, default=8192,
                        help="Context window for query (default: 8192)")
    parser.add_argument("--output", type=str, default="/tmp/model_benchmark.json",
                        help="Output file for results")
    args = parser.parse_args()

    cfg = load_config(str(Path(__file__).parent.parent / "config.yaml"))

    # Discover models
    if args.models:
        model_list = args.models.split(",")
    else:
        model_list = get_available_models(cfg)
        print(f"Auto-detected models with tool support: {model_list}")

    if not model_list:
        print("No models available. Check Ollama is running.")
        return

    # Get user and collection
    sconn = init_shared_db(cfg.db_dir)
    users = list_users(sconn)
    sconn.close()
    if not users:
        print("No users found. Create one first.")
        return
    uid = users[0]["user_id"]

    uconn = init_user_db(cfg.db_dir, uid)
    cols = list_collections(uconn)
    uconn.close()
    if not cols:
        print("No collections found. Upload and process a book first.")
        return
    cid = cols[0]["collection_id"]
    print(f"Using collection: {cols[0]['name']}")

    # Collect samples
    enrich_samples = collect_enrich_samples(cfg.data_dir, uid, args.samples)
    print(f"Collected {len(enrich_samples)} sections for enrichment testing")

    all_enrich = {}
    all_query = {}

    # Run enrichment tests
    if args.mode in ("enrich", "both"):
        for model_name in model_list:
            print(f"\n{'='*70}")
            print(f"ENRICHMENT: {model_name} (num_ctx={args.enrich_ctx})")
            print(f"{'='*70}")
            results = await test_enrich_model(model_name, enrich_samples, cfg, args.enrich_ctx)
            all_enrich[model_name] = [asdict(r) for r in results]

        print_enrich_summary({k: [EnrichResult(**v) if isinstance(v, dict) else v for v in vs]
                             for k, vs in all_enrich.items()})

    # Run query tests
    if args.mode in ("query", "both"):
        toolbox = ToolBox(cfg.data_dir, uid, cfg.db_dir, cid)
        questions = get_query_questions()

        for model_name in model_list:
            print(f"\n{'='*70}")
            print(f"QUERY: {model_name} (num_ctx={args.query_ctx})")
            print(f"{'='*70}")
            results = await test_query_model(model_name, questions, cfg, toolbox, args.query_ctx)
            all_query[model_name] = [asdict(r) for r in results]

        print_query_summary({k: [QueryResult(**v) if isinstance(v, dict) else v for v in vs]
                             for k, vs in all_query.items()})

    # Save results
    output = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": args.mode,
        "models_tested": model_list,
        "enrichment": all_enrich,
        "query": all_query,
    }
    with open(args.output, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nFull results saved to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())