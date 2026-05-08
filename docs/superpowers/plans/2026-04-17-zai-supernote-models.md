# Z.AI Supernote Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `paia-supernote` use the validated Z.AI Coding Plan model pair by default: `glm-4.5v` for OCR and `glm-5.1` for Sam/task-page text work.

**Architecture:** Add a `zai` backend to the reader for OCR plus text classification, add a matching `zai` rewrite path to the task curator, and update service config defaults/wiring so the service can run without Anthropic for the Sam path. Keep `anthropic` and `ollama` as explicit backends.

**Tech Stack:** Python, `httpx`, existing `anthropic` client, existing `pytest` test suite, Z.AI Coding Plan `chat/completions` endpoint

---

### Task 1: Add Red Tests For Z.AI Defaults And API Calls

**Files:**
- Modify: `tests/test_main.py`
- Modify: `tests/test_reader.py`
- Modify: `tests/test_task_curator.py`

- [ ] **Step 1: Write the failing config-default test**

```python
def test_defaults_when_no_file(self, tmp_path: Path) -> None:
    config = load_config(config_path=tmp_path / "nonexistent.toml")
    assert config["vision_backend"] == "zai"
    assert config["rewrite_backend"] == "zai"
    assert config["zai_vision_model"] == "glm-4.5v"
    assert config["zai_text_model"] == "glm-5.1"
```

- [ ] **Step 2: Write the failing reader Z.AI OCR/classification tests**

```python
@pytest.mark.asyncio
@patch("paia_supernote.reader.httpx.AsyncClient")
async def test_transcribe_page_zai_uses_coding_endpoint(...):
    ...

@pytest.mark.asyncio
@patch("paia_supernote.reader.httpx.AsyncClient")
async def test_classification_returns_snippet_via_zai_chat(...):
    ...
```

- [ ] **Step 3: Write the failing task-curator rewrite test**

```python
@pytest.mark.asyncio
@patch("paia_supernote.task_curator.httpx.AsyncClient")
async def test_task_curator_reorganize_with_llm_uses_zai_backend(...):
    ...
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_main.py tests/test_reader.py tests/test_task_curator.py -q`
Expected: FAIL because the current code does not expose Z.AI defaults or Z.AI request paths.

- [ ] **Step 5: Commit**

```bash
git add tests/test_main.py tests/test_reader.py tests/test_task_curator.py
git commit -m "test: cover z.ai supernote defaults and model calls"
```

### Task 2: Implement Reader Z.AI OCR And Text Classification

**Files:**
- Modify: `src/paia_supernote/reader.py`
- Test: `tests/test_reader.py`

- [ ] **Step 1: Add backend/config fields and a shared Z.AI chat helper**

```python
def __init__(
    self,
    anthropic_client: Optional[anthropic.AsyncAnthropic] = None,
    vision_backend: str = "zai",
    ollama_model: str = "qwen2.5vl:7b",
    ollama_url: str = "http://localhost:11434",
    zai_api_key: Optional[str] = None,
    zai_base_url: str = "https://api.z.ai/api/coding/paas/v4",
    zai_vision_model: str = "glm-4.5v",
    zai_text_model: str = "glm-5.1",
):
    self.zai_api_key = zai_api_key or os.environ.get("ZAI_API_KEY")
    self.zai_base_url = zai_base_url.rstrip("/")
    self.zai_vision_model = zai_vision_model
    self.zai_text_model = zai_text_model
```

- [ ] **Step 2: Add the Z.AI OCR implementation**

```python
async def _transcribe_page_zai(self, img_b64: str) -> Optional[str]:
    return await self._zai_chat_completion(
        model=self.zai_vision_model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                {"type": "text", "text": self._TRANSCRIBE_PROMPT},
            ],
        }],
        max_tokens=2048,
    )
```

- [ ] **Step 3: Route classification through Z.AI when using the Z.AI backend**

```python
if self.vision_backend == "zai":
    classification = await self._zai_chat_completion(
        model=self.zai_text_model,
        messages=[{"role": "user", "content": classification_prompt}],
        max_tokens=16,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_reader.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/paia_supernote/reader.py tests/test_reader.py
git commit -m "feat: add z.ai reader backend"
```

### Task 3: Implement Task Curator Z.AI Rewrite Path

**Files:**
- Modify: `src/paia_supernote/task_curator.py`
- Test: `tests/test_task_curator.py`

- [ ] **Step 1: Add rewrite backend and Z.AI config fields**

```python
def __init__(
    ...,
    rewrite_backend: str = "zai",
    zai_api_key: Optional[str] = None,
    zai_base_url: str = "https://api.z.ai/api/coding/paas/v4",
    zai_text_model: str = "glm-5.1",
):
    self.rewrite_backend = rewrite_backend
    self.zai_api_key = zai_api_key or os.environ.get("ZAI_API_KEY")
```

- [ ] **Step 2: Add a Z.AI rewrite helper and switch `_reorganize_with_llm`**

```python
if self.rewrite_backend == "zai":
    return await self._reorganize_with_zai(current_text)
```

- [ ] **Step 3: Preserve the Anthropic path as the explicit fallback**

```python
response = await self.client.messages.create(...)
return response.content[0].text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_task_curator.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/paia_supernote/task_curator.py tests/test_task_curator.py
git commit -m "feat: switch task curator rewrite to z.ai"
```

### Task 4: Update Service Defaults And Wiring

**Files:**
- Modify: `src/paia_supernote/main.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Update defaults and config loading**

```python
DEFAULT_CONFIG = {
    "vision_backend": "zai",
    "rewrite_backend": "zai",
    "ollama_model": "qwen2.5vl:7b",
    "zai_base_url": "https://api.z.ai/api/coding/paas/v4",
    "zai_vision_model": "glm-4.5v",
    "zai_text_model": "glm-5.1",
}
```

- [ ] **Step 2: Pass Z.AI config into the reader and task curator**

```python
self.reader = SupernoteReader(...)
self.task_curator = TaskCurator(..., rewrite_backend=self.config["rewrite_backend"], ...)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `uv run pytest tests/test_main.py tests/test_reader.py tests/test_task_curator.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/paia_supernote/main.py tests/test_main.py
git commit -m "feat: default supernote to z.ai models"
```

### Task 5: Final Verification

**Files:**
- Modify: none

- [ ] **Step 1: Run the targeted verification suite**

Run: `uv run pytest tests/test_main.py tests/test_reader.py tests/test_task_curator.py -q`
Expected: PASS

- [ ] **Step 2: Run a live smoke check on `Quick.note` pages 19-22**

Run:

```bash
uv run python run_user_board.py
```

Expected: the Supernote path can read task pages with the Z.AI defaults and Sam curation no longer depends on Anthropic.

- [ ] **Step 3: Commit**

```bash
git status --short
```

Expected: only the intended Supernote files and docs remain changed.
