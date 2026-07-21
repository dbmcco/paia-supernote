# ABOUTME: Tests CloudPoller poll-health transitions that surface silent 403s.
# ABOUTME: A persistent auth failure must fire monitoring once, then recover once.

from __future__ import annotations

import pytest

from paia_supernote.cloud_poller import CloudPoller


class FakeUploader:
    """Minimal uploader stub: returns a scripted status from each list query."""

    NOTE_FOLDER_ID = "folder-1"

    def __init__(self, statuses: list[int]) -> None:
        self._statuses = list(statuses)
        self.calls = 0

    async def _api_call(self, endpoint: str, body: dict) -> dict:
        self.calls += 1
        status = self._statuses[min(self.calls - 1, len(self._statuses) - 1)]
        body_out: dict | str
        body_out = {"userFileVOList": []} if status == 200 else "error"
        return {"status": status, "body": body_out}

    async def ensure_authenticated(self) -> None:
        """Base stub has no auto-login, so reauth always fails."""
        raise NotImplementedError("no auto-login on the base stub")


class ReauthFakeUploader(FakeUploader):
    """Uploader stub whose ensure_authenticated can succeed (env auto-login)."""

    def __init__(self, statuses: list[int], *, ensure_succeeds: bool = True) -> None:
        super().__init__(statuses)
        self.ensure_succeeds = ensure_succeeds
        self.ensure_calls = 0

    async def ensure_authenticated(self) -> None:
        self.ensure_calls += 1
        if not self.ensure_succeeds:
            raise RuntimeError("login failed")


async def _noop_changed(notebook: str, note_bytes: bytes, update_time) -> None:
    return None


def _make_poller(statuses: list[int]):
    transitions: list[tuple[bool, dict]] = []

    async def on_health(healthy: bool, detail: dict) -> None:
        transitions.append((healthy, detail))

    poller = CloudPoller(
        uploader=FakeUploader(statuses),
        on_note_changed=_noop_changed,
        on_poll_health=on_health,
    )
    return poller, transitions


class TestPollHealthTransitions:
    @pytest.mark.asyncio
    async def test_first_403_fires_degraded_once(self) -> None:
        poller, transitions = _make_poller([403])

        result = await poller._list_notes()

        assert result == []  # silent empty list — the original symptom
        assert len(transitions) == 1
        healthy, detail = transitions[0]
        assert healthy is False
        assert detail["reason"] == "cloud_session_expired"
        assert detail["status"] == 403

    @pytest.mark.asyncio
    async def test_persistent_403_fires_only_once(self) -> None:
        poller, transitions = _make_poller([403, 403, 403])

        for _ in range(3):
            await poller._list_notes()

        # Throttled to the single healthy->degraded transition, not per-poll.
        assert len(transitions) == 1
        assert transitions[0][0] is False

    @pytest.mark.asyncio
    async def test_recovery_after_failure_fires_recovered(self) -> None:
        poller, transitions = _make_poller([403, 200])

        await poller._list_notes()
        await poller._list_notes()

        assert [t[0] for t in transitions] == [False, True]

    @pytest.mark.asyncio
    async def test_healthy_polls_never_fire(self) -> None:
        poller, transitions = _make_poller([200, 200])

        await poller._list_notes()
        await poller._list_notes()

        assert transitions == []

    @pytest.mark.asyncio
    async def test_non_auth_list_failure_also_degrades(self) -> None:
        poller, transitions = _make_poller([500])

        await poller._list_notes()

        assert len(transitions) == 1
        healthy, detail = transitions[0]
        assert healthy is False
        assert detail["reason"] == "cloud_list_failed"
        assert detail["status"] == 500

    @pytest.mark.asyncio
    async def test_callback_error_does_not_break_poll(self) -> None:
        async def boom(healthy: bool, detail: dict) -> None:
            raise RuntimeError("monitoring sink down")

        poller = CloudPoller(
            uploader=FakeUploader([403]),
            on_note_changed=_noop_changed,
            on_poll_health=boom,
        )

        # Must swallow the callback error and still return the empty list.
        assert await poller._list_notes() == []


class TestAutoReauthSelfHeal:
    """On a 401/403 the poller attempts a silent re-auth, then retries once."""

    @pytest.mark.asyncio
    async def test_reauths_and_retries_on_403(self) -> None:
        uploader = ReauthFakeUploader([403, 200])
        poller = CloudPoller(
            uploader=uploader,
            on_note_changed=_noop_changed,
            on_poll_health=None,
        )

        result = await poller._list_notes()

        assert result == []
        assert uploader.ensure_calls == 1  # attempted silent re-auth
        assert uploader.calls == 2  # initial 403, then successful retry

    @pytest.mark.asyncio
    async def test_degrades_when_reauth_fails(self) -> None:
        transitions: list[tuple[bool, dict]] = []

        async def on_health(healthy: bool, detail: dict) -> None:
            transitions.append((healthy, detail))

        uploader = ReauthFakeUploader([403], ensure_succeeds=False)
        poller = CloudPoller(
            uploader=uploader,
            on_note_changed=_noop_changed,
            on_poll_health=on_health,
        )

        result = await poller._list_notes()

        assert result == []
        assert uploader.ensure_calls == 1
        assert len(transitions) == 1
        assert transitions[0][0] is False
        assert transitions[0][1]["reason"] == "cloud_session_expired"

    @pytest.mark.asyncio
    async def test_does_not_reauth_on_non_auth_failure(self) -> None:
        uploader = ReauthFakeUploader([500])
        poller = CloudPoller(
            uploader=uploader,
            on_note_changed=_noop_changed,
            on_poll_health=None,
        )

        await poller._list_notes()

        # A 500 is not an auth problem; no re-auth attempt is made.
        assert uploader.ensure_calls == 0


class TreeFakeUploader:
    """Uploader stub that serves a scripted folder tree per directoryId."""

    NOTE_FOLDER_ID = "root"

    def __init__(self, tree: dict) -> None:
        self.tree = tree

    async def _api_call(self, endpoint: str, body: dict) -> dict:
        return {
            "status": 200,
            "body": {"userFileVOList": list(self.tree.get(body["directoryId"], []))},
        }

    async def ensure_authenticated(self) -> None:
        return None


class TestRecursiveNoteListing:
    """The poller must see notebooks in Cloud subfolders (cos/, know/), not just root."""

    @pytest.mark.asyncio
    async def test_fetch_note_listing_aggregates_subfolder_notebooks(self) -> None:
        tree = {
            "root": [
                {"id": "mgmt-1", "fileName": "Mgmt.note", "isFolder": "N"},
                {"id": "cos-1", "fileName": "cos", "isFolder": "Y"},
            ],
            "cos-1": [
                {"id": "lfw-1", "fileName": "LFW.note", "isFolder": "N"},
                {"id": "synth-1", "fileName": "Synth.note", "isFolder": "N"},
            ],
        }
        poller = CloudPoller(
            uploader=TreeFakeUploader(tree),
            on_note_changed=_noop_changed,
            on_poll_health=None,
        )

        result = await poller._fetch_note_listing()

        names = {e["fileName"] for e in result["body"]["userFileVOList"]}
        assert "Mgmt.note" in names  # root
        assert "LFW.note" in names  # cos/ subfolder
        assert "Synth.note" in names

    @pytest.mark.asyncio
    async def test_fetch_note_listing_does_not_recurse_into_files(self) -> None:
        # A root-only tree (no folders) must cost exactly one listing call.
        tree = {
            "root": [
                {"id": "mgmt-1", "fileName": "Mgmt.note", "isFolder": "N"},
                {"id": "quick-1", "fileName": "Quick.note", "isFolder": "N"},
            ],
        }
        uploader = TreeFakeUploader(tree)
        calls: list[str] = []

        original = uploader._api_call

        async def counting(endpoint: str, body: dict) -> dict:
            calls.append(body["directoryId"])
            return await original(endpoint, body)

        uploader._api_call = counting  # type: ignore[method-assign]
        poller = CloudPoller(
            uploader=uploader, on_note_changed=_noop_changed, on_poll_health=None
        )

        await poller._fetch_note_listing()
        assert calls == ["root"]
