"""Tests for Supernote Cloud uploader module."""

import pytest
import json
import hashlib
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from paia_supernote.uploader import SupernoteUploader, UploadAuthError


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
                "domain": "cloud.supernote.com"
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
            await uploader._initiate_upload("Quick.note", "/path/to/file.note")

    @pytest.mark.asyncio
    async def test_upload_to_s3_raises_on_missing_file(self, uploader):
        """Test that _upload_to_s3 raises FileNotFoundError for missing file."""
        upload_info = {'uploadUrl': 'https://s3.example.com/bucket/key'}
        with pytest.raises(FileNotFoundError):
            await uploader._upload_to_s3("/path/to/nonexistent/file", upload_info)

    @pytest.mark.asyncio
    async def test_finish_upload_requires_browser(self, uploader):
        """Test that _finish_upload raises RuntimeError when browser not started."""
        upload_info = {'uploadId': '123'}
        with pytest.raises(RuntimeError, match="Browser not started"):
            await uploader._finish_upload(upload_info)

    @pytest.mark.asyncio
    async def test_upload_notebook_calls_three_step_flow(self, uploader):
        """Test that upload_notebook calls the three-step upload flow."""
        uploader.page = Mock()

        with patch.object(uploader, '_ensure_authenticated', new_callable=AsyncMock) as mock_auth, \
             patch.object(uploader, '_initiate_upload', new_callable=AsyncMock) as mock_apply, \
             patch.object(uploader, '_upload_to_s3', new_callable=AsyncMock) as mock_s3, \
             patch.object(uploader, '_finish_upload', new_callable=AsyncMock) as mock_finish:

            mock_apply.return_value = {'uploadUrl': 'https://s3.example.com/key'}

            result = await uploader.upload_notebook("/path/to/test.note", "Quick.note")
            assert result is True
            mock_auth.assert_called_once()
            mock_apply.assert_called_once()
            mock_s3.assert_called_once()
            mock_finish.assert_called_once()

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

        # Patch the session file path to use our mock
        uploader.SESSION_FILE = mock_session_file

        with patch('paia_supernote.uploader.async_playwright') as mock_pw:
            mock_pw.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)

            # Start uploader
            await uploader.start()

            # Verify session file was used
            mock_browser.new_context.assert_called_once_with(
                storage_state=str(mock_session_file)
            )

    @pytest.mark.asyncio
    async def test_upload_flow_with_mocked_network(self, uploader, mock_note_file):
        """Test the complete upload flow with mocked network responses."""
        # This test will fail initially because the methods are not implemented
        # But describes the expected behavior when implemented

        uploader.page = Mock()

        # Mock the network calls to return expected data
        with patch.object(uploader, '_ensure_authenticated') as mock_auth, \
             patch.object(uploader, '_initiate_upload') as mock_apply, \
             patch.object(uploader, '_upload_to_s3') as mock_s3, \
             patch.object(uploader, '_finish_upload') as mock_finish:

            mock_auth.return_value = None
            mock_apply.return_value = {
                'uploadUrl': 'https://s3.amazonaws.com/bucket/key',
                'uploadId': 'abc123',
                'headers': {'Authorization': 'AWS4-HMAC-SHA256 ...'}
            }
            mock_s3.return_value = None
            mock_finish.return_value = None

            result = await uploader.upload_notebook(mock_note_file, "Quick.note")

            assert result is True
            mock_auth.assert_called_once()
            mock_apply.assert_called_once_with("Quick.note", mock_note_file)
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
    async def test_ensure_authenticated_checks_login_state(self, uploader):
        """Test that _ensure_authenticated detects login redirect and triggers reauth."""
        mock_page = Mock()
        mock_response = Mock()
        mock_response.status = 200
        mock_page.goto = AsyncMock(return_value=mock_response)
        mock_page.url = "https://cloud.supernote.com/login"
        uploader.page = mock_page

        with patch.object(uploader, '_interactive_reauth', new_callable=AsyncMock) as mock_reauth:
            await uploader._ensure_authenticated()

            # Should have navigated to check auth state
            mock_page.goto.assert_called_once()
            # Should have triggered reauth because URL contains /login
            mock_reauth.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_authenticated_skips_reauth_when_logged_in(self, uploader):
        """Test that _ensure_authenticated does not reauth when already logged in."""
        mock_page = Mock()
        mock_response = Mock()
        mock_response.status = 200
        mock_page.goto = AsyncMock(return_value=mock_response)
        mock_page.url = "https://cloud.supernote.com/files"
        uploader.page = mock_page

        with patch.object(uploader, '_interactive_reauth', new_callable=AsyncMock) as mock_reauth:
            await uploader._ensure_authenticated()
            mock_reauth.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_authenticated_reauths_on_401_response(self, uploader):
        """Test that _ensure_authenticated triggers reauth on 401 from navigation."""
        mock_page = Mock()
        mock_response = Mock()
        mock_response.status = 401
        mock_page.goto = AsyncMock(return_value=mock_response)
        mock_page.url = "https://cloud.supernote.com"
        uploader.page = mock_page

        with patch.object(uploader, '_interactive_reauth', new_callable=AsyncMock) as mock_reauth:
            await uploader._ensure_authenticated()
            mock_reauth.assert_called_once()

    @pytest.mark.asyncio
    async def test_initiate_upload_posts_to_apply_endpoint(self, uploader, mock_note_file):
        """Test that _initiate_upload makes POST request to upload/apply endpoint."""
        mock_page = Mock()

        # Mock the response from the apply endpoint (new {status, body} format)
        api_body = {
            'uploadUrl': 'https://s3.amazonaws.com/bucket/key?params',
            'uploadId': 'abc123',
            'headers': {'Authorization': 'AWS4-HMAC-SHA256 ...'}
        }
        mock_page.evaluate = AsyncMock(return_value={'status': 200, 'body': api_body})
        uploader.page = mock_page

        result = await uploader._initiate_upload("Quick.note", mock_note_file)

        assert result['uploadUrl'] == api_body['uploadUrl']
        assert result['uploadId'] == api_body['uploadId']
        mock_page.evaluate.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_to_s3_puts_file_to_presigned_url(self, uploader, mock_note_file):
        """Test that _upload_to_s3 uploads file to S3 presigned URL."""
        # This will fail because the method is not implemented
        upload_info = {
            'uploadUrl': 'https://s3.amazonaws.com/bucket/key?params',
            'headers': {'Authorization': 'AWS4-HMAC-SHA256 ...'}
        }

        # Should use httpx to PUT file to S3 (no session cookies needed)
        with patch('paia_supernote.uploader.httpx.AsyncClient') as mock_client_class:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.raise_for_status = Mock()

            mock_client = AsyncMock()
            mock_client.put = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            mock_client_class.return_value = mock_client

            await uploader._upload_to_s3(mock_note_file, upload_info)

            # Verify PUT request was made with correct parameters
            mock_client.put.assert_called_once()
            call_args = mock_client.put.call_args
            assert call_args[0][0] == upload_info['uploadUrl']  # URL
            assert 'content' in call_args[1]  # File content
            assert 'x-amz-content-sha256' in call_args[1]['headers']

    @pytest.mark.asyncio
    async def test_finish_upload_posts_to_finish_endpoint(self, uploader):
        """Test that _finish_upload makes POST request to upload/finish endpoint."""
        mock_page = Mock()
        upload_info = {'uploadId': 'abc123'}

        mock_page.evaluate = AsyncMock(return_value={'status': 'success'})
        uploader.page = mock_page

        # Should make API call to finish the upload
        await uploader._finish_upload(upload_info)

        # Verify the API call was made
        mock_page.evaluate.assert_called_once()
        call_args = mock_page.evaluate.call_args
        # The JavaScript should make a POST to /api/file/upload/finish
        assert '/api/file/upload/finish' in call_args[0][0]

    @pytest.mark.asyncio
    async def test_reauth_triggered_on_401_from_apply(self, uploader, mock_note_file):
        """Test that 401 from upload/apply triggers interactive re-auth and retries."""
        uploader.page = Mock()

        # First call to _initiate_upload raises UploadAuthError (401)
        # Second call succeeds after re-auth
        upload_info = {'uploadUrl': 'https://s3.example.com/key', 'headers': {}}

        call_count = 0

        async def mock_initiate(target_path, file_path):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise UploadAuthError("Upload apply returned 401")
            return upload_info

        with patch.object(uploader, '_ensure_authenticated', new_callable=AsyncMock), \
             patch.object(uploader, '_initiate_upload', side_effect=mock_initiate) as mock_apply, \
             patch.object(uploader, '_interactive_reauth', new_callable=AsyncMock) as mock_reauth, \
             patch.object(uploader, '_upload_to_s3', new_callable=AsyncMock), \
             patch.object(uploader, '_finish_upload', new_callable=AsyncMock):

            result = await uploader.upload_notebook(mock_note_file, "Quick.note")

            assert result is True
            # Re-auth was triggered after the 401
            mock_reauth.assert_called_once()
            # _initiate_upload was called twice (first 401, then retry)
            assert mock_apply.call_count == 2

    @pytest.mark.asyncio
    async def test_initiate_upload_raises_upload_auth_error_on_401(self, uploader, mock_note_file):
        """Test that _initiate_upload raises UploadAuthError on 401 response."""
        mock_page = Mock()
        mock_page.evaluate = AsyncMock(return_value={'status': 401, 'body': None})
        uploader.page = mock_page

        with pytest.raises(UploadAuthError, match="401"):
            await uploader._initiate_upload("Quick.note", mock_note_file)

    @pytest.mark.asyncio
    async def test_initiate_upload_raises_upload_auth_error_on_403(self, uploader, mock_note_file):
        """Test that _initiate_upload raises UploadAuthError on 403 response."""
        mock_page = Mock()
        mock_page.evaluate = AsyncMock(return_value={'status': 403, 'body': None})
        uploader.page = mock_page

        with pytest.raises(UploadAuthError, match="403"):
            await uploader._initiate_upload("Quick.note", mock_note_file)