"""通过 GitHub REST API 推送提交（当 git 端点不可达时的备用通道）。

用法：python tools/push_via_api.py <分支> <提交信息>
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

REPO = "zsqzsqzs1/write-robot"
GH = r"C:\Program Files\GitHub CLI\gh.exe"


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


def main() -> int:
    branch = sys.argv[1] if len(sys.argv) > 1 else "master"
    message = sys.argv[2] if len(sys.argv) > 2 else "update"

    root = Path(__file__).resolve().parent.parent

    # 1. 收集要上传的文件（相对上次推送新增/修改的）
    #    -uall 把未跟踪目录展开成一个个文件，避免目录被当成文件上传
    status = subprocess.run(
        ["git", "status", "--porcelain", "-uall"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    files = []
    for line in status.splitlines():
        if not line.strip():
            continue
        code, path = line[:2].strip(), line[3:].strip()
        if "D" in code:
            continue
        if (root / path).is_dir():
            continue
        files.append((path, code))

    if not files:
        print("没有待推送的改动")
        return 0

    # 2. 为每个文件创建 blob
    tree_entries = []
    for path, code in files:
        data = (root / path).read_bytes()
        blob = gh_api(
            "POST",
            f"/repos/{REPO}/git/blobs",
            {"content": base64.b64encode(data).decode(), "encoding": "base64"},
        )
        mode = "100755" if path.endswith(".bat") or path.endswith(".sh") else "100644"
        tree_entries.append(
            {"path": path, "mode": mode, "type": "blob", "sha": blob["sha"]}
        )
        print(f"  blob {blob['sha'][:8]}  {path}")

    # 3. 基于当前分支创建新 tree
    ref = gh_api("GET", f"/repos/{REPO}/git/ref/heads/{branch}")
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
        f"/repos/{REPO}/git/refs/heads/{branch}",
        {"sha": commit["sha"], "force": False},
    )
    print(f"\n已推送到 {REPO}@{branch}: {commit['sha'][:8]}")
    print(f"文件数: {len(tree_entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
