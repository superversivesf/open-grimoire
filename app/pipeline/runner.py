import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Any
from app.pipeline.extract import Extractor
from app.pipeline.structure import Structurer
from app.pipeline.tier import tier_document
from app.pipeline.enrich import Enricher
from app.pipeline.index import index_document
from app.storage.user_db import init_user_db, update_doc_status, update_enrich_progress, get_enrich_completed_paths, add_enrich_completed_path
from app.storage.shared_db import init_shared_db, complete_job, log_enrichment, register_shared_book, link_user_book, find_shared_book, find_existing_user_for_book, unlink_user_book
from app.storage.paths import user_data_dir
from app.pipeline.content_hash import content_hash
from app.usage.tokens import estimate_tokens
from app.logging_utils import get_logger
from app.constants import ENRICH_SUMMARY_MAX_CHARS, ENRICH_EST_OUTPUT_TOKENS_PER_SECTION

log = get_logger("pipeline")


class PipelineRunner:
    def __init__(self, gateway: Any, data_dir: Path, db_dir: Path) -> None:
        self.gateway = gateway
        self.data_dir = data_dir
        self.db_dir = db_dir

    async def run_job(self, job: dict[str, Any]) -> None:
        job_id = job["job_id"]
        user_id = job["user_id"]
        doc_id = job["doc_id"]
        pdf_path = Path(job["pdf_path"])
        log.info(f"JOB {job_id[:8]} START doc={doc_id[:8]} user={user_id[:8]} pdf={pdf_path.name}")
        conn = init_shared_db(self.db_dir)
        uconn = init_user_db(self.db_dir, user_id)
        udata = user_data_dir(self.data_dir, user_id)
        doc_dir = udata / doc_id
        try:
            # Stage 1: Extract
            t0 = time.time()
            update_doc_status(uconn, doc_id, "extracting")
            log.info(f"JOB {job_id[:8]} STAGE 1: extracting text from {pdf_path.name}")
            extractor = Extractor(self.gateway)
            # Sync subprocess calls (poppler/tesseract) — offload so the
            # worker's loop stays free to fire heartbeats during long jobs.
            blocks = await asyncio.to_thread(extractor.extract, pdf_path)
            total_pages = len(blocks)
            ocr_pages = sum(1 for b in blocks if b.get("ocr"))
            text_chars = sum(len(b["text"]) for b in blocks)
            log.info(f"JOB {job_id[:8]} STAGE 1 DONE: {total_pages} pages, {ocr_pages} OCR, {text_chars} chars, {time.time()-t0:.1f}s")
            if not blocks or not any(b["text"].strip() for b in blocks):
                raise ValueError("no text extracted from PDF")

            # Compute content hash for book sharing
            full_text = "\n".join(b["text"] for b in blocks)
            chash = content_hash(full_text)

            # Check if this book already exists in the shared registry
            existing = find_shared_book(conn, chash)
            if existing:
                # Find a user who already has this book processed
                existing_user = find_existing_user_for_book(conn, chash)
                if existing_user and existing_user["user_id"] != user_id:
                    log.info(f"JOB {job_id[:8]} SHARED BOOK: content hash matches existing book from user {existing_user['user_id'][:8]}")
                    # Copy the existing book's processed data instead of reprocessing
                    source_dir = self.data_dir / existing_user["user_id"] / existing_user["doc_id"]
                    if source_dir.exists():
                        import shutil
                        if doc_dir.exists():
                            shutil.rmtree(doc_dir)
                        shutil.copytree(source_dir, doc_dir)
                        # Copy FTS rows and doc metadata from source user's DB
                        source_uconn = init_user_db(self.db_dir, existing_user["user_id"])
                        fts_rows = source_uconn.execute(
                            "SELECT path, title, summary, keywords, content FROM documents_fts WHERE path LIKE ?",
                            (f"{existing_user['doc_id']}/%",),
                        ).fetchall()
                        source_doc = source_uconn.execute(
                            "SELECT enrich_completed_paths, page_count FROM docs WHERE doc_id = ?",
                            (existing_user["doc_id"],),
                        ).fetchone()
                        source_uconn.close()
                        for row in fts_rows:
                            old_path = row["path"]
                            suffix = old_path.split("/", 1)[1] if "/" in old_path else old_path
                            new_path = f"{doc_id}/{suffix}"
                            uconn.execute(
                                "INSERT INTO documents_fts (path, title, summary, keywords, content) VALUES (?, ?, ?, ?, ?)",
                                (new_path, row["title"], row["summary"], row["keywords"], row["content"]),
                            )
                        uconn.commit()

                        # Copy enrich_completed_paths and doc status from source
                        if source_doc:
                            uconn.execute(
                                "UPDATE docs SET enrich_completed_paths = ?, page_count = ? WHERE doc_id = ?",
                                (source_doc["enrich_completed_paths"], source_doc["page_count"], doc_id),
                            )
                            uconn.commit()

                        # Register and link
                        link_user_book(conn, str(user_id), str(doc_id), chash, "")
                        update_doc_status(uconn, doc_id, "done")
                        complete_job(conn, job_id)
                        log.info(f"JOB {job_id[:8]} SHARED COMPLETE: copied {len(fts_rows)} FTS rows from existing book")
                        return

            # Extract cover image (first page as JPG)
            try:
                await self._extract_cover_async(pdf_path, doc_dir)
                log.info(f"JOB {job_id[:8]} COVER: extracted cover.jpg")
            except Exception as e:
                log.warning(f"JOB {job_id[:8]} COVER: failed to extract cover: {e}")

            # Stage 2: Structure
            t0 = time.time()
            update_doc_status(uconn, doc_id, "structuring")
            log.info(f"JOB {job_id[:8]} STAGE 2: detecting structure")
            structurer = Structurer()
            tree = structurer.detect(blocks)
            chapters = len(tree)
            total_nodes, leaf_count = structurer.counts(tree)
            log.info(f"JOB {job_id[:8]} STAGE 2 DONE: {chapters} chapters, {total_nodes} nodes, {leaf_count} leaves, {time.time()-t0:.1f}s")

            # Stage 3: Tier
            t0 = time.time()
            update_doc_status(uconn, doc_id, "tiering")
            log.info(f"JOB {job_id[:8]} STAGE 3: writing markdown files")
            doc_title = self._doc_title(uconn, doc_id)
            # Sync file writes — offload so the loop stays free for heartbeats.
            leaf_paths = await asyncio.to_thread(tier_document, tree, udata, doc_id, doc_title)
            log.info(f"JOB {job_id[:8]} STAGE 3 DONE: {len(leaf_paths)} files written to data/{user_id[:8]}/{doc_id[:8]}/, {time.time()-t0:.1f}s")

            # Stage 4: Enrich
            t0 = time.time()
            update_doc_status(uconn, doc_id, "enriching")
            if self.gateway is not None:
                # Checkpoint: get already enriched paths from the DB. If the
                # DB list is empty but files on disk have frontmatter (job
                # was interrupted mid-gather before the DB was updated),
                # recover by scanning the disk.
                completed_paths = set(get_enrich_completed_paths(uconn, doc_id))
                if not completed_paths:
                    completed_paths = {
                        p for p in leaf_paths
                        if (udata / p).exists() and (udata / p).read_text().startswith("---\n")
                    }
                    if completed_paths:
                        log.info(f"JOB {job_id[:8]} STAGE 4: recovered {len(completed_paths)} enriched files from disk (DB checkpoint was empty)")
                remaining_paths = [p for p in leaf_paths if p not in completed_paths]
                log.info(f"JOB {job_id[:8]} STAGE 4: enriching {len(leaf_paths)} sections ({len(completed_paths)} already done, {len(remaining_paths)} remaining)")
                update_enrich_progress(uconn, doc_id, len(completed_paths), len(leaf_paths))
                enricher = Enricher(self.gateway)
                full_paths = [udata / p for p in remaining_paths]
                page_map = self._build_page_map(tree, udata, leaf_paths)
                enriched = len(completed_paths)
                sem = asyncio.Semaphore(2)

                async def _enrich_one(p: Path, rel_path: str, page: int | None) -> tuple[str, Path, bool, dict[str, Any]]:
                    async with sem:
                        r = await enricher.enrich_leaf(p, page)
                        return rel_path, p, True, r

                tasks = [asyncio.ensure_future(_enrich_one(p, remaining_paths[i], page_map.get(str(p))))
                         for i, p in enumerate(full_paths)]

                for coro in asyncio.as_completed(tasks):
                    try:
                        rel_path, full_path, ok, r = await coro
                    except Exception as exc:
                        log.warning(f"JOB {job_id[:8]} ENRICH FAILED: -> {exc}")
                        update_enrich_progress(uconn, doc_id, enriched, len(leaf_paths))
                        continue
                    if ok:
                        summary = r.get("summary", "")
                        if not isinstance(summary, str):
                            summary = str(summary)
                        summary = summary[:ENRICH_SUMMARY_MAX_CHARS]
                        keywords = r.get("keywords", [])
                        if not keywords:
                            log.warning(f"JOB {job_id[:8]} ENRICH SKIPPED (no keywords): {full_path.name}")
                            update_enrich_progress(uconn, doc_id, enriched, len(leaf_paths))
                            continue
                        enriched += 1
                        add_enrich_completed_path(uconn, doc_id, rel_path)
                        log.debug(f"JOB {job_id[:8]} ENRICH {enriched}/{len(leaf_paths)}: {full_path.name} -> summary=\"{summary}\" keywords={keywords}")
                    update_enrich_progress(uconn, doc_id, enriched, len(leaf_paths))
                log.info(f"JOB {job_id[:8]} STAGE 4 DONE: {enriched}/{len(leaf_paths)} sections enriched, {time.time()-t0:.1f}s")

                enrich_model = str(getattr(self.gateway, "models", {}).get("enrich", "unknown") or "unknown")
                enrich_elapsed = time.time() - t0
                est_input = sum(estimate_tokens(p.read_text()[:2000]) for p in full_paths if p.exists())
                est_output = (enriched - len(completed_paths)) * ENRICH_EST_OUTPUT_TOKENS_PER_SECTION
                log_enrichment(conn, user_id, doc_id, enrich_model,
                                len(leaf_paths), enriched - len(completed_paths), est_input, est_output, enrich_elapsed)
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

            # Register book in shared registry
            register_shared_book(conn, chash, str(doc_title), int(total_pages))
            link_user_book(conn, str(user_id), str(doc_id), chash, "")
        except Exception as e:
            update_doc_status(uconn, doc_id, "failed")
            complete_job(conn, job_id, error=str(e))
            log.error(f"JOB {job_id[:8]} FAILED: doc={doc_id[:8]} error={e}")
        finally:
            uconn.close()
            conn.close()

    def _build_page_map(self, tree: list[dict[str, Any]], udata: Path, leaf_paths: list[str]) -> dict[str, int | None]:
        pages: list[int | None] = []
        node_count = 0
        leaf_count = 0

        def walk(nodes: list[dict[str, Any]]) -> None:
            nonlocal node_count, leaf_count
            for node in nodes:
                node_count += 1
                if node["children"]:
                    walk(node["children"])
                else:
                    leaf_count += 1
                    pages.append(node.get("page_start"))

        walk(tree)
        if len(leaf_paths) != len(pages):
            log.warning(f"_build_page_map: leaf_paths count ({len(leaf_paths)}) != tree leaves ({len(pages)})")
        return {str(udata / p): page for p, page in zip(leaf_paths, pages)}

    def _doc_title(self, uconn: sqlite3.Connection, doc_id: str) -> str:
        from app.storage.user_db import get_doc
        d = get_doc(uconn, doc_id)
        return d["title"] if d else doc_id

    def _extract_cover(self, pdf_path: Path, doc_dir: Path) -> None:
        """Extract first page of PDF as cover.jpg."""
        from pdf2image import convert_from_path
        images = convert_from_path(str(pdf_path), first_page=1, last_page=1, dpi=150)
        if images:
            cover_path = doc_dir / "cover.jpg"
            images[0].save(str(cover_path), "JPEG", quality=85, optimize=True)

    async def _extract_cover_async(self, pdf_path: Path, doc_dir: Path) -> None:
        await asyncio.to_thread(self._extract_cover, pdf_path, doc_dir)