from paia_supernote.native_link_probe import NativeLinkProbeResult, probe_native_links


def test_native_link_probe_fails_closed_without_fixture_paths() -> None:
    result = probe_native_links()

    assert result.status == "blocked"
    assert result.real_note_writes_allowed is False
    assert "fixture notebooks are required" in result.reason


def test_native_link_probe_result_serializes_to_dict() -> None:
    result = NativeLinkProbeResult(
        status="blocked",
        real_note_writes_allowed=False,
        reason="fixture notebooks are required",
        evidence=["supernotelib 0.7.1 has no public link constructor"],
    )

    assert result.to_dict() == {
        "status": "blocked",
        "real_note_writes_allowed": False,
        "reason": "fixture notebooks are required",
        "evidence": ["supernotelib 0.7.1 has no public link constructor"],
    }
