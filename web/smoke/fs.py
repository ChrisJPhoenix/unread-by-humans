"""/fs/* endpoint smoke checks plus the home-page check (Domain 1: filesystem HTTP glue)."""

import os


def run(ctx, check) -> None:
    # 1. Read an existing file.
    r = ctx.client.get("/fs/read?path=notes/hello.txt")
    check(
        "GET /fs/read notes/hello.txt -> 200 + correct body",
        r.status_code == 200 and r.get_data(as_text=True) == "hello body",
    )

    # 2. Traversal via relative path rejected.
    r = ctx.client.get("/fs/read?path=../escape")
    check("GET /fs/read ../escape -> 400", r.status_code == 400)

    # 3. Absolute path rejected.
    r = ctx.client.get("/fs/read?path=/etc/passwd")
    check("GET /fs/read /etc/passwd -> 400", r.status_code == 400)

    # 4. Missing file -> 404.
    r = ctx.client.get("/fs/read?path=notes/missing.txt")
    check("GET /fs/read missing -> 404", r.status_code == 404)

    # 5. mkdir creates nested dirs on disk.
    r = ctx.client.post("/fs/mkdir", json={"path": "made/nested"})
    nested_created = os.path.isdir(os.path.join(ctx.temp_root, "made", "nested"))
    check("POST /fs/mkdir made/nested -> 200 + dir exists", r.status_code == 200 and nested_created)

    # 6. write creates file with correct contents.
    r = ctx.client.post("/fs/write", json={"path": "out/file.txt", "contents": "data"})
    written_path = os.path.join(ctx.temp_root, "out", "file.txt")
    file_has_data = os.path.isfile(written_path) and open(written_path).read() == "data"
    check("POST /fs/write creates file -> 200 + correct contents", r.status_code == 200 and file_has_data)

    # 7. noOverwrite=true on existing file -> 409.
    r = ctx.client.post("/fs/write", json={"path": "out/file.txt", "contents": "other", "noOverwrite": True})
    check("POST /fs/write noOverwrite on existing -> 409", r.status_code == 409)

    # 8. Traversal on write rejected.
    r = ctx.client.post("/fs/write", json={"path": "../escape.txt", "contents": "x"})
    check("POST /fs/write ../escape.txt -> 400", r.status_code == 400)

    # 9. List dir with dangling symlink -> 200, entry present.
    r = ctx.client.get("/fs/list?path=linkdir")
    entries = r.get_json() if r.status_code == 200 else []
    dangling_listed = any(e.get("name") == "dangling" for e in (entries or []))
    check("GET /fs/list linkdir (dangling symlink) -> 200 + entry present", r.status_code == 200 and dangling_listed)
