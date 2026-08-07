"""Worker job-claim lease tests — crashed jobs must be reclaimed."""

import pytest
from app.storage.shared_db import init_shared_db, enqueue_job, claim_next_job, complete_job, get_job


def _enqueue(conn, user="alice", doc="d1", path="/x.pdf"):
    return enqueue_job(conn, user, doc, path)


def test_claim_sets_processing_and_lease(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    jid = _enqueue(conn)
    job = claim_next_job(conn)
    assert job is not None
    assert job["status"] == "processing"
    assert job["lease_expires_at"] is not None
    assert job["attempts"] == 1
    conn.close()


def test_claim_increments_attempts(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    jid = _enqueue(conn)
    claim_next_job(conn)
    # Simulate crash: lease expires, job is reclaimed
    conn.execute("UPDATE queue_jobs SET lease_expires_at = '2000-01-01' WHERE job_id = ?", (jid,))
    conn.commit()
    job = claim_next_job(conn)
    assert job is not None
    assert job["attempts"] == 2
    conn.close()


def test_claim_reclaims_expired_lease(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    jid = _enqueue(conn)
    claim_next_job(conn)
    # Simulate crash: lease expires
    conn.execute("UPDATE queue_jobs SET lease_expires_at = '2000-01-01' WHERE job_id = ?", (jid,))
    conn.commit()
    job = claim_next_job(conn)
    assert job is not None
    assert job["job_id"] == jid
    assert job["status"] == "processing"
    conn.close()


def test_claim_skips_active_lease(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    jid = _enqueue(conn)
    claim_next_job(conn)
    # Lease still active — must NOT be reclaimed
    job = claim_next_job(conn)
    assert job is None
    conn.close()


def test_claim_gives_up_after_max_attempts(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    jid = _enqueue(conn)
    for _ in range(3):
        claim_next_job(conn)
        conn.execute("UPDATE queue_jobs SET lease_expires_at = '2000-01-01' WHERE job_id = ?", (jid,))
        conn.commit()
    # 4th claim: attempts already at max — must not be reclaimed
    job = claim_next_job(conn)
    assert job is None
    conn.close()


def test_heartbeat_refreshes_lease(tmp_dirs):
    import time
    conn = init_shared_db(tmp_dirs["db"])
    jid = _enqueue(conn)
    job = claim_next_job(conn)
    old_lease = job["lease_expires_at"]
    time.sleep(1.1)
    from app.storage.shared_db import heartbeat_job
    heartbeat_job(conn, jid)
    refreshed = get_job(conn, jid)
    assert refreshed["lease_expires_at"] > old_lease
    conn.close()


def test_complete_job_clears_lease(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    jid = _enqueue(conn)
    claim_next_job(conn)
    complete_job(conn, jid)
    job = get_job(conn, jid)
    assert job["status"] == "done"
    assert job["lease_expires_at"] is None
    conn.close()
