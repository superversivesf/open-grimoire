import asyncio
import time
from pathlib import Path
from app.pipeline.extract import Extractor
from app.pipeline.structure import Structurer
from app.pipeline.tier import tier_document
from app.pipeline.enrich import Enricher
from app.pipeline.index import index_document
from app.storage.user_db import init_user_db, update_doc_status, update_enrich_progress
from app.storage.shared_db import init_shared_db, complete_job
from app.storage.paths import user_data_dir
from app.logging_utils import get_logger

log = get_logger("pipeline")


class PipelineRunner:
    def __init__(self, gateway, data_dir: Path, db_dir: Path):
        self.gateway = gateway
        self.data_dir = data_dir
        self.db_dir = db_dir

    async def run_job(self, job: dict) -> None:
        job_id = job["job_id"]
        user_id = job["user_id"]
        doc_id = job["doc_id"]
        pdf_path = Path(job["pdf_path"])
        log.info(f"JOB {job_id[:8]} START doc={doc_id[:8]} user={user_id[:8]} pdf={pdf_path.name}")
        conn = init_shared_db(self.db_dir)
        uconn = init_user_db(self.db_dir, user_id)
        try:
            # Stage 1: Extract
            t0 = time.time()
            update_doc_status(uconn, doc_id, "extracting")
            log.info(f"JOB {job_id[:8]} STAGE 1: extracting text from {pdf_path.name}")
            extractor = Extractor(self.gateway)
            blocks = extractor.extract(pdf_path)
            total_pages = len(blocks)
            ocr_pages = sum(1 for b in blocks if b.get("ocr"))
            text_chars = sum(len(b["text"]) for b in blocks)
            log.info(f"JOB {job_id[:8]} STAGE 1 DONE: {total_pages} pages, {ocr_pages} OCR, {text_chars} chars, {time.time()-t0:.1f}s")
            if not blocks or not any(b["text"].strip() for b in blocks):
                raise ValueError("no text extracted from PDF")

            # Stage 2: Structure
            t0 = time.time()
            update_doc_status(uconn, doc_id, "structuring")
            log.info(f"JOB {job_id[:8]} STAGE 2: detecting structure")
            structurer = Structurer(self.gateway)
            tree = structurer.detect(blocks)
            def count_nodes(nodes):
                total = 0
                for n in nodes:
                    total += 1
                    if n.get("children"):
                        total += count_nodes(n["children"])
                return total
            def count_leaves(nodes):
                total = 0
                for n in nodes:
                    if n.get("children"):
                        total += count_leaves(n["children"])
                    else:
                        total += 1
                return total
            chapters = len(tree)
            total_nodes = count_nodes(tree)
            leaf_count = count_leaves(tree)
            log.info(f"JOB {job_id[:8]} STAGE 2 DONE: {chapters} chapters, {total_nodes} nodes, {leaf_count} leaves, {time.time()-t0:.1f}s")

            # Stage 3: Tier
            t0 = time.time()
            update_doc_status(uconn, doc_id, "tiering")
            log.info(f"JOB {job_id[:8]} STAGE 3: writing markdown files")
            udata = user_data_dir(self.data_dir, user_id)
            doc_title = self._doc_title(uconn, doc_id)
            leaf_paths = tier_document(tree, udata, doc_id, doc_title)
            log.info(f"JOB {job_id[:8]} STAGE 3 DONE: {len(leaf_paths)} files written to data/{user_id[:8]}/{doc_id[:8]}/, {time.time()-t0:.1f}s")

            # Stage 4: Enrich
            t0 = time.time()
            update_doc_status(uconn, doc_id, "enriching")
            if self.gateway is not None:
                log.info(f"JOB {job_id[:8]} STAGE 4: enriching {len(leaf_paths)} sections")
                update_enrich_progress(uconn, doc_id, 0, len(leaf_paths))
                enricher = Enricher(self.gateway)
                full_paths = [udata / p for p in leaf_paths]
                page_map = self._build_page_map(tree, udata, leaf_paths)
                enriched = 0
                for i, p in enumerate(full_paths):
                    page = page_map.get(str(p))
                    try:
                        r = await enricher.enrich_leaf(p, page)
                        enriched += 1
                        summary = r.get("summary", "")[:60]
                        keywords = r.get("keywords", [])
                        log.debug(f"JOB {job_id[:8]} ENRICH {i+1}/{len(full_paths)}: {p.name} -> summary=\"{summary}\" keywords={keywords}")
                    except Exception as e:
                        log.warning(f"JOB {job_id[:8]} ENRICH {i+1}/{len(full_paths)} FAILED: {p.name} -> {e}")
                    update_enrich_progress(uconn, doc_id, i + 1, len(leaf_paths))
                log.info(f"JOB {job_id[:8]} STAGE 4 DONE: {enriched}/{len(leaf_paths)} sections enriched, {time.time()-t0:.1f}s")
            else:
                log.info(f"JOB {job_id[:8]} STAGE 4 SKIPPED: no gateway")

            # Stage 5: Index
            t0 = time.time()
            update_doc_status(uconn, doc_id, "indexing")
            log.info(f"JOB {job_id[:8]} STAGE 5: building FTS5 index")
            index_document(uconn, leaf_paths, udata, doc_id)
            log.info(f"JOB {job_id[:8]} STAGE 5 DONE: FTS5 index built, {time.time()-t0:.1f}s")

            update_doc_status(uconn, doc_id, "done")
            complete_job(conn, job_id)
            log.info(f"JOB {job_id[:8]} COMPLETE: doc={doc_id[:8]} title=\"{doc_title}\"")
        except Exception as e:
            update_doc_status(uconn, doc_id, "failed")
            complete_job(conn, job_id, error=str(e))
            log.error(f"JOB {job_id[:8]} FAILED: doc={doc_id[:8]} error={e}")
        finally:
            uconn.close()
            conn.close()

    def _build_page_map(self, tree: list[dict], udata, leaf_paths: list[str]) -> dict:
        pages = []
        def walk(nodes):
            for node in nodes:
                if node["children"]:
                    walk(node["children"])
                else:
                    pages.append(node.get("page_start"))
        walk(tree)
        return {str(udata / p): page for p, page in zip(leaf_paths, pages)}

    def _doc_title(self, uconn, doc_id: str) -> str:
        from app.storage.user_db import get_doc
        d = get_doc(uconn, doc_id)
        return d["title"] if d else doc_id