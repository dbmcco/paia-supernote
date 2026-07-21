"""Tests for Supernote Cloud uploader module."""

import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from paia_supernote.uploader import (
    SupernoteUploadConflictError,
    SupernoteUploader,
    UploadAuthError,
    UploadSyncInProgressError,
)


@pytest.fixture
def uploader():
    """Create a fresh uploader instance."""
    return SupernoteUploader()


@pytest.fixture
def mock_note_file(tmp_path):
    """Create a temporary .note file with test data."""
    note_file = tmp_path / "test.note"
    test_data = b"mock note file data"
    note_file.write_bytes(test_data)
    return str(note_file)


@pytest.fixture
def mock_session_file(tmp_path):
    """Create a temporary session file."""
    session_file = tmp_path / "session.json"
    session_data = {
        "cookies": [
            {
                "name": "session_cookie",
                "value": "abc123",
                "domain": "cloud.supernote.com",
            }
        ]
    }
    session_file.write_text(json.dumps(session_data))
    return session_file


class TestSupernoteUploader:
    """Test the Supernote Cloud uploader functionality."""

    @pytest.mark.asyncio
    async def test_upload_raises_error_if_browser_not_started(self, uploader):
        """Test that upload raises error if browser is not started."""
        # Don't start browser
        uploader.page = None

        with pytest.raises(RuntimeError, match="Browser not started"):
            await uploader.upload_notebook("/path/to/test.note", "Quick.note")

    @pytest.mark.asyncio
    async def test_ensure_authenticated_requires_browser(self, uploader):
        """_ensure_authenticated raises RuntimeError when browser not started."""
        with pytest.raises(RuntimeError, match="Browser not started"):
            await uploader._ensure_authenticated()

    @pytest.mark.asyncio
    async def test_initiate_upload_raises_without_browser(self, uploader):
        """Test that _initiate_upload raises RuntimeError when browser not started."""
        uploader.page = None
        with pytest.raises(RuntimeError, match="Browser not started"):
            await uploader._initiate_upload("/path/to/file.note", "Quick.note")

    @pytest.mark.asyncio
    async def test_upload_to_s3_raises_on_missing_file(self, uploader):
        """Test that _upload_to_s3 raises FileNotFoundError for missing file."""
        upload_info = {"uploadUrl": "https://s3.example.com/bucket/key"}
        with pytest.raises(FileNotFoundError):
            await uploader._upload_to_s3("/path/to/nonexistent/file", upload_info)

    @pytest.mark.asyncio
    async def test_finish_upload_requires_browser(self, uploader):
        """Test that _finish_upload raises RuntimeError when browser not started."""
        upload_info = {"url": "https://s3.example.com/key.note", "fileServer": "2"}
        with pytest.raises(RuntimeError, match="Browser not started"):
            await uploader._finish_upload("/tmp/fake.note", "fake.note", upload_info)

    @pytest.mark.asyncio
    async def test_upload_notebook_calls_three_step_flow(self, uploader):
        """Test that upload_notebook calls the three-step upload flow."""
        uploader.page = Mock()

        with (
            patch.object(
                uploader, "_ensure_authenticated", new_callable=AsyncMock
            ) as mock_auth,
            patch.object(
                uploader, "_resolve_notebook_location", new_callable=AsyncMock, return_value=(None, ROOT_ID)
            ),
            patch.object(
                uploader,
                "_find_blocking_sibling_names",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_siblings,
            patch.object(
                uploader, "_initiate_upload", new_callable=AsyncMock
            ) as mock_apply,
            patch.object(uploader, "_upload_to_s3", new_callable=AsyncMock) as mock_s3,
            patch.object(
                uploader, "_finish_upload", new_callable=AsyncMock
            ) as mock_finish,
            patch.object(
                uploader, "_wait_for_stable_single_target", new_callable=AsyncMock
            ) as mock_verify,
        ):

            mock_apply.return_value = {"uploadUrl": "https://s3.example.com/key"}

            result = await uploader.upload_notebook("/path/to/test.note", "Quick.note")
            assert result is True
            mock_auth.assert_called_once()
            mock_siblings.assert_awaited_once_with("Quick.note")
            mock_apply.assert_called_once()
            mock_s3.assert_called_once()
            mock_finish.assert_called_once()
            mock_verify.assert_awaited_once_with("Quick.note")

    @pytest.mark.asyncio
    async def test_upload_notebook_blocks_when_conflict_or_numbered_copy_exists(
        self, uploader, mock_note_file
    ):
        """Existing conflict/numbered siblings must be cleaned before upload."""
        uploader.page = Mock()

        with (
            patch.object(uploader, "_ensure_authenticated", new_callable=AsyncMock),
            patch.object(
                uploader,
                "_find_blocking_sibling_names",
                new_callable=AsyncMock,
                return_value=["Quick_CONFLICT_20260507154554506.note", "Quick(1).note"],
            ),
            patch.object(
                uploader, "_find_file_ids", new_callable=AsyncMock
            ) as mock_find,
        ):

            with pytest.raises(
                SupernoteUploadConflictError, match="blocking cloud copies"
            ):
                await uploader.upload_notebook(mock_note_file, "Quick.note")

            mock_find.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_by_ids_raises_when_cloud_delete_fails(self, uploader):
        """Failed deletes cannot be ignored because they lead to target(1).note."""
        uploader._api_call = AsyncMock(
            return_value={
                "status": 200,
                "body": {"success": False, "errorCode": "E999", "errorMsg": "nope"},
            }
        )

        with pytest.raises(RuntimeError, match="delete failed"):
            await uploader._delete_by_ids(["old-id"])

    @pytest.mark.asyncio
    async def test_initiate_upload_raises_sync_in_progress_on_e0301(
        self, uploader, mock_note_file
    ):
        """Supernote's sync-in-progress response is a mechanical retry condition."""
        uploader.page = Mock()
        uploader._api_call = AsyncMock(
            return_value={
                "status": 200,
                "body": {
                    "success": False,
                    "errorCode": "E0301",
                    "errorMsg": "Sync in progress, please wait",
                },
            }
        )

        with pytest.raises(UploadSyncInProgressError, match="Sync in progress"):
            await uploader._initiate_upload(mock_note_file, "Walk.note")

    @pytest.mark.asyncio
    async def test_session_reload_from_disk_works(self, uploader, mock_session_file):
        """Test that session can be reloaded from disk storage."""
        # Mock playwright components
        mock_playwright = Mock()
        mock_browser = Mock()
        mock_context = Mock()
        mock_page = Mock()

        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_page.goto = AsyncMock()
        mock_page.wait_for_function = AsyncMock()

        # Patch the session file path to use our mock
        uploader.SESSION_FILE = mock_session_file

        with patch("paia_supernote.uploader.async_playwright") as mock_pw:
            mock_pw.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)

            # Start uploader
            await uploader.start()

            # Verify session file was used
            mock_browser.new_context.assert_called_once_with(
                storage_state=str(mock_session_file)
            )
            mock_page.goto.assert_awaited_once_with(
                uploader.CLOUD_HOME_URL,
                wait_until="domcontentloaded",
            )

    @pytest.mark.asyncio
    async def test_upload_flow_with_mocked_network(self, uploader, mock_note_file):
        """Test the complete upload flow with mocked network responses."""
        # This test will fail initially because the methods are not implemented
        # But describes the expected behavior when implemented

        uploader.page = Mock()

        # Mock the network calls to return expected data
        with (
            patch.object(uploader, "_ensure_authenticated") as mock_auth,
            patch.object(
                uploader, "_resolve_notebook_location", new_callable=AsyncMock, return_value=(None, ROOT_ID)
            ),
            patch.object(
                uploader,
                "_find_blocking_sibling_names",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch.object(uploader, "_initiate_upload") as mock_apply,
            patch.object(uploader, "_upload_to_s3") as mock_s3,
            patch.object(uploader, "_finish_upload") as mock_finish,
            patch.object(
                uploader, "_wait_for_stable_single_target", new_callable=AsyncMock
            ),
        ):

            mock_auth.return_value = None
            mock_apply.return_value = {
                "uploadUrl": "https://s3.amazonaws.com/bucket/key",
                "uploadId": "abc123",
                "headers": {"Authorization": "AWS4-HMAC-SHA256 ..."},
            }
            mock_s3.return_value = None
            mock_finish.return_value = None

            result = await uploader.upload_notebook(mock_note_file, "Quick.note")

            assert result is True
            mock_auth.assert_called_once()
            mock_apply.assert_called_once_with(mock_note_file, "Quick.note", ROOT_ID)
            mock_s3.assert_called_once()
            mock_finish.assert_called_once()

    @pytest.mark.asyncio
    async def test_compute_file_md5(self, uploader, mock_note_file):
        """Test that file MD5 is computed correctly."""
        # This will fail because the method doesn't exist yet
        file_data = Path(mock_note_file).read_bytes()
        expected_md5 = hashlib.md5(file_data).hexdigest()

        computed_md5 = uploader._compute_file_md5(mock_note_file)
        assert computed_md5 == expected_md5

    @pytest.mark.asyncio
    async def test_ensure_authenticated_skips_reauth_when_api_probe_succeeds(
        self, uploader
    ):
        """A live session returns from the file-list probe; no re-auth needed."""
        uploader.page = Mock()

        with (
            patch.object(
                uploader, "_list_note_files", new_callable=AsyncMock
            ) as mock_list,
            patch.object(
                uploader, "_interactive_reauth", new_callable=AsyncMock
            ) as mock_reauth,
        ):
            mock_list.return_value = []
            await uploader._ensure_authenticated()
            mock_list.assert_called_once()
            mock_reauth.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_authenticated_reauths_when_api_probe_is_unauthorized(
        self, uploader
    ):
        """A dead session (403 from the file-list probe) triggers re-auth."""
        uploader.page = Mock()

        with (
            patch.object(
                uploader, "_list_note_files", new_callable=AsyncMock
            ) as mock_list,
            patch.object(
                uploader, "_interactive_reauth", new_callable=AsyncMock
            ) as mock_reauth,
        ):
            mock_list.side_effect = UploadAuthError("list/query returned 403")
            await uploader._ensure_authenticated()
            mock_list.assert_called_once()
            mock_reauth.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_authenticated_reauths_when_probe_hits_navigation_error(
        self, uploader
    ):
        """A probe fetch aborted by SPA navigation also triggers re-auth."""
        uploader.page = Mock()
        with (
            patch.object(
                uploader, "_list_note_files", new_callable=AsyncMock
            ) as mock_list,
            patch.object(
                uploader, "_interactive_reauth", new_callable=AsyncMock
            ) as mock_reauth,
        ):
            mock_list.side_effect = PlaywrightError("Failed to fetch")
            await uploader._ensure_authenticated()
            mock_list.assert_called_once()
            mock_reauth.assert_called_once()

    @pytest.mark.asyncio
    async def test_interactive_reauth_logs_in_programmatically_with_env_creds(
        self, uploader, monkeypatch
    ):
        """With SN_PHONE/SN_PASSWORD set, reauth fills the form automatically."""
        uploader.page = Mock()
        monkeypatch.setenv("SN_PHONE", "+15555550100")
        monkeypatch.setenv("SN_PASSWORD", "hunter2")
        with (
            patch.object(
                uploader, "_login_programmatically", new_callable=AsyncMock
            ) as mock_prog,
            patch.object(
                uploader, "_wait_for_human_login", new_callable=AsyncMock
            ) as mock_human,
            patch.object(
                uploader, "_persist_session", new_callable=AsyncMock
            ) as mock_persist,
        ):
            await uploader._interactive_reauth()
            mock_prog.assert_called_once_with("+15555550100", "hunter2")
            mock_human.assert_not_called()
            mock_persist.assert_called_once()

    @pytest.mark.asyncio
    async def test_interactive_reauth_waits_for_human_when_no_env_creds(
        self, uploader, monkeypatch
    ):
        """Without env creds, reauth falls back to a visible-browser human login."""
        uploader.page = Mock()
        monkeypatch.delenv("SN_PHONE", raising=False)
        monkeypatch.delenv("SN_PASSWORD", raising=False)
        with (
            patch.object(
                uploader, "_login_programmatically", new_callable=AsyncMock
            ) as mock_prog,
            patch.object(
                uploader, "_wait_for_human_login", new_callable=AsyncMock
            ) as mock_human,
            patch.object(uploader, "_persist_session", new_callable=AsyncMock),
        ):
            await uploader._interactive_reauth()
            mock_human.assert_called_once()
            mock_prog.assert_not_called()

    @pytest.mark.asyncio
    async def test_login_programmatically_fills_form_in_order(self, uploader):
        """Programmatic login fills phone, then password, then submits."""
        page = AsyncMock()
        locator = AsyncMock()
        checkbox = AsyncMock()
        checkbox.get_attribute.return_value = ""  # unchecked
        page.get_by_role = Mock(return_value=locator)  # sync in real Playwright
        page.query_selector.return_value = checkbox
        uploader.page = page

        await uploader._login_programmatically("+15555550100", "hunter2")

        page.goto.assert_called_once()
        page.wait_for_selector.assert_called_once_with(
            "input[type='text']", timeout=20_000
        )
        values = [call.args[1] for call in page.fill.call_args_list]
        assert values == ["+15555550100", "hunter2"]
        checkbox.click.assert_called_once()  # ticked because it was unchecked
        locator.click.assert_called_once()  # Login button clicked
        page.wait_for_function.assert_called_once()

    @pytest.mark.asyncio
    async def test_login_programmatically_skips_already_checked_box(self, uploader):
        """If the agreement checkbox is already checked, we do not toggle it off."""
        page = AsyncMock()
        locator = AsyncMock()
        checkbox = AsyncMock()
        checkbox.get_attribute.return_value = "el-checkbox is-checked"
        page.get_by_role = Mock(return_value=locator)  # sync in real Playwright
        page.query_selector.return_value = checkbox
        uploader.page = page

        await uploader._login_programmatically("+15555550100", "hunter2")

        checkbox.click.assert_not_called()
        locator.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_login_timeout_raises_actionable_auth_error(
        self, uploader, monkeypatch
    ):
        """A login that times out surfaces as UploadAuthError, not a raw timeout."""
        page = AsyncMock()
        page.wait_for_function.side_effect = PlaywrightTimeoutError("timed out")
        page.query_selector.return_value = None
        page.get_by_role = Mock(return_value=AsyncMock())  # sync in real Playwright
        uploader.page = page
        monkeypatch.setenv("SN_PHONE", "+15555550100")
        monkeypatch.setenv("SN_PASSWORD", "hunter2")

        with pytest.raises(UploadAuthError, match="did not complete"):
            await uploader._interactive_reauth()

    @pytest.mark.asyncio
    async def test_initiate_upload_posts_to_apply_endpoint(
        self, uploader, mock_note_file
    ):
        """Test that _initiate_upload makes POST request to upload/apply endpoint."""
        mock_page = Mock()

        # Real API returns: url, s3Authorization, xamzDate, fileServer
        api_body = {
            "success": True,
            "url": "https://s3.amazonaws.com/bucket/key.note",
            "s3Authorization": "AWS4-HMAC-SHA256 Credential=...",
            "xamzDate": "20260414T200355Z",
            "fileServer": "2",
            "innerName": None,
        }
        mock_page.evaluate = AsyncMock(return_value={"status": 200, "body": api_body})
        uploader.page = mock_page

        result = await uploader._initiate_upload(mock_note_file, "Quick.note")

        assert result["url"] == api_body["url"]
        assert result["s3Authorization"] == api_body["s3Authorization"]
        mock_page.evaluate.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_to_s3_puts_file_to_presigned_url(
        self, uploader, mock_note_file
    ):
        """Test that _upload_to_s3 uploads file to S3 presigned URL."""
        # Real API: url = presigned S3 URL, s3Authorization + xamzDate are headers
        upload_info = {
            "url": "https://s3.amazonaws.com/bucket/key.note",
            "s3Authorization": "AWS4-HMAC-SHA256 Credential=...",
            "xamzDate": "20260414T200355Z",
        }

        with patch("paia_supernote.uploader.httpx.AsyncClient") as mock_client_class:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.raise_for_status = Mock()

            mock_client = AsyncMock()
            mock_client.put = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            mock_client_class.return_value = mock_client

            await uploader._upload_to_s3(mock_note_file, upload_info)

            mock_client.put.assert_called_once()
            call_args = mock_client.put.call_args
            assert call_args[0][0] == upload_info["url"]
            assert "content" in call_args[1]
            assert "x-amz-content-sha256" in call_args[1]["headers"]
            assert (
                call_args[1]["headers"]["Authorization"]
                == upload_info["s3Authorization"]
            )

    @pytest.mark.asyncio
    async def test_finish_upload_posts_to_finish_endpoint(
        self, uploader, mock_note_file
    ):
        """Test that _finish_upload makes POST request to upload/finish endpoint."""
        mock_page = Mock()
        upload_info = {
            "url": "https://s3.amazonaws.com/key.note",
            "fileServer": "2",
            "innerName": None,
        }

        # evaluate is called by _api_call inside _finish_upload
        mock_page.evaluate = AsyncMock(
            return_value={"status": 200, "body": {"success": True}}
        )
        uploader.page = mock_page

        await uploader._finish_upload(mock_note_file, "Quick.note", upload_info)

        mock_page.evaluate.assert_called_once()
        # The JS string passed to evaluate should reference /api/file/upload/finish
        call_args = mock_page.evaluate.call_args
        js_or_args = str(call_args)
        assert "/api/file/upload/finish" in js_or_args

    @pytest.mark.asyncio
    async def test_reauth_triggered_on_401_from_apply(self, uploader, mock_note_file):
        """Test that 401 from upload/apply triggers interactive re-auth and retries."""
        uploader.page = Mock()

        # First call to _initiate_upload raises UploadAuthError (401)
        # Second call succeeds after re-auth
        upload_info = {"uploadUrl": "https://s3.example.com/key", "headers": {}}

        call_count = 0

        async def mock_initiate(target_path, file_path, directory_id=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise UploadAuthError("Upload apply returned 401")
            return upload_info

        with (
            patch.object(uploader, "_ensure_authenticated", new_callable=AsyncMock),
            patch.object(
                uploader, "_resolve_notebook_location", new_callable=AsyncMock, return_value=(None, ROOT_ID)
            ),
            patch.object(
                uploader,
                "_find_blocking_sibling_names",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch.object(
                uploader, "_initiate_upload", side_effect=mock_initiate
            ) as mock_apply,
            patch.object(
                uploader, "_interactive_reauth", new_callable=AsyncMock
            ) as mock_reauth,
            patch.object(uploader, "_upload_to_s3", new_callable=AsyncMock),
            patch.object(uploader, "_finish_upload", new_callable=AsyncMock),
            patch.object(
                uploader, "_wait_for_stable_single_target", new_callable=AsyncMock
            ),
        ):

            result = await uploader.upload_notebook(mock_note_file, "Quick.note")

            assert result is True
            # Re-auth was triggered after the 401
            mock_reauth.assert_called_once()
            # _initiate_upload was called twice (first 401, then retry)
            assert mock_apply.call_count == 2

    @pytest.mark.asyncio
    async def test_initiate_upload_raises_upload_auth_error_on_401(
        self, uploader, mock_note_file
    ):
        """Test that _initiate_upload raises UploadAuthError on 401 response."""
        mock_page = Mock()
        mock_page.evaluate = AsyncMock(return_value={"status": 401, "body": None})
        uploader.page = mock_page

        with pytest.raises(UploadAuthError, match="401"):
            await uploader._initiate_upload(mock_note_file, "Quick.note")

    @pytest.mark.asyncio
    async def test_initiate_upload_raises_upload_auth_error_on_403(
        self, uploader, mock_note_file
    ):
        """Test that _initiate_upload raises UploadAuthError on 403 response."""
        mock_page = Mock()
        mock_page.evaluate = AsyncMock(return_value={"status": 403, "body": None})
        mock_page.goto = AsyncMock()
        mock_page.wait_for_function = AsyncMock()
        uploader.page = mock_page

        with pytest.raises(UploadAuthError, match="403"):
            await uploader._initiate_upload(mock_note_file, "Quick.note")

    @pytest.mark.asyncio
    async def test_download_notebook_raises_without_browser(self, uploader):
        """Test that download_notebook raises RuntimeError when browser not started."""
        uploader.page = None
        with pytest.raises(RuntimeError, match="Browser not started"):
            await uploader.download_notebook("Quick.note")

    @pytest.mark.asyncio
    async def test_download_notebook_raises_if_file_not_found(self, uploader):
        """download_notebook raises when the file is not in the Note folder."""
        uploader.page = Mock()
        with (
            patch.object(uploader, "_ensure_authenticated", new_callable=AsyncMock),
            patch.object(
                uploader, "_resolve_notebook_entry", new_callable=AsyncMock, return_value=None
            ),
        ):
            with pytest.raises(RuntimeError, match="not found"):
                await uploader.download_notebook("NoSuchFile.note")

    @pytest.mark.asyncio
    async def test_download_notebook_restarts_session_once_after_list_auth_error(
        self, uploader
    ):
        """A stale browser context should be restarted before giving up on downloads."""
        uploader.page = Mock()
        file_id = "123456789"
        presigned_url = "https://s3.amazonaws.com/bucket/Quick.note?sig=abc"
        expected_bytes = b"fake note content"

        uploader._api_call = AsyncMock(
            return_value={"status": 200, "body": {"url": presigned_url}}
        )

        with (
            patch.object(
                uploader,
                "_resolve_notebook_entry",
                new_callable=AsyncMock,
                side_effect=[UploadAuthError("list/query returned 403"), {"id": file_id, "_directoryId": ROOT_ID}],
            ) as mock_find,
            patch.object(
                uploader, "_restart_browser_session", new_callable=AsyncMock
            ) as mock_restart,
            patch("paia_supernote.uploader.httpx.AsyncClient") as mock_client_class,
        ):

            mock_response = Mock()
            mock_response.content = expected_bytes
            mock_response.raise_for_status = Mock()

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await uploader.download_notebook("Quick.note")

        assert result == expected_bytes
        assert mock_find.await_count == 2
        mock_restart.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_download_notebook_fetches_presigned_url_and_downloads(
        self, uploader
    ):
        """Test that download_notebook calls download/url API then GETs from S3."""
        mock_page = Mock()
        file_id = "123456789"
        presigned_url = "https://s3.amazonaws.com/bucket/Quick.note?sig=abc"
        expected_bytes = b"fake note content"

        mock_page.evaluate = AsyncMock(
            return_value={"status": 200, "body": {"url": presigned_url}}
        )
        uploader.page = mock_page

        with (
            patch.object(
                uploader,
                "_resolve_notebook_entry",
                new_callable=AsyncMock,
                return_value={"id": file_id, "_directoryId": ROOT_ID},
            ),
            patch("paia_supernote.uploader.httpx.AsyncClient") as mock_client_class,
        ):

            mock_response = Mock()
            mock_response.content = expected_bytes
            mock_response.raise_for_status = Mock()

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await uploader.download_notebook("Quick.note")

            assert result == expected_bytes
            mock_client.get.assert_called_once_with(presigned_url, timeout=120.0)

    @pytest.mark.asyncio
    async def test_download_notebook_raises_on_missing_url_in_response(self, uploader):
        """Test that download_notebook raises RuntimeError when API returns no URL."""
        mock_page = Mock()
        mock_page.evaluate = AsyncMock(
            return_value={"status": 200, "body": {"success": True}}  # no 'url' key
        )
        uploader.page = mock_page

        with patch.object(
            uploader, "_resolve_notebook_entry", new_callable=AsyncMock, return_value={"id": "some-id", "_directoryId": ROOT_ID}
        ):
            with pytest.raises(RuntimeError, match="No URL"):
                await uploader.download_notebook("Quick.note")

    @pytest.mark.asyncio
    async def test_api_call_recovers_when_page_target_is_closed(self, uploader):
        """_api_call restarts the session once when the Playwright target is closed."""
        dead_page = Mock()
        dead_page.evaluate = AsyncMock(
            side_effect=RuntimeError(
                "Page.evaluate: Target page, context or browser has been closed"
            )
        )
        live_page = Mock()
        live_page.evaluate = AsyncMock(
            return_value={"status": 200, "body": {"success": True}}
        )
        uploader.page = dead_page

        async def fake_start() -> None:
            uploader.page = live_page

        with (
            patch.object(uploader, "stop", new_callable=AsyncMock) as mock_stop,
            patch.object(uploader, "start", side_effect=fake_start) as mock_start,
        ):
            result = await uploader._api_call("/api/file/list/query", {"pageNo": 1})

        assert result == {"status": 200, "body": {"success": True}}
        mock_stop.assert_awaited_once()
        mock_start.assert_awaited_once()
        dead_page.evaluate.assert_awaited_once()
        live_page.evaluate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ensure_authenticated_recovers_when_page_target_is_closed(
        self, uploader
    ):
        """Restart the session once when the API probe hits a closed page target."""
        closed_err = RuntimeError(
            "Page.evaluate: Target page, context or browser has been closed"
        )
        dead_page = Mock()
        dead_page.evaluate = AsyncMock(side_effect=closed_err)
        live_page = Mock()
        live_page.evaluate = AsyncMock(
            return_value={"status": 200, "body": {"userFileVOList": []}}
        )
        uploader.page = dead_page

        async def fake_start() -> None:
            uploader.page = live_page

        with (
            patch.object(uploader, "stop", new_callable=AsyncMock) as mock_stop,
            patch.object(uploader, "start", side_effect=fake_start) as mock_start,
            patch.object(
                uploader, "_interactive_reauth", new_callable=AsyncMock
            ) as mock_reauth,
        ):
            await uploader._ensure_authenticated()

        mock_stop.assert_awaited_once()
        mock_start.assert_awaited_once()
        mock_reauth.assert_not_called()

    @pytest.mark.asyncio
    async def test_api_call_refreshes_csrf_token_and_retries_once(self, uploader):
        """_api_call should refresh the XSRF token and retry once on CSRF expiry."""
        mock_page = Mock()
        mock_page.evaluate = AsyncMock(
            side_effect=[
                {
                    "status": 403,
                    "body": '{"error":"CSRF token validation failed",'
                    '"code":"CSRF_TOKEN_EXPIRED"}',
                },
                {
                    "status": 200,
                    "body": {"success": True},
                },
            ]
        )
        uploader.page = mock_page

        with patch.object(
            uploader, "_refresh_csrf_token", new_callable=AsyncMock
        ) as mock_refresh:
            result = await uploader._api_call("/api/file/list/query", {"pageNo": 1})

        assert result == {"status": 200, "body": {"success": True}}
        mock_refresh.assert_awaited_once()
        assert mock_page.evaluate.await_count == 2

    @pytest.mark.asyncio
    async def test_api_call_is_wrapped_by_cloud_api_lock(self, uploader):
        """Cloud API calls should serialize across uploader instances."""
        events: list[str] = []

        @asynccontextmanager
        async def fake_lock():
            events.append("lock_enter")
            try:
                yield
            finally:
                events.append("lock_exit")

        mock_page = Mock()

        async def evaluate(*_args, **_kwargs):
            events.append("evaluate")
            return {"status": 200, "body": {"success": True}}

        mock_page.evaluate = AsyncMock(side_effect=evaluate)
        uploader.page = mock_page

        with patch.object(uploader, "_cloud_api_lock", fake_lock):
            result = await uploader._api_call("/api/file/list/query", {"pageNo": 1})

        assert result == {"status": 200, "body": {"success": True}}
        assert events == ["lock_enter", "evaluate", "lock_exit"]

    @pytest.mark.asyncio
    async def test_refresh_csrf_token_uses_cloud_home_route(self, uploader):
        """Refreshing auth lands on the Cloud route and waits for the XSRF cookie."""
        mock_page = Mock()
        mock_page.goto = AsyncMock()
        mock_page.wait_for_function = AsyncMock()
        uploader.page = mock_page

        await uploader._refresh_csrf_token()

        mock_page.goto.assert_awaited_once_with(
            "https://cloud.supernote.com/#/home",
            wait_until="domcontentloaded",
        )
        mock_page.wait_for_function.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_note_files_raises_auth_error_on_401(self, uploader):
        """Auth failures must not be collapsed into an empty Note folder listing."""
        uploader._api_call = AsyncMock(return_value={"status": 401, "body": ""})

        with pytest.raises(UploadAuthError, match="list/query returned 401"):
            await uploader._list_note_files()


# --- Folder support: resolve notebooks across Cloud subfolders ----------------

COS_ID = "1283876382790647808"
ROOT_ID = SupernoteUploader.NOTE_FOLDER_ID
FOLDER_TREE = {
    ROOT_ID: [
        {"id": "mgmt-1", "fileName": "Mgmt.note", "isFolder": "N"},
        {"id": COS_ID, "fileName": "cos", "isFolder": "Y"},
    ],
    COS_ID: [
        {"id": "lfw-1", "fileName": "LFW.note", "isFolder": "N"},
        {"id": "synth-1", "fileName": "Synth.note", "isFolder": "N"},
        {"id": "nav-1", "fileName": "Navicyte.note", "isFolder": "N"},
    ],
}


def _listing_api(tree):
    async def _api(endpoint, body):
        if endpoint == "/api/file/list/query":
            return {
                "status": 200,
                "body": {"userFileVOList": list(tree.get(body["directoryId"], []))},
            }
        raise AssertionError(f"unexpected api call {endpoint}")

    return _api


class TestFolderSupport:
    """Notebooks in Cloud subfolders (cos/, know/) must be resolvable by name."""

    @pytest.mark.asyncio
    async def test_resolve_root_notebook_does_not_recurse(self, uploader):
        seen = []

        async def api(endpoint, body):
            seen.append(body["directoryId"])
            return {
                "status": 200,
                "body": {"userFileVOList": list(FOLDER_TREE.get(body["directoryId"], []))},
            }

        uploader._api_call = api
        entry = await uploader._resolve_notebook_entry("Mgmt.note")
        assert entry is not None and entry["id"] == "mgmt-1"
        assert entry["_directoryId"] == ROOT_ID
        assert seen == [ROOT_ID]  # root hit short-circuits; no subfolder queries

    @pytest.mark.asyncio
    async def test_resolve_recurses_into_subfolder(self, uploader):
        uploader._api_call = _listing_api(FOLDER_TREE)
        entry = await uploader._resolve_notebook_entry("LFW.note")
        assert entry is not None and entry["id"] == "lfw-1"
        assert entry["_directoryId"] == COS_ID

    @pytest.mark.asyncio
    async def test_resolve_returns_none_when_absent(self, uploader):
        uploader._api_call = _listing_api(FOLDER_TREE)
        assert await uploader._resolve_notebook_entry("Ghost.note") is None

    @pytest.mark.asyncio
    async def test_resolve_location_defaults_new_notebook_to_root(self, uploader):
        uploader._api_call = _listing_api(FOLDER_TREE)
        file_id, directory_id = await uploader._resolve_notebook_location("NewBook.note")
        assert file_id is None
        assert directory_id == ROOT_ID  # new notebooks upload to the root Note folder

    @pytest.mark.asyncio
    async def test_download_notebook_resolves_subfolder_notebook(self, uploader):
        uploader.page = Mock()

        async def api(endpoint, body):
            if endpoint == "/api/file/list/query":
                return {
                    "status": 200,
                    "body": {"userFileVOList": list(FOLDER_TREE.get(body["directoryId"], []))},
                }
            if endpoint == "/api/file/download/url":
                assert body["id"] == "lfw-1"  # resolved cos/ file id, not a root miss
                return {"status": 200, "body": {"url": "https://s3.example.com/lfw"}}
            raise AssertionError(endpoint)

        uploader._api_call = api
        with (
            patch.object(uploader, "_ensure_authenticated", new_callable=AsyncMock),
            patch("paia_supernote.uploader.httpx.AsyncClient") as mock_client_cls,
        ):
            client = AsyncMock()
            resp = Mock()
            resp.content = b"lfw-bytes"
            resp.raise_for_status = Mock()
            client.get = AsyncMock(return_value=resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = client

            data = await uploader.download_notebook("LFW.note")
            assert data == b"lfw-bytes"

    @pytest.mark.asyncio
    async def test_upload_threads_subfolder_directory_into_apply_and_finish(
        self, uploader, mock_note_file
    ):
        uploader.page = Mock()
        with (
            patch.object(uploader, "_ensure_authenticated", new_callable=AsyncMock),
            patch.object(
                uploader, "_find_blocking_sibling_names", new_callable=AsyncMock, return_value=[]
            ),
            patch.object(
                uploader, "_resolve_notebook_location", new_callable=AsyncMock, return_value=("lfw-1", COS_ID)
            ),
            patch.object(uploader, "_delete_by_ids", new_callable=AsyncMock) as mock_del,
            patch.object(uploader, "_wait_for_target_absent", new_callable=AsyncMock),
            patch.object(
                uploader, "_initiate_upload_with_recovery", new_callable=AsyncMock, return_value={}
            ) as mock_apply,
            patch.object(uploader, "_upload_to_s3", new_callable=AsyncMock),
            patch.object(uploader, "_finish_upload", new_callable=AsyncMock) as mock_finish,
            patch.object(uploader, "_wait_for_stable_single_target", new_callable=AsyncMock),
        ):
            result = await uploader.upload_notebook(mock_note_file, "LFW.note")
            assert result is True
            mock_del.assert_awaited_once_with(["lfw-1"])  # deletes the cos/ copy by id
            _, apply_kwargs = mock_apply.call_args
            assert apply_kwargs.get("directory_id") == COS_ID
            _, finish_kwargs = mock_finish.call_args
            assert finish_kwargs.get("directory_id") == COS_ID

    @pytest.mark.asyncio
    async def test_upload_new_notebook_defaults_to_root_folder(self, uploader, mock_note_file):
        uploader.page = Mock()
        with (
            patch.object(uploader, "_ensure_authenticated", new_callable=AsyncMock),
            patch.object(
                uploader, "_find_blocking_sibling_names", new_callable=AsyncMock, return_value=[]
            ),
            patch.object(
                uploader, "_resolve_notebook_location", new_callable=AsyncMock, return_value=(None, ROOT_ID)
            ),
            patch.object(uploader, "_delete_by_ids", new_callable=AsyncMock) as mock_del,
            patch.object(
                uploader, "_initiate_upload_with_recovery", new_callable=AsyncMock, return_value={}
            ) as mock_apply,
            patch.object(uploader, "_upload_to_s3", new_callable=AsyncMock),
            patch.object(uploader, "_finish_upload", new_callable=AsyncMock) as mock_finish,
            patch.object(uploader, "_wait_for_stable_single_target", new_callable=AsyncMock),
        ):
            await uploader.upload_notebook(mock_note_file, "NewBook.note")
            mock_del.assert_not_awaited()  # nothing existing to delete
            _, apply_kwargs = mock_apply.call_args
            assert apply_kwargs.get("directory_id") == ROOT_ID
