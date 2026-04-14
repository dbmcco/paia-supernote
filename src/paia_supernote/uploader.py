"""
ABOUTME: Supernote Cloud uploader module
Author: Braydon McCormick <braydon@braydondm.com>
Purpose: Pushes merged .note files to Supernote Cloud using Playwright browser automation

NOTE: The three-step upload flow (upload/apply → S3 PUT → upload/finish) requires an
authenticated Supernote Cloud session.  The auth + upload API calls need to be developed
against a live session — they are stubbed with clear raise points until that calibration
pass happens.  See design spec "Known Open Items #3".
"""

import hashlib
from typing import Optional, Dict, Any
from pathlib import Path

import httpx
from playwright.async_api import async_playwright, Browser, BrowserContext, Page


class SupernoteUploader:
    """Handles uploads to Supernote Cloud via Playwright browser automation."""

    CLOUD_URL = "https://cloud.supernote.com"
    SESSION_FILE = Path("~/.cache/paia-supernote/session.json").expanduser()

    def __init__(self) -> None:
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._playwright = None

    async def start(self) -> None:
        """Launch browser and restore session if available."""
        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(headless=False)

        if self.SESSION_FILE.exists():
            self.context = await self.browser.new_context(
                storage_state=str(self.SESSION_FILE)
            )
        else:
            self.context = await self.browser.new_context()

        self.page = await self.context.new_page()

    async def stop(self) -> None:
        """Persist session and close browser."""
        if self.context:
            self.SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            await self.context.storage_state(path=str(self.SESSION_FILE))
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def upload_notebook(self, notebook_path: str, target_path: str) -> bool:
        """Upload .note file to Supernote Cloud (three-step flow)."""
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")

        await self._ensure_authenticated()

        upload_info = await self._initiate_upload(target_path, notebook_path)
        await self._upload_to_s3(notebook_path, upload_info)
        await self._finish_upload(upload_info)
        return True

    # ----- stubs: require live session calibration (spec Known Open Items #3) -----

    async def _ensure_authenticated(self) -> None:
        """Check auth state; re-authenticate interactively when token expires."""
        if not self.page:
            raise RuntimeError("Browser not started")

        # Navigate to cloud to check auth state
        await self.page.goto(self.CLOUD_URL)

        # If we're redirected to login page, authentication is needed
        current_url = self.page.url
        if "/login" in current_url:
            # For now, just navigate to the main page to check
            # TODO: Implement interactive re-authentication
            await self.page.goto(f"{self.CLOUD_URL}/files")

    async def _initiate_upload(self, target_path: str, file_path: str) -> Dict[str, Any]:
        """POST upload/apply to get presigned S3 URL."""
        if not self.page:
            raise RuntimeError("Browser not started")

        # Compute file metadata
        file_size = Path(file_path).stat().st_size
        file_md5 = self._compute_file_md5(file_path)
        file_name = Path(file_path).name

        # Make POST request to upload/apply endpoint
        apply_data = {
            'directoryId': self._get_directory_id(target_path),
            'fileName': file_name,
            'md5': file_md5,
            'size': file_size
        }

        # Use Playwright to make the API call with session cookies and CSRF token
        upload_info = await self.page.evaluate("""
            async (data) => {
                const response = await fetch('/api/file/upload/apply', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-XSRF-TOKEN': document.querySelector('meta[name="csrf-token"]')?.content || ''
                    },
                    body: JSON.stringify(data)
                });
                if (!response.ok) {
                    throw new Error(`Upload apply failed: ${response.status}`);
                }
                return await response.json();
            }
        """, apply_data)

        return upload_info

    async def _upload_to_s3(self, file_path: str, upload_info: Dict[str, Any]) -> None:
        """PUT file to S3 using presigned URL (no auth header needed)."""
        file_data = Path(file_path).read_bytes()
        upload_url = upload_info['uploadUrl']

        headers = {
            'x-amz-content-sha256': 'UNSIGNED-PAYLOAD',
        }

        # Add any additional headers from upload_info
        if 'headers' in upload_info:
            headers.update(upload_info['headers'])

        async with httpx.AsyncClient() as client:
            response = await client.put(
                upload_url,
                content=file_data,
                headers=headers
            )
            response.raise_for_status()

    async def _finish_upload(self, upload_info: Dict[str, Any]) -> None:
        """POST upload/finish to confirm the upload."""
        if not self.page:
            raise RuntimeError("Browser not started")

        await self.page.evaluate("""
            async (data) => {
                const response = await fetch('/api/file/upload/finish', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-XSRF-TOKEN': document.querySelector('meta[name="csrf-token"]')?.content || ''
                    },
                    body: JSON.stringify(data)
                });
                if (!response.ok) {
                    throw new Error(`Upload finish failed: ${response.status}`);
                }
                return await response.json();
            }
        """, upload_info)

    def _compute_file_md5(self, file_path: str) -> str:
        """Compute MD5 hash of file contents."""
        file_data = Path(file_path).read_bytes()
        return hashlib.md5(file_data).hexdigest()

    def _get_directory_id(self, target_path: str) -> str:
        """Get directory ID for target notebook."""
        # TODO: Implement directory ID mapping
        # For now, return a placeholder
        directory_mapping = {
            "Quick.note": "quick_dir_id",
            "LFW.note": "lfw_dir_id",
            "Synth.note": "synth_dir_id"
        }
        return directory_mapping.get(target_path, "default_dir_id")