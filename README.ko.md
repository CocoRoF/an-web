# AN-Web — AI-Native 웹 브라우저 엔진

[English](README.md) | [한국어](README.ko.md)

**AN-Web**은 AI 에이전트를 위해 설계된 Python 네이티브 헤드리스 브라우저 엔진입니다.
사람의 눈을 위한 픽셀 렌더링 대신, 웹을 **실행 가능한 상태 머신**으로 처리합니다 — 모든 페이지가 에이전트가 추론하고 행동할 수 있는 구조화된 시맨틱 그래프가 됩니다.

```
Navigate → Snapshot → Decide → Act → Observe → Repeat
```

핵심 인터페이스는 의도적으로 최소화되었습니다: **3개의 메서드**만 있으면 됩니다.

```python
async with ANWebEngine() as engine:
    session = await engine.create_session()

    await session.navigate("https://example.com")     # 1. 로드
    page   = await session.snapshot()                  # 2. 관찰
    result = await session.act({"tool": "click", "target": "#btn"})  # 3. 행동
```

---

## 목차

- [왜 AN-Web인가?](#왜-an-web인가)
- [Playwright 실측 벤치마크](#playwright-실측-벤치마크)
- [설치](#설치)
- [빠른 시작](#빠른-시작)
- [핵심 개념](#핵심-개념)
- [사용 패턴 — 3단계 API](#사용-패턴--3단계-api)
- [13개 도구 레퍼런스](#11개-도구-레퍼런스)
- [시맨틱 타겟팅](#시맨틱-타겟팅)
- [데이터 추출](#데이터-추출)
- [PageSemantics — AI 월드 모델](#pagesemantics--ai-월드-모델)
- [AI 모델 연동 (Claude / OpenAI)](#ai-모델-연동-claude--openai)
- [정책 & 안전](#정책--안전)
- [트레이싱 & 리플레이](#트레이싱--리플레이)
- [JavaScript 실행 & SPA 지원](#javascript-실행--spa-지원)
- [아키텍처](#아키텍처)
- [테스트](#테스트)
- [API 레퍼런스 요약](#api-레퍼런스-요약)
- [알려진 한계](#알려진-한계)
- [라이선스](#라이선스)
- [기여하기](#기여하기)

---

## 왜 AN-Web인가?

기존 헤드리스 브라우저(Playwright, Puppeteer)는 사람이 주도하는 테스트를 위해 설계되었습니다.
AN-Web은 **처음부터** AI 에이전트 루프를 위해 설계되었습니다:

| 관점 | 기존 헤드리스 브라우저 | AN-Web |
|---|---|---|
| **주요 출력** | 스크린샷 / DOM 문자열 | `PageSemantics` — 구조화된 월드 모델 |
| **JS 엔진** | V8 (Chromium 전체) | V8 via mini-racer (Chrome 동급, 경량 임베딩) |
| **설치** | pip **+ 별도 ~650 MB 브라우저 다운로드** | pip만 — 브라우저 바이너리 없음, 총 ~111 MB |
| **콜드 스타트** | ~3.3초 브라우저 기동 *(실측)* | ~0.15–0.35초 엔진+첫 세션 *(실측)* |
| **액션 지연** | ~1.9 ms IPC 왕복 *(실측)* | ~0.04 ms 인프로세스 *(실측)* |
| **메모리** | 10개 사이트 순회 피크 1.38 GB *(실측)* | 동일 순회 피크 484 MB *(실측)* |
| **액션 타겟팅** | CSS 셀렉터 / XPath 전용 | 시맨틱: `{"by": "role", "role": "button", "text": "로그인"}` |
| **정책 & 안전** | 내장 없음 | 도메인 규칙, 속도 제한, 샌드박스, 승인 프로세스 |
| **관측성** | 외부 트레이싱 | 내장 `ArtifactCollector`, `StructuredLogger`, `ReplayEngine` |
| **SPA 지원** | Full V8 | V8 + 호스트 Web API (webpack 5, React 18, jQuery) |

---

## Playwright 실측 벤치마크

아래 모든 수치는 **추정이 아닌 실측**입니다 — 동일 호스트·동일 네트워크·동일 성공 기준. 패배도 그대로 공개합니다.

> **방법** — 2026-07-03, Ubuntu 24.04, Python 3.12.3, 16코어.
> `an-web 0.9.0` vs `playwright 1.61.0` + Chromium 1228 (headless shell).
> 성공 = 타이틀 존재 **및** 사이트별 임계치 이상의 가시 텍스트 **및** 최소 링크 수.

### 리소스 풋프린트

| 지표 | AN-Web | Playwright + Chromium |
|---|---|---|
| 설치 단계 | `pip install an-web` | `pip install playwright` **+** `playwright install chromium` |
| 설치 후 디스크 | **111 MB** | 136 MB (pip) + 646 MB (브라우저) ≈ **782 MB** |
| 셋업 시 추가 다운로드 | 없음 | 114 MB 브라우저 아카이브 |
| 콜드 스타트 (엔진+첫 페이지 컨텍스트) | **0.15–0.35초** | 3.3초 |
| 웜 액션 지연 (`extract h1` ×20) | **0.04 ms** | 1.9 ms |
| 10개 사이트 순회 피크 RSS(프로세스 트리) | **484 MB** | 1,382 MB |
| en.wikipedia.org 에이전트 스냅샷 크기 | **13.7k자** (시맨틱 트리) | 293k자 (aria 스냅샷) |

### 유명 사이트 10곳 정면 비교

| 사이트 | AN-Web | Playwright | 비고 |
|---|---|---|---|
| example.com | ✅ 0.7초 | ✅ 0.7초 | |
| en.wikipedia.org (문서) | ✅ 9.8초 | ✅ 2.3초 | 추출 텍스트 **사실상 동일** (86,351 vs 86,352자); AN-Web은 JS settle 비용 — `navigate(timeout=3)`으로 상한 설정 가능 |
| news.ycombinator.com | ➖ | ➖ | 양쪽 모두 동일한 197개 링크 추출; 페이지에 `<h*>`/`<p>`가 없어 공용 텍스트 기준 미적용 |
| github.com | ✅ 1.6초 | ✅ 2.4초 | |
| stackoverflow.com | ✅ **5.4초** | ❌ HTTP 403 | Cloudflare가 headless Chromium 차단; AN-Web의 일반 HTTP 클라이언트는 통과 |
| developer.mozilla.org | ✅ 0.8초 | ✅ 1.6초 | |
| python.org | ✅ 15.4초 | ✅ 15.4초 | 양쪽 모두 settle/network-idle 예산 소진 |
| naver.com | ⚠️ 1.1초 부분 | ✅ 8.4초 | v0.9.0: 1,000+ 요소 렌더 — 메뉴·헤드라인·링크 110개 (0.8.x에선 죽은 셸); 텍스트 밀집 하위 블록은 아직 미완 |
| bbc.com | ✅ **5.1초** | ✅ 15.6초 | Playwright는 network-idle 타임아웃까지 대기. AN-Web은 SSR 보존 폴백 작동 (JS가 콘텐츠 삭제 → 사전-JS DOM 복원, `dom_restored` 플래그) |
| hrletsgo.me (Next.js 14, 클라이언트 fetch) | ✅ 2.1초 | ✅ 1.5초 | 클라이언트 `fetch` 데이터 양쪽 모두 도달; 하이드레이션 관련은 [알려진 한계](#알려진-한계) 참고 |

**스코어: 8/10 vs 8/10 — 실패 양상이 다릅니다.** AN-Web은 완전한 브라우저 환경을 요구하는 포털을 부분 렌더하고(naver — v0.9.0에서 대폭 개선; daum/youtube 부분), Playwright는 headless Chromium 겨냥 안티봇(stackoverflow)·idle 정체(bbc)에서 집니다. 일반 HTTP 클라이언트를 지문 차단하는 월은 AN-Web을 막습니다(medium/npmjs 403, amazon 202). 대상별로 골라 쓰거나 병행하십시오.

---

## 설치

**요구사항:** Python **3.12+** — 그게 전부입니다.

```bash
pip install an-web
```

> **브라우저가 필요 없습니다.** AN-Web은 Chromium, Chrome, Firefox, WebDriver를
> 다운로드하거나 요구하지 않습니다. V8 JavaScript 엔진은 `mini-racer` 휠 안에
> 프리빌트 네이티브 라이브러리로 포함되어 있어 `pip install`이 셋업의 전부입니다
> (총 ~111 MB). `an-web install` 같은 후속 단계도, apt 패키지도 없습니다.

MCP 서버 포함 설치 (Claude Desktop / MCP 클라이언트용):

```bash
pip install "an-web[mcp]"     # an-web-mcp 콘솔 스크립트 추가
```

소스에서 설치:

```bash
git clone https://github.com/CocoRoF/an-web
cd an-web
pip install -e .

# 개발 도구 포함 (pytest, ruff, mypy)
pip install -e ".[dev]"
```

**의존성** (자동 설치):

| 패키지 | 용도 |
|---|---|
| `httpx` | 비동기 HTTP 클라이언트 (리다이렉트 & 쿠키 지원) |
| `selectolax` | 고속 HTML 파서 (Lexbor 백엔드) |
| `html5lib` | 스펙 호환 폴백 파서 |
| `pydantic` | 요청/응답 검증 |
| `mini-racer` | 임베디드 V8 JavaScript 엔진 (V8 14.x) — V8 내장, 시스템 의존성 없음 |
| `cssselect` | CSS 셀렉터 파싱 |
| `brotli` | 콘텐츠 인코딩 지원 |

**플랫폼 노트**

- `mini-racer` 프리빌트 휠: Linux(glibc/manylinux), macOS(x86-64·arm64), Windows — 컴파일러 불필요.
- **Alpine/musl 컨테이너는 미지원** (V8 휠 제약); `python:3.12-slim`(Debian) 베이스를 사용하십시오.
- 브라우저 프로세스가 없으므로 Docker에서 `--shm-size`, seccomp 조정, 가상 디스플레이 없이 그대로 동작합니다.

```dockerfile
# 최소 동작 Dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir an-web
```

---

## 빠른 시작

### 핵심 로직 3줄

```python
import asyncio
from an_web import ANWebEngine

async def main():
    async with ANWebEngine() as engine:
        session = await engine.create_session()
        await session.navigate("https://example.com")
        page = await session.snapshot()

        print(page.title)               # "Example Domain"
        print(page.page_type)           # "generic"
        print(len(page.primary_actions))  # 인터랙티브 요소 수

asyncio.run(main())
```

세 번의 메서드 호출: `navigate()` → `snapshot()` → 끝.

### Navigate → Type → Click → 확인

```python
async with ANWebEngine() as engine:
    session = await engine.create_session()

    await session.navigate("https://example.com/login")
    await session.act({"tool": "type", "target": "#email", "text": "user@example.com"})
    await session.act({"tool": "type", "target": "#password", "text": "secret123"})
    await session.act({"tool": "click", "target": "#login-btn"})

    page = await session.snapshot()
    print(page.url)  # 로그인 후 리다이렉트된 URL
```

모든 상호작용은 동일한 `session.act({...})` 패턴을 따릅니다.
하나의 메서드, 13개의 도구, 보일러플레이트 제로.

---

## 핵심 개념

### 세 가지 축

| 개념 | 역할 | 메서드 |
|---|---|---|
| **Navigate** | URL 로드, JS 실행, 페이지 안정화 | `session.navigate(url)` |
| **Snapshot** | 구조화된 시맨틱 모델로 페이지 상태 획득 | `session.snapshot()` |
| **Act** | 액션 수행 (클릭, 입력, 추출, ...) | `session.act({...})` |

### ANWebEngine → Session → Action

```
ANWebEngine (프로세스 레벨, 비동기 컨텍스트 매니저)
  └── Session (하나의 "브라우저 탭")
        ├── navigate(url)       → 페이지 로드, JS 실행, 안정화
        ├── snapshot()          → PageSemantics 객체 반환
        ├── act({tool, ...})    → 13개 도구 중 하나 실행
        ├── execute_script(js)  → JavaScript 직접 실행
        ├── back()              → 이전 URL로 이동
        └── close()             → 리소스 정리
```

```python
from an_web import ANWebEngine

async with ANWebEngine() as engine:
    # 세션(독립적인 브라우저 탭) 생성
    session1 = await engine.create_session()
    session2 = await engine.create_session()
    # 각 세션은 독립된 쿠키, 스토리지, JS 런타임, 히스토리를 가짐

    # 세션도 비동기 컨텍스트 매니저로 사용 가능
    async with await engine.create_session() as session3:
        await session3.navigate("https://example.com")
    # session3은 여기서 자동으로 닫힘
```

---

## 사용 패턴 — 3단계 API

AN-Web은 용도에 맞게 선택할 수 있는 3단계 API를 제공합니다:

### Level 1: `session.act()` — 범용 인터페이스

**가장 간단함. 대부분의 경우 권장.**

하나의 메서드로 13개 도구를 모두 처리합니다. 입력은 일반 dict:

```python
async with ANWebEngine() as engine:
    session = await engine.create_session()

    # 페이지 로드
    await session.act({"tool": "navigate", "url": "https://example.com"})

    # 페이지 상태 조회
    result = await session.act({"tool": "snapshot"})

    # 클릭
    await session.act({"tool": "click", "target": "#submit"})

    # 텍스트 입력
    await session.act({"tool": "type", "target": "#search", "text": "hello"})

    # 데이터 추출
    result = await session.act({"tool": "extract", "query": "h1"})

    # JavaScript 실행
    result = await session.act({"tool": "eval_js", "script": "document.title"})
```

모든 호출은 동일한 구조의 dict를 반환합니다:
```python
{
    "status": "ok",        # "ok" | "failed" | "blocked"
    "action": "click",     # 도구 이름
    "effects": {...},      # 도구별 결과
    "error": None,         # 실패 시 에러 메시지
}
```

`session.act()`는 Anthropic의 tool_use 형식도 지원합니다:
```python
await session.act({
    "name": "click",
    "input": {"target": "#btn"},
    "type": "tool_use"
})
```

### Level 2: `ANWebToolInterface` — 타입 지정 헬퍼 메서드

IDE 자동완성이 되는 명명된 메서드. 리플레이를 위한 도구 히스토리를 자동 기록합니다.

```python
from an_web.api import ANWebToolInterface

async with ANWebEngine() as engine:
    session = await engine.create_session()
    tools = ANWebToolInterface(session)

    await tools.navigate("https://example.com/login")
    await tools.type("#email", "user@example.com")
    await tools.type("#password", "secret123")
    await tools.click("#login-btn")

    snap = await tools.snapshot()    # dict 반환
    data = await tools.extract("table.results tr")

    # 범용 run() 메서드도 사용 가능
    await tools.run({"tool": "scroll", "delta_y": 500})

    # 세션을 ReplayTrace로 내보내기
    trace = tools.history_as_trace()
```

사용 가능한 메서드:
| 메서드 | 시그니처 |
|---|---|
| `navigate(url)` | URL 로드 |
| `click(target)` | 요소 클릭 |
| `type(target, text)` | 입력 필드에 텍스트 입력 |
| `snapshot()` | 페이지 상태를 dict로 반환 |
| `extract(query)` | 페이지에서 데이터 추출 |
| `eval_js(script)` | JavaScript 실행 |
| `wait_for(condition, selector?, timeout_ms?)` | 조건 대기 |
| `run(tool_call)` | 임의의 도구 호출 dict 실행 |

### Level 3: `dispatch_tool()` — 저수준 완전 제어

검증 및 아티팩트 수집 토글이 가능한 직접 함수 호출:

```python
from an_web.api import dispatch_tool

result = await dispatch_tool(
    {"tool": "navigate", "url": "https://example.com"},
    session,
    validate=True,           # Pydantic 요청 검증 (기본값: True)
    collect_artifacts=True,  # 액션 트레이스 아티팩트 기록 (기본값: True)
)
```

파이프라인: 파싱 → 검증 → 정규화 → 정책 검사 → 디스패치 → 아티팩트 수집 → 반환.

---

## 13개 도구 레퍼런스

### `navigate` — URL 로드

```python
await session.act({"tool": "navigate", "url": "https://example.com"})
```
URL을 가져오고, HTML을 파싱하고, DOM을 구축하고, 스크립트를 실행(인라인 → 지연)하고, `DOMContentLoaded`와 `load` 이벤트를 발생시키고, 페이지를 안정화합니다.

### `snapshot` — 시맨틱 페이지 상태 조회

```python
result = await session.act({"tool": "snapshot"})

result["page_type"]       # "login_form", "search", "article", "listing", ...
result["title"]           # 페이지 제목
result["url"]             # 현재 URL
result["primary_actions"] # 순위가 매겨진 인터랙티브 요소
result["inputs"]          # 폼 필드
result["blocking_elements"]  # 모달, 쿠키 배너
result["semantic_tree"]   # 전체 페이지 트리
```

> **참고:** `session.snapshot()`은 속성 접근이 가능한 `PageSemantics` 객체를 반환합니다.
> `session.act({"tool": "snapshot"})`은 동일한 데이터를 일반 dict로 반환합니다.

### `click` — 요소 클릭

```python
await session.act({"tool": "click", "target": "#submit-btn"})
await session.act({"tool": "click", "target": {"by": "role", "role": "button", "text": "로그인"}})
```

### `type` — 입력 필드에 텍스트 입력

```python
await session.act({"tool": "type", "target": "#search", "text": "hello world"})
await session.act({"tool": "type", "target": "#search", "text": " more", "append": True})
```

### `clear` — 입력 필드 초기화

```python
await session.act({"tool": "clear", "target": "#search"})
```

### `select` — 드롭다운 옵션 선택

```python
await session.act({"tool": "select", "target": "#country", "value": "KR"})
await session.act({"tool": "select", "target": "#country", "value": "대한민국", "by_text": True})
```

### `submit` — 폼 제출

```python
await session.act({"tool": "submit", "target": "form#login"})
await session.act({"tool": "submit", "target": {"by": "role", "role": "form"}})
```

### `extract` — 페이지에서 데이터 추출

```python
result = await session.act({"tool": "extract", "query": "h1"})
# result["effects"]["results"] → [{"tag": "h1", "text": "Hello World", ...}]
```
4가지 모드에 대한 자세한 내용은 [데이터 추출](#데이터-추출) 섹션을 참조하세요.

### `scroll` — 페이지 스크롤

```python
await session.act({"tool": "scroll", "delta_y": 500})       # 아래로 500px 스크롤
await session.act({"tool": "scroll", "delta_y": -300})      # 위로 300px 스크롤
await session.act({"tool": "scroll", "target": "#section"}) # 요소가 보이도록 스크롤
```

### `wait_for` — 조건 대기

```python
await session.act({"tool": "wait_for", "condition": "network_idle"})
await session.act({"tool": "wait_for", "condition": "dom_stable", "timeout_ms": 3000})
await session.act({"tool": "wait_for", "condition": "selector", "selector": "#results"})
```

### `eval_js` — JavaScript 실행

```python
result = await session.act({"tool": "eval_js", "script": "document.title"})
result = await session.act({
    "tool": "eval_js",
    "script": "Array.from(document.querySelectorAll('a')).map(a => a.href)"
})
```

---

### `fetch` — 에이전트 직접 HTTP 요청 (APIRequestContext)

페이지 JavaScript를 거치지 않고 세션의 쿠키·정책으로 HTTP 요청을 수행합니다.
Playwright의 APIRequestContext에 해당하며, 페이지가 클라이언트에서 렌더링하는
데이터를 가장 확실하게 가져오는 방법입니다.

```python
result = await session.act({"tool": "fetch", "url": "/api/v1/posts"})
result["effects"]["json"]     # JSON 응답은 파싱되어 반환
result["effects"]["status"]   # HTTP 상태
```

### `network` — 런타임 네트워크 활동

페이지가 런타임에 수행한 fetch/XHR가 모두 기록됩니다. DOM에 데이터가
없다면(클라이언트 렌더링) 보통 여기에 있습니다.

```python
result = await session.act({"tool": "network"})              # 목록+미리보기
result = await session.act({"tool": "network", "index": 0})  # 전체 본문
```

---

## 시맨틱 타겟팅

액션 도구(`click`, `type`, `clear`, `select`, `submit`)는 5가지 타겟 해결 전략을 지원합니다:

### 1. CSS 셀렉터 (문자열)
```python
await session.act({"tool": "click", "target": "#login-btn"})
await session.act({"tool": "click", "target": "button[type=submit]"})
await session.act({"tool": "type",  "target": "input[name=email]", "text": "user@example.com"})
```

### 2. ARIA Role + 텍스트 (AI 에이전트에 권장)
```python
await session.act({"tool": "click", "target": {"by": "role", "role": "button", "text": "로그인"}})
await session.act({"tool": "type",  "target": {"by": "role", "role": "textbox", "name": "이메일"}, "text": "user@example.com"})
await session.act({"tool": "click", "target": {"by": "role", "role": "link", "text": "비밀번호 찾기"}})
```

### 3. 보이는 텍스트 매칭
```python
await session.act({"tool": "click", "target": {"by": "text", "text": "비밀번호 찾기"}})
```

### 4. Node ID (스냅샷에서 획득)
```python
page = await session.snapshot()
# 시맨틱 트리의 node_id 사용
await session.act({"tool": "click", "target": {"by": "node_id", "node_id": "n42"}})
```

### 5. 일반 시맨틱 쿼리
```python
await session.act({"tool": "click", "target": {"by": "semantic", "text": "제출 버튼"}})
```

---

## 데이터 추출

`extract` 도구는 다양한 추출 요구에 맞는 4가지 모드를 지원합니다:

### CSS 모드 (기본)

CSS 셀렉터에 매칭되는 요소 추출:
```python
result = await session.act({"tool": "extract", "query": "h1"})
# → {"effects": {"count": 1, "results": [{"tag": "h1", "text": "Hello World", "node_id": "n5"}]}}

result = await session.act({"tool": "extract", "query": "ul.menu li a"})
# → {"effects": {"count": 5, "results": [{"tag": "a", "text": "홈", ...}, ...]}}
```

### 구조화 모드

항목별 명명된 필드 추출 — 테이블, 상품 목록, 검색 결과에 이상적:
```python
result = await session.act({
    "tool": "extract",
    "query": {
        "selector": ".product-card",
        "fields": {
            "name":  ".product-name",
            "price": ".product-price",
            "image": {"sel": "img", "attr": "src"},
            "url":   {"sel": "a", "attr": "href"},
        }
    }
})
# → {"effects": {"count": 10, "results": [
#     {"name": "위젯 A", "price": "₩9,900", "image": "/img/a.jpg", "url": "/product/a"},
#     ...
# ]}}
```

### JSON 모드

내장 JSON 파싱 (예: `<script type="application/ld+json">`):
```python
result = await session.act({
    "tool": "extract",
    "query": {"mode": "json", "selector": "script[type='application/ld+json']"}
})
```

### HTML 모드

매칭된 요소의 원시 HTML 반환:
```python
result = await session.act({
    "tool": "extract",
    "query": {"mode": "html", "selector": "article.main"}
})
```

---

## PageSemantics — AI 월드 모델

`session.snapshot()` 호출 시 `PageSemantics` 객체를 받습니다 — AI 에이전트가 추론할 수 있는 전체 페이지의 구조화된 표현입니다:

```python
page = await session.snapshot()

# 페이지 레벨 메타데이터
page.page_type            # "login_form" | "search" | "listing" | "article" | "dashboard" | ...
page.title                # 페이지 제목
page.url                  # 현재 URL
page.snapshot_id          # 이 스냅샷의 고유 ID

# 사전 분류된 요소 카테고리 (에이전트의 빠른 판단을 위해)
page.primary_actions      # 순위가 매겨진 인터랙티브 요소: 버튼, 링크, 제출
page.inputs               # 폼 필드: 텍스트박스, 선택, 체크박스, 라디오
page.blocking_elements    # 모달, 쿠키 배너, 오버레이

# 전체 페이지 구조
page.semantic_tree        # 루트 SemanticNode — 전체 계층 트리

# AI 모델 컨텍스트를 위해 직렬화
page_dict = page.to_dict()
```

### SemanticNode — 트리의 요소

`semantic_tree`의 각 요소는 `SemanticNode`입니다:

```python
node = page.semantic_tree

node.node_id          # 타겟팅용 안정 ID: "n42"
node.tag              # HTML 태그: "button", "input", "a", "div", ...
node.role             # ARIA 역할: "button", "textbox", "link", "navigation", ...
node.name             # 접근성 이름 (텍스트 내용, aria-label 등)
node.value            # 입력 필드의 현재 값
node.xpath            # 이 요소까지의 XPath
node.is_interactive   # AI가 상호작용할 수 있는가? (클릭, 입력 등)
node.visible          # 페이지에 보이는가?
node.affordances      # 가능한 액션: ["clickable", "typeable", "submittable"]
node.attributes       # HTML 속성 dict
node.children         # 자식 SemanticNode 리스트

# 검색 메서드
buttons     = node.find_by_role("button")
interactive = node.find_interactive()
matches     = node.find_by_text("로그인", partial=True)
```

### 페이지 타입 분류

AN-Web은 자동으로 페이지를 시맨틱 타입으로 분류합니다:

| `page_type` | 설명 | 예시 |
|---|---|---|
| `login_form` | 로그인 / 인증 페이지 | GitHub 로그인 |
| `search` | 검색 입력 페이지 | Google 홈 |
| `search_results` | 검색 결과 목록 | Google 결과 |
| `listing` | 항목 리스트 (상품, 기사) | Amazon 카테고리 |
| `article` | 장문 콘텐츠 | 블로그 포스트 |
| `dashboard` | 대시보드 / 관리 패널 | 분석 페이지 |
| `form` | 일반 폼 | 문의 폼 |
| `error` | 에러 페이지 (404, 500) | Not Found |
| `generic` | 기타 | 랜딩 페이지 |

---

## MCP 서버 — an-web-mcp

Microsoft의 playwright-mcp 규약을 따르는 MCP 서버를 내장합니다 — Chromium 없이
같은 계약으로 동작합니다.

```bash
uvx --from 'an-web[mcp]' an-web-mcp
# Claude Code 등록:
claude mcp add an-web -- uvx --from 'an-web[mcp]' an-web-mcp
```

**도구 (13종)**: `browser_navigate`, `browser_navigate_back`, `browser_snapshot`,
`browser_click`, `browser_type`, `browser_select_option`, `browser_wait_for`,
`browser_evaluate`, `browser_extract`, `browser_fetch`,
`browser_network_requests`, `browser_network_request`, `browser_close`

- `browser_snapshot`은 접근성 트리 형식이며 인터랙티브 요소에 `[ref=nN]` 핸들이 붙습니다.
- 액션 도구는 `element`(설명) + `target`(ref 또는 CSS 셀렉터) 쌍을 받습니다.
- 모든 변경 도구는 새 스냅샷을 함께 반환합니다.
- 환경변수: `ANWEB_ALLOWED_DOMAINS`, `ANWEB_BLOCKED_DOMAINS`, `ANWEB_NAV_TIMEOUT`

---

## AI 모델 연동 (Claude / OpenAI)

### 준비된 도구 스키마

AN-Web은 Anthropic과 OpenAI 두 형식의 도구 스키마를 내장합니다. AI 모델에 직접 전달하세요:

```python
from an_web.api import TOOLS_FOR_CLAUDE, TOOLS_FOR_OPENAI

# Anthropic Claude
response = client.messages.create(
    model="claude-opus-4-6",
    tools=TOOLS_FOR_CLAUDE,             # ← 바로 연결
    messages=[{"role": "user", "content": "Google에서 'Python asyncio' 검색해줘"}],
)

# OpenAI / 호환 API
response = client.chat.completions.create(
    model="gpt-4o",
    tools=TOOLS_FOR_OPENAI,             # ← 바로 연결
    messages=[...],
)
```

### 완전한 에이전트 루프 예제

```python
import anthropic
from an_web import ANWebEngine
from an_web.api import ANWebToolInterface, TOOLS_FOR_CLAUDE

async def run_agent(task: str):
    client = anthropic.Anthropic()

    async with ANWebEngine() as engine:
        session = await engine.create_session()
        tools = ANWebToolInterface(session)

        messages = [{"role": "user", "content": task}]

        while True:
            response = client.messages.create(
                model="claude-opus-4-6",
                max_tokens=4096,
                tools=TOOLS_FOR_CLAUDE,
                messages=messages,
            )

            # 모델이 도구를 사용하려 하는지 확인
            if response.stop_reason != "tool_use":
                # 모델 완료 — 최종 답변 출력
                print(response.content[0].text)
                break

            # 각 도구 호출 실행
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = await tools.run({
                        "name": block.name,
                        "input": block.input,
                    })
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result),
                    })

            # 결과를 모델에 피드백
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
```

### 도구 스키마 유틸리티

```python
from an_web.api import get_tool_names, get_tool, get_schema

get_tool_names()        # ["navigate", "snapshot", "click", "type", ...]
get_tool("navigate")    # 하나의 도구에 대한 전체 스키마 dict
get_schema("claude")    # Anthropic 형식 전체 스키마
get_schema("openai")    # OpenAI 형식 전체 스키마
```

---

## 정책 & 안전

AN-Web에는 내장된 안전 제어가 있습니다. 모든 액션은 실행 전에 `PolicyChecker`의 검사를 받습니다.

### 빠른 프리셋

```python
from an_web.policy.rules import PolicyRules

# 허용적 (기본값) — 모든 도메인, 120 req/min
policy = PolicyRules.default()

# 엄격 — 30 req/min, navigate + submit에 승인 필요
policy = PolicyRules.strict()

# 샌드박스 — 특정 도메인만 허용
policy = PolicyRules.sandboxed(allowed_domains=["example.com", "api.example.com"])
```

### 커스텀 정책

```python
from an_web.policy.rules import PolicyRules, NavigationScope

policy = PolicyRules(
    allowed_domains=["example.com", "*.example.com"],
    denied_domains=["evil.com"],
    allowed_schemes=["https"],                        # http 차단
    navigation_scope=NavigationScope.SAME_DOMAIN,
    max_requests_per_minute=60,
    max_requests_per_hour=500,
    allow_form_submission=True,
    allow_file_download=False,
    require_approval_for=["submit"],                  # 폼 제출 시 사람 승인 필요
)

async with ANWebEngine() as engine:
    session = await engine.create_session(policy=policy)

    # 허용됨
    await session.navigate("https://example.com")             # ✓

    # 정책에 의해 차단 — {"status": "blocked", ...} 반환
    result = await session.act({"tool": "navigate", "url": "https://evil.com"})
    print(result["status"])  # "blocked"
```

### 샌드박스 리소스 제한

```python
from an_web.policy.sandbox import Sandbox, SandboxLimits

limits = SandboxLimits(
    max_requests=100,
    max_dom_nodes=10_000,
    max_navigations=20,
)

# 프리셋
SandboxLimits.default()     # 균형 잡힌 제한
SandboxLimits.strict()      # 엄격한 제한
SandboxLimits.unlimited()   # 제한 없음
```

### 승인 프로세스 (Human-in-the-Loop)

```python
from an_web.policy.approvals import ApprovalManager

approvals = ApprovalManager(auto_approve=False)

# 선택적으로 액션 승인
approvals.grant_once("submit")                              # 일회성
approvals.grant_pattern("navigate:https://example.com/*")   # 패턴 기반
```

---

## 트레이싱 & 리플레이

### 구조화된 로깅

```python
from an_web.tracing.logs import get_logger

logger = get_logger("my_agent", session_id=session.session_id)
logger.info("로그인 플로우 시작")

# 이후 로그에 액션 컨텍스트 태그
logger.action_context("login_step_1")

# 로그 조회
errors   = logger.get_errors()
all_logs = logger.get_all()
```

### 아티팩트 수집

모든 도구 호출은 자동으로 아티팩트를 기록합니다. 커스텀 아티팩트도 기록 가능:

```python
from an_web.tracing.artifacts import ArtifactCollector

collector = ArtifactCollector(session_id=session.session_id)
collector.record_action_trace("navigate", status="ok", url="https://example.com")
collector.record_js_exception("TypeError", stack="...", url="https://example.com")

# 조회
all_artifacts = collector.get_all()
js_errors     = collector.get_by_kind("js_exception")
summary       = collector.summary()
# → {"total": 5, "by_kind": {"action_trace": 3, "js_exception": 2}, ...}
```

6가지 아티팩트 종류: `action_trace`, `dom_snapshot`, `js_exception`, `network_request`, `screenshot`, `custom`.

### 리플레이 엔진

테스트, 디버깅, 회귀 테스트를 위해 액션 시퀀스 기록 및 재생:

```python
from an_web.tracing.replay import ReplayTrace, ReplayEngine

# 트레이스 구축
trace = ReplayTrace.new(session_id="test-1")
trace.add_step("navigate", {"url": "https://example.com"}, expected_status="ok")
trace.add_step("click",    {"target": "#btn"},              expected_status="ok")
trace.add_step("snapshot", {},                              expected_status="ok")

# 리플레이
replay_engine = ReplayEngine()
result = await replay_engine.replay_trace(trace, session)
print(result.succeeded)       # 모든 스텝 통과 시 True
print(result.failed_steps)    # 실패한 스텝 상세

# 직렬화 / 역직렬화
json_str = trace.to_json()
trace2   = ReplayTrace.from_json(json_str)
```

### ANWebToolInterface에서 내보내기

```python
tools = ANWebToolInterface(session)
await tools.navigate("https://example.com")
await tools.click("#btn")

# tool_history에서 자동 생성
trace_dict = tools.history_as_trace()
```

---

## JavaScript 실행 & SPA 지원

### 임베디드 V8 런타임

AN-Web은 포괄적인 호스트 Web API 레이어를 갖춘 V8 JavaScript 엔진(PyMiniRacer 경유)을 내장합니다:

```python
# 도구 인터페이스를 통해
result = await session.act({"tool": "eval_js", "script": "document.title"})
result = await session.act({
    "tool": "eval_js",
    "script": "Array.from(document.querySelectorAll('a')).map(a => a.href)"
})

# 직접 런타임 접근 (고급)
js = session.js_runtime
result = js.eval_safe("1 + 1")          # EvalResult(ok=True, value=2)
await js.drain_microtasks()              # Promise 체인 처리
```

### 호스트 Web API 지원 범위

호스트 API 레이어는 Python DOM ↔ V8를 브릿지하며, 브라우저 호환 API를 제공합니다:

| 카테고리 | API |
|---|---|
| **DOM** | `document.getElementById`, `querySelector`, `querySelectorAll`, `createElement`, `appendChild`, `removeChild`, `insertBefore`, `cloneNode`, `innerHTML`, `textContent`, `getAttribute`, `setAttribute`, `classList`, `style` |
| **이벤트** | `addEventListener`, `removeEventListener`, `dispatchEvent`, `Event`, `CustomEvent`, `MouseEvent`, `KeyboardEvent`, `FocusEvent`, `InputEvent`, `ErrorEvent` |
| **타이머** | `setTimeout`, `setInterval`, `clearTimeout`, `clearInterval`, `requestAnimationFrame` |
| **네트워크** | `fetch`, `XMLHttpRequest` |
| **스토리지** | `localStorage`, `sessionStorage` |
| **네비게이션** | `location`, `history.pushState`, `history.replaceState` |
| **인코딩** | `TextEncoder`, `TextDecoder`, `btoa`, `atob` |
| **기타** | `console`, `JSON`, `Promise`, `MutationObserver`, `IntersectionObserver`, `ResizeObserver`, `performance.now()`, `DOMParser`, `Blob`, `URL`, `URLSearchParams` |

### SPA 프레임워크 지원

AN-Web은 현대적인 싱글 페이지 애플리케이션을 렌더링할 수 있습니다:

- **Webpack 5** — 폴리필 번들에서 자동 런타임 추출
- **React 18** — 호스트 DOM API 브릿지를 통한 완전한 컴포넌트 렌더링
- **jQuery / Sizzle** — 호환 셀렉터 엔진 지원
- **`defer` 스크립트** — 올바른 HTML5 실행 순서 (인라인 먼저, 파싱 후 지연)
- **DOMContentLoaded / load** — 적절한 라이프사이클 이벤트 발생

---

## 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│                     AI Tool API                         │
│   dispatch_tool()  ANWebToolInterface  tool_schema.py   │
├───────────────┬──────────────────────┬──────────────────┤
│  정책 레이어  │   트레이싱 레이어    │  시맨틱 레이어   │
│  rules/sandbox│   artifacts/logs/    │  extractor/      │
│  checker/     │   replay             │  page_type/roles │
│  approvals    │                      │  affordances     │
├───────────────┴──────────────────────┴──────────────────┤
│                   액션 레이어                            │
│  navigate  click  type  submit  extract  scroll  eval_js│
├─────────────────────────────────────────────────────────┤
│              실행 플레인                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐  │
│  │ DOM Core │  │ JS Bridge│  │ Network  │  │Layout  │  │
│  │ nodes/   │  │ V8       │  │ httpx +  │  │Lite    │  │
│  │ selectors│  │ host_api │  │ cookies  │  │hit_test│  │
│  └──────────┘  └──────────┘  └──────────┘  └────────┘  │
├─────────────────────────────────────────────────────────┤
│              제어 플레인                                  │
│    ANWebEngine   Session   Scheduler   SnapshotManager  │
└─────────────────────────────────────────────────────────┘
```

### 패키지 구조

```
an_web/
├── core/         # ANWebEngine, Session, Scheduler, SnapshotManager, PageState
├── dom/          # Node/Element/Document, CSS 셀렉터, Mutation, Semantics
├── js/           # V8 브릿지, JSRuntime, 호스트 Web API (DOM ↔ V8 브릿지)
├── net/          # NetworkClient (httpx), CookieJar, ResourceLoader
├── actions/      # navigate, click, type, submit, extract, scroll, eval_js, wait_for
├── layout/       # Visibility, flow 추론, hit-testing, LayoutEngine
├── semantic/     # SemanticExtractor, page_type 분류기, roles, affordances
├── policy/       # PolicyRules, PolicyChecker, Sandbox, ApprovalManager
├── tracing/      # ArtifactCollector, StructuredLogger, ReplayEngine
├── browser/      # HTML 파서 (selectolax + html5lib)
└── api/          # dispatch_tool, ANWebToolInterface, Pydantic 모델, 도구 스키마
```

---

## 예제

### 로그인 플로우

```python
from an_web import ANWebEngine
from an_web.api import ANWebToolInterface

async def login():
    async with ANWebEngine() as engine:
        session = await engine.create_session()
        tools = ANWebToolInterface(session)

        await tools.navigate("https://example.com/login")

        # 페이지 확인
        snap = await tools.snapshot()
        print(f"페이지: {snap['page_type']}")  # "login_form"

        # 입력 및 제출
        await tools.type("#email", "user@example.com")
        await tools.type("#password", "password123")
        await tools.click({"by": "role", "role": "button", "text": "로그인"})

        # 확인
        snap = await tools.snapshot()
        print(f"로그인 완료: {snap['url']}")
```

### 웹 스크래핑

```python
from an_web import ANWebEngine

async def scrape_headlines():
    async with ANWebEngine() as engine:
        session = await engine.create_session()
        await session.navigate("https://news.ycombinator.com")

        result = await session.act({
            "tool": "extract",
            "query": "span.titleline > a"
        })

        for item in result["effects"]["results"]:
            print(item["text"])
```

### 멀티 세션 병렬 스크래핑

```python
import asyncio
from an_web import ANWebEngine

async def scrape_url(engine, url):
    session = await engine.create_session()
    await session.navigate(url)
    result = await session.act({"tool": "extract", "query": "h1"})
    await session.close()
    return result["effects"]["results"]

async def main():
    async with ANWebEngine() as engine:
        urls = [
            "https://example.com",
            "https://httpbin.org/html",
            "https://www.python.org",
        ]
        results = await asyncio.gather(*(scrape_url(engine, u) for u in urls))
        for url, data in zip(urls, results):
            print(f"{url}: {data}")
```

### SPA 렌더링 (React / Webpack)

```python
from an_web import ANWebEngine

async def render_spa():
    async with ANWebEngine() as engine:
        session = await engine.create_session()

        # AN-Web이 처리: webpack 런타임, defer 스크립트, React 렌더링
        await session.navigate("https://www.naver.com")
        page = await session.snapshot()

        print(f"제목: {page.title}")
        print(f"요소 수: {len(page.semantic_tree.children)}")

        # 렌더링된 콘텐츠 추출
        result = await session.act({"tool": "extract", "query": "a"})
        for link in result["effects"]["results"][:5]:
            print(f"  {link['text']}: {link.get('href', '')}")
```

### 샌드박스 세션 (정책 적용)

```python
from an_web import ANWebEngine
from an_web.policy.rules import PolicyRules

async def safe_browse():
    policy = PolicyRules.sandboxed(allowed_domains=["example.com"])

    async with ANWebEngine() as engine:
        session = await engine.create_session(policy=policy)

        # 허용됨
        await session.navigate("https://example.com")

        # 정책에 의해 차단
        result = await session.act({"tool": "navigate", "url": "https://other.com"})
        print(result["status"])  # "blocked"
```

---

## 테스트

```bash
# 전체 테스트 실행 (1565개)
pytest

# 커버리지 포함
pytest --cov=an_web --cov-report=term-missing

# 특정 모듈
pytest tests/unit/dom/ -v

# 통합 테스트
pytest tests/integration/ -v
```

**테스트 스위트 (1565개 테스트):**

| 스위트 | 수량 | 대상 |
|---|---|---|
| DOM / 셀렉터 / 파서 | ~330 | 코어 DOM 트리, CSS 셀렉터, HTML 파싱 |
| JS 브릿지 + 런타임 + 호스트 API | ~300 | V8 평가, Promise 드레인, 호스트 Web API |
| 스케줄러 / 세션 / 엔진 | ~130 | 이벤트 루프, 내비게이션, 스토리지, 스냅샷 |
| 액션 | ~190 | click, type, submit, extract, scroll, eval_js |
| 레이아웃 | ~160 | Visibility, flow, hit-testing |
| 정책 + 트레이싱 + API | ~330 | 규칙, 샌드박스, 아티팩트, 로그, 리플레이, dispatch |
| 통합 (E2E) | ~46 | 로그인 플로우, 검색 & 추출, 멀티 세션 |

---

## API 레퍼런스 요약

### 코어

| 클래스 | 임포트 | 설명 |
|---|---|---|
| `ANWebEngine` | `from an_web import ANWebEngine` | 최상위 팩토리. 비동기 컨텍스트 매니저. |
| `Session` | `engine.create_session()`으로 생성 | 브라우저 탭. 쿠키, 스토리지, JS 런타임 소유. |

### Session 메서드

| 메서드 | 반환 타입 | 설명 |
|---|---|---|
| `navigate(url, timeout=None)` | `dict` | URL 로드, DOM 구축, JS 실행, 안정화 (기본 15초 예산) |
| `snapshot()` | `PageSemantics` | 구조화된 시맨틱 페이지 상태 (객체) |
| `act(tool_call)` | `dict` | 13개 도구 중 하나 실행 |
| `execute_script(js)` | `Any` | JavaScript 직접 실행 |
| `back()` | `dict` | 이전 URL로 이동 |
| `close()` | `None` | 리소스 해제 |

### API 레이어

| 심볼 | 임포트 | 설명 |
|---|---|---|
| `ANWebToolInterface` | `from an_web.api import ANWebToolInterface` | 타입 지정 도구 헬퍼 메서드 |
| `dispatch_tool()` | `from an_web.api import dispatch_tool` | 저수준 도구 디스패치 |
| `TOOLS_FOR_CLAUDE` | `from an_web.api import TOOLS_FOR_CLAUDE` | Anthropic 도구 스키마 |
| `TOOLS_FOR_OPENAI` | `from an_web.api import TOOLS_FOR_OPENAI` | OpenAI 도구 스키마 |
| `get_tool(name)` | `from an_web.api import get_tool` | 개별 도구 스키마 조회 |
| `get_tool_names()` | `from an_web.api import get_tool_names` | 모든 도구 이름 목록 |

### 정책

| 클래스 | 임포트 | 설명 |
|---|---|---|
| `PolicyRules` | `from an_web.policy.rules import PolicyRules` | 도메인/속도/범위 규칙 |
| `PolicyRules.default()` | | 허용적 기본값 (120 req/min) |
| `PolicyRules.strict()` | | 보수적 (30 req/min, 승인 필요) |
| `PolicyRules.sandboxed(domains)` | | 도메인 잠금 |
| `Sandbox` | `from an_web.policy.sandbox import Sandbox` | 리소스 제한 강제 |
| `ApprovalManager` | `from an_web.policy.approvals import ApprovalManager` | Human-in-the-loop |

### 데이터 모델

| 클래스 | 설명 |
|---|---|
| `PageSemantics` | 전체 페이지 상태: page_type, title, url, primary_actions, inputs, blocking_elements, semantic_tree |
| `SemanticNode` | 시맨틱 트리의 요소: node_id, tag, role, name, value, is_interactive, visible, affordances, children |
| `ActionResult` | 액션 결과: status, action, effects, error, recommended_next_actions |

---

## 알려진 한계

AN-Web은 완전한 브라우저 충실도를 무게·속도와 맞바꿉니다. 선택 전에 트레이드오프를 확인하고, Playwright가 이기는 영역에서는 Playwright를 쓰십시오:

| 영역 | 상태 | 상세 |
|---|---|---|
| **iframe** | ❌ 미실행 | 프레임 요소는 보이지만 내부 문서는 로드/스크립팅되지 않음. 결제·로그인 플로우 다수가 iframe 기반 — 해당 플로우는 Playwright 사용 권장. |
| **WebSocket** | ❌ 부재 | WS 스트리밍(라이브 대시보드, 채팅) 콘텐츠 미수신. `fetch`/XHR은 완전 브리지됨. |
| **포인터 리얼리즘** | ⚠️ 부분 | `click`/`type`/`select`/`scroll`/`submit`은 시맨틱 이벤트. `hover`, 드래그앤드롭, 저수준 키 코드는 없음. |
| **스크린샷** | 🚫 의도적 부재 | AN-Web은 픽셀이 아닌 구조화된 증거를 생산. 시각 검증이 필요하면 픽셀 브라우저 사용. |
| **안티봇 월** | ⚠️ 프로파일 상이 | AN-Web은 일반 HTTP 클라이언트 TLS 지문 — 일부 월은 이를 차단하고(medium/npmjs → 403, amazon → 202 인터스티셜), 다른 월은 headless Chromium을 차단(stackoverflow.com에서 Cloudflare가 Playwright에 403, AN-Web은 통과). 대상별 테스트 필수. |
| **조용한 미렌더** | ⚠️ 일부 포털 | 스크립트가 에러 없이 전부 돌아도 콘텐츠를 커밋하지 않는 JS 셸 사이트 존재(daum.net, youtube.com 부분). 반대로 JS가 SSR 콘텐츠를 *삭제*하면 SSR 보존 폴백이 사전-JS DOM을 복원하고 navigate effects에 `dom_restored`를 플래그합니다. |
| **React 하이드레이션** | ⚠️ 부분 | SSR 주석 마커 보존(v0.8.0), 클라이언트 fetch 데이터는 정확히 1회 스냅샷 도달. 다만 일부 Next.js 페이지에서 *정적* 섹션이 중복 표시될 수 있음. |
| **스크립트 헤비 settle** | ⚠️ 비용 | Wikipedia류 사이트는 settle 루프에서 수 초의 JS 실행. `session.navigate(url, timeout=3)`으로 상한 설정 — 서버 렌더 콘텐츠는 그 시점에 이미 완성되어 있음. |

**경험칙:** *읽기·추출·입력·제출* 에이전트 루프 → AN-Web. *hover·드래그·스크린샷·iframe 결제·WS 스트림* → Playwright.

---

## 라이선스

Apache-2.0

---

## 기여하기

```bash
git clone https://github.com/CocoRoF/an-web
cd an-web
pip install -e ".[dev]"
pytest                    # 1565개 테스트 모두 통과해야 함
ruff check an_web/        # 린팅
mypy an_web/              # 타입 체크
```
