"""
Teachers Pay Teachers browser connector.

Navigates to the "Upload a Resource" flow and fills in title, description,
and thumbnail. The user reviews pricing/grade/subject fields and clicks Publish.
Limit: 5 posts / 7 days.
"""
from pipeline.browser.base_connector import BrowserConnector
from pipeline.core.content_item import ContentItem

UPLOAD_URL = "https://www.teacherspayteachers.com/Sell/Product/new"


class TPTConnector(BrowserConnector):
    platform_id = "tpt"
    login_url   = "https://www.teacherspayteachers.com/Login"

    def __init__(self, credentials: dict = None):
        self._creds = credentials or {}

    def _detect_logged_in(self, page) -> bool:
        return (
            "teacherspayteachers.com" in page.url
            and "/Login" not in page.url
            and page.locator('a[href*="/My-Store"]').count() > 0
        )

    def _fill_and_pause(self, page, item: ContentItem) -> str:
        page.goto(UPLOAD_URL, wait_until="networkidle")
        page.wait_for_timeout(2000)

        lines = item.caption.split("\n")
        title = lines[0][:80]
        description = "\n".join(lines[1:]).strip() or item.caption

        # Title field
        title_inp = page.locator('input[name="title"], input[placeholder*="title" i]').first
        if title_inp.count():
            title_inp.fill(title)

        # Description — TinyMCE or plain textarea
        try:
            frame = page.frame_locator('iframe[id*="description"]')
            body = frame.locator("body[contenteditable]")
            body.click()
            body.type(description)
        except Exception:
            desc = page.locator('textarea[name="description"]').first
            if desc.count():
                desc.fill(description)

        # Thumbnail upload
        if item.media_path and item.media_path.exists():
            try:
                file_input = page.locator('input[type="file"][accept*="image"]').first
                if file_input.count():
                    file_input.set_input_files(str(item.media_path))
                    page.wait_for_timeout(3000)
            except Exception:
                pass

        # User completes grade/subject/price and submits
        return "teacherspayteachers.com/Product/"

    def get_account_info(self) -> dict:
        return {"platform": "tpt", "note": "Browser session"}
