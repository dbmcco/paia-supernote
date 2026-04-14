"""
ABOUTME: Script to probe Supernote Cloud API — inspect current file state.
ABOUTME: Uses the saved Playwright session to authenticate API calls.

Run from paia-supernote root:
    uv run python scripts/probe_cloud_api.py
"""

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

SESSION_FILE = Path("~/.paia/supernote/session.json").expanduser()
CLOUD_URL = "https://cloud.supernote.com"
NOTE_FOLDER_ID = "955311389939859457"


async def api_call(page, endpoint: str, body: dict) -> dict:
    result = await page.evaluate("""
        async ([endpoint, body]) => {
            const xsrfCookie = document.cookie.split(';')
                .map(c => c.trim())
                .find(c => c.startsWith('XSRF-TOKEN='));
            const xsrfToken = xsrfCookie ? decodeURIComponent(xsrfCookie.split('=')[1]) : '';
            const resp = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-XSRF-TOKEN': xsrfToken },
                body: JSON.stringify(body),
            });
            return { status: resp.status, body: resp.ok ? await resp.json() : await resp.text() };
        }
    """, [endpoint, body])
    return result


async def delete_file(page, file_id: str) -> dict:
    result = await page.evaluate("""
        async (fileId) => {
            const xsrfCookie = document.cookie.split(';')
                .map(c => c.trim())
                .find(c => c.startsWith('XSRF-TOKEN='));
            const xsrfToken = xsrfCookie ? decodeURIComponent(xsrfCookie.split('=')[1]) : '';
            const resp = await fetch('/api/file/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-XSRF-TOKEN': xsrfToken },
                body: JSON.stringify({ fileIdList: [fileId] }),
            });
            return { status: resp.status, body: resp.ok ? await resp.json() : await resp.text() };
        }
    """, file_id)
    return result


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=str(SESSION_FILE))
        page = await context.new_page()
        await page.goto(f"{CLOUD_URL}/#/home", wait_until="networkidle")
        print("Auth OK\n")

        # List Note folder — show all Personal.note entries
        result = await api_call(page, "/api/file/list/query", {
            "directoryId": NOTE_FOLDER_ID,
            "pageNo": 1,
            "pageSize": 100,
            "order": "time",
            "filterType": 0,
        })
        files = result['body'].get('userFileVOList', [])
        print(f"Note folder — {result['body'].get('total', '?')} total items:")
        personal_files = []
        for f in files:
            marker = " <-- Personal.note" if f['fileName'] == 'Personal.note' else ""
            print(f"  [{f['isFolder']}] {f['fileName']:40s} id={f['id']}  size={f['size']:>10,}{marker}")
            if f['fileName'] == 'Personal.note':
                personal_files.append(f)

        print(f"\nFound {len(personal_files)} Personal.note entries:")
        for f in personal_files:
            from datetime import datetime
            updated = datetime.fromtimestamp(f['updateTime'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
            print(f"  id={f['id']}  size={f['size']:,}  updated={updated}")

        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
