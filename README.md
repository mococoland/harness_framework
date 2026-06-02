# Harness Framework

새 프로젝트를 시작할 때마다 복사해 쓰는 **재사용 개발 하네스**다.
프로젝트의 기획·아키텍처 문서를 가드레일로 삼아, 구현 작업을 여러 *step*으로 쪼갠 뒤
`scripts/execute.py`가 각 step을 독립된 Claude Code 세션으로 순차 실행한다.

각 세션은 빈 맥락에서 시작하지만, 하네스가 매 step마다 다음을 자동 주입한다:
- **가드레일** — `CLAUDE.md` + `docs/*.md` 전체
- **누적 컨텍스트** — 앞서 완료된 step들의 한 줄 요약(summary)
- **자가 교정** — 실패 시 직전 에러를 피드백하며 최대 3회 재시도

## 요구사항

- Python 3.9+ (`python`이 PATH에 있어야 함 — Windows는 `python`, Unix는 보통 `python3`)
- [Claude Code CLI](https://claude.com/claude-code) (`claude`가 PATH에 있어야 함)
- (도구 개발/테스트용) `pip install -r requirements-dev.txt`

## 새 프로젝트 시작 절차

1. **이 레포를 복사**해 새 프로젝트의 출발점으로 삼는다.
2. **가드레일 문서를 채운다** — `CLAUDE.md`, `docs/PRD.md`, `docs/ARCHITECTURE.md`,
   `docs/ADR.md`, `docs/UI_GUIDE.md`의 `{placeholder}`를 실제 내용으로 교체한다.
   (안 채우면 execute.py가 실행 시 경고한다. `--strict`면 중단.)
3. **step을 설계한다** — Claude Code에서 `/harness` 커맨드를 실행하면 워크플로우에 따라
   `phases/{task}/index.json`과 `step{N}.md`들을 만들어 준다.
4. **실행한다**:

   ```bash
   python scripts/execute.py {task}            # 순차 실행
   python scripts/execute.py {task} --push      # 완료 후 origin push
   python scripts/execute.py {task} --strict    # placeholder 남아있으면 중단
   ```

   > `feat-{task}` 브랜치에서 실행된다. **main(또는 깨끗한 기준 브랜치)에서 시작**하길 권장한다.

## 동작 한눈에 보기

```
phases/
├── index.json              # 전체 task 현황 (execute.py가 status 갱신)
└── {task}/
    ├── index.json          # 이 task의 step 목록 + 상태 (execute.py 단독 소유)
    ├── step0.md            # 각 step의 지시서 (사람이/​/harness가 작성)
    ├── step1.md
    ├── step{N}.result.json # 자식 세션이 결과를 보고하는 파일 (gitignore, 임시)
    └── step{N}-output.json # Claude 원시 출력 로그 (gitignore, 디버그용)
```

- **index.json은 execute.py가 단독으로 쓴다.** step 세션은 절대 수정하지 않고,
  결과를 `step{N}.result.json`에 적는다 → execute.py가 읽어 index.json에 머지한다.
  (두 주체가 같은 파일을 쓰다 summary/timestamp가 유실되는 것을 막기 위함)
- 상태: `pending` → `completed` | `error` | `blocked`. 타임스탬프는 execute.py가 기록.

### 에러 복구

- **error**: 해당 step의 `status`를 `pending`으로 되돌리고 `error_message`를 지운 뒤 재실행.
- **blocked**: `blocked_reason`의 사유(API 키 등)를 해결하고 같은 방식으로 재실행.

> 재시도 시, 직전 시도가 만든 *커밋되지 않은* 변경은 작업 트리에 남아 다음 시도에 누적될 수
> 있다. 깨끗한 재시도가 필요하면 수동으로 `git stash`/정리 후 실행하라.

## 자동화 훅 (`.claude/settings.json`)

- **PreToolUse** (`scripts/guard.py`) — Bash/PowerShell의 위험 명령(`rm -rf`,
  `git push --force`, `DROP TABLE`, `Remove-Item -Recurse -Force` 등)을 차단한다.
  자식 세션은 `--dangerously-skip-permissions`로 돌기 때문에 이 훅이 사실상 유일한 안전망이다.
- **Stop** (`scripts/verify.py`) — 프로젝트 종류를 자동 감지해 검증한다
  (`package.json`→npm 스크립트, `pyproject.toml`/`pytest.ini` 등→pytest, 없으면 통과).
  `HARNESS_NO_VERIFY=1`로 끌 수 있고, 하네스 자식 세션(`HARNESS_CHILD`)에서는 자동 스킵된다.

## 하네스 도구 개발

```bash
pip install -r requirements-dev.txt
python -m pytest scripts/ -q
```

## 빠른 체험

```bash
python scripts/execute.py example
```

`phases/example/`의 무해한 스모크 테스트 step이 실행되며 전체 흐름을 한 바퀴 돌려볼 수 있다.
