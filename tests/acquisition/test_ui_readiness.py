from erp_assistant.acquisition.browser.ui_readiness import UIReadinessWaiter


class FakeLocator:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    def is_visible(self):
        return self.page.current_visible


class FakePage:
    def __init__(self, visibility):
        self.visibility = list(visibility)
        self.index = 0
        self.waits = []

    @property
    def current_visible(self):
        return self.visibility[min(self.index, len(self.visibility) - 1)]

    def locator(self, selector):
        assert selector == ".busy"
        return FakeLocator(self)

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)
        self.index += 1


def profile(**overrides):
    config = {
        "enabled": True,
        "blocking_selectors": [".busy"],
        "timeout_ms": 1000,
        "poll_interval_ms": 100,
        "clear_stability_ms": 100,
    }
    config.update(overrides)
    return {"ui_readiness": config}


def test_readiness_returns_immediately_when_no_blocker_was_observed():
    page = FakePage([False])
    result = UIReadinessWaiter(page, profile()).wait_until_ready()

    assert result.ready is True
    assert result.waited_ms == 0
    assert result.saw_blocker is False
    assert page.waits == []


def test_readiness_waits_until_seen_blocker_clears_and_remains_clear():
    page = FakePage([True, True, False, False])
    result = UIReadinessWaiter(page, profile()).wait_until_ready()

    assert result.ready is True
    assert result.saw_blocker is True
    assert result.waited_ms == 300
    assert result.checks == 4
    assert page.waits == [100, 100, 100]


def test_readiness_reports_timeout_for_persistent_blocker():
    page = FakePage([True])
    result = UIReadinessWaiter(
        page,
        profile(timeout_ms=200, poll_interval_ms=100),
    ).wait_until_ready()

    assert result.ready is False
    assert result.waited_ms == 200
    assert result.active_selectors == (".busy",)
    assert result.saw_blocker is True
