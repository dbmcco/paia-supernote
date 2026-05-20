"""
ABOUTME: Supernote Cloud uploader — three-step flow (apply → S3 PUT → finish).
ABOUTME: Uses Playwright browser context for auth; httpx for the direct S3 PUT.

Upload flow (calibrated against live Supernote Cloud 2026-04-14):
  1. POST /api/file/upload/apply  → presigned S3 URL + s3Authorization + xamzDate
  2. PUT  <s3_url>                → S3 direct upload
                                      (Authorization + x-amz-date headers)
  3. POST /api/file/upload/finish → { innerName: <s3_object_key>, ... }

All notebooks (Quick.note, LFW.note, Synth.note) live in the "Note" folder,
directoryId = NOTE_FOLDER_ID.  Files sync at SYNC_BASE on Mac.
"""

import asyncio
from contextlib import asynccontextmanager
import fcntl
import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from playwright.async_api import Browser, BrowserContext, Page, async_playwright


class UploadAuthError(Exception):
    """Raised when an upload API call returns 401/403, indicating session expiry."""


class UploadSyncInProgressError(RuntimeError):
    """Raised when Supernote Cloud reports the target is still syncing."""


class SupernoteUploadConflictError(RuntimeError):
    """Raised when a target has duplicate/conflict cloud copies."""


_UPLOAD_LOCKS: dict[str, asyncio.Lock] = {}


class SupernoteUploader:
    """Handles uploads to Supernote Cloud via Playwright browser automation."""

    CLOUD_URL = "https://cloud.supernote.com"
    CLOUD_HOME_URL = f"{CLOUD_URL}/#/home"
    SESSION_FILE = Path("~/.paia/supernote/session.json").expanduser()
    CLOUD_API_LOCK_FILE = Path("~/.paia/supernote/cloud-api.lock").expanduser()

    # All user notebooks live in the "Note" folder on the cloud
    NOTE_FOLDER_ID = "955311389939859457"

    # Local Partner-app sync path
    SYNC_BASE = Path(
        "~/Library/Containers/com.ratta.supernote/Data/Library/"
        "Application Support/com.ratta.supernote/908410628964298752/Supernote/Note"
    ).expanduser()

    def __init__(self) -> None:
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._playwright = None
        self._recovery_lock = asyncio.Lock()

    async def start(self) -> None:
        """Launch browser and restore session if available."""
        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(headless=True)

        if self.SESSION_FILE.exists():
            self.context = await self.browser.new_context(
                storage_state=str(self.SESSION_FILE)
            )
        else:
            self.context = await self.browser.new_context()

        self.page = await self.context.new_page()
        await self._refresh_csrf_token()

    async def stop(self) -> None:
        """Persist session and close browser. Safe to call more than once."""
        if self.context:
            try:
                self.SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
                await self.context.storage_state(path=str(self.SESSION_FILE))
            except Exception:
                pass  # context already closed (double-stop or forced shutdown)
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = None
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
            self.browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    async def upload_notebook(self, notebook_path: str, target_name: str) -> bool:
        """Upload .note file to Supernote Cloud (three-step flow).

        Safe replace strategy: record the existing file ID first, upload the new
        version (cloud creates a versioned duplicate), then delete the OLD file
        by its saved ID. If the upload fails, the original is untouched.

        Args:
            notebook_path: Local path to the .note file.
            target_name:   Cloud filename, e.g. "Quick.note".

        On 401/403 from the apply step, triggers interactive re-auth and retries once.
        Returns True on success.
        """
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")

        async with _upload_lock(target_name):
            await self._ensure_authenticated()

            blocking_siblings = await self._find_blocking_sibling_names(target_name)
            if blocking_siblings:
                joined = ", ".join(blocking_siblings)
                raise SupernoteUploadConflictError(
                    f"{target_name} has blocking cloud copies: {joined}"
                )

            # Delete existing file(s) BEFORE uploading so the new upload gets the
            # correct name. If the old file exists when we upload, the cloud
            # auto-increments to "test(1).note" etc.
            existing_ids = await self._find_file_ids(target_name)
            if existing_ids:
                await self._delete_by_ids(existing_ids)
                await self._wait_for_target_absent(target_name)

            upload_info = await self._initiate_upload_with_recovery(
                notebook_path, target_name
            )

            if not upload_info.get("_skip_s3"):
                await self._upload_to_s3(notebook_path, upload_info)
            await self._finish_upload(notebook_path, target_name, upload_info)
            await self._wait_for_stable_single_target(target_name)

            return True

    async def download_notebook(self, target_name: str) -> bytes:
        """Download a .note file from Supernote Cloud by filename.

        Looks up the file ID in the Note folder, fetches a presigned download
        URL via /api/file/download/url, then GETs the bytes from S3.

        Args:
            target_name: Cloud filename, e.g. "Quick.note".

        Returns:
            Raw .note bytes.

        Raises:
            RuntimeError: If browser not started, file not found, or download fails.
        """
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")

        try:
            file_ids = await self._find_file_ids(target_name)
        except UploadAuthError:
            await self._restart_browser_session()
            file_ids = await self._find_file_ids(target_name)
        if not file_ids:
            raise RuntimeError(f"{target_name!r} not found in Note folder")

        # Use the most recent entry; list/query returns newest first by time.
        file_id = file_ids[0]

        result = await self._api_call("/api/file/download/url", {
            "id": file_id,
            "type": "1",
        })
        if result["status"] != 200 or not isinstance(result["body"], dict):
            raise RuntimeError(
                f"download/url failed ({result['status']}): {result['body']}"
            )

        download_url = result["body"].get("url")
        if not download_url:
            raise RuntimeError(f"No URL in download/url response: {result['body']}")

        async with httpx.AsyncClient() as client:
            response = await client.get(download_url, timeout=120.0)
            response.raise_for_status()
            return response.content

    async def _find_file_ids(self, target_name: str) -> List[str]:
        """Return cloud file IDs matching target_name in the Note folder."""
        files = await self._list_note_files()
        return [
            f["id"] for f in files
            if f.get("fileName") == target_name and f.get("isFolder") == "N"
        ]

    async def _list_note_files(self) -> list[dict[str, Any]]:
        """Return current file entries in the cloud Note folder."""
        result = await self._api_call("/api/file/list/query", {
            "directoryId": self.NOTE_FOLDER_ID,
            "pageNo": 1,
            "pageSize": 200,
            "order": "time",
            "sequence": "desc",
            "filterType": 0,
        })
        if result["status"] in (401, 403):
            raise UploadAuthError(f"list/query returned {result['status']}")
        if result["status"] != 200 or not isinstance(result["body"], dict):
            return []
        return list(result["body"].get("userFileVOList", []))

    async def _find_blocking_sibling_names(self, target_name: str) -> list[str]:
        """Return conflict or numbered copies for target_name in the Note folder."""
        stem = target_name.removesuffix(".note")
        files = await self._list_note_files()
        return sorted(
            {
                str(f.get("fileName") or "")
                for f in files
                if f.get("isFolder") == "N"
                and _is_blocking_sibling_name(str(f.get("fileName") or ""), stem)
            }
        )

    async def _delete_by_ids(self, file_ids: list) -> None:
        """Delete cloud files by ID list. No-op on empty list."""
        if not file_ids:
            return
        result = await self._api_call("/api/file/delete", {
            "idList": file_ids,
            "directoryId": self.NOTE_FOLDER_ID,
        })
        if result.get("status") != 200:
            raise RuntimeError(
                f"delete failed ({result.get('status')}): {result.get('body')}"
            )
        body = result.get("body")
        if isinstance(body, dict) and not body.get("success", False):
            raise RuntimeError(f"delete failed: {body}")

    async def _ensure_authenticated(self) -> None:
        """Check auth state; trigger interactive re-auth when session expired."""
        async def do_check() -> None:
            if not self.page:
                raise RuntimeError("Browser not started")

            response = await self.page.goto(
                self.CLOUD_HOME_URL, wait_until="networkidle"
            )

            needs_reauth = False
            if response and response.status in (401, 403):
                needs_reauth = True
            elif "login" in self.page.url:
                needs_reauth = True

            if needs_reauth:
                await self._interactive_reauth()

        await self._retry_on_closed_target(do_check)

    async def _refresh_csrf_token(self) -> None:
        """Reload the cloud app route so the browser session gets a fresh XSRF token."""

        async def do_refresh() -> None:
            if not self.page:
                raise RuntimeError("Browser not started")
            await self.page.goto(self.CLOUD_HOME_URL, wait_until="networkidle")

        await self._retry_on_closed_target(do_refresh)

    async def _interactive_reauth(self) -> None:
        """Open login page and wait for user to complete authentication."""
        if not self.page:
            raise RuntimeError("Browser not started")

        await self.page.goto(f"{self.CLOUD_URL}/#/login")
        # SPA hash routing: wait until hash no longer contains /login
        await self.page.wait_for_function(
            "() => !window.location.hash.includes('/login')",
            timeout=300_000,
        )

        if self.context:
            self.SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            await self.context.storage_state(path=str(self.SESSION_FILE))

    async def _api_call(self, endpoint: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Make an authenticated API call via the Playwright page context."""
        async def do_call() -> Dict[str, Any]:
            if not self.page:
                raise RuntimeError("Browser not started")
            result = await self.page.evaluate("""
                async ([endpoint, body]) => {
                    const xsrfCookie = document.cookie.split(';')
                        .map(c => c.trim())
                        .find(c => c.startsWith('XSRF-TOKEN='));
                    const xsrfToken = xsrfCookie
                        ? decodeURIComponent(xsrfCookie.split('=')[1])
                        : '';

                    const resp = await fetch(endpoint, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-XSRF-TOKEN': xsrfToken,
                        },
                        body: JSON.stringify(body),
                    });
                    return {
                        status: resp.status,
                        body: resp.ok ? await resp.json() : await resp.text(),
                    };
                }
            """, [endpoint, body])
            return result  # type: ignore[return-value]

        async with self._cloud_api_lock():
            result = await self._retry_on_closed_target(do_call)
            if self._is_csrf_expired_response(result):
                await self._refresh_csrf_token()
                result = await self._retry_on_closed_target(do_call)
        return result  # type: ignore[return-value]

    @asynccontextmanager
    async def _cloud_api_lock(self):
        """Serialize Supernote Cloud API calls across service processes."""
        self.CLOUD_API_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.CLOUD_API_LOCK_FILE.open("a+")
        try:
            await asyncio.to_thread(fcntl.flock, lock_file.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            await asyncio.to_thread(fcntl.flock, lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()

    async def _retry_on_closed_target(self, operation):
        try:
            return await operation()
        except Exception as exc:
            if not self._is_closed_target_error(exc):
                raise
            await self._restart_browser_session()
            return await operation()

    async def _restart_browser_session(self) -> None:
        async with self._recovery_lock:
            await self.stop()
            await self.start()

    @staticmethod
    def _is_closed_target_error(exc: Exception) -> bool:
        message = str(exc)
        return "Target page, context or browser has been closed" in message

    @staticmethod
    def _is_csrf_expired_response(result: Dict[str, Any]) -> bool:
        """Treat any 403 as potentially CSRF-related — refresh token and retry once."""
        return result.get("status") == 403

    async def _initiate_upload(
        self, file_path: str, target_name: str
    ) -> Dict[str, Any]:
        """POST /api/file/upload/apply — returns S3 presigned URL + auth headers."""
        if not self.page:
            raise RuntimeError("Browser not started")

        data = Path(file_path).read_bytes()
        md5 = hashlib.md5(data).hexdigest()
        size = len(data)

        result = await self._api_call("/api/file/upload/apply", {
            "directoryId": self.NOTE_FOLDER_ID,
            "fileName": target_name,
            "md5": md5,
            "size": size,
        })

        if result["status"] in (401, 403):
            raise UploadAuthError(f"upload/apply returned {result['status']}")
        if result["status"] != 200 or not isinstance(result["body"], dict):
            raise RuntimeError(
                f"upload/apply failed ({result['status']}): {result['body']}"
            )

        body = result["body"]
        # E0310 = identical MD5 already exists; go straight to finish.
        # Mark with a flag so upload_notebook can skip _upload_to_s3.
        if not body.get("success") and body.get("errorCode") == "E0310":
            body["_skip_s3"] = True
            return body

        if not body.get("success") and body.get("errorCode") == "E0301":
            raise UploadSyncInProgressError(
                str(body.get("errorMsg") or "Sync in progress")
            )

        if not body.get("success"):
            raise RuntimeError(f"upload/apply error: {body}")

        return body

    async def _initiate_upload_with_recovery(
        self,
        notebook_path: str,
        target_name: str,
    ) -> Dict[str, Any]:
        """Initiate upload with auth recovery and bounded sync-in-progress waits."""
        attempts = 0
        while True:
            attempts += 1
            try:
                return await self._initiate_upload(notebook_path, target_name)
            except UploadAuthError:
                if attempts > 1:
                    raise
                await self._interactive_reauth()
            except UploadSyncInProgressError:
                if attempts >= 6:
                    raise
                await asyncio.sleep(min(2 ** attempts, 10))

    async def _wait_for_target_absent(self, target_name: str) -> None:
        """Wait briefly for a deleted target name to disappear from cloud listing."""
        for _ in range(10):
            if not await self._find_file_ids(target_name):
                return
            await asyncio.sleep(0.5)
        raise RuntimeError(f"{target_name} still exists after delete")

    async def _wait_for_stable_single_target(self, target_name: str) -> None:
        """Verify the upload settled as exactly one target and no generated siblings."""
        await asyncio.sleep(3)
        exact_ids = await self._find_file_ids(target_name)
        blocking_siblings = await self._find_blocking_sibling_names(target_name)
        if len(exact_ids) != 1 or blocking_siblings:
            details = {
                "exact_count": len(exact_ids),
                "blocking_siblings": blocking_siblings,
            }
            raise SupernoteUploadConflictError(
                f"{target_name} did not settle as a single cloud notebook: {details}"
            )

    async def _upload_to_s3(
        self, file_path: str, upload_info: Dict[str, Any]
    ) -> None:
        """PUT file bytes directly to S3 using the presigned URL."""
        file_data = Path(file_path).read_bytes()
        s3_url = upload_info["url"]

        headers = {
            "Authorization": upload_info["s3Authorization"],
            "x-amz-date": upload_info["xamzDate"],
            "x-amz-content-sha256": "UNSIGNED-PAYLOAD",
        }

        async with httpx.AsyncClient() as client:
            response = await client.put(
                s3_url,
                content=file_data,
                headers=headers,
                timeout=120.0,
            )
            response.raise_for_status()

    @staticmethod
    def _compute_file_md5(file_path: str) -> str:
        """Return hex MD5 of file contents."""
        return hashlib.md5(Path(file_path).read_bytes()).hexdigest()

    async def _finish_upload(
        self,
        file_path: str,
        target_name: str,
        upload_info: Dict[str, Any],
    ) -> None:
        """POST /api/file/upload/finish to link S3 object to the account."""
        if not self.page:
            raise RuntimeError("Browser not started")

        data = Path(file_path).read_bytes()
        md5 = hashlib.md5(data).hexdigest()
        size = len(data)

        # innerName: for a normal upload, extract from the presigned S3 URL path.
        # For E0310 dedup, the apply response returns the inner key directly in 'url'.
        raw_url = upload_info["url"]
        if upload_info.get("_skip_s3"):
            # Dedup case: 'url' is already the S3 key (not a presigned URL)
            s3_key = raw_url
        else:
            s3_key = urlparse(raw_url).path.lstrip("/")

        result = await self._api_call("/api/file/upload/finish", {
            "directoryId": self.NOTE_FOLDER_ID,
            "fileName": target_name,
            "md5": md5,
            "size": size,
            "fileSize": size,
            "fileServer": upload_info.get("fileServer", "2"),
            "innerName": s3_key,
        })

        if result["status"] != 200 or not isinstance(result["body"], dict):
            raise RuntimeError(
                f"upload/finish failed ({result['status']}): {result['body']}"
            )
        if not result["body"].get("success"):
            raise RuntimeError(f"upload/finish error: {result['body']}")


def _upload_lock(target_name: str) -> asyncio.Lock:
    key = target_name.casefold()
    lock = _UPLOAD_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _UPLOAD_LOCKS[key] = lock
    return lock


def _is_blocking_sibling_name(file_name: str, target_stem: str) -> bool:
    lower_name = file_name.casefold()
    lower_stem = target_stem.casefold()
    if not lower_name.endswith(".note"):
        return False
    if lower_name.startswith(f"{lower_stem}_") and "conflict" in lower_name:
        return True
    return (
        re.fullmatch(rf"{re.escape(lower_stem)}\(\d+\)\.note", lower_name)
        is not None
    )
