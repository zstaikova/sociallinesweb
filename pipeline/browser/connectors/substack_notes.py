"""
Substack Notes browser connector.

Notes are short-form posts on Substack's social feed.
Unlike newsletters, the official API doesn't cover Notes — browser only.
Limit: 3 posts / 1 day, minimum 2-hour gap (SafePolicy).
"""
from pipeline.browser.base_connector import BrowserConnector
from pipeline.core.content_item import ContentItem

SUBSTACK_URL = "https://substack.com"


class SubstackNotesConnector(BrowserConnector):
    platform_id = "substack_notes"
    login_url   = "https://substack.com/sign-in"

    def __init__(self, credentials: dict = None):
        self._creds = credentials or {}

    def _detect_logged_in(self, page) -> bool:
        return (
            "substack.com" in page.url
            and "/sign-in" not in page.url
            and page.locator('button[aria-label*="Profile"], a[href*="/profile"]').count() > 0
        )

    def _fill_and_pause(self, page, item: ContentItem) -> str:
        page.goto(SUBSTACK_URL, wait_until="networkidle")
        page.wait_for_timeout(2000)

        # Open the Notes compose box
        compose_btn = page.locator(
            'button:has-text("Note"), button[aria-label*="note" i], '
            'div[placeholder*="Start a note"]'
        )
        if compose_btn.count():
            compose_btn.first.click()
            page.wait_for_timeout(1000)

        # Type content into the editor
        editor = page.locator('div[contenteditable="true"]').first
        if editor.count():
            editor.click()
            page.keyboard.type(item.caption[:500])  # Notes have a character limit

        # Attach image if available
        if item.media_path and item.media_path.exists():
            try:
                img_btn = page.locator('button[aria-label*="image" i], button[title*="Photo"]')
                if img_btn.count():
                    with page.expect_file_chooser() as fc_info:
                        img_btn.first.click()
                    fc_info.value.set_files(str(item.media_path))
                    page.wait_for_timeout(3000)
            except Exception:
                pass

        # User clicks "Post" — URL will stay on substack.com but feed updates
        # Watch for the compose box to disappear or a success toast
        return "substack.com"

    def get_account_info(self) -> dict:
        return {"platform": "substack_notes", "note": "Browser session"}
