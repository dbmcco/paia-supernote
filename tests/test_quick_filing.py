from paia_supernote.quick_filing import StarDetector, parse_filing_header, route_page


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
