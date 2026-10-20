# Day Zero

루프랩·모아런이라는 가상 회사에서 초대받은 지원자가 미니 PC의 메신저 / AI 채팅 / 업무·제출 앱을 오가며 120분 동안 독립 팀장·동료·개인 AI와 일하는 로컬 MVP입니다. 리포트는 업무 수행 역량·AI 활용 역량·협업 태도의 기록과 원문 근거를 제공합니다. 점수·순위·합격 판정은 없습니다.

**구현·검증 진행 중입니다. P0 완료 또는 운영 준비 완료 상태가 아닙니다.** 기능별 증거와 미검증 항목은 [MVP_PLAN.md](MVP_PLAN.md)와 [검증 기록](docs/VERIFICATION.md)을 참고하세요. P1 4개와 선택 기능은 PRD 정의·우선순위를 유지하며 필수 구현 범위에서 제외합니다.

## 개인·기업 화면

- `/`: 개인 / 기업 관계자 진입 선택.
- `/personal`: 기업에서 받은 초대 링크 확인 → 안내·동의 → 개인 미니 PC 업무 공간.
- `/admin`: 기업 로그인·지원자 초대·전체 진행 상태.
- `/admin/reports`: 제출을 마친 지원자 목록과 보고서 생성 상태.
- `/admin/reports/{sessionId}`: 해당 지원자의 최종본·품질 대조·협업/AI 활용 원문 보고서.

기업 인증은 기존 기업 단위 권한을 사용합니다. 다른 기업의 보고서는 주소를 직접 입력해도 볼 수 없습니다. UI 피드백용 ‘합성 예시 A/B’는 화면 검증 데이터이며 실제 지원자나 모델 생성 품질의 증거가 아닙니다.

## 실행 환경

- Python 3.12, uv, Node.js 22 이상, Docker Desktop/Engine.
- PostgreSQL 16 + pgvector, RabbitMQ 4.1.
- 한 Python 패키지의 Backend·AI Worker·Report Worker·Business MCP·RAG MCP를 별도 프로세스로 실행합니다.
- 호환 버전은 `services/dayzero/uv.lock`과 `apps/web/package-lock.json`에 잠겨 있습니다.

```sh
cp .env.example .env
# JWT_SECRET, INTERNAL_SECRET을 서로 다른 32자 이상 무작위 값으로 설정
uv sync --project services/dayzero --python 3.12 --extra rag
npm ci --prefix apps/web
docker compose up -d postgres rabbitmq
services/dayzero/.venv/bin/alembic -c services/dayzero/alembic.ini upgrade head
```

모든 아래 명령은 저장소 루트에서 실행합니다. `.env`는 Git에서 제외됩니다. 작업 메시지·로그·문서에 API 키나 로그인 토큰을 넣지 마세요.

## 모델 선택

코드의 기본값은 `LLM_BACKEND=openai`, API 모델은 `gpt-4o-mini`입니다. 키 유무로 경로를 자동 선택하거나 호출 실패 후 전환하지 않습니다.

```dotenv
LLM_BACKEND=openai
OPENAI_API_KEY=<실행 환경에서 제공>
OPENAI_MODEL=gpt-4o-mini
```

로컬 CLI 시연은 별도로 선택합니다. 이 작업의 실제 CLI 호출에서 `gpt-6.1-sol`은 ChatGPT 계정의 HTTP 400 미지원 응답을 받았으며, `gpt-5.6-sol`은 단순 응답·구조화 도구 제안이 확인되었습니다. 이 결과는 API gpt-4o-mini 검증이 아닙니다.

```dotenv
LLM_BACKEND=codex_cli
CODEX_BIN=codex
CODEX_MODEL=gpt-5.6-sol
CODEX_ISOLATION_PROFILE=macos-seatbelt-v1
```

현재 CLI 실행 프로파일은 macOS arm64 + CLI 0.153.4의 전용 로컬 환경만 대상으로 합니다. `codex login`을 Worker가 실행되는 사용자 환경에서 별도로 완료해야 합니다. CLI는 매 호출마다 새 임시 디렉터리와 새 대화를 사용하며 호스트 `config.toml`·규칙·MCP·플러그인을 가져오지 않습니다. 인증 파일은 전용 런타임에 심볼릭 링크만 두고 복사하지 않습니다. OS 프로파일은 프로젝트·다른 지원자 파일 읽기와 셸 프로세스 실행을 거절하고 native 도구를 비활성화합니다. 제품 API 키는 자식 환경에서 제외합니다.

미지원 OS·버전·격리 프로파일에서는 명확히 실패합니다. Docker Worker에 호스트 CLI·로그인이 자동 적용되지 않습니다. 현재 Docker의 CLI 경로는 미지원이며, Docker 전체 서비스 실행은 API 경로용입니다. sandbox를 해제해 CLI 성공으로 처리하지 않습니다.

세션 시작 때 실행 방식·모델·어댑터 버전·CLI 버전과 시나리오·품질·역할/권한·제공 한도 버전을 고정합니다. leader / colleague / chat은 각각 세션 ID에 에이전트 ID를 붙인 별도 thread·기억·순번·리스를 사용합니다. 과거 collaboration 체크포인트는 두 인물에게 복제하지 않습니다. 설정을 바꾸면 서버를 재시작하고 새 세션을 만들어야 합니다. 기존 세션은 기존 바인딩을 사용합니다. CLI의 미수집 토큰·비용은 null이며 API 비용으로 환산하지 않습니다.

리포트 분석만 CLI로 실행하려면 세 Graph의 API 설정을 유지하고 아래를 추가합니다. 생략하면 기존 에이전트 설정을 상속합니다. 리포트 호출은 도구 없이 저장된 근거만 분석하며 원문·주체·최종본 검사에 통과해야 공개됩니다.

```dotenv
REPORT_LLM_BACKEND=codex_cli
REPORT_MODEL=gpt-5.6-sol
CODEX_ISOLATION_PROFILE=macos-seatbelt-v1
```

세션 시작 때 `binding`과 `report_binding`을 각각 고정합니다. 리포트에는 분석 모델과 `agent_binding`을 함께 남깁니다. 기존 세션의 모델을 바꾸거나 호출 실패 시 API/CLI를 자동 대체하지 않습니다. 리포트 전용 CLI의 실제 연결·품질 비교 증거는 `docs/evidence/revision-cli-*.json`에서 확인하며, 단순 연결 성공은 전체 검증이 아닙니다.

## 프로세스 시작

각각 별도 터미널에서 실행합니다.

```sh
services/dayzero/.venv/bin/uvicorn dayzero.backend.main:app --host 127.0.0.1 --port 8000 --no-access-log
services/dayzero/.venv/bin/python -m dayzero.mcp_servers.business
services/dayzero/.venv/bin/python -m dayzero.mcp_servers.rag
services/dayzero/.venv/bin/python -m dayzero.workers.ai
services/dayzero/.venv/bin/python -m dayzero.workers.report
npm run dev --prefix apps/web
```

Backend 내부 API가 인증·세션·업무 변경을 검증합니다. Worker와 Business MCP는 업무 ORM으로 직접 쓰지 않습니다. Graph는 별도 `graph` DB 역할의 `checkpoints` 스키마, RAG 검색은 `company_documents` SELECT만 허용한 `rag` 역할을 사용합니다. Compose 내부 MCP·Worker는 호스트에 공개하지 않으며, 웹·Backend와 개발 DB·큐 포트는 loopback으로만 노출합니다. 로컬 직접 실행 시 MCP를 신뢰할 수 있는 로컬 환경에서 사용하세요.

API 경로에서 모든 프로세스를 컨테이너로 실행하려면 `.env`를 API 방식으로 준비한 뒤 `docker compose up --build`를 실행합니다. 임베딩 모델 캐시 준비와 색인은 별도 단계이며 아래를 확인하세요.

## 초기 자료와 색인

기업 가입 시 PRD 6장의 고정 가상 회사 문서 5개가 기업별로 생성됩니다. 인터뷰 요약은 세션 경과 65분 전까지 검색·원문 모두 차단됩니다. 공통 본문과 같은 버전의 색인 벡터가 있으면 기업 초기화 시 재사용합니다.

```sh
# 최초 1회 모델 다운로드와 회사 문서 색인. 모델 파일은 Git에 포함하지 않음
services/dayzero/.venv/bin/python -m dayzero.rag.index
```

BGE-M3는 1024차원입니다. RAG 서버는 사전 다운로드한 동일 캐시의 모델을 `local_files_only=True`로 사용하고, 질의 벡터와 pgvector 코사인 거리로 검색합니다. 캐시가 없거나 색인이 비어 있으면 오류를 반환하며 다른 임베딩이나 단어 검색으로 조용히 대체하지 않습니다. 최초 기업 등록 후 색인하고, 이후 새 기업의 동일한 가상 문서 벡터를 재사용할 수 있습니다. `HF_HOME`을 사용할 때 색인 명령과 RAG 서버에 같은 경로를 지정하세요.

## 시연

1. [로컬 화면](http://localhost:3000) → 기업 관계자 → 가입.
2. 별칭·이메일로 72시간 초대 링크를 만들고 지원자 브라우저에 전달. 이메일 발송 기능은 없습니다.
3. 지원자 안내·동의 → 팀 소개 → 시작. 서버의 120분 시계는 브라우저를 닫아도 계속 흐릅니다.
4. 메신저의 팀 채널·동료 1:1, 개인 AI, 업무·제출 앱을 전환합니다. 팀 채널 수신자는 기본 동료이며 팀장 또는 둘을 선택할 수 있습니다. 문서 5파트와 별개로 세부 태스크를 만들고 메시지 원문·담당·상태·파트를 연결합니다. 동료 담당 업무는 업무 계기로 전달되며 90분 리뷰는 별도 초안입니다.
5. 제출 → 네 상황의 디브리핑. 별도 Report Worker가 제출 시 고정한 입력으로 리포트를 만들며 기업 목록에서 확인합니다.

## 검증

```sh
services/dayzero/.venv/bin/pytest services/dayzero/tests/unit -q
# DB·Backend·두 MCP 서버 실행 필요. 실제 모델 검증과 별개입니다.
services/dayzero/.venv/bin/pytest services/dayzero/tests/integration -q
npm run typecheck --prefix apps/web
npm run build --prefix apps/web
npm exec --prefix apps/web -- playwright install chromium
npm run test:e2e --prefix apps/web
# macOS의 실제 OS 차단과 비밀정보 없는 일회용 canary 경계 검증
services/dayzero/.venv/bin/python scripts/verify_seatbelt.py
services/dayzero/.venv/bin/python scripts/check_cli_boundary.py
# 기존 Worker를 정지하고 테스트 fixture의 대기 작업이 0개인 상태에서 실행합니다.
# 스크립트가 임시 Worker를 시작·종료하며 PRD 가상 회사와 새 합성 입력만 전송합니다.
# 키는 기존 실행 환경에서 제공하고 파일에 복사하지 않습니다.
services/dayzero/.venv/bin/python scripts/live_smoke.py
# 시계 조정 약속 동작 / 실제 120분: 서로 다른 검증입니다.
services/dayzero/.venv/bin/python scripts/verify_promised_work.py
services/dayzero/.venv/bin/python scripts/run_full_verification.py
# 별도 일회용 DB·RabbitMQ vhost에서 실제 Backend 중단/복구 (모델 호출 없음)
services/dayzero/.venv/bin/python scripts/verify_backend_outbox.py
```

단위 테스트의 가짜 API 응답과 통합 테스트의 주입 리포트는 계약·저장 동작만 입증합니다. 실제 모델 호출, 압축 시계 시나리오, 실제 120분 세션은 각각 다른 검증으로 기록합니다. `docs/evidence`의 비밀정보 없는 요약만 공유하고, Playwright trace는 초대 토큰을 포함할 수 있어 Git에서 제외됩니다.


개정 계약은 추가 마이그레이션 `0005`·`0006`과 새 세션부터 적용합니다. 기존 확정 snapshot·리포트는 소급 변경하지 않습니다. 진행 중인 구버전 collaboration 세션을 새 에이전트로 변환하거나 체크포인트를 복제하지 않으며 새 초대로 검증합니다. 상세 단계·검증 증거는 MVP_PLAN 9장을 우선합니다.

최종 리포트는 snapshot v2의 실제 제공 조건·최종본 다섯 파트·품질 대조·세 축 관찰과 요청/응답/태스크/문서 변경 연결을 보여줍니다. 인용·ID·버전·주체는 코드로 검사하며 의미 해석은 원문과 함께 사람이 검토합니다. 알림 전달·대화 열람 기록은 별도이며 이해·역량의 지표로 환산하지 않습니다.
