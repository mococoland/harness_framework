#!/usr/bin/env python3
"""
Stop 훅용 검증 스크립트 — 프로젝트 종류를 자동 감지해 lint/build/test를 실행한다.

이 레포는 새 프로젝트마다 복사해 쓰는 재사용 하네스다. 따라서 npm을 하드코딩하지
않고, 루트의 마커 파일을 보고 검증 커맨드를 고른다.

종료 코드:
    0 — 통과 (또는 검증할 것이 없음 / 스킵)
    2 — 검증 실패. Claude Code Stop 훅은 2를 받으면 종료를 막고 stderr를 되먹인다.

환경변수:
    HARNESS_CHILD   — execute.py가 띄운 자식 세션 표시. 있으면 즉시 통과(재귀 방지).
    HARNESS_NO_VERIFY — 검증을 일시적으로 끄는 탈출구. 있으면 즉시 통과.
    CLAUDE_PROJECT_DIR — 프로젝트 루트. 없으면 현재 작업 디렉토리.
"""

import contextlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Windows 콘솔(cp949)에서도 한글 메시지가 깨지지 않도록 stderr를 utf-8로 맞춘다.
with contextlib.suppress(Exception):
    sys.stderr.reconfigure(encoding="utf-8")


def _root() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))


def _run(cmd: list[str], cwd: Path) -> int:
    print(f"  [verify] $ {' '.join(cmd)}", file=sys.stderr)
    try:
        r = subprocess.run(cmd, cwd=str(cwd))
        return r.returncode
    except FileNotFoundError:
        print(f"  [verify] '{cmd[0]}' 를 찾을 수 없어 건너뜁니다.", file=sys.stderr)
        return 0


def _npm_checks(root: Path) -> int:
    """package.json에 정의된 lint/build/test 중 존재하는 것만 실행한다."""
    try:
        pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    scripts = pkg.get("scripts", {})
    npm = shutil.which("npm") or "npm"
    for name in ("lint", "build", "test"):
        if name in scripts:
            code = _run([npm, "run", name], root)
            if code != 0:
                return code
    return 0


def _pytest_checks(root: Path) -> int:
    if shutil.which("pytest") is None and shutil.which("py.test") is None:
        # python -m pytest 로 시도
        return _run([sys.executable, "-m", "pytest", "-q"], root)
    return _run(["pytest", "-q"], root)


def main() -> int:
    if os.environ.get("HARNESS_CHILD") or os.environ.get("HARNESS_NO_VERIFY"):
        return 0

    root = _root()

    # 우선순위: npm 프로젝트 → python 프로젝트 → 검증 대상 없음
    if (root / "package.json").exists():
        return _npm_checks(root)

    py_markers = ("pyproject.toml", "requirements.txt", "pytest.ini", "setup.cfg")
    if any((root / m).exists() for m in py_markers) or list(root.glob("test_*.py")):
        return _pytest_checks(root)

    # 감지된 마커가 없으면 검증할 것이 없으므로 통과한다.
    return 0


if __name__ == "__main__":
    code = main()
    if code != 0:
        print(f"\n  [verify] 검증 실패 (exit {code}). 위 에러를 고치세요.", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)
