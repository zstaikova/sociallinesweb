"""
Quora browser connector.

Quora requires a specific question URL to answer — stored in platform_options
on the ContentItem or as a default in credentials. Limit: 1 post / 1 day.

Credentials key: QUORA_QUESTION_URL (optional default question)
"""
from pipeline.browser.base_connector import BrowserConnector
from pipeline.core.content_item import ContentItem


class QuoraConnector(BrowserConnector):
    platform_id = "quora"
    login_url   = "https://www.quora.com"

    def __init__(self, credentials: dict = None):
        self._creds = credentials or {}
        self._default_question = self._creds.get("QUORA_QUESTION_URL", "")

    def _detect_logged_in(self, page) -> bool:
        return (
            "quora.com" in page.url
            and "/login" not in page.url
            and page.locator('div[data-testid="profile-link"]').count() > 0
        )

    def _fill_and_pause(self, page, item: ContentItem) -> str:
        question_url = (
            item.metadata.get("quora_question_url")
            or self._default_question
        )
        if not question_url:
            raise ValueError(
                "Quora connector requires a question URL. "
                "Set QUORA_QUESTION_URL in credentials or pass quora_question_url in item metadata."
            )

        page.goto(question_url, wait_until="networkidle")
        page.wait_for_timeout(2000)

        # Click "Answer" button
        answer_btn = page.locator('a[href*="/answer"], button:has-text("Answer")')
        if answer_btn.count():
            answer_btn.first.click()
            page.wait_for_timeout(1500)

        # Type the answer in the editor
        editor = page.locator('div[contenteditable="true"]').first
        if editor.count():
            editor.click()
            page.keyboard.type(item.caption)

        # Image upload if available
        if item.media_path and item.media_path.exists():
            try:
                img_btn = page.locator('button[aria-label*="image"], button[title*="Image"]')
                if img_btn.count():
                    with page.expect_file_chooser() as fc_info:
                        img_btn.first.click()
                    fc_info.value.set_files(str(item.media_path))
                    page.wait_for_timeout(3000)
            except Exception:
                pass

        # User submits — watch for /answer/ in URL (submitted answer page)
        return "quora.com"

    def get_account_info(self) -> dict:
        return {"platform": "quora", "note": "Browser session"}
