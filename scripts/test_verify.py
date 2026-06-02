"""verify.py (Stop 훅 자동감지 검증) 테스트."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import verify


class TestVerify:
    def test_skips_when_harness_child(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("HARNESS_CHILD", "1")
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        assert verify.main() == 0

    def test_skips_when_no_verify_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.delenv("HARNESS_CHILD", raising=False)
        monkeypatch.setenv("HARNESS_NO_VERIFY", "1")
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        assert verify.main() == 0

    def test_no_marker_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.delenv("HARNESS_CHILD", raising=False)
        monkeypatch.delenv("HARNESS_NO_VERIFY", raising=False)
        assert verify.main() == 0

    def test_npm_project_runs_npm_checks(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.delenv("HARNESS_CHILD", raising=False)
        monkeypatch.delenv("HARNESS_NO_VERIFY", raising=False)
        pkg = {"scripts": {"test": "jest", "build": "tsc"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        called = []
        with patch.object(verify, "_run", side_effect=lambda cmd, cwd: called.append(cmd) or 0):
            assert verify.main() == 0
        # build, test 두 스크립트가 실행돼야 한다 (lint는 없음)
        assert len(called) == 2

    def test_npm_failure_propagates(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.delenv("HARNESS_CHILD", raising=False)
        monkeypatch.delenv("HARNESS_NO_VERIFY", raising=False)
        pkg = {"scripts": {"test": "jest"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        with patch.object(verify, "_run", return_value=1):
            assert verify.main() == 1

    def test_python_project_runs_pytest(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.delenv("HARNESS_CHILD", raising=False)
        monkeypatch.delenv("HARNESS_NO_VERIFY", raising=False)
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        with patch.object(verify, "_run", return_value=0) as m:
            assert verify.main() == 0
        assert m.called
