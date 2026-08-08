# Collection Sharing + Self-Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add (A) opt-in collection sharing — a collection can be shared with other users who can view it and add books — and (B) self-registration with admin approval plus admin-page account creation.

**Architecture:** Shared collections are first-class rows in `shared.sqlite` with a `collection_members` table. The collection's docs, FTS index, and files stay in the **owner's** per-user DB and `data/<owner_uid>/` tree — zero duplication, single FTS index. Every collection-scoped route resolves through a new `resolve_collection(db_dir, data_dir, collection_id, uid)` seam that returns `(owner_uid, role)`; membership is checked there, and all reads/writes then operate on the owner's tree. Feature B is a `users.status` column plus register/approve routes — independent and lands first.

**Tech Stack:** FastAPI, SQLite (per-user DBs + shared DB), existing slowapi rate limiter, existing HMAC session tokens, htmx templates, pytest.

**Agent consultation summary (3/3):** B is low effort (~1–2 days) and independent — do it first. A is the hard one (~4–8 days total for A+B): claude recommends owner-DB-holds-everything + resolution seam (no duplication, ~4–5d); codex recommends per-member copies via the existing dedup path (lowest risk, ~3–5d, but duplicates storage); pi estimates 1.5–3 weeks for a union-across-members FTS. **Chosen: claude's owner-DB model** — least storage, single FTS, and the dedup path in `runner.py:57-91` already copies trees between users so member-adds reuse it. All three flagged the same critical security gap: today there is **no ownership check** on collection/doc routes (security-by-obscurity via random UUIDs) — sharing requires explicit membership checks everywhere.

## Global Constraints

- Zero new dependencies (stdlib + existing: slowapi, argon2, hmac).
- CSRF: all new POST routes use `require_csrf` (session-token pattern); `/register` and `/login` use the double-submit `require_login_csrf` pattern (no session yet).
- Rate limit: registration reuses `_rate_key` + slowapi; new `REGISTER_RATE_LIMIT = "5/hour"`.
- Shared migration version: next `SHARED_MIGRATIONS` version is 2 (current is 1). No per-user migration needed for MVP.
- Existing CLI-created users get `status='active'` by default (no data migration).
- Storage quota for shared collections charges the **owner** (MVP tradeoff, documented).
- Sessions stay per-user (history is personal); `sessions.collection_id` is an opaque string (SQLite FKs not enforced) so it already works with shared collection ids.
- `user_books.collection_id` is currently hardcoded `""` by the runner (`runner.py:89`) — record real collection ids for shared adds.

---

### Task 1: Shared DB migration v2 — users.status + collection_members

**Files:**
- Modify: `app/storage/migrations.py:14-91` (SHARED_MIGRATIONS list)
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `collection_members(collection_id, user_id, role, added_at)` table; `users.status` column (`'active'` | `'pending'`); helper `_add_column(conn, table, column, ddl)` (idempotent ALTER, per the earlier migration-abort fix).

- [ ] **Step 1: Write the failing test**

```python
def test_shared_migration_v2_applies(tmp_path):
    conn = sqlite3.connect(tmp_path / "s.sqlite")
    conn.row_factory = sqlite3.Row
    migrate_shared_db(conn)
    assert _get_shared_version(conn) == 2
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    assert "status" in cols
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='collection_members'").fetchone()
    assert row is not None
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_migrations.py -v`
Expected: FAIL — version stays 1

- [ ] **Step 3: Append migration v2 to SHARED_MIGRATIONS**

```python
    (2, "add_user_status_and_collection_members", """
        ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
        CREATE TABLE IF NOT EXISTS collection_members (
            collection_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'member')),
            added_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (collection_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_collection_members_user ON collection_members(user_id);
    """),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_migrations.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/storage/migrations.py tests/test_migrations.py
git commit -m "feat(db): shared migration v2 — users.status + collection_members"
```

---

### Task 2: Shared DB helpers — membership, status, registration

**Files:**
- Modify: `app/storage/shared_db.py`
- Test: `tests/test_shared_db_membership.py` (new)

**Interfaces:**
- Produces:
  - `add_collection_member(conn, collection_id, user_id, role='member') -> None`
  - `remove_collection_member(conn, collection_id, user_id) -> None`
  - `list_collection_members(conn, collection_id) -> list[dict]`
  - `get_membership(conn, collection_id, user_id) -> dict | None`
  - `list_shared_collections_for_user(conn, user_id) -> list[dict]` (collections where user is owner or member, with role)
  - `create_user_with_status(conn, username, password_hash, is_admin=False, status='pending') -> str`
  - `set_user_status(conn, user_id, status) -> None`
  - `list_users_by_status(conn, status) -> list[dict]`
  - `get_user_by_username` — return `status` in the row dict

- [ ] **Step 1: Write the failing tests**

```python
def test_membership_roundtrip(tmp_path):
    conn = init_shared_db(tmp_path)
    add_collection_member(conn, "c1", "alice", "owner")
    add_collection_member(conn, "c1", "bob", "member")
    members = list_collection_members(conn, "c1")
    assert len(members) == 2
    assert get_membership(conn, "c1", "alice")["role"] == "owner"
    remove_collection_member(conn, "c1", "bob")
    assert get_membership(conn, "c1", "bob") is None


def test_list_shared_collections_for_user(tmp_path):
    conn = init_shared_db(tmp_path)
    add_collection_member(conn, "c1", "alice", "owner")
    add_collection_member(conn, "c1", "bob", "member")
    add_collection_member(conn, "c2", "bob", "owner")
    got = list_shared_collections_for_user(conn, "bob")
    assert {g["collection_id"] for g in got} == {"c1", "c2"}
    got = list_shared_collections_for_user(conn, "eve")
    assert got == []


def test_user_status_flow(tmp_path):
    conn = init_shared_db(tmp_path)
    uid = create_user_with_status(conn, "newbie", "hash", status="pending")
    row = get_user_by_username(conn, "newbie")
    assert row["status"] == "pending"
    set_user_status(conn, uid, "active")
    assert get_user_by_username(conn, "newbie")["status"] == "active"
    pending = list_users_by_status(conn, "pending")
    assert len(pending) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_shared_db_membership.py -v`
Expected: FAIL — functions not defined

- [ ] **Step 3: Implement the helpers in `app/storage/shared_db.py`**

Follow the existing function style (module-level `conn.execute` + `conn.commit()`). Membership helpers are plain CRUD on `collection_members`; `list_shared_collections_for_user` joins `collection_members` where `user_id = ?`; status helpers update `users.status`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_shared_db_membership.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/storage/shared_db.py tests/test_shared_db_membership.py
git commit -m "feat(db): membership and user-status helpers"
```

---

### Task 3: Registration routes (GET/POST /register) with rate limit + CSRF

**Files:**
- Modify: `app/auth/routes.py`, `app/auth/middleware.py` (SKIP_AUTH_PATHS), `app/constants.py`, `app/config.py`, `app/web/templates/register.html` (new), `app/web/templates/login.html` (link)
- Test: `tests/test_register.py` (new)

**Interfaces:**
- Consumes: `create_user_with_status`, `require_login_csrf`, `_rate_key`, `_DUMMY_HASH`
- Produces: `GET /register` (mints csrf cookie like login), `POST /register` (username/password → status='pending', generic response), config flag `allow_registration: bool = False` (env `ALLOW_REGISTRATION=1`)

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_register_disabled_by_default(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.get("/register", follow_redirects=False)
        assert r.status_code == 303
        assert "/login" in r.headers["location"]


@pytest.mark.asyncio
async def test_register_flow(app_with_user, monkeypatch):
    monkeypatch.setenv("ALLOW_REGISTRATION", "1")
    app = create_app(app_with_user.state.config, "testsecret")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/register")
        token = client.cookies.get("login_csrf")
        r = await client.post("/register", data={"username": "newbie", "password": "pw123", "_csrf": token})
        assert r.status_code in (200, 303)
        conn = init_shared_db(app.state.config.db_dir)
        row = get_user_by_username(conn, "newbie")
        assert row["status"] == "pending"
        conn.close()


@pytest.mark.asyncio
async def test_pending_user_cannot_login(app_with_user, monkeypatch):
    monkeypatch.setenv("ALLOW_REGISTRATION", "1")
    app = create_app(app_with_user.state.config, "testsecret")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/register")
        token = client.cookies.get("login_csrf")
        await client.post("/register", data={"username": "newbie", "password": "pw123", "_csrf": token})
        await client.get("/login")
        token = client.cookies.get("login_csrf")
        r = await client.post("/login", data={"username": "newbie", "password": "pw123", "_csrf": token})
        assert r.status_code == 401
        assert "pending" in r.text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_register.py -v`
Expected: FAIL — no /register route; pending user logs in fine

- [ ] **Step 3: Implement**

```python
# app/config.py — add field
allow_registration: bool = False

# load_config — parse
allow_registration=os.environ.get("ALLOW_REGISTRATION", "").lower() in ("1", "true", "yes"),

# app/constants.py
REGISTER_RATE_LIMIT = "5/hour"

# app/auth/middleware.py SKIP_AUTH_PATHS — add "/register"

# app/auth/routes.py
@router.get("/register")
async def register_page(request: Request) -> Response:
    cfg = getattr(request.app.state, "config", None)
    if not getattr(cfg, "allow_registration", False):
        return RedirectResponse("/login", status_code=303)
    token = request.cookies.get("login_csrf") or secrets.token_urlsafe(16)
    resp = _templates.TemplateResponse(request, "register.html", {"user_id": None, "csrf_token": token})
    resp.headers["Cache-Control"] = "no-store"
    secure = getattr(request.app.state.config, "cookie_secure", False)
    resp.set_cookie("login_csrf", token, httponly=True, max_age=SESSION_COOKIE_MAX_AGE, samesite="lax", secure=secure)
    return resp


@router.post("/register")
@_get_limiter().limit(REGISTER_RATE_LIMIT)
async def register_submit(request: Request, username: str = Form(...), password: str = Form(...), csrf: str = Form("", alias="_csrf")) -> Response:
    require_login_csrf(request, csrf)
    cfg = getattr(request.app.state, "config", None)
    if not getattr(cfg, "allow_registration", False):
        return RedirectResponse("/login", status_code=303)
    conn = init_shared_db(_db_dir)
    try:
        create_user_with_status(conn, username.strip(), hash_password(password), status="pending")
    except Exception:
        pass  # duplicate username → same generic response (no enumeration)
    finally:
        conn.close()
    return _templates.TemplateResponse(
        request, "register.html",
        {"user_id": None, "csrf_token": request.cookies.get("login_csrf", ""),
         "submitted": True},
    )
```

Login gate (modify `login_submit`): after a valid verify, if `user["status"] == "pending"` return 401 with a "pending approval" message instead of issuing a session.

Register template: same form style as `login.html` with the `_csrf` hidden field; when `submitted` is true, show "Registration submitted — an admin must approve your account."

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_register.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/config.py app/constants.py app/auth/routes.py app/auth/middleware.py app/web/templates/register.html app/web/templates/login.html tests/test_register.py
git commit -m "feat(auth): self-registration with pending status (admin approval required)"
```

---

### Task 4: Admin approval — dashboard section + approve/reject routes + CLI

**Files:**
- Modify: `app/web/admin_routes.py`, `app/web/templates/admin.html`, `app/cli/user.py`, `app/storage/shared_db.py` (already has helpers)
- Test: `tests/test_admin_approval.py` (new)

**Interfaces:**
- Consumes: `list_users_by_status`, `set_user_status`, `get_user_by_username`
- Produces: `POST /admin/users/{user_id}/approve`, `POST /admin/users/{user_id}/reject` (both CSRF-protected via `require_csrf`, admin-only), CLI `python -m app.cli.user approve <username>` / `reject <username>`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_admin_approves_pending_user(app_with_users, tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user_with_status(conn, "newbie", hash_password("pw"), status="pending")
    conn.close()
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        await client.get("/login")
        token = client.cookies.get("login_csrf")
        await client.post("/login", data={"username": "admin", "password": "adminpw", "_csrf": token})
        r = await client.post(f"/admin/users/{uid}/approve", data={"_csrf": csrf_for(client)})
        assert r.status_code in (200, 303)
        conn = init_shared_db(tmp_dirs["db"])
        assert get_user_by_username(conn, "newbie")["status"] == "active"
        conn.close()


@pytest.mark.asyncio
async def test_non_admin_cannot_approve(app_with_users, tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user_with_status(conn, "newbie", hash_password("pw"), status="pending")
    conn.close()
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        await client.get("/login")
        token = client.cookies.get("login_csrf")
        await client.post("/login", data={"username": "alice", "password": "pw123", "_csrf": token})
        r = await client.post(f"/admin/users/{uid}/approve", data={"_csrf": csrf_for(client)})
        assert r.status_code == 303  # redirected away, not approved
        conn = init_shared_db(tmp_dirs["db"])
        assert get_user_by_username(conn, "newbie")["status"] == "pending"
        conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_admin_approval.py -v`
Expected: FAIL — routes don't exist

- [ ] **Step 3: Implement**

Admin dashboard: add a "Pending users" section listing `list_users_by_status(sconn, "pending")` with approve/reject forms (hidden `_csrf`). Routes:

```python
@router.post("/admin/users/{user_id}/approve")
async def approve_user_route(request: Request, user_id: str, _: None = Depends(require_csrf)) -> Response:
    if not _is_admin_request(request):
        return RedirectResponse("/", status_code=303)
    sconn = init_shared_db(_db_dir)
    set_user_status(sconn, user_id, "active")
    sconn.close()
    return RedirectResponse("/admin", status_code=303)
```

Where `_is_admin_request(request)` reuses the existing admin check pattern from `admin_dashboard` (scan `list_users` for `uid` + `is_admin`). Reject route mirrors it with status `'rejected'`. CLI: `approve`/`reject` commands calling the same helpers.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_admin_approval.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/web/admin_routes.py app/web/templates/admin.html app/cli/user.py tests/test_admin_approval.py
git commit -m "feat(admin): approve/reject pending registrations (dashboard + CLI)"
```

**Phase 0 complete — Feature B done. Tests: `pytest tests/ -q -m 'not e2e'` green.**

---

### Task 5: resolve_collection seam + share/unshare endpoints

**Files:**
- Create: `app/storage/resolver.py` (new)
- Modify: `app/storage/shared_db.py` (add helpers already in Task 2), `app/web/routes.py` (share UI), `app/web/templates/library.html`, `app/web/templates/collection.html`
- Test: `tests/test_resolver.py` (new)

**Interfaces:**
- Produces: `resolve_collection(db_dir, collection_id, uid) -> dict | None` returning `{"collection_id", "owner_uid", "role", "name"}`; `POST /collections/{collection_id}/share` (username + role), `POST /collections/{collection_id}/unshare` (username), `GET /collections/{collection_id}/members`

- [ ] **Step 1: Write the failing tests**

```python
def test_resolve_private_collection(tmp_dirs):
    uid = "alice"
    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "Mine")
    uconn.close()
    r = resolve_collection(tmp_dirs["db"], cid, uid)
    assert r["owner_uid"] == uid
    assert r["role"] == "owner"
    assert resolve_collection(tmp_dirs["db"], cid, "bob") is None


def test_resolve_shared_collection(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", hash_password("pw"))
    create_user(conn, "bob", hash_password("pw"))
    add_collection_member(conn, "c1", "alice", "owner")
    add_collection_member(conn, "c1", "bob", "member")
    conn.close()
    r = resolve_collection(tmp_dirs["db"], "c1", "bob")
    assert r["owner_uid"] == "alice"
    assert r["role"] == "member"
    assert resolve_collection(tmp_dirs["db"], "c1", "eve") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_resolver.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement the resolver**

```python
# app/storage/resolver.py
def resolve_collection(db_dir: Path, collection_id: str, uid: str) -> dict[str, Any] | None:
    """Map (collection_id, uid) -> owner + role, or None if the user has no
    access. Private collections are owned by their creator (no membership
    row); shared collections consult collection_members."""
    conn = init_shared_db(db_dir)
    try:
        membership = get_membership(conn, collection_id, uid)
        if membership:
            return {"collection_id": collection_id, "owner_uid": membership["user_id"],
                    "role": membership["role"]}
        # Private fallback: the collection exists in the user's own DB
        uconn = init_user_db(db_dir, uid)
        try:
            row = uconn.execute("SELECT name FROM collections WHERE collection_id = ?", (collection_id,)).fetchone()
            if row:
                return {"collection_id": collection_id, "owner_uid": uid, "role": "owner"}
        finally:
            uconn.close()
        return None
    finally:
        conn.close()
```

Share endpoint (owner-only, CSRF-protected): look up the target username via `get_user_by_username`, then `add_collection_member(conn, collection_id, target_uid, role)`. Return 404 if username unknown. Unshare: `remove_collection_member`; blocking owner removal (or demote) is owner-only policy — MVP: reject unshare of the owner row.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_resolver.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/storage/resolver.py app/web/routes.py app/web/templates/library.html app/web/templates/collection.html tests/test_resolver.py
git commit -m "feat(sharing): resolve_collection seam + share/unshare endpoints"
```

---

### Task 6: Read path — library merge, collection/doc/pdf/cover routes through the seam

**Files:**
- Modify: `app/web/routes.py` (library, collection_view, collection_table, upload_form, doc_view, doc_view_leaf, doc_cover, doc_pdf, doc_search_path, delete_doc_route)
- Test: `tests/test_shared_read.py` (new)

**Interfaces:**
- Consumes: `resolve_collection`, `list_shared_collections_for_user`
- Produces: library page lists private + shared collections (badge for shared); every collection/doc route resolves `(collection_id, uid)` → `(owner_uid, role)` and **rejects with 303 → `/` if None**; all DB/file access switches from `uid` to `owner_uid`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_member_reads_shared_collection(tmp_dirs):
    # alice owns c1 with a doc; bob is a member
    # bob GET /collections/c1 → 200, sees the doc title
    ...

@pytest.mark.asyncio
async def test_non_member_cannot_read(tmp_dirs):
    # eve (no membership) GET /collections/c1 → 303 → "/"
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_shared_read.py -v`
Expected: FAIL — no membership checks; bob gets 404/empty, eve can read (security hole)

- [ ] **Step 3: Implement**

Add a helper in `app/web/routes.py`:

```python
def _resolve_owner(request: Request, collection_id: str) -> str | None:
    """Returns owner_uid for an accessible collection, or None."""
    uid = current_user_id(request)
    if not uid:
        return None
    r = resolve_collection(_db_dir, collection_id, uid)
    return r["owner_uid"] if r else None
```

Then in each route replace `uid = current_user_id(request)` + `init_user_db(_db_dir, uid)` with `owner = _resolve_owner(request, collection_id); if not owner: return RedirectResponse("/", 303)` and use `init_user_db(_db_dir, owner)` / `data_dir / owner / doc_id` for file access. For `collection_view`, also pass `role` into the template so the UI can show owner-only actions. Library view: `list_shared_collections_for_user` merged with `list_collections`; each shared entry needs its owner's collection name — resolve names by opening the owner's DB (bounded: one extra DB open per shared collection).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_shared_read.py -v` then full suite
Expected: PASS; full suite green (existing per-user tests still pass — private collections resolve to self)

- [ ] **Step 5: Commit**

```bash
git add app/web/routes.py app/web/templates/library.html tests/test_shared_read.py
git commit -m "feat(sharing): member read path — resolve collections through owner seam"
```

---

### Task 7: Write path — member upload into owner's DB + job with owner uid

**Files:**
- Modify: `app/web/routes.py` (upload, delete_doc_route), `app/pipeline/runner.py` (`user_books.collection_id` wart), `app/storage/shared_db.py`
- Test: `tests/test_shared_write.py` (new)

**Interfaces:**
- Consumes: `resolve_collection`
- Produces: member upload writes files to `data/<owner_uid>/<doc_id>/`, `create_doc` into owner's DB with the shared collection_id, `enqueue_job(sconn, owner_uid, ...)`; owner-only delete; `link_user_book` records the real collection_id (fix the `""` wart at `runner.py:89`).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_member_upload_lands_in_owner_tree(tmp_dirs):
    # alice owns shared c1; bob is member
    # bob POST /upload (collection_id=c1, real PDF) → 303
    # owner's DB has the doc; file exists at data/<alice>/<doc_id>/original.pdf
    # bob's DB has nothing
    ...

@pytest.mark.asyncio
async def test_member_cannot_delete_owner_doc(tmp_dirs):
    # bob POST /docs/{doc_id}/delete on owner's doc → 303 to "/" with no delete
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_shared_write.py -v`
Expected: FAIL — upload writes to bob's tree

- [ ] **Step 3: Implement**

Upload route: `owner = _resolve_owner(request, collection_id); if not owner: return RedirectResponse("/", 303)` — then the existing body runs with `owner` in place of `uid` (write to `user_data_dir(_data_dir, owner)`, `create_doc(uconn, ..., collection_id, ...)`, `enqueue_job(sconn, owner, ...)`, `_invalidate_storage(owner)`). Delete route: resolve owner; if `role != "owner"` → 303 `/` (owner-only delete for shared collections; private collections keep owner=self semantics). Runner wart fix: in the shared-book copy branch, pass the real `collection_id` to `link_user_book` (thread it from the job — `job["collection_id"]` is not currently stored; add it to `enqueue_job` payload or look up from the owner's DB by doc_id).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_shared_write.py -v` then full suite
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/web/routes.py app/pipeline/runner.py app/storage/shared_db.py tests/test_shared_write.py
git commit -m "feat(sharing): member upload into owner DB; owner-only delete; fix user_books collection_id"
```

---

### Task 8: Agent path — sessions + ToolBox resolve shared collections

**Files:**
- Modify: `app/agent/routes.py` (start_session, continue_session, continue_session_stream, view_session), `app/agent/tools.py` (ToolBox), `app/agent/history.py` (no change — sessions stay personal)
- Test: `tests/test_shared_agent.py` (new)

**Interfaces:**
- Consumes: `resolve_collection`
- Produces: `ToolBox(data_dir, user_id, db_dir, collection_id, owner_uid=None)` — when `owner_uid` is set, all DB access (`fts_search`, `_keyword_synonyms`) and path validation use the **owner's** DB/tree; `validate_user_path(data_dir, owner_uid, path)`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_member_asks_question_against_shared_collection(tmp_dirs):
    # alice owns c1 with goblin doc; bob is member
    # bob POST /sessions (collection_id=c1) with mocked loop → answer from owner's FTS
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_shared_agent.py -v`
Expected: FAIL — bob's ToolBox searches bob's empty DB

- [ ] **Step 3: Implement**

`start_session`/`continue_session`/stream: `owner = _resolve_owner(request, collection_id); if not owner: return RedirectResponse("/", 303)`; pass `owner_uid=owner` into `_make_loop` → `ToolBox(_data_dir, uid, _db_dir, collection_id, owner_uid=owner)`. In `ToolBox.__init__`, store `self.owner_uid = owner_uid or user_id`; replace every `self.user_id` use in DB access (`init_user_db(self.db_dir, self.owner_uid)`) and path validation (`validate_user_path(self.data_dir, self.owner_uid, path)`, `self.data_dir / self.owner_uid / ...`). Keep `self.user_id` for session/personal bookkeeping only. `view_session` resolves the session's collection via the seam before loading.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_shared_agent.py -v` then full suite
Expected: PASS (existing agent tests use private collections → owner_uid == user_id, unchanged behavior)

- [ ] **Step 5: Commit**

```bash
git add app/agent/routes.py app/agent/tools.py tests/test_shared_agent.py
git commit -m "feat(sharing): agent queries resolve shared collections to owner's FTS"
```

---

### Task 9: Hardening + cross-user authorization tests

**Files:**
- Test: `tests/test_shared_security.py` (new)

**Interfaces:** none (verification only)

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_non_member_blocked_everywhere(tmp_dirs):
    # eve: GET /collections/c1, GET /docs/{id}/cover, GET /docs/{id}/pdf,
    # POST /sessions (c1), GET /docs/search?path=..., /docs/{id}/view → all 303 → "/"
    ...

@pytest.mark.asyncio
async def test_member_cannot_reach_other_private_collections(tmp_dirs):
    # bob is member of c1 only; alice has private c2
    # bob GET /collections/c2 → 303 "/"
    ...

@pytest.mark.asyncio
async def test_removed_member_loses_access(tmp_dirs):
    # remove bob from c1 → bob's next access → 303 "/"
    ...

@pytest.mark.asyncio
async def test_toolbox_cannot_escape_owner_root(tmp_dirs):
    # ToolBox(owner_uid=alice) grep/read_file with path targeting ../bob/... → rejected
    ...
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_shared_security.py -v`
Expected: PASS after Tasks 5–8 (fix any failures — this is the authz regression net)

- [ ] **Step 3: Full regression run**

Run: `pytest tests/ -q -m "not e2e"`
Expected: all pass (existing isolation tests unchanged — private collections are the default)

- [ ] **Step 4: Commit**

```bash
git add tests/test_shared_security.py
git commit -m "test(sharing): cross-user authorization regression net"
```

---

### Task 10: UI polish + config documentation

**Files:**
- Modify: `app/web/templates/library.html` (share button + members modal), `app/web/templates/collection.html` (role badge, owner-only delete), `README.md`, `config.yaml`

- [ ] **Step 1: Share/members UI**

Library card for each collection: "Share" button (owner) opening a small form (username + role select, `_csrf`). Collection page: "Shared with N users" line + role badge for members; owner sees Delete/Reprocess, members see read/ask/upload only.

- [ ] **Step 2: Config docs**

`config.yaml` gains `allow_registration` commented-out; README documents `ALLOW_REGISTRATION=1`, the share flow, and the owner-quota tradeoff.

- [ ] **Step 3: Manual smoke test**

Run: `docker compose up -d --build prod`; register → approve → share → member upload → member asks question. Verify spinner still works (htmx vendored).

- [ ] **Step 4: Commit**

```bash
git add app/web/templates/ app/web/static/ README.md config.yaml
git commit -m "feat(sharing): share UI + registration docs"
```

---

## Summary

| Phase | Tasks | Effort (agent consensus) |
|-------|-------|--------------------------|
| B: Registration + approval | 1–4 | 1–2 days |
| A: Sharing | 5–10 | 4–6 days |
| **Total** | 1–10 | **~5–8 days** |

**Key risks (all agents):** (1) the implicit-authz invariant — today's routes trust random UUIDs; every collection/doc route must gain the membership check (Tasks 6–8). (2) `validate_user_path` must always resolve to the owner's root for shared collections — never validate a shared doc against the member's root. (3) Existing isolation tests must stay green — private collections resolve to self, so behavior is unchanged unless shared.

**Out of scope (documented):** shared sessions/chat history (per-user stays), member storage quota (owner-charged), delete-user while owning shared collections (no delete-user exists today), first-class `db/coll_<id>.sqlite` refactor (the resolver seam makes this a future one-line change per agent).
