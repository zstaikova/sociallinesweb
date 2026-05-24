"""
Base class for browser-automation connectors.

Flow for first-time login:
  connector.first_login()   → opens headed browser, user logs in, session saved

Flow for posting:
  connector.publish(item)   → loads session, fills form, pauses, detects submission
"""
import os
import threading
from abc import abstractmethod
from pathlib import Path

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem
from pipeline.browser.sessions import BrowserSessionStore
from pipeline.browser.policy import SafePolicy

_session_store = BrowserSessionStore()
_policy = SafePolicy()

# How long to wait for the user to submit the paused form (seconds)
USER_SUBMIT_TIMEOUT = 600


class BrowserConnector(BasePublisher):
    """
    Abstract browser-based publisher.  Subclasses implement:
      - platform_id: str property
      - login_url: str property (where to navigate for first_login)
      - _detect_logged_in(page) -> bool
      - _fill_and_pause(page, item) -> str  (URL to watch for after user submits)
      - _detect_submitted(page, watch_url) -> bool
    """

    @property
    @abstractmethod
    def platform_id(self) -> str:
        ...

    @property
    @abstractmethod
    def login_url(self) -> str:
        ...

    @abstractmethod
    def _detect_logged_in(self, page) -> bool:
        """Return True if the current page state shows a logged-in user."""
        ...

    @abstractmethod
    def _fill_and_pause(self, page, item: ContentItem) -> str:
        """
        Navigate to compose page, fill all fields, then return the URL prefix
        that indicates the post was submitted (e.g. 'medium.com/@' for published).
        The browser stays open for the user to review and click Publish.
        """
        ...

    # ── session management ──────────────────────────────────────────────────

    def _is_headed(self) -> bool:
        return os.environ.get("BROWSER_HEADLESS", "1") == "0"

    def _launch(self, playwright, headless: bool | None = None):
        from playwright.sync_api import BrowserType
        chromium: BrowserType = playwright.chromium
        if headless is None:
            headless = not self._is_headed()
        return chromium.launch(headless=headless)

    def _new_context(self, browser, storage_state: dict | None = None):
        kwargs = {}
        if storage_state:
            kwargs["storage_state"] = storage_state
        return browser.new_context(**kwargs)

    def _save_session(self, context):
        state = context.storage_state()
        _session_store.save(self.platform_id, state)

    # ── public API ──────────────────────────────────────────────────────────

    def first_login(self, on_saved: callable = None):
        """
        Open a headed browser so the user can log in manually.
        Blocks until login is detected, then saves the session.
        Called from a background thread via the /api/accounts/browser-login endpoint.
        """
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = self._launch(pw, headless=False)
            context = self._new_context(browser)
            page = context.new_page()
            page.goto(self.login_url)

            # Poll until logged in (up to 5 minutes)
            deadline = 300
            elapsed = 0
            while elapsed < deadline:
                page.wait_for_timeout(2000)
                elapsed += 2
                if self._detect_logged_in(page):
                    break

            self._save_session(context)
            browser.close()

        if on_saved:
            on_saved()

    def session_exists(self) -> bool:
        return _session_store.exists(self.platform_id)

    def clear_session(self):
        _session_store.delete(self.platform_id)

    def publish(self, item: ContentItem) -> bool:
        if not self.session_exists():
            print(f"[{self.platform_id}] No session — run first_login() first")
            return False

        _policy.check_and_record(self.platform_id)  # raises PolicyViolation if over limit

        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            storage_state = _session_store.load(self.platform_id)
            browser = self._launch(pw, headless=False)  # always headed so user can submit
            context = self._new_context(browser, storage_state)
            page = context.new_page()

            try:
                watch_url = self._fill_and_pause(page, item)

                # Wait for user to submit (URL changes to watch_url prefix)
                submitted = False
                elapsed = 0
                while elapsed < USER_SUBMIT_TIMEOUT:
                    page.wait_for_timeout(2000)
                    elapsed += 2
                    if watch_url and page.url.startswith(watch_url):
                        submitted = True
                        break
                    if watch_url and watch_url in page.url:
                        submitted = True
                        break

                if submitted:
                    self._save_session(context)

                return submitted
            finally:
                browser.close()

    def verify_auth(self) -> bool:
        return self.session_exists()
