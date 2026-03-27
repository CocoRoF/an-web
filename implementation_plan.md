# AN-Web: AI-Native Web Browser Engine
## 구현 계획서 v1.1

> 작성일: 2026-03-24
> 최종 수정: 2026-03-27 (V8 마이그레이션 반영)
> 기반 분석: lightpanda-io/browser (Zig), ai_native_browser_engine_plan.md
> 목표: Python-native AI execution runtime for the web

> **[v0.5.0 변경]** JS 엔진을 QuickJS에서 V8 (PyMiniRacer)으로 마이그레이션 완료.
> V8은 Chrome과 동일한 엔진으로, ES2024+ 완전 지원 및 실제 사이트 호환성이 크게 향상됨.

---

## 1. 프로젝트 개요

### 1.1 제품명 및 정의

**AN-Web (AI-Native Web Engine)**

> HTML/JS 기반 웹을 AI가 행동 가능한 상태 기계(state machine)로 실행하는 Python-native 경량 브라우저 엔진

### 1.2 핵심 차별점: Lightpanda vs AN-Web

| 항목 | Lightpanda | AN-Web |
|------|-----------|--------|
| 구현 언어 | Zig (시스템 언어) | Python 3.12+ (AI 제어 계층) |
| JS 엔진 | V8 (Chromium V8) | V8 via PyMiniRacer (Chrome 동급 호환성) |
| HTML 파서 | html5ever (Rust) | selectolax(Lexbor) + html5lib |
| 네트워크 | libcurl | httpx (async) |
| 인터페이스 | CDP (Chrome DevTools Protocol) | AI Tool API (Python-native) |
| Semantic Layer | SemanticTree (JSON/Text 덤프) | Semantic Execution Layer (AI world model) |
| 목표 사용자 | 자동화 도구 (Playwright/Puppeteer) | AI Agent (직접 Tool 호출) |
| 렌더링 | 없음 (headless) | 없음 (layout-lite inference만) |
| 세션 격리 | Browser → Session → Page | Session = independent execution world |
| 스냅샷 | DOM dump | Deterministic snapshot (재현 가능) |

### 1.3 Lightpanda에서 배운 핵심 인사이트

1. **SemanticTree 아키텍처**: Lightpanda는 이미 `SemanticTree.zig`를 구현하여 DOM을 role/interactivity/xpath 기반으로 변환한다. AN-Web은 이를 Python으로 더 깊게 구현하여 AI 행동 계획까지 연결한다.
2. **actions.zig 패턴**: `click`, `fill`, `scroll` 등 액션이 event dispatch + DOM mutation + event flush 패턴으로 구성된다. 이 패턴을 Python transaction 모델로 차용한다.
3. **Session 계층 설계**: Browser → Session → Page 계층에서 Session이 cookie jar, storage, navigation history를 공유한다. 동일 구조를 Python으로 채택한다.
4. **Event Loop 분리**: `runMicrotasks()`, `runMacrotasks()`, `pumpMessageLoop()`가 명확히 분리되어 있다. Python asyncio 위에 같은 구조를 구현한다.
5. **V8 선택**: Lightpanda와 동일하게 V8을 사용한다. PyMiniRacer를 통해 Python에서 V8을 경량으로 임베딩하여, 빌드 복잡도 없이 Chrome 동급 JS 호환성을 달성한다.

---

## 2. 시스템 아키텍처

### 2.1 전체 구조

```
┌─────────────────────────────────────────────────┐
│               AI Agent / Tool Caller             │
└──────────────────────┬──────────────────────────┘
                       │ Python Tool API
┌──────────────────────▼──────────────────────────┐
│              AN-Web Engine (Python)              │
│                                                  │
│  ┌─────────────────────────────────────────────┐ │
│  │           Control Plane (Python)            │ │
│  │  SessionManager │ PolicyEngine │ Scheduler  │ │
│  └─────────────────┬───────────────────────────┘ │
│                    │                             │
│  ┌─────────────────▼───────────────────────────┐ │
│  │           Execution Plane                   │ │
│  │  NetworkLoader │ HTMLParser │ DOMCore        │ │
│  │  JSBridge(V8)      │ EventLoop │ LayoutLite  │ │
│  └─────────────────┬───────────────────────────┘ │
│                    │                             │
│  ┌─────────────────▼───────────────────────────┐ │
│  │       Semantic Layer (AI-native)            │ │
│  │  SemanticExtractor │ ActionRuntime          │ │
│  │  ArtifactCollector │ SnapshotManager        │ │
│  └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

### 2.2 패키지 구조

```
an_web/
├── core/
│   ├── engine.py          # 메인 엔진 진입점
│   ├── session.py         # Session 생명주기 관리
│   ├── scheduler.py       # asyncio 기반 task/micro/macro 스케줄러
│   ├── state.py           # 엔진 전역 상태
│   └── snapshot.py        # Deterministic snapshot 관리
│
├── dom/
│   ├── nodes.py           # Node/Element/Document/Text 모델
│   ├── document.py        # Document API (querySelector 등)
│   ├── selectors.py       # CSS selector 엔진
│   ├── mutation.py        # MutationObserver / DOM mutation
│   └── semantics.py       # SemanticNode 모델 + 추출 로직
│
├── js/
│   ├── runtime.py         # V8 (PyMiniRacer) 런타임 래퍼
│   ├── host_api.py        # document/window/fetch/timer 구현
│   ├── bridge.py          # JS ↔ Python 객체 마샬링
│   └── timers.py          # setTimeout/queueMicrotask/Promise drain
│
├── net/
│   ├── client.py          # httpx.AsyncClient 래퍼
│   ├── cookies.py         # Cookie jar 관리
│   ├── loader.py          # 리소스 로딩 파이프라인
│   └── resources.py       # 리소스 타입 분류 및 정책
│
├── actions/
│   ├── base.py            # Action base class (precondition/execute/postcondition)
│   ├── navigate.py        # navigate(url)
│   ├── click.py           # click(target) + MouseEvent dispatch
│   ├── input.py           # type/clear/select + input/change event
│   ├── extract.py         # extract(selector | semantic_query)
│   └── submit.py          # submit(form)
│
├── layout/
│   ├── visibility.py      # display:none / visibility:hidden 처리
│   ├── hit_test.py        # click target disambiguation
│   └── flow.py            # block/inline 흐름 추론
│
├── semantic/
│   ├── extractor.py       # DOM → SemanticGraph 변환
│   ├── roles.py           # ARIA role inference
│   ├── affordances.py     # action affordance 추론
│   └── page_type.py       # login/search/list 등 page type 분류
│
├── policy/
│   ├── rules.py           # domain allow/deny, rate limit
│   ├── sandbox.py         # 실행 격리
│   └── approvals.py       # destructive action 확인
│
├── tracing/
│   ├── artifacts.py       # DOM/semantic snapshot, network trace
│   ├── logs.py            # structured logging
│   └── replay.py          # deterministic replay
│
└── api/
    ├── models.py          # Pydantic 요청/응답 모델
    ├── tool_schema.py     # AI tool 스키마 정의
    └── rpc.py             # RPC/HTTP 서버 (선택적)
```

---

## 3. 핵심 데이터 모델

### 3.1 SemanticNode (Lightpanda SemanticTree 대응)

```python
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class SemanticNode:
    node_id: str
    tag: str
    role: str                          # ARIA role (button, link, textbox 등)
    name: Optional[str]                # accessible name
    value: Optional[str]               # input value
    xpath: str                         # XPath 위치
    is_interactive: bool               # 클릭/입력 가능 여부
    visible: bool                      # CSS visibility
    attributes: dict[str, str]
    children: list["SemanticNode"] = field(default_factory=list)
    options: Optional[list[dict]] = None  # select options
    affordances: list[str] = field(default_factory=list)  # ["click", "type", "select"]
    stable_selector: Optional[str] = None
    confidence: float = 1.0
```

### 3.2 ActionResult

```python
@dataclass
class ActionResult:
    status: str                        # "ok" | "failed" | "blocked"
    action: str
    target: Optional[str]
    effects: dict = field(default_factory=dict)
    #   navigation: bool
    #   dom_mutations: int
    #   network_requests: int
    #   modal_opened: bool
    state_delta_id: Optional[str] = None
    error: Optional[str] = None
    recommended_next_actions: list[dict] = field(default_factory=list)
```

### 3.3 PageSemantics (Lightpanda SemanticTree JSON 대응)

```python
@dataclass
class PageSemantics:
    page_type: str                     # "login_form" | "search" | "listing" | ...
    title: str
    url: str
    primary_actions: list[dict]        # ranked action candidates
    inputs: list[dict]                 # input fields
    blocking_elements: list[dict]      # modal, cookie banner 등
    semantic_tree: SemanticNode        # full semantic tree
    snapshot_id: str
```

---

## 4. 핵심 서브시스템 구현 계획

### 4.1 JS Runtime Bridge (V8 via PyMiniRacer)

Lightpanda와 동일하게 V8 엔진을 사용한다. PyMiniRacer를 통해 Python에서 V8을 임베딩한다.

> **참고**: PyMiniRacer는 `add_callable()`을 지원하지 않으므로, Python 함수를 JS에서
> 직접 호출할 수 없다. 대신 모든 `_py_*` 함수를 순수 JS로 구현하고, DOM 상태를
> JSON으로 사전 주입한 뒤 mutation log를 통해 변경사항을 동기화한다.

```python
# js/runtime.py
from py_mini_racer import MiniRacer

class JSRuntime:
    def __init__(self, session):
        self.ctx = MiniRacer()
        self.session = session
        self._setup_host_api()

    def _setup_host_api(self):
        # DOM 트리를 JSON으로 직렬화하여 V8에 주입
        # 모든 host API는 순수 JS로 사전 주입된 _domTree, _sessionState에서 동작
        install_host_api(self.ctx, self.session)

    def eval(self, script: str) -> any:
        result = self.ctx.eval(script)
        self._process_bridge_commands()  # mutation sync
        return result

    async def drain_microtasks(self):
        # V8은 eval() 후 microtask를 자동 flush
        self.ctx.eval('typeof _fireReadyTimers==="function"&&_fireReadyTimers()')
```

### 4.2 Event Loop (asyncio 기반)

Lightpanda의 `runMicrotasks()` / `runMacrotasks()` / `pumpMessageLoop()` 패턴을 asyncio로 구현한다.

```python
# core/scheduler.py
import asyncio
import heapq

class EventLoopScheduler:
    def __init__(self):
        self._microtask_queue = asyncio.Queue()
        self._macrotask_queue = []  # heapq (priority: timestamp)
        self._pending_timers: dict[int, asyncio.TimerHandle] = {}

    async def run_transaction(self, action_coro):
        """
        Lightpanda actions.zig 패턴:
        precondition → execute → event_flush → postcondition → artifact
        """
        result = await action_coro
        await self.drain_microtasks()    # Promise jobs 처리
        await self.settle_network()      # pending XHR/fetch 처리
        await self.flush_dom_mutations() # MutationObserver 콜백
        return result

    async def drain_microtasks(self):
        while not self._microtask_queue.empty():
            task = await self._microtask_queue.get()
            await task()
```

### 4.3 Actions (Lightpanda actions.zig 대응)

```python
# actions/click.py
from .base import Action, ActionResult

class ClickAction(Action):
    async def execute(self, target: str | dict, session) -> ActionResult:
        # 1. precondition: target 존재 및 interactable 확인
        node = await self._resolve_target(target, session)
        if not node.is_interactive:
            return ActionResult(status="failed", error="not_interactive")

        # 2. execute: MouseEvent dispatch (Lightpanda actions.zig 패턴)
        mouse_event = MouseEvent(type="click", bubbles=True, cancelable=True)
        await session.event_manager.dispatch(node, mouse_event)

        # 3. event/microtask flush (Lightpanda runMicrotasks 대응)
        await session.scheduler.drain_microtasks()

        # 4. postcondition: DOM 변화 관찰
        mutations = session.mutation_observer.collect()
        navigation = session.navigation_monitor.check()

        # 5. artifact 수집
        return ActionResult(
            status="ok",
            action="click",
            target=node.node_id,
            effects={
                "dom_mutations": len(mutations),
                "navigation": navigation is not None,
                "modal_opened": self._detect_modal(session),
            }
        )
```

### 4.4 Semantic Extraction (Lightpanda SemanticTree.zig 대응)

Lightpanda는 `SemanticTree.zig`에서 role/interactivity/xpath/visibility를 계산한다.
AN-Web은 이를 Python으로 구현하고, 추가로 page_type 분류와 action candidate ranking을 포함한다.

```python
# semantic/extractor.py
class SemanticExtractor:
    def extract(self, dom: Document, page: Page) -> PageSemantics:
        tree = self._walk_dom(dom.root, visibility_cache={})
        return PageSemantics(
            page_type=self._classify_page_type(tree),
            primary_actions=self._rank_actions(tree),
            inputs=self._find_inputs(tree),
            blocking_elements=self._find_blockers(tree),
            semantic_tree=tree,
            snapshot_id=page.snapshot_id,
        )

    def _classify_page_type(self, tree: SemanticNode) -> str:
        # login form, search, listing, detail, checkout, error 등 분류
        ...

    def _rank_actions(self, tree: SemanticNode) -> list[dict]:
        # primary CTA 찾기 (submit button, primary button 등)
        ...
```

---

## 5. AI Tool Interface

### 5.1 Tool 정의

```python
# api/tool_schema.py
TOOLS = [
    {
        "name": "navigate",
        "description": "URL로 이동하고 페이지 로드를 완료한다",
        "parameters": {"url": {"type": "string"}},
    },
    {
        "name": "click",
        "description": "요소를 클릭한다",
        "parameters": {
            "target": {
                "oneOf": [
                    {"type": "string", "description": "CSS selector 또는 XPath"},
                    {
                        "type": "object",
                        "properties": {
                            "by": {"enum": ["semantic", "role", "text", "node_id"]},
                            "role": {"type": "string"},
                            "text": {"type": "string"},
                        }
                    }
                ]
            }
        },
    },
    {
        "name": "type",
        "description": "텍스트를 입력 필드에 입력한다",
        "parameters": {
            "target": {"type": "string"},
            "text": {"type": "string"},
        }
    },
    {
        "name": "extract",
        "description": "페이지에서 구조화된 데이터를 추출한다",
        "parameters": {
            "query": {"type": "string"},  # semantic query 또는 CSS selector
        }
    },
    {
        "name": "snapshot",
        "description": "현재 페이지의 semantic 상태를 반환한다",
        "parameters": {},
    },
    {
        "name": "wait_for",
        "description": "조건이 만족될 때까지 대기한다",
        "parameters": {
            "condition": {"enum": ["network_idle", "dom_stable", "element_visible"]},
            "timeout_ms": {"type": "integer", "default": 5000},
        }
    },
]
```

### 5.2 Session API

```python
# 사용 예시
engine = ANWebEngine()
session = await engine.create_session(policy=default_policy)

await session.navigate("https://example.com/login")
state = await session.snapshot()
# → PageSemantics(page_type="login_form", primary_actions=[...], inputs=[...])

await session.act({
    "tool": "type",
    "target": {"by": "semantic", "role": "textbox", "name": "Email"},
    "text": "user@example.com"
})

await session.act({
    "tool": "click",
    "target": {"by": "semantic", "role": "button", "text": "Log in"}
})
```

---

## 6. 구현 로드맵

### Phase 0: 기반 연구 및 PoC (2~3주)

| 작업 | 산출물 |
|------|--------|
| ~~QuickJS~~ → V8 (PyMiniRacer) 바인딩 평가 | PoC 코드 + 성능 측정 ✅ |
| selectolax vs html5lib 비교 | 파서 선택 결정 |
| DOM 모델 최소 스펙 정의 | SemanticNode 스키마 v1 |
| 최소 host API 목록 정의 | host_api_spec.md |
| ADR 작성 | architecture_decisions.md |

**핵심 결정 사항:**
- ~~QuickJS~~ → V8 바인딩 라이브러리 선택: **PyMiniRacer** ✅
- DOM 내부 표현 (순수 Python dict vs 커스텀 Node 클래스)
- 1차 타깃 사이트 유형 (로그인 폼, 검색, 데이터 추출 중 우선순위)

### Phase 1: Minimal Executable Runtime (6~8주)

**목표**: 정적 사이트 + 기본 JS 폼 동작

| 주차 | 작업 |
|------|------|
| 1~2주 | NetworkLoader (httpx) + HTMLParser (selectolax) |
| 2~3주 | DOM Core (Node/Element/Document/selector) |
| 3~4주 | V8 bridge + document/window host API 기초 |
| 4~5주 | setTimeout/queueMicrotask/Promise drain |
| 5~6주 | click/type/submit actions + event dispatch |
| 6~7주 | Cookie/localStorage/sessionStorage |
| 7~8주 | Semantic DOM v1 + snapshot |

**성공 기준:**
- [ ] 정적 HTML 페이지 로드 + JS 실행
- [ ] 로그인 폼 자동화 (입력 + 제출)
- [ ] 검색 폼 제출 후 결과 추출
- [ ] SemanticTree JSON 출력

### Phase 2: Agent-Ready Runtime (8~12주)

**목표**: 실제 AI Agent 워크플로우 지원

| 주차 | 작업 |
|------|------|
| 1~2주 | fetch/XHR 브릿지 강화 |
| 2~3주 | Event loop 안정화 (async ordering) |
| 3~4주 | Action transaction model 고도화 |
| 4~5주 | Policy & Safety layer |
| 5~6주 | Artifact/Trace layer (deterministic replay) |
| 6~7주 | Semantic extraction 고도화 (page_type, ranking) |
| 7~8주 | Recovery / retry 전략 |
| 8~12주 | Modal/banner 감지, stable selector 생성 |

**성공 기준:**
- [ ] 로그인 → 검색 → 필터 → 상세 → 추출 시나리오 성공
- [ ] modal/cookie banner 자동 감지 및 처리
- [ ] 실패 시 structured diagnosis 반환
- [ ] session export/import

### Phase 3: Compatibility & AI Differentiation (지속)

- 실패 로그 기반 compatibility 확장 (spec chase 금지)
- semantic page typing 고도화
- action planning hints (AI가 다음 행동을 더 잘 선택하도록)
- causal effect summaries
- world-model delta export
- learned affordance ranking

---

## 7. 기술 선택 근거

### 7.1 언어: Python 3.12+

- AI agent 생태계와의 네이티브 통합 (langchain, anthropic SDK 등)
- asyncio 기반 비동기 제어 계층
- 빠른 프로토타이핑과 semantic layer 구현 적합

### 7.2 JS 엔진: V8 (PyMiniRacer)

- Lightpanda: V8 (Zig에서 직접 빌드, 복잡)
- AN-Web: V8 via PyMiniRacer (pip install로 간편 설치, Chrome 동급 호환성)
- ES2024+ 완전 지원, 자동 microtask flush, webpack/React 완벽 호환
- **주의**: `add_callable()` 미지원 → 순수 JS host API + mutation log 아키텍처 채택

### 7.3 HTML 파서: selectolax + html5lib

- Lightpanda: html5ever (Rust 기반, 매우 빠름)
- AN-Web: selectolax(Lexbor) 기본 + html5lib fallback
- 성능/정확성 균형

### 7.4 네트워크: httpx

- Lightpanda: libcurl (C 라이브러리)
- AN-Web: httpx.AsyncClient (Python async, 충분한 성능)

---

## 8. Lightpanda와의 핵심 차별점

| 항목 | Lightpanda | AN-Web |
|------|-----------|--------|
| **인터페이스** | CDP (Playwright/Puppeteer 호환) | AI Tool API (semantic targeting) |
| **Semantic Layer** | 덤프용 SemanticTree | AI world model + action planning |
| **실패 처리** | 에러 반환 | Structured failure + recommended_next_actions |
| **스냅샷** | DOM dump | Deterministic reproducible snapshot |
| **타깃팅** | selector/XPath | semantic query ("primary_button", "email_input") |
| **재현성** | 없음 | Deterministic replay 지원 |
| **정책** | robots.txt, proxy | AI 행동 정책 (domain scope, rate limit, approval) |
| **평가 기준** | 웹 표준 호환성 | AI task success rate |

---

## 9. 리스크 및 대응

| 리스크 | 확률 | 대응 전략 |
|--------|------|-----------|
| V8 host API 구현 범위 과다 | 높음 | 타깃 사이트 중심 최소 집합으로 시작, failure-driven 확장 |
| 현대 SPA(React/Vue) 호환성 부족 | 중간 | Compatibility fixtures 운영, Phase 2에서 점진 보완 |
| Python asyncio + JS event loop 동기화 오류 | 중간 | deterministic scheduler + 명시적 drain checkpoint |
| Python 성능 병목 | 낮음 | 초기엔 수용, 병목 구간 프로파일링 후 Rust/C 확장 점진 검토 |
| Semantic extraction 부정확 | 중간 | 신뢰도(confidence) 점수 부여, AI에게 불확실성 노출 |

---

## 10. 성공 지표 (KPI)

- **Navigation success rate** ≥ 95%
- **Login flow success rate** ≥ 90%
- **Form completion success rate** ≥ 85%
- **Extraction accuracy** ≥ 90%
- **Recovery success rate** ≥ 70%
- **Deterministic replay success rate** ≥ 99%
- **Startup time** < 100ms
- **Memory per session** < 50MB

---

## 11. 첫 90일 실행 계획

### Day 1-15: 기반 결정
- [x] ~~QuickJS~~ → V8 (PyMiniRacer) 바인딩 PoC ✅
- [ ] selectolax vs html5lib 성능/정확성 비교
- [ ] 최소 DOM 모델 설계 (SemanticNode 스키마)
- [ ] ADR 문서 작성

### Day 16-30: 최소 실행 엔진
- [ ] NetworkLoader (httpx) 구현
- [ ] HTMLParser 연동 + DOM Tree 생성
- [x] V8 context + document/window 기초 host API ✅
- [ ] `navigate()` + `snapshot()` 동작

### Day 31-45: 액션 구현
- [ ] click/type/submit action (Lightpanda actions.zig 패턴 채용)
- [ ] Cookie + localStorage/sessionStorage
- [ ] Semantic DOM v1 (role + interactable + xpath)
- [ ] Failure artifact v1

### Day 46-60: 네트워크 & 이벤트
- [ ] fetch/XHR bridge
- [ ] Action transaction model
- [ ] Network trace 수집
- [ ] Stable selector 생성

### Day 61-75: 평가 기반 구축
- [ ] Login/search/form benchmark suite
- [ ] Modal/banner detection
- [ ] Recovery 전략
- [ ] Layout-lite v1 (visibility/hit-test)

### Day 76-90: 안정화
- [ ] Evaluation dashboard
- [ ] Unsupported API telemetry
- [ ] Target-site compatibility matrix
- [ ] v0 release criteria 정의

---

## 12. 참고 자료

- [lightpanda-io/browser](https://github.com/lightpanda-io/browser) - 참조 구현체
- [PyMiniRacer](https://github.com/nicolo-ribaudo/pimini-racer) - V8 임베딩 (기존 QuickJS에서 마이그레이션)
- [selectolax](https://github.com/rushter/selectolax) - HTML 파서
- [WHATWG HTML Standard](https://html.spec.whatwg.org/) - HTML/DOM 표준
- [MDN Microtasks Guide](https://developer.mozilla.org/en-US/docs/Web/API/HTML_DOM_API/Microtask_guide) - 이벤트 루프
- [httpx](https://www.python-httpx.org/) - 비동기 HTTP
