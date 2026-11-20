"""通过 GitHub REST API 推送本地提交（当 git 端点不可达时的备用通道）。

背景：某些网络环境下 ``github.com``（git 端点）不通，而 ``api.github.com``
通，此时普通 ``git push`` 会失败。本脚本改用 Git Data API 复现本地 HEAD：
创建 blob -> 创建 tree -> 创建 commit -> 更新 ref。

用法::

    python tools/push_via_api.py                 # 推送本地 HEAD
    python tools/push_via_api.py --message "..." # 指定提交信息
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

REPO = "zsqzsqzs1/write-robot"
BRANCH = "master"
GH = r"C:\Program Files\GitHub CLI\gh.exe"

# 不纳入仓库的工具目录
EXCLUDE_DIRS = {"out", ".git", "__pycache__", ".pytest_cache"}


def gh_api(method: str, path: str, payload: dict | None = None) -> dict:
    cmd = [GH, "api", "-X", method, path]
    if payload is not None:
        cmd += ["--input", "-"]
    result = subprocess.run(
        cmd,
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh api {method} {path} 失败:\n{result.stderr}")
    if not result.stdout.strip():
        return {}
    return json.loads(result.stdout)


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败:\n{result.stderr}")
    return result.stdout


def main() -> int:
    root = Path(__file__).resolve().parent.parent

    message = None
    if "--message" in sys.argv:
        message = sys.argv[sys.argv.index("--message") + 1]
    if message is None:
        message = git("log", "-1", "--pretty=%B").strip()

    # 1. 列出本地 HEAD 里的所有文件
    listing = git("ls-tree", "-r", "HEAD", "--format=%(objectmode) %(path)")
    entries = []
    for line in listing.splitlines():
        if not line.strip():
            continue
        mode, path = line.split(" ", 1)
        if any(part in EXCLUDE_DIRS for part in Path(path).parts):
            continue
        entries.append((mode, path))

    print(f"本地 HEAD 共 {len(entries)} 个文件")

    # 2. 创建 blob
    tree_entries = []
    for mode, path in entries:
        data = (root / path).read_bytes()
        blob = gh_api(
            "POST",
            f"/repos/{REPO}/git/blobs",
            {"content": base64.b64encode(data).decode(), "encoding": "base64"},
        )
        tree_entries.append(
            {"path": path, "mode": mode, "type": "blob", "sha": blob["sha"]}
        )
    print(f"  已上传 {len(tree_entries)} 个 blob")

    # 3. 基于远端当前分支创建新 tree
    ref = gh_api("GET", f"/repos/{REPO}/git/ref/heads/{BRANCH}")
    base_commit = ref["object"]["sha"]
    base_tree = gh_api("GET", f"/repos/{REPO}/git/commits/{base_commit}")["tree"]["sha"]

    new_tree = gh_api(
        "POST",
        f"/repos/{REPO}/git/trees",
        {"base_tree": base_tree, "tree": tree_entries},
    )
    print(f"  tree {new_tree['sha'][:8]}")

    # 4. 创建提交
    commit = gh_api(
        "POST",
        f"/repos/{REPO}/git/commits",
        {"message": message, "tree": new_tree["sha"], "parents": [base_commit]},
    )
    print(f"  commit {commit['sha'][:8]}")

    # 5. 更新分支引用
    gh_api(
        "PATCH",
        f"/repos/{REPO}/git/refs/heads/{BRANCH}",
        {"sha": commit["sha"], "force": False},
    )
    print(f"\n已推送到 {REPO}@{BRANCH}")
    print(f"提交: {commit['sha'][:8]}  (远端基线 {base_commit[:8]})")
    print(f"信息: {message.splitlines()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
