"""guard.py (PreToolUse 위험명령 차단기) 테스트."""

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import guard


def _run(payload) -> int:
    raw = json.dumps(payload) if not isinstance(payload, str) else payload
    with patch.object(sys, "stdin", io.StringIO(raw)):
        return guard.main()


class TestGuard:
    def test_allows_safe_command(self):
        assert _run({"tool_name": "Bash", "tool_input": {"command": "ls -la"}}) == 0

    def test_blocks_rm_rf(self):
        assert _run({"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/x"}}) == 2

    def test_blocks_git_push_force(self):
        assert _run({"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}}) == 2

    def test_blocks_git_reset_hard(self):
        assert _run({"tool_name": "Bash", "tool_input": {"command": "git reset --hard HEAD~3"}}) == 2

    def test_blocks_drop_table(self):
        assert _run({"tool_name": "Bash", "tool_input": {"command": "psql -c 'DROP TABLE users'"}}) == 2

    def test_blocks_powershell_remove_item(self):
        cmd = "Remove-Item -Recurse -Force C:\\data"
        assert _run({"tool_name": "PowerShell", "tool_input": {"command": cmd}}) == 2

    def test_no_false_positive_on_mention(self):
        # 커밋 메시지에 'rm' 단어가 들어가도 실제 rm -rf 가 아니면 통과해야 한다.
        cmd = "git commit -m 'document the rm helper'"
        assert _run({"tool_name": "Bash", "tool_input": {"command": cmd}}) == 0

    def test_empty_stdin_allows(self):
        assert _run("") == 0

    def test_malformed_json_allows(self):
        assert _run("{not valid") == 0

    def test_missing_command_allows(self):
        assert _run({"tool_name": "Bash", "tool_input": {}}) == 0
