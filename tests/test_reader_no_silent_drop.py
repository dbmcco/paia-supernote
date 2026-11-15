"""A requested page that yields nothing must say why, not vanish.

Item apps-supernote-ocr-silent-stop, second half. The retry sweep now reaches a
pending page, asks the reader for it, and the reader could answer with neither a
result nor an error. Two paths did that, both silent:

  * an index outside the notebook's own page count (``continue``), and
  * a page whose transcription came back empty (``_build_read_result`` returns None
    and the caller dropped it).

The page then stayed ledger-pending with its retry claim intact, so it was asked
for again on every sweep, while its recorded error stayed frozen at whatever failed
days earlier. Mgmt page 6 is exactly that: pending since 13 November, and its alarm
still carries a 504 from that night.

Whichever branch a given page takes, the answer is the same: a requested page that
produces no result is a named failure. A blank page is reported as a blank page,
not marked done (an empty string would silently drop content), and not left to look
like a success.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from paia_supernote import reader as reader_mod


class _FakeNotebook:
    def __init__(self, total_pages: int) -> None:
        self._total = total_pages

    def get_total_pages(self) -> int:
        return self._total


class _FakeConverter:
    def convert(self, page_num: int):
        return f"image-{page_num}"


def _patch_notebook(monkeypatch, total_pages: int) -> None:
    monkeypatch.setattr(
        reader_mod.supernotelib,
        "load_notebook",
        lambda path: _FakeNotebook(total_pages),
    )
    monkeypatch.setattr(reader_mod, "ImageConverter", lambda notebook: _FakeConverter())


async def _resilient(
    tmp_path: Path, monkeypatch, total_pages: int, pages: list[int], text
):
    _patch_notebook(monkeypatch, total_pages)
    reader = reader_mod.SupernoteReader.__new__(reader_mod.SupernoteReader)

    async def fake_transcribe(self, page_image):
        return text

    async def fake_classify(self, transcription):
        return "general"

    monkeypatch.setattr(reader_mod.SupernoteReader, "_transcribe_page", fake_transcribe)
    monkeypatch.setattr(reader_mod.SupernoteReader, "classify_content", fake_classify)
    return await reader.read_pages_resilient(b"note", "Mgmt", pages=pages)


@pytest.mark.asyncio
async def test_a_page_index_beyond_the_notebook_is_a_named_error(
    tmp_path: Path, monkeypatch
) -> None:
    """The page does not exist in the bytes we hold, and that is not silence."""
    results, errors = await _resilient(tmp_path, monkeypatch, 6, [5, 6], "some text")

    assert [r.page_num for r in results] == [5]
    assert 6 in errors
    assert "6" in errors[6] and "6 page" in errors[6].replace("6\n", "6 page")
    assert "page" in errors[6].lower()


@pytest.mark.asyncio
async def test_a_page_whose_transcription_is_empty_is_a_named_error(
    tmp_path: Path, monkeypatch
) -> None:
    """Empty text is a result about the page, so it is reported, not dropped.

    Marking it done would silently drop content; dropping it leaves a pending page
    that is retried forever against a stale error. Naming it is the honest third way.
    """
    results, errors = await _resilient(tmp_path, monkeypatch, 7, [6], "")

    assert results == []
    assert 6 in errors
    assert "no text" in errors[6].lower()
    assert "page 6" in errors[6].lower()


@pytest.mark.asyncio
async def test_a_normal_page_yields_a_result_and_no_error(
    tmp_path: Path, monkeypatch
) -> None:
    """The guard against over-firing: a page with text is untouched by this change."""
    results, errors = await _resilient(tmp_path, monkeypatch, 7, [6], "real transcript")

    assert [r.page_num for r in results] == [6]
    assert errors == {}


@pytest.mark.asyncio
async def test_every_requested_page_is_accounted_for(
    tmp_path: Path, monkeypatch
) -> None:
    """No page may be asked for and then answered with nothing at all."""
    results, errors = await _resilient(tmp_path, monkeypatch, 8, [0, 6, 9], "text")

    assert set(errors) == {9}
    assert [r.page_num for r in results] == [0, 6]
    assert set(errors) | {r.page_num for r in results} == {0, 6, 9}
