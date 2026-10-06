#!/usr/bin/env python3
"""OCR gate eval: local qwen3-vl:8b vs hosted gpt-5.6-luna (AtlasCloud
transport) on real Supernote pages.

Spec §4.4.2 gate (local becomes primary only if ALL hold):
  1. local mean WER <= 10%
  2. no page-class mean WER > 25%
  3. local mean WER within +5 points of gpt-5.6-luna on the same set

Usage:
  uv run scripts/ocr_eval.py --pages eval-corpus/ --engines local,luna \
      --report docs/ocr-eval-2026-10.md

Corpus layout under --pages DIR:
  DIR/*.png            page images (any subdir depth 1)
  DIR/truth/*.txt      hand-corrected transcripts (same basename as PNG)
  DIR/manifest.tsv     optional: "<basename>\\t<class>" per line; missing -> untagged
  DIR/raw/<engine>/    cached raw responses (reused when prompt hash matches)

Exit codes: 0 gate PASS, 1 gate FAIL, 2 corpus/engine errors prevented a verdict.

Normalization: gate WER is computed on normalized tokens (lowercased,
punctuation stripped) - standard OCR/ASR practice; strict WER (exact tokens,
punctuation attached) is reported alongside for transparency.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import string
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from paia_supernote.ocr_contract import TRANSCRIPTION_PROMPT

# Engines -----------------------------------------------------------------

LOCAL_BASE_URL_DEFAULT = "http://127.0.0.1:11434/v1"
LOCAL_MODEL = "qwen3-vl:8b"
# Owner ruling 2026-10-06: the OpenAI API workspace has no credits and pi
# auth covers pi-side use only - the hosted comparator rides AtlasCloud's
# OpenAI-compatible endpoint instead (same gpt-5.6-luna model, AtlasCloud
# slug openai/gpt-5.6-luna; verified live 2026-10-06, ~$0.004/page).
LUNA_BASE_URL_DEFAULT = os.environ.get(
    "LUNA_BASE_URL", "https://api.atlascloud.ai/v1")
LUNA_MODEL = "openai/gpt-5.6-luna"

RETRIES = 2  # retries per engine per page (3 attempts total)
RETRY_BACKOFF_S = (2.0, 8.0)
MAX_TOKENS = 4096
REQUEST_TIMEOUT_S = float(os.environ.get("OCR_EVAL_TIMEOUT_S", "300"))

GATE_MEAN_WER = 0.10
GATE_CLASS_WER = 0.25
GATE_DELTA_VS_LUNA = 0.05

_PUNCT_TABLE = str.maketrans("", "", string.punctuation)


# Metrics (pure; unit-tested) ----------------------------------------------


def normalize_text(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    lowered = text.lower().translate(_PUNCT_TABLE)
    return " ".join(lowered.split())


def _levenshtein(a: list[str] | str, b: list[str] | str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(
                min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            )
        prev = cur
    return prev[-1]


def wer(ref: str, hyp: str, *, normalized: bool = True) -> float:
    """Word error rate = Levenshtein(word lists) / len(ref words).

    Returns 1.0 when ref is empty and hyp is not; 0.0 when both empty.
    """
    r, h = (ref, hyp)
    if normalized:
        r, h = normalize_text(r), normalize_text(h)
    r_words, h_words = r.split(), h.split()
    if not r_words:
        return 0.0 if not h_words else 1.0
    return _levenshtein(r_words, h_words) / len(r_words)


def cer(ref: str, hyp: str, *, normalized: bool = True) -> float:
    """Character error rate over normalized text (whitespace removed)."""
    r, h = (ref, hyp)
    if normalized:
        r, h = normalize_text(r), normalize_text(h)
    r_chars = list(r.replace(" ", ""))
    h_chars = list(h.replace(" ", ""))
    if not r_chars:
        return 0.0 if not h_chars else 1.0
    return _levenshtein(r_chars, h_chars) / len(r_chars)


# Corpus -------------------------------------------------------------------


@dataclass
class Page:
    name: str  # basename without suffix
    image: Path
    truth: Path
    page_class: str = "untagged"


def load_corpus(pages_dir: Path) -> tuple[list[Page], list[str]]:
    """Load PNG pages + truth transcripts + class manifest.

    Returns (pages, problems). Pages missing truth are reported as problems
    and excluded - a page without ground truth cannot be scored.
    """
    manifest: dict[str, str] = {}
    manifest_path = pages_dir / "manifest.tsv"
    if manifest_path.exists():
        for line in manifest_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                manifest[parts[0].strip()] = parts[1].strip()

    problems: list[str] = []
    pages: list[Page] = []
    pngs = sorted(pages_dir.glob("*.png"))
    for png in pngs:
        name = png.stem
        truth = pages_dir / "truth" / f"{name}.txt"
        if not truth.exists():
            problems.append(f"{name}: no truth transcript at {truth}")
            continue
        pages.append(Page(name=name, image=png, truth=truth,
                          page_class=manifest.get(name, "untagged")))
    if not pngs:
        problems.append(f"no *.png pages found under {pages_dir}")
    return pages, problems


# Engine calls --------------------------------------------------------------


def _data_url(png: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()


def _messages(png: Path) -> list[dict]:
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": TRANSCRIPTION_PROMPT},
            {"type": "image_url", "image_url": {"url": _data_url(png)}},
        ],
    }]


def _prompt_hash() -> str:
    return hashlib.sha256(TRANSCRIPTION_PROMPT.encode()).hexdigest()[:12]


def _cached(engine: str, page: Page, pages_dir: Path) -> str | None:
    cache = pages_dir / "raw" / engine / f"{page.name}.json"
    if not cache.exists():
        return None
    try:
        payload = json.loads(cache.read_text())
    except json.JSONDecodeError:
        return None
    if payload.get("prompt_hash") != _prompt_hash():
        return None  # prompt changed - stale
    text = payload.get("text")
    return text if isinstance(text, str) else None


def _store(engine: str, page: Page, pages_dir: Path, text: str,
           elapsed_s: float) -> None:
    cache = pages_dir / "raw" / engine / f"{page.name}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({
        "prompt_hash": _prompt_hash(),
        "engine": engine,
        "model": LOCAL_MODEL if engine == "local" else LUNA_MODEL,
        "text": text,
        "elapsed_s": round(elapsed_s, 2),
    }, indent=2))


def _local_body(png: Path) -> dict:
    """Native ollama /api/chat body for qwen3-vl.

    Thinking CANNOT be disabled by parameter on ollama 0.35.x for this
    model: ``think:false``, the ``/no_think`` prompt switch, and
    ``chat_template_kwargs`` are all ignored (verified 2026-10-06) - the
    model then burns its whole budget in a degenerate thinking loop and
    returns empty content. The assistant-prefill of a CLOSED empty think
    block ends thinking at template level: generation starts at content
    directly (~8s/page vs ~377s, done=stop).
    """
    return {
        "model": LOCAL_MODEL,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": TRANSCRIPTION_PROMPT,
                "images": [base64.b64encode(png.read_bytes()).decode()],
            },
            # Closed think block = no thinking (see docstring).
            {"role": "assistant", "content": "<think>\n\n</think>\n\n"},
        ],
        "options": {"temperature": 0, "num_predict": MAX_TOKENS},
    }


def _luna_body(png: Path) -> dict:
    return {
        "model": LUNA_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": TRANSCRIPTION_PROMPT},
                {"type": "image_url",
                 "image_url": {"url": _data_url(png)}},
            ],
        }],
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
    }


def _endpoint(engine: str) -> tuple[str, dict, dict[str, str]]:
    """Returns (url, request_body_builder, headers) for the engine."""
    if engine == "local":
        return "http://127.0.0.1:11434/api/chat", _local_body, {}
    api_key = (os.environ.get("ATLASCLOUD_API_KEY")
               or os.environ.get("GROK_AURORA_ATLASCLOUD_API_KEY", ""))
    if not api_key:
        raise SystemExit("luna engine requires ATLASCLOUD_API_KEY")
    return (LUNA_BASE_URL_DEFAULT + "/chat/completions", _luna_body,
            {"Authorization": f"Bearer {api_key}",
             # AtlasCloud fronts with Cloudflare; the default python UA is
             # bot-blocked (CF 1010). SDK-style UA verified OK 2026-10-06.
             "User-Agent": "OpenAI/Python 1.54.4"})


def transcribe(engine: str, page: Page, pages_dir: Path) -> tuple[str, float, str]:
    """One page through one engine. Returns (text, elapsed_s, error)."""
    cached = _cached(engine, page, pages_dir)
    if cached is not None:
        return cached, 0.0, ""

    url, body_builder, headers = _endpoint(engine)
    body = body_builder(page.image)
    last_error = ""
    for attempt in range(RETRIES + 1):
        started = time.monotonic()
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT_S) as client:
                resp = client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
            if engine == "local":
                text = payload.get("message", {}).get("content") or ""
            else:
                text = payload["choices"][0]["message"]["content"] or ""
            elapsed = time.monotonic() - started
            _store(engine, page, pages_dir, text, elapsed)
            return text.strip(), elapsed, ""
        except Exception as exc:  # noqa: BLE001 - recorded, never silent
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < RETRIES:
                time.sleep(RETRY_BACKOFF_S[min(attempt, len(RETRY_BACKOFF_S) - 1)])
    return "", 0.0, last_error


# Report --------------------------------------------------------------------


@dataclass
class EngineStats:
    name: str
    mean_wer: float = 0.0
    mean_cer: float = 0.0
    strict_wer: float = 0.0
    errors: int = 0
    per_class: dict[str, float] = field(default_factory=dict)


@dataclass
class GateResult:
    mean_ok: bool
    class_ok: bool
    delta_ok: bool
    local_mean_wer: float
    luna_mean_wer: float | None
    worst_class: tuple[str, float] | None

    @property
    def passed(self) -> bool:
        return self.mean_ok and self.class_ok and self.delta_ok


def gate_verdict(local: EngineStats, luna: EngineStats | None) -> GateResult:
    mean_ok = local.mean_wer <= GATE_MEAN_WER
    worst: tuple[str, float] | None = None
    for cls, wer_value in local.per_class.items():
        if worst is None or wer_value > worst[1]:
            worst = (cls, wer_value)
    class_ok = worst is None or worst[1] <= GATE_CLASS_WER
    if luna is None:
        return GateResult(mean_ok, class_ok, True, local.mean_wer, None, worst)
    delta_ok = (local.mean_wer - luna.mean_wer) <= GATE_DELTA_VS_LUNA
    return GateResult(mean_ok, class_ok, delta_ok,
                      local.mean_wer, luna.mean_wer, worst)


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def build_report(pages: list[Page], rows: dict[str, dict[str, dict]],
                 stats: dict[str, EngineStats], gate: GateResult | None,
                 engines: list[str]) -> str:
    lines: list[str] = []
    lines.append("# Supernote OCR gate eval - local qwen3-vl:8b vs gpt-5.6-luna")
    lines.append("")
    lines.append(f"- Date: {time.strftime('%Y-%m-%d %H:%M%z')}")
    lines.append(f"- Pages: {len(pages)} | Engines: {', '.join(engines)}")
    lines.append(f"- Prompt hash: {_prompt_hash()} "
                 f"(TRANSCRIPTION_PROMPT in `ocr_contract.py`)")
    lines.append(f"- Gate: mean WER <= {_fmt_pct(GATE_MEAN_WER)}, "
                 f"no class > {_fmt_pct(GATE_CLASS_WER)}, "
                 f"local within +{_fmt_pct(GATE_DELTA_VS_LUNA)} of luna "
                 f"(normalized WER; strict WER reported alongside)")
    lines.append("")
    for engine in engines:
        s = stats[engine]
        lines.append(f"## {engine}: mean WER {_fmt_pct(s.mean_wer)}, "
                     f"mean CER {_fmt_pct(s.mean_cer)}, "
                     f"strict WER {_fmt_pct(s.strict_wer)}, "
                     f"errors {s.errors}/{len(pages)}")
    lines.append("")
    lines.append("## Per-page results (normalized WER / CER)")
    lines.append("")
    header = "| page | class | " + " | ".join(
        f"{e} WER | {e} CER" for e in engines) + " |"
    lines.append(header)
    lines.append("|" + "---|" * (1 + 1 + 2 * len(engines)))
    for page in pages:
        cells = [page.name, page.page_class]
        for engine in engines:
            row = rows[engine].get(page.name, {})
            if "error" in row and row["error"]:
                cells += ["ERROR", row["error"][:40]]
            else:
                cells += [_fmt_pct(row.get("wer", 0.0)),
                          _fmt_pct(row.get("cer", 0.0))]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    for engine in engines:
        s = stats[engine]
        if not s.per_class:
            continue
        lines.append(f"## {engine} per-class mean WER")
        lines.append("")
        lines.append("| class | mean WER |")
        lines.append("|---|---|")
        for cls in sorted(s.per_class):
            lines.append(f"| {cls} | {_fmt_pct(s.per_class[cls])} |")
        lines.append("")
    if gate is not None:
        lines.append("## Gate verdict: "
                     + ("**PASS**" if gate.passed else "**FAIL**"))
        lines.append("")
        lines.append(f"1. local mean WER {_fmt_pct(gate.local_mean_wer)} "
                     f"<= {_fmt_pct(GATE_MEAN_WER)}: "
                     f"{'PASS' if gate.mean_ok else 'FAIL'}")
        if gate.worst_class:
            cls, value = gate.worst_class
            lines.append(f"2. worst class {cls} {_fmt_pct(value)} "
                         f"<= {_fmt_pct(GATE_CLASS_WER)}: "
                         f"{'PASS' if gate.class_ok else 'FAIL'}")
        else:
            lines.append(f"2. worst class <= {_fmt_pct(GATE_CLASS_WER)}: "
                         "PASS (no classes)")
        if gate.luna_mean_wer is not None:
            delta = gate.local_mean_wer - gate.luna_mean_wer
            lines.append(f"3. local vs luna delta {delta * 100:+.2f} pts "
                         f"(local {_fmt_pct(gate.local_mean_wer)}, luna "
                         f"{_fmt_pct(gate.luna_mean_wer)}, budget "
                         f"+{_fmt_pct(GATE_DELTA_VS_LUNA)}): "
                         f"{'PASS' if gate.delta_ok else 'FAIL'}")
        lines.append("")
    return "\n".join(lines) + "\n"


# Main -----------------------------------------------------------------------


def run_eval(pages_dir: Path, engines: list[str], limit: int | None,
             report_path: Path) -> int:
    pages, problems = load_corpus(pages_dir)
    if limit:
        pages = pages[:limit]
    hard_errors = [p for p in problems if "no truth" in p or "no *.png" in p]
    if not pages:
        for p in problems:
            print(f"ERROR: {p}", file=sys.stderr)
        return 2
    for p in problems:
        print(f"WARN: {p}", file=sys.stderr)

    rows: dict[str, dict[str, dict]] = {e: {} for e in engines}
    stats: dict[str, EngineStats] = {e: EngineStats(e) for e in engines}

    for engine in engines:
        for page in pages:
            truth = page.truth.read_text()
            text, elapsed, error = transcribe(engine, page, pages_dir)
            row: dict = {"elapsed_s": round(elapsed, 2)}
            if error:
                row["error"] = error
                row["wer"] = row["cer"] = row["wer_strict"] = 1.0
                print(f"[{engine}] {page.name}: ERROR {error}", file=sys.stderr)
            else:
                row["wer"] = wer(truth, text)
                row["cer"] = cer(truth, text)
                row["wer_strict"] = wer(truth, text, normalized=False)
                print(f"[{engine}] {page.name} ({page.page_class}): "
                      f"WER {_fmt_pct(row['wer'])} "
                      f"({row['elapsed_s']}s)")
            rows[engine][page.name] = row

    for engine in engines:
        engine_rows = rows[engine]
        page_rows = [engine_rows[p.name] for p in pages]
        s = stats[engine]
        s.mean_wer = sum(r["wer"] for r in page_rows) / len(page_rows)
        s.mean_cer = sum(r["cer"] for r in page_rows) / len(page_rows)
        s.strict_wer = sum(r["wer_strict"] for r in page_rows) / len(page_rows)
        s.errors = sum(1 for r in page_rows if r.get("error"))
        by_class: dict[str, list[float]] = {}
        for page in pages:
            by_class.setdefault(page.page_class, []).append(
                engine_rows[page.name]["wer"])
        s.per_class = {c: sum(v) / len(v) for c, v in by_class.items()}

    gate = gate_verdict(stats["local"], stats.get("luna")) \
        if "local" in stats else None
    report = build_report(pages, rows, stats, gate, engines)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report)
    print(f"\nReport written: {report_path}")
    if gate is not None:
        print(f"Gate: {'PASS' if gate.passed else 'FAIL'} "
              f"(local mean WER {_fmt_pct(gate.local_mean_wer)})")
    if hard_errors or any(s.errors for s in stats.values()):
        return 2 if hard_errors else (0 if gate and gate.passed else 1)
    return 0 if gate is None or gate.passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pages", type=Path, required=True,
                        help="corpus dir: *.png + truth/*.txt (+ manifest.tsv)")
    parser.add_argument("--engines", default="local,luna",
                        help="comma-separated subset of: local,luna")
    parser.add_argument("--report", type=Path,
                        default=Path("docs/ocr-eval-2026-10.md"))
    parser.add_argument("--limit", type=int, default=None,
                        help="evaluate only the first N pages (smoke runs)")
    args = parser.parse_args(argv)

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    unknown = [e for e in engines if e not in ("local", "luna")]
    if unknown:
        parser.error(f"unknown engines: {unknown}")
    if "local" not in engines and "luna" not in engines:
        parser.error("no engines selected")
    return run_eval(args.pages, engines, args.limit, args.report)


if __name__ == "__main__":
    sys.exit(main())
