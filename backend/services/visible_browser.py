from __future__ import annotations

from urllib.parse import quote_plus


class VisibleBrowserError(Exception):
    pass


class VisibleBrowserDemo:
    def __init__(self, *, slow_mo_ms: int = 250) -> None:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            raise VisibleBrowserError(
                "Playwright is not installed. Run 'python -m pip install -r requirements.txt'."
            ) from exc

        self._playwright_error = PlaywrightError
        self._playwright = sync_playwright().start()
        self._browser = self._launch_browser(slow_mo_ms)
        self._page = self._browser.new_page(viewport={"width": 1280, "height": 860})
        self._page.goto("about:blank")

    def _launch_browser(self, slow_mo_ms: int):
        try:
            return self._playwright.chromium.launch(
                channel="chrome",
                headless=False,
                slow_mo=slow_mo_ms,
            )
        except self._playwright_error:
            try:
                return self._playwright.chromium.launch(
                    headless=False,
                    slow_mo=slow_mo_ms,
                )
            except self._playwright_error as exc:
                self._playwright.stop()
                raise VisibleBrowserError(
                    "Could not open Chrome/Chromium. Install a browser with "
                    "'python -m playwright install chromium', then try again."
                ) from exc

    def show_search(self, query: str) -> None:
        url = f"https://duckduckgo.com/?q={quote_plus(query)}"
        self.show_url(url, wait_until="domcontentloaded")

    def show_url(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        self._page.goto(url, wait_until=wait_until, timeout=20_000)

    def is_active(self) -> bool:
        return bool(self._browser and self._browser.is_connected())

    def close(self) -> None:
        try:
            if self._browser and self._browser.is_connected():
                self._browser.close()
        finally:
            self._playwright.stop()
