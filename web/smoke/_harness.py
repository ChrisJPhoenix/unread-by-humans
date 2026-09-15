"""Web smoke-test harness that discovers web/smoke/*.py project files and runs their optional seed(ctx) then run(ctx, check) hooks against a shared temp-root context."""

import glob
import importlib.util
import os
import shutil
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from web.server import create_app


class Checker:
    def __init__(self):
        self.passed = 0
        self.failures = []

    def check(self, label: str, condition: bool) -> None:
        if condition:
            print(f"[PASS] {label}")
            self.passed += 1
        else:
            print(f"[FAIL] {label}")
            self.failures.append(label)

    @property
    def failed(self) -> int:
        return len(self.failures)


class SmokeContext:
    def __init__(self, *, temp_root, project_root, client, real_client):
        self.temp_root = temp_root
        self.project_root = project_root
        self.client = client
        self.real_client = real_client


def _write_file(path: str, contents: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(contents)


def build_ctx() -> SmokeContext:
    tmp_dir = os.path.join(PROJECT_ROOT, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    temp_root = tempfile.mkdtemp(dir=tmp_dir, prefix="smoke_")

    # A readable text file.
    notes_dir = os.path.join(temp_root, "notes")
    os.makedirs(notes_dir)
    _write_file(os.path.join(notes_dir, "hello.txt"), "hello body")

    # Nested empty dir.
    os.makedirs(os.path.join(temp_root, "sub", "inner"))

    # Dir containing a dangling symlink (regression for os.lstat in /fs/list).
    linkdir = os.path.join(temp_root, "linkdir")
    os.makedirs(linkdir)
    os.symlink("does-not-exist-target", os.path.join(linkdir, "dangling"))

    client = create_app(temp_root).test_client()
    real_client = create_app(PROJECT_ROOT).test_client()

    return SmokeContext(
        temp_root=temp_root,
        project_root=PROJECT_ROOT,
        client=client,
        real_client=real_client,
    )


def discover_project_modules():
    smoke_dir = os.path.dirname(os.path.abspath(__file__))
    paths = sorted(glob.glob(os.path.join(smoke_dir, "*.py")))
    modules = []
    for path in paths:
        basename = os.path.basename(path)
        if basename.startswith("_"):
            continue
        name = basename[:-3]
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append((name, module))
    return modules


def main() -> int:
    checker = Checker()
    ctx = build_ctx()
    try:
        modules = discover_project_modules()
        for _name, module in modules:
            if callable(getattr(module, "seed", None)):
                module.seed(ctx)
        for _name, module in modules:
            module.run(ctx, checker.check)
    finally:
        shutil.rmtree(ctx.temp_root, ignore_errors=True)

    print(f"smoke: {checker.passed} passed, {checker.failed} failed")
    return 0 if checker.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
