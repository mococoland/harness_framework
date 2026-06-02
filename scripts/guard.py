#!/usr/bin/env python3
"""
PreToolUse 훅용 위험명령 차단기 — stdin으로 들어온 훅 JSON을 파싱해
Bash / PowerShell 명령에 위험 패턴이 있으면 차단한다.

자식 세션은 `--dangerously-skip-permissions`로 돌기 때문에, 이 훅이 사실상
유일한 안전망이다. 따라서 Bash와 PowerShell 도구를 모두 검사한다.

종료 코드:
    0 — 허용
    2 — 차단. Claude Code는 PreToolUse 훅이 2를 반환하면 도구 실행을 막고
        stderr를 모델에 되먹인다.

훅 입력(JSON, stdin):
    { "tool_name": "Bash", "tool_input": { "command": "..." }, ... }
"""

import contextlib
import json
import re
import sys

# Windows 콘솔(cp949)에서도 한글 메시지가 깨지지 않도록 stderr를 utf-8로 맞춘다.
with contextlib.suppress(Exception):
    sys.stderr.reconfigure(encoding="utf-8")

# 위험 패턴. 단어경계/플래그를 함께 봐서 커밋 메시지 등의 단순 언급은 오탐하지 않는다.
DANGEROUS = [
    (r"\brm\s+-[a-z]*[rf][a-z]*\s", "rm -rf 류 재귀/강제 삭제"),
    (r"\bgit\s+push\s+.*--force\b", "git push --force"),
    (r"\bgit\s+push\s+.*-f\b", "git push -f"),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard"),
    (r"\bgit\s+clean\s+-[a-z]*f", "git clean -f"),
    (r"\bDROP\s+TABLE\b", "DROP TABLE"),
    (r"\bDROP\s+DATABASE\b", "DROP DATABASE"),
    (r"\bTRUNCATE\s+TABLE\b", "TRUNCATE TABLE"),
    (r"\bmkfs\b", "mkfs (파일시스템 포맷)"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "fork bomb"),
    # PowerShell
    (r"\bRemove-Item\b.*-Recurse\b.*-Force\b", "Remove-Item -Recurse -Force"),
    (r"\bRemove-Item\b.*-Force\b.*-Recurse\b", "Remove-Item -Force -Recurse"),
    (r"\bFormat-Volume\b", "Format-Volume"),
]


def _extract_command(payload: dict) -> str:
    """Bash / PowerShell 도구 입력에서 실행 문자열을 뽑는다."""
    ti = payload.get("tool_input") or {}
    if not isinstance(ti, dict):
        return ""
    # Bash, PowerShell 둘 다 'command' 필드를 쓴다.
    parts = []
    for key in ("command", "script"):
        val = ti.get(key)
        if isinstance(val, str):
            parts.append(val)
    return "\n".join(parts)


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # 입력을 해석 못하면 차단하지 않는다 (가용성 우선).
        return 0

    command = _extract_command(payload)
    if not command:
        return 0

    for pattern, label in DANGEROUS:
        if re.search(pattern, command, re.IGNORECASE):
            print(f"BLOCKED: 위험한 명령어가 감지되었습니다 — {label}", file=sys.stderr)
            print(f"  명령: {command[:200]}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
