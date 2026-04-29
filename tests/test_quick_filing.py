from paia_supernote.quick_filing import (
    StarDetector,
    notebook_name_to_tag,
    parse_filing_header,
    route_page,
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


def test_route_page_requires_known_destination_tag() -> None:
    routed = route_page(
        notebook="Test Note 1",
        page=0,
        source_revision="rev-1",
        text="2026-04-29 #unknown\nUntitled",
        starred=True,
        destination_map={"test": "Test Note 2"},
    )

    assert routed.status == "needs_review"
    assert routed.target_notebook is None
    assert "no known destination tag" in routed.reason


def test_route_page_uses_test_destination_when_starred() -> None:
    routed = route_page(
        notebook="Test Note 1",
        page=3,
        source_revision="rev-1",
        text="2026-04-29 #test #meeting\nPilot page",
        starred=True,
        destination_map={"test": "Test Note 2"},
    )

    assert routed.status == "ready"
    assert routed.target_notebook == "Test Note 2"
    assert routed.source_pages == [3]


def test_route_page_supports_target_note_name_tag() -> None:
    routed = route_page(
        notebook="Test Note 1",
        page=0,
        source_revision="rev-1",
        text="2026-04-29 #test-note-2\nPilot page",
        starred=True,
        destination_map={"test-note-2": "Test Note 2"},
    )

    assert routed.status == "ready"
    assert routed.target_notebook == "Test Note 2"
    assert routed.reason == "matched #test-note-2"


def test_route_page_does_not_move_unstarred_page() -> None:
    routed = route_page(
        notebook="Test Note 1",
        page=3,
        source_revision="rev-1",
        text="2026-04-29 #test #meeting\nPilot page",
        starred=False,
        destination_map={"test": "Test Note 2"},
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

    assert detector.starred_pages_from_metadata(
        {
            "page_metadata": [
                {"FIVESTAR": "14031,1694,14395,1581,14164,1885,14164,1502,14395,1809,1"},
                {"FIVESTAR": "0"},
                {},
            ]
        }
    ) == {0}
