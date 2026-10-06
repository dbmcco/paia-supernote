"""Unit tests for scripts/ocr_eval.py metrics, corpus loading, and gate."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ocr_eval.py"
_spec = importlib.util.spec_from_file_location("ocr_eval", _SCRIPT)
ocr_eval = importlib.util.module_from_spec(_spec)
sys.modules["ocr_eval"] = ocr_eval  # dataclass needs a registered module
_spec.loader.exec_module(ocr_eval)


# wer / cer --------------------------------------------------------------


class TestWer:
    def test_plan_case_substitution(self):
        # plan spec: "a b c" vs "a x c" -> 1/3
        assert ocr_eval.wer("a b c", "a x c") == pytest.approx(1 / 3)

    def test_identical_is_zero(self):
        assert ocr_eval.wer("hello world", "hello world") == 0.0

    def test_insertion(self):
        assert ocr_eval.wer("a b", "a b c") == pytest.approx(1 / 2)

    def test_deletion(self):
        assert ocr_eval.wer("a b c", "a c") == pytest.approx(1 / 3)

    def test_empty_ref_nonempty_hyp_is_one(self):
        assert ocr_eval.wer("", "guess") == 1.0

    def test_both_empty_is_zero(self):
        assert ocr_eval.wer("", "") == 0.0

    def test_normalization_case_and_punct(self):
        # "Mgmt." vs "mgmt" should be a perfect match normalized
        assert ocr_eval.wer("Mgmt. notes!", "mgmt notes") == 0.0

    def test_strict_mode_keeps_case_and_punct(self):
        strict = ocr_eval.wer("Mgmt. notes!", "mgmt notes", normalized=False)
        assert strict > 0.0


class TestCer:
    def test_one_char_sub(self):
        assert ocr_eval.cer("cat", "cot") == pytest.approx(1 / 3)

    def test_identical_is_zero(self):
        assert ocr_eval.cer("line one", "line one") == 0.0

    def test_lower_than_wer_on_partial_words(self):
        # "handwritng" (missing i) is 1 word error but only 1 char error
        ref = "handwriting"
        assert ocr_eval.cer(ref, "handwritng") < ocr_eval.wer(ref, "handwritng")

    def test_empty_ref_nonempty_hyp_is_one(self):
        assert ocr_eval.cer("", "x") == 1.0


# corpus loading -----------------------------------------------------------


class TestLoadCorpus:
    def _make_corpus(self, tmp_path: Path):
        (tmp_path / "p1.png").write_bytes(b"\x89PNG fake")
        (tmp_path / "p2.png").write_bytes(b"\x89PNG fake")
        (tmp_path / "truth").mkdir()
        (tmp_path / "truth" / "p1.txt").write_text("one")
        (tmp_path / "truth" / "p2.txt").write_text("two")
        (tmp_path / "manifest.tsv").write_text("p1\tnormal\np2\tnumbers\n")

    def test_loads_pages_truth_and_classes(self, tmp_path: Path):
        self._make_corpus(tmp_path)
        pages, problems = ocr_eval.load_corpus(tmp_path)
        assert problems == []
        assert [p.name for p in pages] == ["p1", "p2"]
        assert pages[0].page_class == "normal"
        assert pages[1].page_class == "numbers"

    def test_missing_truth_reported_and_excluded(self, tmp_path: Path):
        self._make_corpus(tmp_path)
        (tmp_path / "truth" / "p2.txt").unlink()
        pages, problems = ocr_eval.load_corpus(tmp_path)
        assert [p.name for p in pages] == ["p1"]
        assert any("p2" in p and "no truth" in p for p in problems)

    def test_no_manifest_means_untagged(self, tmp_path: Path):
        (tmp_path / "only.png").write_bytes(b"\x89PNG fake")
        (tmp_path / "truth").mkdir()
        (tmp_path / "truth" / "only.txt").write_text("x")
        pages, _ = ocr_eval.load_corpus(tmp_path)
        assert pages[0].page_class == "untagged"

    def test_no_pngs_is_error(self, tmp_path: Path):
        _, problems = ocr_eval.load_corpus(tmp_path)
        assert any("no *.png" in p for p in problems)

    def test_non_png_files_ignored(self, tmp_path: Path):
        (tmp_path / "notes.txt").write_text("not a page")
        _, problems = ocr_eval.load_corpus(tmp_path)
        assert any("no *.png" in p for p in problems)


# gate ----------------------------------------------------------------------


def _stats(name: str, mean_wer: float, per_class: dict[str, float] | None = None):
    return ocr_eval.EngineStats(name=name, mean_wer=mean_wer,
                                per_class=per_class or {})


class TestGateVerdict:
    def test_pass_all_criteria(self):
        local = _stats("local", 0.08, {"normal": 0.06, "cursive": 0.24})
        luna = _stats("luna", 0.05)
        gate = ocr_eval.gate_verdict(local, luna)
        assert gate.passed
        assert gate.worst_class == ("cursive", 0.24)

    def test_fail_on_mean(self):
        gate = ocr_eval.gate_verdict(_stats("local", 0.12), _stats("luna", 0.05))
        assert not gate.mean_ok
        assert not gate.passed

    def test_fail_on_class(self):
        local = _stats("local", 0.08, {"faint": 0.30})
        gate = ocr_eval.gate_verdict(local, _stats("luna", 0.05))
        assert gate.mean_ok and not gate.class_ok and not gate.passed

    def test_fail_on_delta_vs_luna(self):
        # local fine in absolute terms but clearly worse than luna
        gate = ocr_eval.gate_verdict(_stats("local", 0.09), _stats("luna", 0.01))
        assert not gate.delta_ok and not gate.passed

    def test_pass_when_within_delta(self):
        gate = ocr_eval.gate_verdict(_stats("local", 0.09), _stats("luna", 0.06))
        assert gate.delta_ok

    def test_local_only_gate_skips_delta(self):
        gate = ocr_eval.gate_verdict(_stats("local", 0.08), None)
        assert gate.luna_mean_wer is None
        assert gate.delta_ok  # not evaluated
        assert gate.passed


# report --------------------------------------------------------------------


class TestBuildReport:
    def test_report_contains_verdict_and_rows(self, tmp_path: Path):
        page = ocr_eval.Page(name="p1", image=tmp_path / "p1.png",
                             truth=tmp_path / "t.txt", page_class="normal")
        rows = {"local": {"p1": {"wer": 0.05, "cer": 0.02, "elapsed_s": 4.0}}}
        stats = {"local": _stats("local", 0.05, {"normal": 0.05})}
        gate = ocr_eval.gate_verdict(stats["local"], None)
        report = ocr_eval.build_report([page], rows, stats, gate, ["local"])
        assert "Gate verdict" in report
        assert "**PASS**" in report
        assert "| p1 | normal |" in report
        assert "5.00%" in report

    def test_error_row_rendered_not_hidden(self, tmp_path: Path):
        page = ocr_eval.Page(name="p9", image=tmp_path / "p9.png",
                             truth=tmp_path / "t.txt")
        rows = {"local": {"p9": {"error": "ConnectError: boom", "wer": 1.0,
                                 "cer": 1.0}}}
        stats = {"local": _stats("local", 1.0)}
        gate = ocr_eval.gate_verdict(stats["local"], None)
        report = ocr_eval.build_report([page], rows, stats, gate, ["local"])
        assert "ERROR" in report
        assert "ConnectError" in report
