#!/usr/bin/env python3
"""
Harness Step Executor — phase 내 step을 순차 실행하고 자가 교정한다.

Usage:
    python3 scripts/execute.py <phase-dir> [--push]
"""

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


@contextlib.contextmanager
def progress_indicator(label: str):
    """터미널 진행 표시기. with 문으로 사용하며 .elapsed 로 경과 시간을 읽는다."""
    frames = "◐◓◑◒"
    stop = threading.Event()
    t0 = time.monotonic()

    def _animate():
        idx = 0
        while not stop.wait(0.12):
            sec = int(time.monotonic() - t0)
            sys.stderr.write(f"\r{frames[idx % len(frames)]} {label} [{sec}s]")
            sys.stderr.flush()
            idx += 1
        sys.stderr.write("\r" + " " * (len(label) + 20) + "\r")
        sys.stderr.flush()

    th = threading.Thread(target=_animate, daemon=True)
    th.start()
    info = types.SimpleNamespace(elapsed=0.0)
    try:
        yield info
    finally:
        stop.set()
        th.join()
        info.elapsed = time.monotonic() - t0


class StepExecutor:
    """Phase 디렉토리 안의 step들을 순차 실행하는 하네스."""

    MAX_RETRIES = 3
    FEAT_MSG = "feat({phase}): step {num} — {name}"
    CHORE_MSG = "chore({phase}): step {num} output"
    TZ = timezone(timedelta(hours=9))

    def __init__(self, phase_dir_name: str, *, auto_push: bool = False, strict: bool = False):
        self._root = str(ROOT)
        self._phases_dir = ROOT / "phases"
        self._phase_dir = self._phases_dir / phase_dir_name
        self._phase_dir_name = phase_dir_name
        self._top_index_file = self._phases_dir / "index.json"
        self._auto_push = auto_push
        self._strict = strict

        if not self._phase_dir.is_dir():
            print(f"ERROR: {self._phase_dir} not found")
            sys.exit(1)

        self._index_file = self._phase_dir / "index.json"
        if not self._index_file.exists():
            print(f"ERROR: {self._index_file} not found")
            sys.exit(1)

        idx = self._read_json(self._index_file)
        self._project = idx.get("project", "project")
        self._phase_name = idx.get("phase", phase_dir_name)
        self._total = len(idx["steps"])

    def run(self):
        self._print_header()
        self._validate_plan()
        self._check_blockers()
        self._checkout_branch()
        guardrails = self._load_guardrails()
        self._warn_placeholders(guardrails)
        self._ensure_created_at()
        self._execute_all_steps(guardrails)
        self._finalize()

    # --- timestamps ---

    def _stamp(self) -> str:
        return datetime.now(self.TZ).strftime("%Y-%m-%dT%H:%M:%S%z")

    # --- JSON I/O ---

    @staticmethod
    def _read_json(p: Path) -> dict:
        return json.loads(p.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(p: Path, data: dict):
        """index.json 등 진실원본을 원자적으로 교체한다.

        temp 파일에 먼저 쓰고 os.replace로 교체하므로, 쓰기 도중 중단돼도
        기존 파일이 깨진 JSON으로 남지 않는다.
        """
        text = json.dumps(data, indent=2, ensure_ascii=False)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)

    # --- git ---

    def _run_git(self, *args) -> subprocess.CompletedProcess:
        cmd = ["git"] + list(args)
        return subprocess.run(cmd, cwd=self._root, capture_output=True, text=True)

    def _checkout_branch(self):
        branch = f"feat-{self._phase_name}"

        r = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        if r.returncode != 0:
            print(f"  ERROR: git을 사용할 수 없거나 git repo가 아닙니다.")
            print(f"  {r.stderr.strip()}")
            sys.exit(1)

        if r.stdout.strip() == branch:
            return

        r = self._run_git("rev-parse", "--verify", branch)
        r = self._run_git("checkout", branch) if r.returncode == 0 else self._run_git("checkout", "-b", branch)

        if r.returncode != 0:
            print(f"  ERROR: 브랜치 '{branch}' checkout 실패.")
            print(f"  {r.stderr.strip()}")
            print(f"  Hint: 변경사항을 stash하거나 commit한 후 다시 시도하세요.")
            sys.exit(1)

        print(f"  Branch: {branch}")

    def _commit_step(self, step_num: int, step_name: str):
        output_rel = f"phases/{self._phase_dir_name}/step{step_num}-output.json"
        index_rel = f"phases/{self._phase_dir_name}/index.json"

        self._run_git("add", "-A")
        self._run_git("reset", "HEAD", "--", output_rel)
        self._run_git("reset", "HEAD", "--", index_rel)

        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = self.FEAT_MSG.format(phase=self._phase_name, num=step_num, name=step_name)
            r = self._run_git("commit", "-m", msg)
            if r.returncode == 0:
                print(f"  Commit: {msg}")
            else:
                print(f"  WARN: 코드 커밋 실패: {r.stderr.strip()}")

        self._run_git("add", "-A")
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = self.CHORE_MSG.format(phase=self._phase_name, num=step_num)
            r = self._run_git("commit", "-m", msg)
            if r.returncode != 0:
                print(f"  WARN: housekeeping 커밋 실패: {r.stderr.strip()}")

    # --- top-level index ---

    def _update_top_index(self, status: str):
        if not self._top_index_file.exists():
            return
        top = self._read_json(self._top_index_file)
        ts = self._stamp()
        for phase in top.get("phases", []):
            if phase.get("dir") == self._phase_dir_name:
                phase["status"] = status
                ts_key = {"completed": "completed_at", "error": "failed_at", "blocked": "blocked_at"}.get(status)
                if ts_key:
                    phase[ts_key] = ts
                break
        self._write_json(self._top_index_file, top)

    # --- guardrails & context ---

    def _load_guardrails(self) -> str:
        sections = []
        claude_md = ROOT / "CLAUDE.md"
        if claude_md.exists():
            sections.append(f"## 프로젝트 규칙 (CLAUDE.md)\n\n{claude_md.read_text(encoding='utf-8')}")
        docs_dir = ROOT / "docs"
        if docs_dir.is_dir():
            for doc in sorted(docs_dir.glob("*.md")):
                sections.append(f"## {doc.stem}\n\n{doc.read_text(encoding='utf-8')}")
        return "\n\n---\n\n".join(sections) if sections else ""

    def _warn_placeholders(self, guardrails: str):
        """가드레일에 아직 채우지 않은 {...} 템플릿 placeholder가 남아있으면 경고한다.

        이 레포는 재사용 하네스라 docs는 새 프로젝트에서 채워진다. 안 채운 채
        실행하면 빈 껍데기가 매 step 프롬프트에 주입되므로 사용자에게 알린다.
        """
        placeholders = re.findall(r"\{[^{}\n]{1,60}\}", guardrails)
        if not placeholders:
            return
        sample = ", ".join(sorted(set(placeholders))[:5])
        print(f"  ⚠ 가드레일에 미완성 템플릿 placeholder {len(placeholders)}개 발견: {sample} ...")
        print(f"    CLAUDE.md / docs/*.md 를 프로젝트 내용으로 채우는 것을 권장합니다.")
        if self._strict:
            print(f"  ERROR: --strict 모드이므로 중단합니다.")
            sys.exit(1)

    @staticmethod
    def _build_step_context(index: dict) -> str:
        lines = [
            f"- Step {s['step']} ({s['name']}): {s['summary']}"
            for s in index["steps"]
            if s["status"] == "completed" and s.get("summary")
        ]
        if not lines:
            return ""
        return "## 이전 Step 산출물\n\n" + "\n".join(lines) + "\n\n"

    def _result_file(self, step_num: int) -> Path:
        return self._phase_dir / f"step{step_num}.result.json"

    def _build_preamble(self, guardrails: str, step_context: str, step_num: int,
                        prev_error: Optional[str] = None) -> str:
        commit_example = self.FEAT_MSG.format(
            phase=self._phase_name, num="N", name="<step-name>"
        )
        result_rel = f"phases/{self._phase_dir_name}/step{step_num}.result.json"
        retry_section = ""
        if prev_error:
            retry_section = (
                f"\n## ⚠ 이전 시도 실패 — 아래 에러를 반드시 참고하여 수정하라\n\n"
                f"{prev_error}\n\n---\n\n"
            )
        return (
            f"당신은 {self._project} 프로젝트의 개발자입니다. 아래 step을 수행하세요.\n\n"
            f"{guardrails}\n\n---\n\n"
            f"{step_context}{retry_section}"
            f"## 작업 규칙\n\n"
            f"1. 이전 step에서 작성된 코드를 확인하고 일관성을 유지하라.\n"
            f"2. 이 step에 명시된 작업만 수행하라. 추가 기능이나 파일을 만들지 마라.\n"
            f"3. 기존 테스트를 깨뜨리지 마라.\n"
            f"4. AC(Acceptance Criteria) 검증을 직접 실행하라.\n"
            f"5. index.json은 절대 수정하지 마라 (하네스가 단독 관리한다). "
            f"대신 작업 결과를 `{result_rel}` 파일에 JSON으로 기록하라:\n"
            f"   - AC 통과 → {{\"status\": \"completed\", \"summary\": \"이 step의 산출물 한 줄 요약\"}}\n"
            f"   - {self.MAX_RETRIES}회 수정 시도 후에도 실패 → {{\"status\": \"error\", \"error_message\": \"구체적 에러\"}}\n"
            f"   - 사용자 개입 필요 (API 키, 인증, 수동 설정 등) → {{\"status\": \"blocked\", \"blocked_reason\": \"사유\"}} 후 즉시 중단\n"
            f"6. 코드 변경사항만 커밋하라 (result.json은 커밋하지 마라):\n"
            f"   {commit_example}\n\n---\n\n"
        )

    # --- Claude 호출 ---

    @staticmethod
    def _resolve_claude() -> str:
        """claude 실행 파일 경로를 해석한다 (Windows의 .cmd 래퍼 포함)."""
        exe = shutil.which("claude")
        if exe is None:
            print(f"  ERROR: 'claude' CLI를 PATH에서 찾을 수 없습니다.")
            print(f"  Hint: Claude Code CLI를 설치하고 PATH에 등록하세요.")
            sys.exit(1)
        return exe

    def _invoke_claude(self, step: dict, preamble: str) -> dict:
        step_num, step_name = step["step"], step["name"]
        step_file = self._phase_dir / f"step{step_num}.md"

        if not step_file.exists():
            print(f"  ERROR: {step_file} not found")
            sys.exit(1)

        # 프롬프트는 stdin으로 전달한다. argv로 넘기면 Windows의 명령줄 길이
        # 한계(32767자)를 가드레일+docs가 초과해 실행이 실패할 수 있다.
        prompt = preamble + step_file.read_text(encoding="utf-8")
        # 자식 세션이 Stop 훅에서 verify를 재귀 실행하지 않도록 표시한다.
        child_env = {**os.environ, "HARNESS_CHILD": "1"}

        try:
            result = subprocess.run(
                [self._resolve_claude(), "-p", "--dangerously-skip-permissions",
                 "--output-format", "json"],
                cwd=self._root, capture_output=True, text=True, encoding="utf-8",
                input=prompt, env=child_env, timeout=1800,
            )
            exit_code, stdout, stderr = result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired as e:
            print(f"\n  WARN: Claude가 1800초 내에 끝나지 않아 타임아웃되었습니다.")
            exit_code, stdout, stderr = -1, (e.stdout or ""), "TimeoutExpired (1800s)"

        if exit_code != 0:
            print(f"\n  WARN: Claude가 비정상 종료됨 (code {exit_code})")
            if stderr:
                print(f"  stderr: {stderr[:500]}")

        output = {
            "step": step_num, "name": step_name,
            "exitCode": exit_code,
            "stdout": stdout, "stderr": stderr,
        }
        out_path = self._phase_dir / f"step{step_num}-output.json"
        out_path.write_text(
            json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        return output

    def _read_step_result(self, step_num: int) -> Optional[dict]:
        """자식이 남긴 step{N}.result.json을 읽는다. 없거나 깨졌으면 None."""
        rf = self._result_file(step_num)
        if not rf.exists():
            return None
        try:
            data = self._read_json(rf)
        except (json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None

    # --- 헤더 & 검증 ---

    def _print_header(self):
        print(f"\n{'='*60}")
        print(f"  Harness Step Executor")
        print(f"  Phase: {self._phase_name} | Steps: {self._total}")
        if self._auto_push:
            print(f"  Auto-push: enabled")
        print(f"{'='*60}")

    def _validate_plan(self):
        """실행 전에 plan(index.json + step 파일)의 정합성을 선검증한다.

        중반에 터지지 않도록, 누락/비순차/필수필드 없음 등을 미리 잡는다.
        """
        index = self._read_json(self._index_file)
        steps = index.get("steps")
        if not isinstance(steps, list) or not steps:
            print(f"  ERROR: index.json에 steps 배열이 없거나 비어있습니다.")
            sys.exit(1)

        valid_status = {"pending", "completed", "error", "blocked"}
        seen_pending = False
        for i, s in enumerate(steps):
            for field in ("step", "name", "status"):
                if field not in s:
                    print(f"  ERROR: steps[{i}]에 '{field}' 필드가 없습니다.")
                    sys.exit(1)
            if s["step"] != i:
                print(f"  ERROR: step 번호가 0부터 순차가 아닙니다 (steps[{i}].step={s['step']}).")
                sys.exit(1)
            if s["status"] not in valid_status:
                print(f"  ERROR: steps[{i}].status='{s['status']}'는 허용되지 않습니다 {valid_status}.")
                sys.exit(1)
            # 완료 뒤에 미완료가 다시 오면 안 됨 (_check_blockers 역방향 스캔 엣지케이스 방지)
            if s["status"] == "pending":
                seen_pending = True
            elif s["status"] == "completed" and seen_pending:
                print(f"  ERROR: 완료된 step{s['step']}이 미완료 step 뒤에 있습니다. 순서가 깨졌습니다.")
                sys.exit(1)
            step_file = self._phase_dir / f"step{s['step']}.md"
            if s["status"] == "pending" and not step_file.exists():
                print(f"  ERROR: {step_file} 가 없습니다. step 파일을 먼저 작성하세요.")
                sys.exit(1)

    def _check_blockers(self):
        index = self._read_json(self._index_file)
        for s in reversed(index["steps"]):
            if s["status"] == "error":
                print(f"\n  ✗ Step {s['step']} ({s['name']}) failed.")
                print(f"  Error: {s.get('error_message', 'unknown')}")
                print(f"  Fix and reset status to 'pending' to retry.")
                sys.exit(1)
            if s["status"] == "blocked":
                print(f"\n  ⏸ Step {s['step']} ({s['name']}) blocked.")
                print(f"  Reason: {s.get('blocked_reason', 'unknown')}")
                print(f"  Resolve and reset status to 'pending' to retry.")
                sys.exit(2)
            if s["status"] != "pending":
                break

    def _ensure_created_at(self):
        index = self._read_json(self._index_file)
        if "created_at" not in index:
            index["created_at"] = self._stamp()
            self._write_json(self._index_file, index)

    # --- 실행 루프 ---

    def _set_step_fields(self, step_num: int, **fields):
        """index.json의 특정 step에 필드를 머지한다 (index.json은 부모 단독 소유)."""
        index = self._read_json(self._index_file)
        for s in index["steps"]:
            if s["step"] == step_num:
                for k, v in fields.items():
                    if v is None:
                        s.pop(k, None)
                    else:
                        s[k] = v
        self._write_json(self._index_file, index)

    def _execute_single_step(self, step: dict, guardrails: str) -> bool:
        """단일 step 실행 (재시도 포함). 완료되면 True, 실패/차단이면 False."""
        step_num, step_name = step["step"], step["name"]
        done = sum(1 for s in self._read_json(self._index_file)["steps"] if s["status"] == "completed")
        prev_error = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            index = self._read_json(self._index_file)
            step_context = self._build_step_context(index)
            preamble = self._build_preamble(guardrails, step_context, step_num, prev_error)

            tag = f"Step {step_num}/{self._total - 1} ({done} done): {step_name}"
            if attempt > 1:
                tag += f" [retry {attempt}/{self.MAX_RETRIES}]"

            # 이전 시도의 결과 파일이 남아 오판하지 않도록 먼저 제거한다.
            self._result_file(step_num).unlink(missing_ok=True)

            with progress_indicator(tag) as pi:
                self._invoke_claude(step, preamble)
                elapsed = int(pi.elapsed)

            result = self._read_step_result(step_num)
            status = result.get("status") if result else None
            ts = self._stamp()

            if status == "completed":
                self._set_step_fields(
                    step_num, status="completed",
                    summary=result.get("summary", ""), completed_at=ts,
                    error_message=None, blocked_reason=None,
                )
                self._result_file(step_num).unlink(missing_ok=True)
                self._commit_step(step_num, step_name)
                print(f"  ✓ Step {step_num}: {step_name} [{elapsed}s]")
                return True

            if status == "blocked":
                reason = result.get("blocked_reason", "") if result else ""
                self._set_step_fields(step_num, status="blocked",
                                      blocked_reason=reason, blocked_at=ts)
                print(f"  ⏸ Step {step_num}: {step_name} blocked [{elapsed}s]")
                print(f"    Reason: {reason}")
                self._update_top_index("blocked")
                sys.exit(2)

            # status == "error" 이거나, 결과 파일이 없음(미보고)
            if result and result.get("error_message"):
                err_msg = result["error_message"]
            else:
                err_msg = "Step이 결과(step{N}.result.json)를 보고하지 않았습니다."

            if attempt < self.MAX_RETRIES:
                prev_error = err_msg
                print(f"  ↻ Step {step_num}: retry {attempt}/{self.MAX_RETRIES} — {err_msg}")
            else:
                self._set_step_fields(
                    step_num, status="error",
                    error_message=f"[{self.MAX_RETRIES}회 시도 후 실패] {err_msg}",
                    failed_at=ts,
                )
                self._commit_step(step_num, step_name)
                print(f"  ✗ Step {step_num}: {step_name} failed after {self.MAX_RETRIES} attempts [{elapsed}s]")
                print(f"    Error: {err_msg}")
                self._update_top_index("error")
                sys.exit(1)

        return False  # unreachable

    def _execute_all_steps(self, guardrails: str):
        while True:
            index = self._read_json(self._index_file)
            pending = next((s for s in index["steps"] if s["status"] == "pending"), None)
            if pending is None:
                print("\n  All steps completed!")
                return

            step_num = pending["step"]
            for s in index["steps"]:
                if s["step"] == step_num and "started_at" not in s:
                    s["started_at"] = self._stamp()
                    self._write_json(self._index_file, index)
                    break

            self._execute_single_step(pending, guardrails)

    def _finalize(self):
        index = self._read_json(self._index_file)
        index["completed_at"] = self._stamp()
        self._write_json(self._index_file, index)
        self._update_top_index("completed")

        self._run_git("add", "-A")
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = f"chore({self._phase_name}): mark phase completed"
            r = self._run_git("commit", "-m", msg)
            if r.returncode == 0:
                print(f"  ✓ {msg}")

        if self._auto_push:
            branch = f"feat-{self._phase_name}"
            r = self._run_git("push", "-u", "origin", branch)
            if r.returncode != 0:
                print(f"\n  ERROR: git push 실패: {r.stderr.strip()}")
                sys.exit(1)
            print(f"  ✓ Pushed to origin/{branch}")

        print(f"\n{'='*60}")
        print(f"  Phase '{self._phase_name}' completed!")
        print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="Harness Step Executor")
    parser.add_argument("phase_dir", help="Phase directory name (e.g. 0-mvp)")
    parser.add_argument("--push", action="store_true", help="Push branch after completion")
    parser.add_argument("--strict", action="store_true",
                        help="Abort if guardrail docs still contain {placeholder} templates")
    args = parser.parse_args()

    StepExecutor(args.phase_dir, auto_push=args.push, strict=args.strict).run()


if __name__ == "__main__":
    main()
