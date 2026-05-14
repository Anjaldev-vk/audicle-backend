import os
import sys

import pytest

BOT_SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BOT_SERVICE_DIR not in sys.path:
    sys.path.insert(0, BOT_SERVICE_DIR)

from platforms import google_meet
from platforms.google_meet import GoogleMeetBot


class FakeKeyboard:
    def __init__(self):
        self.typed = []

    def type(self, char):
        self.typed.append(char)


class FakeLocator:
    def __init__(self, visible=False, count=0):
        self.visible = visible
        self.count_value = count
        self.clicked = False

    def is_visible(self, timeout=None):
        return self.visible

    def click(self, timeout=None, force=False):
        self.clicked = True

    def count(self):
        return self.count_value


class FakePage:
    def __init__(self, url="https://meet.google.com/abc-defg-hij", content=""):
        self.url = url
        self.content_value = content
        self.keyboard = FakeKeyboard()
        self.locators = {}
        self.clicked_selectors = []
        self.goto_calls = []
        self.screenshots = []

    def locator(self, selector):
        return self.locators.get(selector, FakeLocator())

    def click(self, selector):
        self.clicked_selectors.append(selector)

    def wait_for_timeout(self, delay):
        pass

    def goto(self, url, **kwargs):
        self.goto_calls.append(url)

    def reload(self):
        pass

    def content(self):
        return self.content_value

    def screenshot(self, path, timeout=None):
        self.screenshots.append(path)

    def wait_for_selector(self, selector, timeout=None):
        if selector == '[aria-label="Leave call"]':
            return object()
        raise TimeoutError(selector)


@pytest.fixture(autouse=True)
def no_human_delay(monkeypatch):
    monkeypatch.setattr(google_meet, "human_delay", lambda *args, **kwargs: None)


@pytest.fixture
def bot(tmp_path):
    return GoogleMeetBot(
        meeting_url="https://meet.google.com/abc-defg-hij",
        audio_output_path=str(tmp_path / "audio.mp3"),
        duration_cap=5,
    )


def test_redirect_detection_raises_after_retry(bot):
    page = FakePage(url="https://workspace.google.com/products/meet/")

    with pytest.raises(RuntimeError, match="Meeting requires Google sign-in"):
        bot._handle_workspace_redirect(page)

    assert page.goto_calls == ["https://meet.google.com/abc-defg-hij"]
    assert "/tmp/workspace_redirect.png" in page.screenshots


def test_name_input_is_typed_like_human(bot):
    page = FakePage()
    page.locators["input[aria-label='Your name']"] = FakeLocator(visible=True)

    assert bot._fill_display_name(page) is True
    assert "input[aria-label='Your name']" in page.clicked_selectors
    assert "".join(page.keyboard.typed) == "Audicle Bot"


def test_join_button_is_clicked(bot):
    page = FakePage()
    join_locator = FakeLocator(visible=True)
    page.locators["text=Ask to join"] = join_locator

    assert bot._click_join_button(page) is True
    assert join_locator.clicked is True


def test_waiting_room_returns_when_bot_is_admitted(bot, monkeypatch):
    page = FakePage(content="Waiting for host to admit you")
    monkeypatch.setattr(bot, "is_in_meeting", lambda page: True)

    bot._handle_waiting_room(page)


def test_waiting_room_timeout_has_actionable_message(bot, monkeypatch):
    page = FakePage(content="Waiting for host to admit you")
    monkeypatch.setattr(bot, "is_in_meeting", lambda page: False)

    times = iter([0, 301])
    monkeypatch.setattr(google_meet.time, "time", lambda: next(times))

    with pytest.raises(RuntimeError, match="Host did not admit the bot"):
        bot._handle_waiting_room(page)
