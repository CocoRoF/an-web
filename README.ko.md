# AN-Web

[English](README.md) | [한국어](README.ko.md)

[![PyPI](https://img.shields.io/pypi/v/an-web)](https://pypi.org/project/an-web/) [![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**AI 에이전트를 위한 Python 네이티브 헤드리스 브라우저 엔진. 모든 웹 페이지를 픽셀이 아니라 구조화된, 실행 가능한 시맨틱 그래프로 바꿉니다.**

## 해결하는 문제

Playwright와 Puppeteer는 사람이 작성하는 테스트를 위해 Chromium 전체를 구동합니다. 에이전트 루프(탐색, 관찰, 판단, 행동)에서는 설치 용량, 콜드 스타트, 스크린샷이나 거대한 DOM/aria 덤프라는 출력이 부담이 됩니다. AN-Web은 이 루프에 맞춰 만들었습니다.

- `pip install an-web`이 설치의 전부입니다. V8은 `mini-racer` 휠에 들어 있고 브라우저 다운로드가 없습니다.
- 페이지는 `PageSemantics` 모델로 반환됩니다: 페이지 타입, 순위가 매겨진 액션, 입력 필드, 방해 요소(모달, 쿠키 배너), role/name 트리.
- CSS 셀렉터뿐 아니라 시맨틱 타겟팅(`{"by": "role", "role": "button", "text": "Sign In"}`)을 지원합니다.
- 정책(도메인 규칙, 요청 속도 제한, 승인), 트레이싱, 리플레이가 내장되어 있습니다.
- 13개 도구, Anthropic/OpenAI용 도구 스키마, MCP 서버를 제공합니다.

AN-Web은 완전한 브라우저가 아닙니다. 스크린샷, iframe, WebSocket, hover/드래그가 없습니다. [알려진 한계](#알려진-한계)를 참고하세요.

## 설치

Python 3.12 이상이 필요합니다.

```bash
pip install an-web              # 엔진 + Python API
pip install "an-web[mcp]"       # `an-web-mcp` MCP 서버 포함
```

PyPI 최신 릴리스는 0.9.1입니다. 소스 설치: `git clone https://github.com/CocoRoF/an-web && cd an-web && pip install -e ".[dev]"` (또는 `uv sync`).

런타임 의존성: `httpx`, `brotli`, `selectolax`, `html5lib`, `pydantic`, `cssselect`, `mini-racer`(내장 V8, 휠에 포함, 시스템 패키지 불필요).
지원 플랫폼은 사전 빌드된 `mini-racer` 휠을 따릅니다.

## 빠른 시작

```python
import asyncio
from an_web import ANWebEngine

async def main():
    async with ANWebEngine() as engine:
        session = await engine.create_session()
        await session.navigate("https://example.com")

        page = await session.snapshot()          # PageSemantics 객체
        print(page.title, page.page_type, len(page.primary_actions))

        links = await session.act({"tool": "extract", "query": "a"})
        print(links["effects"]["count"])

asyncio.run(main())
```

핵심 호출은 `navigate(url)`, `snapshot()`, `act({...})` 세 가지이며 모든 도구는 `act`를 거칩니다.

```
ANWebEngine (async 컨텍스트 매니저)
  └── Session  (탭 하나: 고유한 쿠키, 스토리지, JS 런타임, 히스토리)
        navigate(url, timeout=None) · snapshot() · act(tool_call) · execute_script(js) · back() · close()
```

`engine.create_session(policy=None, session_id=None)`. 세션도 async 컨텍스트 매니저입니다.

## 3단계 API

1. **`session.act(dict)`**는 `{"status": "ok" | "failed" | "blocked", "action", "effects", "error", ...}` dict를 반환합니다. Anthropic `tool_use` 블록(`{"name": "click", "input": {...}, "type": "tool_use"}`)도 받습니다.
2. **`ANWebToolInterface(session)`**(`from an_web.api import ...`)는 타입이 있는 헬퍼 `navigate`, `click`, `type`, `snapshot`, `extract`, `eval_js`, `wait_for(condition, selector, timeout_ms)`, `run(tool_call)`을 제공합니다. 도구 이력을 기록하며 `history_as_trace()`로 내보낼 수 있습니다.
3. **`dispatch_tool(call, session, validate=True, collect_artifacts=True)`**는 저수준 파이프라인입니다: 파싱, 검증, 정규화, 정책 검사, 디스패치, 아티팩트 수집.

## 도구

| 도구 | 용도 | 주요 인자 |
|---|---|---|
| `navigate` | URL 로드, 스크립트 실행, 안정화 | `url` |
| `snapshot` | 시맨틱 페이지 상태(dict) | |
| `click` | 클릭 | `target` |
| `type` | 입력 필드에 입력 | `target`, `text`, `append` |
| `clear` | 입력 초기화 | `target` |
| `select` | 옵션 선택 | `target`, `value`, `by_text` |
| `submit` | 폼 제출 | `target` |
| `extract` | 데이터 추출(css/structured/json/html) | `query` |
| `scroll` | 스크롤 | `delta_y`, 또는 `target`(요소를 뷰로) |
| `wait_for` | 대기 | `condition`(`network_idle` 기본, `dom_stable`, `selector`, `element_visible`), `selector`, `timeout_ms`(5000) |
| `eval_js` | JS 실행, Promise는 await | `script` |
| `fetch` | 세션 쿠키와 정책을 쓰는 HTTP 요청(페이지 JS 우회) | `url`, `method`, `headers`, `body` |
| `network` | 페이지가 런타임에 보낸 fetch/XHR 로그, `index`로 본문 전체 조회 | `index` |

참고:

- `snapshot` 도구는 camelCase 키를 반환합니다: `pageType`, `title`, `url`, `primaryActions`, `inputs`, `blockingElements`, `semanticTree`, `snapshotId`. `session.snapshot()`은 같은 데이터를 snake_case 속성을 가진 `PageSemantics` 객체로 반환합니다.
- `fetch` effects: `status`, `ok`, `url`, `content_type`, `body`(최대 20만 자), `json`, `truncated`. 상대 URL은 현재 페이지 기준으로 해석됩니다.
- `network` effects: `count`, `requests`(각 항목에 `method`, `url`, `status`, `content_type`, `body_size`, 기본 2048자 본문 미리보기).
- 클라이언트 렌더링으로 DOM에 내용이 없으면 먼저 `network`/`fetch`를 확인하세요.
- `navigate`의 안정화 예산은 기본 15초이며 `session.navigate(url, timeout=3)`으로 줄일 수 있습니다. 페이지 JS가 서버 렌더링 내용을 지우면 JS 실행 전 DOM을 복원하고 effects에 `dom_restored`를 표시합니다.

### 타겟팅

`click`, `type`, `clear`, `select`, `submit`은 CSS 셀렉터 문자열 또는 dict를 받습니다.

```python
{"by": "role", "role": "button", "text": "Sign In"}   # ARIA role, 접근 가능한 이름에 대한 선택적 텍스트 필터
{"by": "text", "text": "Forgot password?"}            # 보이는 텍스트(정확히 일치하는 것을 우선)
{"by": "semantic", "text": "submit button"}           # "text"와 같은 텍스트 매칭
{"by": "node_id", "node_id": "n42"}                   # 스냅샷의 node_id
```

### 추출 모드

```python
await session.act({"tool": "extract", "query": "ul.menu li a"})                    # css (기본)
await session.act({"tool": "extract", "query": {                                    # structured
    "selector": ".product-card",
    "fields": {"name": ".product-name", "image": {"sel": "img", "attr": "src"}}}})
await session.act({"tool": "extract", "query": {"mode": "json", "selector": "script[type='application/ld+json']"}})
await session.act({"tool": "extract", "query": {"mode": "html", "selector": "article.main"}})
```

결과는 `result["effects"]["results"]`에 있으며 `count`, `mode`도 함께 반환됩니다.

## PageSemantics

`page.page_type`, `title`, `url`, `snapshot_id`, `primary_actions`, `inputs`, `blocking_elements`, `semantic_tree`, `to_dict()`.
`SemanticNode`는 `node_id`, `tag`, `role`, `name`, `value`, `xpath`, `visible`, `is_interactive`, `affordances`, `attributes`, `children`과 `find_by_role()`, `find_interactive()`, `find_by_text(text, partial=True)`를 가집니다.

분류기가 내놓을 수 있는 페이지 타입: `login_form`, `registration_form`, `checkout`, `search`, `search_results`, `product_detail`, `listing`, `article`, `dashboard`, `profile`, `settings`, `form`, `error`, `generic`, `empty`.

## MCP 서버

`an-web-mcp`는 playwright-mcp를 본뜬 도구 표면을 제공합니다(`mcp` extra 필요).

```bash
claude mcp add an-web -- uvx --from 'an-web[mcp]' an-web-mcp
```

```json
{ "mcpServers": { "an-web": { "command": "uvx", "args": ["--from", "an-web[mcp]", "an-web-mcp"] } } }
```

도구(14개): `browser_navigate`, `browser_navigate_back`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_select_option`, `browser_wait_for`, `browser_evaluate`, `browser_extract`, `browser_fetch`, `browser_network_requests`, `browser_network_request`, `browser_console_messages`, `browser_close`.
스냅샷은 `[ref=nN]` 핸들이 붙은 압축 트리이고, 액션 도구는 `element`(설명)와 `target`(ref 또는 CSS 셀렉터)을 받아 새 스냅샷을 반환합니다.

| 환경 변수 | 효과 |
|---|---|
| `ANWEB_ALLOWED_DOMAINS` | 쉼표로 구분한 허용 도메인 |
| `ANWEB_BLOCKED_DOMAINS` | 쉼표로 구분한 차단 도메인 |
| `ANWEB_NAV_TIMEOUT` | 탐색 안정화 예산(초, 기본 15) |

## Claude / OpenAI 연동

```python
from an_web.api import TOOLS_FOR_CLAUDE, TOOLS_FOR_OPENAI, get_tool_names, get_tool, get_schema
```

Anthropic SDK를 쓰는 최소 에이전트 루프:

```python
import anthropic
from an_web import ANWebEngine
from an_web.api import ANWebToolInterface, TOOLS_FOR_CLAUDE

async def run_agent(task: str, model: str):
    client = anthropic.Anthropic()
    async with ANWebEngine() as engine:
        tools = ANWebToolInterface(await engine.create_session())
        messages = [{"role": "user", "content": task}]
        while True:
            response = client.messages.create(model=model, max_tokens=4096,
                                              tools=TOOLS_FOR_CLAUDE, messages=messages)
            if response.stop_reason != "tool_use":
                return response.content[0].text
            results = []
            for block in response.content:
                if block.type == "tool_use":
                    out = await tools.run({"name": block.name, "input": block.input})
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(out)})
            messages += [{"role": "assistant", "content": response.content},
                         {"role": "user", "content": results}]
```

## 정책과 안전

모든 액션은 `PolicyChecker`를 거치며, 차단되면 `status: "blocked"`를 반환합니다.

```python
from an_web.policy.rules import PolicyRules, NavigationScope
from an_web.policy.sandbox import SandboxLimits
from an_web.policy.approvals import ApprovalManager

PolicyRules.default()                                      # 관대함, 분당 120 요청
PolicyRules.strict()                                       # 분당 30 요청, navigate + submit 승인 필요
PolicyRules.sandboxed(allowed_domains=["example.com"])

policy = PolicyRules(
    allowed_domains=["example.com", "*.example.com"], denied_domains=["evil.com"],
    allowed_schemes=["https"], navigation_scope=NavigationScope.SAME_DOMAIN,  # UNRESTRICTED, SAME_ORIGIN, PREFIX도 있음
    max_requests_per_minute=60, max_requests_per_hour=500,
    allow_form_submission=True, allow_file_download=False, require_approval_for=["submit"],
)
session = await engine.create_session(policy=policy)
```

- `SandboxLimits(max_requests, max_dom_nodes, max_script_ops, max_navigations, max_snapshots)`, 프리셋 `default()`, `strict()`, `unlimited()`.
- `ApprovalManager(auto_approve=False)`: `request(action, details)`, `grant(request_id)` / `approve`, `deny`, `grant_once(action)`, `grant_unlimited(glob_pattern)`, `is_approved`, `audit_log`. 세션마다 `session.approvals`로 하나씩 가집니다.

## 트레이싱과 리플레이

```python
from an_web.tracing.logs import get_logger
from an_web.tracing.artifacts import ArtifactCollector
from an_web.tracing.replay import ReplayTrace, ReplayEngine

logger = get_logger("agent", session_id=session.session_id)   # StructuredLogger: info/error/..., action_context(), get_errors()
collector = ArtifactCollector(session_id=session.session_id)  # record_action_trace/js_exception/network/dom/semantic/policy_violation, get_by_kind(), summary()

trace = ReplayTrace.new(session_id="t1")
trace.add_step("navigate", {"url": "https://example.com"}, expected_status="ok")
result = await ReplayEngine().replay_trace(trace, session)    # result.succeeded, result.failed_steps
trace2 = ReplayTrace.from_json(trace.to_json())
```

아티팩트 종류: `dom_snapshot`, `semantic_snapshot`, `network_trace`, `js_exception`, `action_trace`, `policy_violation`, `custom`. `dispatch_tool`은 호출마다 액션 트레이스를 기록합니다.

## JavaScript와 SPA 지원

페이지는 내장 V8(`mini-racer` 0.14, V8 14.x)에서 실행되며 Python 기반 호스트 API를 갖습니다: DOM, 이벤트, 타이머, `fetch`/`XMLHttpRequest`(비동기 네트워크 계층에 연결), `localStorage`/`sessionStorage`, `location`/`history`, `MutationObserver`, `IntersectionObserver`/`ResizeObserver`(실제로 발화), `TextEncoder`/`TextDecoder`, `DOMParser`, `FormData`/`File`, `Blob`, `URL`, `postMessage` 등. `session.execute_script(js)`와 `eval_js` 도구로 페이지에서 코드를 실행할 수 있습니다. Next.js/React와 webpack 페이지가 끝까지 렌더링되는 것을 확인했습니다.

## 벤치마크

2026-07-03에 같은 호스트에서 동일한 성공 기준으로 `playwright 1.61.0`과 비교해 측정했습니다. 하네스와 원본 수치는 [`benchmarks/`](benchmarks/)에 있습니다. 이번 README 개정에서 다시 돌리지는 않았습니다.

| 지표 | AN-Web 0.9.1 | Playwright + Chromium |
|---|---|---|
| 설치 후 디스크 | 111 MB | 약 782 MB |
| 콜드 스타트(엔진 + 첫 페이지 컨텍스트) | 0.15 ~ 0.35초 | 3.3초 |
| 웜 액션당 지연 | 0.04 ms | 1.9 ms |
| 10개 사이트 크롤 피크 RSS | 484 MB | 1,382 MB |
| 유명 사이트 점수(10곳) | 10/10 | 9/10 |

Playwright는 stackoverflow.com에서 안티봇 403으로 졌고, AN-Web은 일반 HTTP 클라이언트를 식별하는 사이트(medium.com, npmjs.com, amazon.com)에서 막힙니다. Wikipedia는 JS 안정화에 수 초가 걸립니다(`timeout=3`으로 제한). 자세한 내용은 [`benchmarks/README.md`](benchmarks/README.md)를 참고하세요.

## 알려진 한계

- **iframe**: 요소로는 나타나지만 문서를 로드하거나 스크립트를 실행하지 않습니다.
- **WebSocket**: 없습니다(`fetch`/XHR은 연결됨).
- **포인터 재현**: hover, 드래그 앤 드롭, 키 조합이 없으며 click/type/select/scroll/submit은 시맨틱 이벤트입니다.
- **스크린샷**: 설계상 없습니다.
- **안티봇 차단**: AN-Web은 일반 HTTP 클라이언트 TLS 지문을 쓰므로 일부 사이트가 막습니다(medium.com, npmjs.com 403, amazon.com 202).
- **하이드레이션**: 클라이언트가 가져온 데이터는 스냅샷에 한 번 반영되지만, 일부 Next.js 페이지에서는 하이드레이션 불일치로 정적 섹션이 중복될 수 있습니다.
- **부분 렌더링**: 일부 JS 셸 포털(daum.net, youtube.com)은 부분적으로만 렌더링됩니다.

경험칙: 읽기, 추출, 입력, 제출 중심의 에이전트 루프는 AN-Web, hover, 드래그, 스크린샷, iframe 내 결제, WebSocket 스트리밍이 필요한 흐름은 Playwright가 맞습니다.

## 프로젝트 구조

```
an_web/
  core/       ANWebEngine, Session, 스케줄러, 스냅샷 매니저
  dom/        노드, 문서, 셀렉터, 변경 감지, 시맨틱 모델
  browser/    HTML 파서 (selectolax + html5lib)
  js/         V8 브리지, JSRuntime, 호스트 Web API
  net/        httpx 클라이언트, 쿠키, 리소스 로더
  layout/     가시성, 흐름, 히트 테스트 (layout-lite)
  semantic/   추출기, 페이지 타입 분류기, role, affordance
  actions/    13개 도구
  api/        dispatch_tool, ANWebToolInterface, 요청 모델, 도구 스키마, rpc
  policy/     규칙, 검사기, 샌드박스, 승인
  tracing/    아티팩트, 로그, 리플레이
  mcp_server.py   an-web-mcp 진입점
tests/        unit/, integration/
benchmarks/   AN-Web 대 Playwright 하네스
```

## 개발

```bash
uv sync                       # 또는: pip install -e ".[dev]"
uv run pytest                 # 작성 시점 기준 1582 passed, 1 skipped
uv run ruff check an_web/
uv run mypy an_web/
```

저장소 루트의 `smoke_real_world.py`와 `test_*_*.py`는 실제 사이트 스모크 스크립트이며 `tests/`에 포함되지 않습니다.

## 라이선스

[Apache License 2.0](LICENSE)로 배포됩니다.
