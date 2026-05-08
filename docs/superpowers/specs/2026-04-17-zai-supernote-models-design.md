# Z.AI Supernote Models Design

**Date:** 2026-04-17

## Goal

Switch the Supernote service off the Anthropic-dependent Sam path and onto the Z.AI Coding Plan models that were validated live against `Quick.note` pages 19-22.

## Approved Model Pair

- OCR: `glm-4.5v`
- Rewrite / text reasoning: `glm-5.1`

## Design

The reader gains a `zai` backend that calls the Z.AI Coding Plan `chat/completions` endpoint for page transcription. When the reader is using the `zai` backend, snippet/general classification also uses Z.AI text chat so the service does not silently retain an Anthropic dependency.

Task-page curation switches from a hard-coded Anthropic call to a backend-driven text rewrite path. The default service config is updated so a normal `paia-supernote` run uses the validated Z.AI pair without extra per-run flags, while keeping `anthropic` and `ollama` available as explicit alternatives.

## Config Surface

- `vision_backend = "zai"`
- `rewrite_backend = "zai"`
- `zai_base_url = "https://api.z.ai/api/coding/paas/v4"`
- `zai_vision_model = "glm-4.5v"`
- `zai_text_model = "glm-5.1"`
- `ollama_model = "qwen2.5vl:7b"` as the local fallback

`ZAI_API_KEY` remains the credential source for Z.AI requests.

## Validation

- Reader unit tests cover Z.AI OCR request/response handling and Z.AI snippet classification.
- Task curator unit tests cover Z.AI rewrite request/response handling.
- Config tests confirm the new defaults.
- Targeted pytest passes for `tests/test_main.py`, `tests/test_reader.py`, and `tests/test_task_curator.py`.
