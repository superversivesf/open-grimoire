import asyncio
from pathlib import Path
from app.pipeline.extract import Extractor
from app.pipeline.structure import Structurer
from app.pipeline.tier import tier_document
from app.pipeline.enrich import Enricher
from app.pipeline.index import index_document
from app.storage.user_db import init_user_db, update_doc_status
from app.storage.shared_db import init_shared_db, complete_job
from app.storage.paths import user_data_dir


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
        conn = init_shared_db(self.db_dir)
        uconn = init_user_db(self.db_dir, user_id)
        try:
            update_doc_status(uconn, doc_id, "extracting")
            extractor = Extractor(self.gateway)
            blocks = extractor.extract(pdf_path)
            if not blocks or not any(b["text"].strip() for b in blocks):
                raise ValueError("no text extracted from PDF")

            update_doc_status(uconn, doc_id, "structuring")
            structurer = Structurer(self.gateway)
            tree = structurer.detect(blocks)

            update_doc_status(uconn, doc_id, "tiering")
            udata = user_data_dir(self.data_dir, user_id)
            leaf_paths = tier_document(tree, udata, doc_id, self._doc_title(uconn, doc_id))

            update_doc_status(uconn, doc_id, "enriching")
            if self.gateway is not None:
                enricher = Enricher(self.gateway)
                full_paths = [udata / p for p in leaf_paths]
                page_map = {}
                await enricher.enrich_all(full_paths, page_map)

            update_doc_status(uconn, doc_id, "indexing")
            index_document(uconn, leaf_paths, udata, doc_id)

            update_doc_status(uconn, doc_id, "done")
            complete_job(conn, job_id)
        except Exception as e:
            update_doc_status(uconn, doc_id, "failed")
            complete_job(conn, job_id, error=str(e))
        finally:
            uconn.close()
            conn.close()

    def _doc_title(self, uconn, doc_id: str) -> str:
        from app.storage.user_db import get_doc
        d = get_doc(uconn, doc_id)
        return d["title"] if d else doc_id