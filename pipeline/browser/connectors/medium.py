"""
Medium browser connector.

Compose URL: https://medium.com/new-story
After publish the URL changes to https://medium.com/@{username}/{slug}-{id}
Limit: 3 posts / 7 days (SafePolicy).
"""
from pipeline.browser.base_connector import BrowserConnector
from pipeline.core.content_item import ContentItem


class MediumConnector(BrowserConnector):
    platform_id = "medium"
    login_url   = "https://medium.com/m/signin"

    def __init__(self, credentials: dict = None):
        self._creds = credentials or {}

    def _detect_logged_in(self, page) -> bool:
        return "medium.com" in page.url and "/m/signin" not in page.url

    def _fill_and_pause(self, page, item: ContentItem) -> str:
        page.goto("https://medium.com/new-story", wait_until="networkidle")
        page.wait_for_timeout(2000)

        # Title — first contenteditable with data-testid or the first h3
        title_sel = 'h3[data-testid="editor-title-input"], div[data-testid="editor-title"]'
        try:
            page.locator(title_sel).first.click()
            page.keyboard.type(item.caption.split("\n")[0][:200])
        except Exception:
            pass

        # Body
        body_text = "\n".join(item.caption.split("\n")[1:]).strip() or item.caption
        body_sel = 'div[data-testid="editor-body"]'
        try:
            page.locator(body_sel).first.click()
            page.keyboard.press("End")
            page.keyboard.type(body_text)
        except Exception:
            pass

        # Image — if media_path exists, upload via the add-image toolbar
        if item.media_path and item.media_path.exists():
            try:
                # Click the + add button to open the inline toolbar
                page.keyboard.press("Enter")
                add_btn = page.locator('button[aria-label="Add an image"]')
                if add_btn.count():
                    with page.expect_file_chooser() as fc_info:
                        add_btn.first.click()
                    fc = fc_info.value
                    fc.set_files(str(item.media_path))
                    page.wait_for_timeout(3000)
            except Exception:
                pass

        # Browser stays open — user reviews and clicks Publish
        # We watch for the URL to change away from /new-story
        return "medium.com/@"

    def get_account_info(self) -> dict:
        return {"platform": "medium", "note": "Browser session"}
