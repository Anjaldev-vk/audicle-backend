import logging
import re
import time

from playwright.sync_api import Page

from .base import BaseMeetingBot

logger = logging.getLogger(__name__)


class ZoomBot(BaseMeetingBot):
    """
    Bot for Zoom meetings.
    Joins via browser (no Zoom client required).
    """

    def _safe_screenshot(self, page: Page, path: str) -> None:
        """Take a screenshot without crashing if fonts/resources are slow."""
        try:
            page.screenshot(path=path, timeout=5000)
        except Exception:
            logger.debug(f'Screenshot skipped (timeout): {path}')

    def join(self, page: Page) -> None:
        """Handle Zoom browser join flow."""
        try:
            page.wait_for_timeout(3000)
            self._safe_screenshot(page, "/tmp/zoom_step1_landing.png")
            logger.info("Zoom landing page URL: %s", page.url)

            # ── Step 1: Navigate to Zoom web client ──────────────────────────
            current_url = page.url
            match = re.search(r"/j/(\d+)", current_url)
            if match:
                meeting_number = match.group(1)
                pwd = ""
                pwd_match = re.search(r"pwd=([^&#]+)", current_url)
                if pwd_match:
                    pwd = f"?pwd={pwd_match.group(1)}"
                web_url = f"https://app.zoom.us/wc/join/{meeting_number}{pwd}"
                logger.info("Navigating to Zoom web client: %s", web_url)
                page.goto(web_url, wait_until="domcontentloaded", timeout=30000)
            
            # Wait for page to fully load
            page.wait_for_timeout(5000)
            self._safe_screenshot(page, "/tmp/zoom_step2_webclient.png")
            logger.info("Web client URL: %s", page.url)

            # ── Step 2: Dismiss popups ────────────────────────────────────────
            self._dismiss_cookie_banner(page)
            self._dismiss_app_popup(page)
            page.wait_for_timeout(1000)

            # ── Step 3: Fill name using React-compatible input method ─────────
            # Zoom uses React — standard fill() doesn't trigger React state.
            # We must use nativeInputValueSetter to properly update React state.
            name_filled = False
            for selector in [
                "input#inputname",
                "input[placeholder='Your Name']",
                "input[type='text']",
            ]:
                try:
                    el = page.locator(selector).first
                    if el.is_visible(timeout=3000):
                        # Click to focus
                        el.click(timeout=3000)
                        page.wait_for_timeout(500)

                        # Clear existing value
                        page.keyboard.press("Control+A")
                        page.keyboard.press("Backspace")
                        page.wait_for_timeout(300)

                        # Use React nativeInputValueSetter trick
                        page.evaluate("""
                            (selector) => {
                                const input = document.querySelector(selector);
                                if (!input) return;
                                const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                                    window.HTMLInputElement.prototype, 'value'
                                ).set;
                                nativeInputValueSetter.call(input, 'Audicle Bot');
                                input.dispatchEvent(new Event('input', { bubbles: true }));
                                input.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                        """, selector)

                        page.wait_for_timeout(500)

                        # Verify it worked
                        value = el.input_value()
                        if value and len(value) > 0:
                            logger.info("Name filled successfully: '%s'", value)
                            name_filled = True
                            break
                        else:
                            # Fallback: type character by character
                            el.click()
                            page.keyboard.press("Control+A")
                            page.keyboard.press("Backspace")
                            page.keyboard.type("Audicle Bot", delay=100)
                            page.wait_for_timeout(300)
                            value = el.input_value()
                            if value:
                                logger.info("Name filled via keyboard type: '%s'", value)
                                name_filled = True
                                break
                except Exception as exc:
                    logger.warning("Name fill attempt failed for %s: %s", selector, exc)
                    continue

            if not name_filled:
                logger.warning("Could not fill name field — attempting join anyway")

            page.wait_for_timeout(1000)
            self._safe_screenshot(page, "/tmp/zoom_step3_before_join.png")

            # ── Step 4: Handle reCAPTCHA ──────────────────────────────────────
            # Check if reCAPTCHA iframe is present
            recaptcha_count = page.locator('iframe[src*="recaptcha"]').count()
            if recaptcha_count > 0:
                logger.warning("reCAPTCHA detected — waiting for it to resolve")
                # Wait longer — sometimes reCAPTCHA auto-resolves for non-bot traffic
                page.wait_for_timeout(5000)

            # ── Step 5: Click Join button ─────────────────────────────────────
            join_clicked = False

            # Method 1: Direct Playwright click with coordinates
            try:
                join_btn = page.locator(
                    "button:has-text('Join'), "
                    "button#joinBtn, "
                    "button[class*='join-btn'], "
                    "button[class*='joinBtn']"
                ).first
                if join_btn.is_visible(timeout=3000):
                    # Scroll into view first
                    join_btn.scroll_into_view_if_needed()
                    page.wait_for_timeout(500)
                    join_btn.click(force=True, timeout=5000)
                    join_clicked = True
                    logger.info("Clicked Join button via Playwright")
            except Exception as exc:
                logger.warning("Playwright join click failed: %s", exc)

            # Method 2: JavaScript click
            if not join_clicked:
                try:
                    join_clicked = page.evaluate("""
                        () => {
                            const buttons = Array.from(
                                document.querySelectorAll('button, a, div[role="button"]')
                            );
                            for (const btn of buttons) {
                                const text = (btn.innerText || btn.textContent || '').trim();
                                if (text === 'Join' || text === 'Join Meeting') {
                                    btn.click();
                                    return true;
                                }
                            }
                            return false;
                        }
                    """)
                    if join_clicked:
                        logger.info("Clicked Join via JavaScript")
                except Exception as exc:
                    logger.warning("JS join click failed: %s", exc)

            # Method 3: Enter key
            page.keyboard.press("Enter")
            logger.info("Pressed Enter as final join attempt")

            # ── Step 6: Wait and verify ───────────────────────────────────────
            page.wait_for_timeout(8000)
            self._safe_screenshot(page, "/tmp/zoom_step4_after_join.png")

            current_url = page.url
            logger.info("After join URL: %s", current_url)

            try:
                body = page.evaluate(
                    "document.body ? document.body.innerText.substring(0, 800) : ''"
                )
                logger.info("Page body after join: %s", body)

                if "please wait" in body.lower() or "waiting for the host" in body.lower():
                    logger.info("Successfully reached waiting room")
                elif "invalid meeting id" in body.lower():
                    logger.error("Invalid meeting ID — meeting may have ended")
                elif "your name" in body.lower():
                    logger.warning("Still on join form — name/join did not submit")
            except Exception:
                pass

            # ── Step 7: Handle audio popup ────────────────────────────────────
            try:
                for audio_selector in [
                    "button:has-text('Join Audio by Computer')",
                    "button:has-text('Computer Audio')",
                    "button:has-text('Join Audio')",
                ]:
                    try:
                        audio_btn = page.locator(audio_selector).first
                        if audio_btn.is_visible(timeout=5000):
                            audio_btn.click(force=True)
                            logger.info("Clicked audio button: %s", audio_selector)
                            break
                    except Exception:
                        pass
            except Exception:
                pass

            # ── Step 8: Handle waiting room ───────────────────────────────────
            try:
                if page.locator("text=Please wait").is_visible(timeout=3000):
                    logger.info("In Zoom waiting room — waiting for host to admit")
            except Exception:
                pass

        except Exception as exc:
            logger.warning("Zoom join error: %s", exc)
            self._safe_screenshot(page, "/tmp/zoom_error.png")

    def _dismiss_cookie_banner(self, page: Page) -> None:
        """Dismiss the cookie consent banner if present."""
        for selector in [
            'button:has-text("Accept")',
            'button:has-text("Accept All")',
            'button:has-text("OK")',
            'button:has-text("Got it")',
            '.onetrust-close-btn-handler',
            '#onetrust-accept-btn-handler',
            'button[aria-label="Close"]',
            # The × close button on the cookie banner
            'button.osano-cm-dialog__close',
        ]:
            try:
                element = page.locator(selector).first
                if element.is_visible(timeout=1000):
                    element.click(force=True)
                    logger.info(f'Dismissed cookie banner via: {selector}')
                    page.wait_for_timeout(500)
                    return
            except Exception:
                pass

    def _dismiss_app_popup(self, page: Page) -> None:
        """Dismiss the 'Did not open Zoom Workplace app?' popup."""
        for selector in [
            # The × close button on the popup
            'button[aria-label="Close"]',
            '.popover-close',
            'button.close',
            # Generic close × buttons near the popup text
        ]:
            try:
                elements = page.locator(selector)
                for i in range(elements.count()):
                    try:
                        el = elements.nth(i)
                        if el.is_visible(timeout=1000):
                            el.click(force=True)
                            logger.info(f'Dismissed app popup via: {selector} (index {i})')
                            page.wait_for_timeout(500)
                    except Exception:
                        pass
            except Exception:
                pass

    def is_in_meeting(self, page: Page) -> bool:
        """Detect if bot is inside the Zoom meeting.
        
        The Zoom web client join page shows Mute/Stop Video preview controls
        even BEFORE actually joining. These must NOT be used for detection.
        
        Reliable signals:
        - URL no longer contains '/join' (Zoom redirects after joining)
        - The 'Leave' button appears (only in active meeting, not preview)
        - Waiting room text appears (means join form was submitted)
        """
        try:
            url = page.url

            # If URL still contains /join, we're on the join form page.
            # The ONLY way to be "in meeting" from this URL is if we see
            # waiting room text (join was submitted, waiting for host).
            if '/join' in url:
                try:
                    body = page.evaluate("document.body ? document.body.innerText.toLowerCase() : ''")
                    if ('please wait' in body and 'host' in body) or 'waiting for the host' in body:
                        logger.info('is_in_meeting: detected waiting room')
                        return True
                except Exception:
                    pass
                return False

            # URL changed away from /join — we're likely in the meeting.
            # Verify by checking for the Leave button.
            try:
                has_leave = page.evaluate("""
                    () => {
                        if (document.querySelector('[aria-label="Leave"]')) return true;
                        if (document.querySelector('[aria-label="leave"]')) return true;
                        if (document.querySelector('button[class*="leave"]')) return true;
                        return false;
                    }
                """)
                if has_leave:
                    logger.info('is_in_meeting: Leave button found — in meeting')
                    return True
            except Exception:
                pass

            # URL changed but no Leave button yet — might still be loading
            # Check for any meeting content indicators
            try:
                has_meeting_ui = page.evaluate("""
                    () => {
                        if (document.querySelector('#wc-container-left')) return true;
                        if (document.querySelector('.meeting-client-inner')) return true;
                        return false;
                    }
                """)
                if has_meeting_ui:
                    logger.info('is_in_meeting: meeting UI container found')
                    return True
            except Exception:
                pass

            return False
        except Exception:
            return False




    def is_meeting_ended(self, page: Page) -> bool:
        """Detect if Zoom meeting has ended."""
        try:
            for selector in [
                'text=This meeting has been ended',
                'text=The host has ended the meeting',
                'text=Meeting ended',
                'text=This meeting has ended',
            ]:
                if page.locator(selector).count() > 0:
                    return True
            return False
        except Exception:
            return False
