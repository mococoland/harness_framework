# Step 0: smoke-test

이 step은 하네스 실행 흐름을 처음 체험해보기 위한 **무해한 예제**다.
실제 빌드나 외부 의존성 없이, 파일 하나를 만들고 결과를 보고하는 것까지만 한다.

## 읽어야 할 파일

- `README.md` — 하네스가 어떻게 동작하는지
- `.claude/commands/harness.md` — step/result 규약

## 작업

1. 레포 루트에 `phases/example/HELLO.md` 파일을 만들고, 다음 한 줄을 적어라:

   ```
   하네스 스모크 테스트 성공 — <현재 시각>
   ```

2. 이 파일 외에 다른 파일은 만들거나 수정하지 마라.

## Acceptance Criteria

```bash
# 파일이 생성되었는지 확인
test -f phases/example/HELLO.md
```

## 검증 절차

1. 위 파일이 존재하는지 확인한다.
2. 결과를 `phases/example/step0.result.json`에 기록한다 (index.json은 수정하지 마라):
   - 성공 → `{ "status": "completed", "summary": "HELLO.md 생성, 스모크 테스트 통과" }`

## 금지사항

- 레포의 다른 파일을 건드리지 마라. 이유: 이 step은 흐름 확인용 스모크 테스트일 뿐이다.
- `index.json`을 직접 수정하지 마라. 이유: 하네스(execute.py)가 단독 관리한다.
