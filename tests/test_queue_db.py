from app.storage.shared_db import (
    init_shared_db, enqueue_job, claim_next_job, complete_job, get_job, list_jobs_by_user,
)


def test_queue_table_created(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    assert "queue_jobs" in {r[0] for r in cur.fetchall()}
    conn.close()


def test_enqueue_and_claim(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    jid = enqueue_job(conn, "alice", "d1", "/tmp/x.pdf")
    job = claim_next_job(conn)
    assert job["job_id"] == jid
    assert job["status"] == "processing"
    assert job["user_id"] == "alice"
    conn.close()


def test_claim_empty_returns_none(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    assert claim_next_job(conn) is None
    conn.close()


def test_complete_job_done(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    jid = enqueue_job(conn, "alice", "d1", "/tmp/x.pdf")
    claim_next_job(conn)
    complete_job(conn, jid)
    job = get_job(conn, jid)
    assert job["status"] == "done"
    conn.close()


def test_complete_job_failed_with_error(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    jid = enqueue_job(conn, "alice", "d1", "/tmp/x.pdf")
    claim_next_job(conn)
    complete_job(conn, jid, error="OCR failed")
    job = get_job(conn, jid)
    assert job["status"] == "failed"
    assert job["error"] == "OCR failed"
    conn.close()


def test_claim_is_atomic_fifo(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    j1 = enqueue_job(conn, "alice", "d1", "/x.pdf")
    j2 = enqueue_job(conn, "bob", "d2", "/y.pdf")
    job = claim_next_job(conn)
    assert job["job_id"] == j1
    job2 = claim_next_job(conn)
    assert job2["job_id"] == j2
    conn.close()


def test_list_jobs_by_user(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    enqueue_job(conn, "alice", "d1", "/x.pdf")
    enqueue_job(conn, "alice", "d2", "/y.pdf")
    enqueue_job(conn, "bob", "d3", "/z.pdf")
    jobs = list_jobs_by_user(conn, "alice")
    assert len(jobs) == 2
    conn.close()