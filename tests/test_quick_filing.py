from paia_supernote.quick_filing import (
    FilingDestinationDecision,
    StarDetector,
    notebook_name_to_tag,
    parse_filing_header,
    route_page_from_decision,
)


def test_parse_filing_header_with_bundle_marker() -> None:
    parsed = parse_filing_header(
        "2026-04-29 #test #meeting 1/2\nGene King check-in\nBody text"
    )

    assert parsed.note_date == "2026-04-29"
    assert parsed.tags == ["test", "meeting"]
    assert parsed.bundle_index == 1
    assert parsed.bundle_total == 2
    assert parsed.title == "Gene King check-in"


def test_route_page_requires_model_move_decision() -> None:
    routed = route_page_from_decision(
        notebook="Test Note 1",
        page=0,
        source_revision="rev-1",
        text="2026-04-29 #unknown\nUntitled",
        starred=True,
        decision=FilingDestinationDecision(
            action="needs_review",
            target_notebook=None,
            evidence="No destination marker was visible.",
            confidence=0.0,
            raw_response="{}",
        ),
    )

    assert routed.status == "needs_review"
    assert routed.target_notebook is None
    assert "No destination marker" in routed.reason


def test_route_page_uses_model_destination_when_starred() -> None:
    routed = route_page_from_decision(
        notebook="Test Note 1",
        page=3,
        source_revision="rev-1",
        text="2026-04-29 #meeting\nTest Note 2\nPilot page",
        starred=True,
        decision=FilingDestinationDecision(
            action="move",
            target_notebook="Test Note 2",
            evidence="The target note name is written beside the star.",
            confidence=0.92,
            raw_response='{"action":"move"}',
        ),
    )

    assert routed.status == "ready"
    assert routed.target_notebook == "Test Note 2"
    assert routed.source_pages == [3]
    assert routed.confidence == 0.92


def test_route_page_preserves_tags_without_using_them_as_destination() -> None:
    routed = route_page_from_decision(
        notebook="Test Note 1",
        page=0,
        source_revision="rev-1",
        text="2026-04-29 #test-note-2\nPilot page",
        starred=True,
        decision=FilingDestinationDecision(
            action="needs_review",
            target_notebook=None,
            evidence="Has tags, but no visible move destination.",
            confidence=0.0,
            raw_response="{}",
        ),
    )

    assert routed.status == "needs_review"
    assert routed.target_notebook is None
    assert routed.detected_tags == ["test-note-2"]


def test_route_page_records_model_evidence_instead_of_semantic_tags() -> None:
    routed = route_page_from_decision(
        notebook="Test Note 1",
        page=0,
        source_revision="rev-1",
        text="2026-05-01\nTest Note 2\n#meeting #pilot\nPage content",
        starred=True,
        decision=FilingDestinationDecision(
            action="move",
            target_notebook="Test Note 2",
            evidence="The model saw Test Note 2 written as the destination.",
            confidence=1.0,
            raw_response="{}",
        ),
    )

    assert routed.status == "ready"
    assert routed.target_notebook == "Test Note 2"
    assert routed.detected_tags == ["meeting", "pilot"]
    assert "model selected destination Test Note 2" in routed.reason


def test_route_page_does_not_move_unstarred_page() -> None:
    routed = route_page_from_decision(
        notebook="Test Note 1",
        page=3,
        source_revision="rev-1",
        text="2026-04-29 #test #meeting\nPilot page",
        starred=False,
        decision=FilingDestinationDecision(
            action="move",
            target_notebook="Test Note 2",
            evidence="ignored for unstarred pages",
            confidence=1.0,
            raw_response="{}",
        ),
    )

    assert routed.status == "detected"
    assert routed.target_notebook is None
    assert routed.reason == "page is not starred"


def test_star_detector_defaults_to_no_star_when_metadata_unknown() -> None:
    detector = StarDetector()

    assert detector.starred_pages_from_metadata({}) == set()


def test_notebook_name_to_tag_normalizes_target_note_name() -> None:
    assert notebook_name_to_tag("Test Note 2") == "test-note-2"
    assert notebook_name_to_tag("Navicyte.note") == "navicyte"
    assert notebook_name_to_tag("LFW / HEC") == "lfw-hec"


def test_star_detector_reads_fivestar_page_metadata() -> None:
    detector = StarDetector()
    star_value = "14031,1694,14395,1581,14164,1885,14164,1502,14395,1809,1"

    assert detector.starred_pages_from_metadata(
        {
            "page_metadata": [
                {"FIVESTAR": star_value},
                {"FIVESTAR": "0"},
                {},
            ]
        }
    ) == {0}
