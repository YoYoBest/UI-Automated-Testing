from ei_ui_smoke.tab_navigation import activate_page_tab


class _Tab:
    def __init__(self, *, active=False):
        self.active = active
        self.clicks = 0

    def wait_for(self, **_kwargs):
        return None

    def get_attribute(self, name):
        if name == "aria-selected":
            return "true" if self.active else "false"
        if name == "class":
            return "is-active" if self.active else ""
        return None

    def click(self):
        self.clicks += 1
        self.active = True


class _Tabs:
    def __init__(self, tab):
        self.tab = tab

    def filter(self, **_kwargs):
        return self

    @property
    def first(self):
        return self.tab


class _Page:
    def __init__(self, tab):
        self.tab = tab

    def locator(self, _selector):
        return _Tabs(self.tab)


def test_activate_page_tab_clicks_inactive_tab_once():
    tab = _Tab()

    activate_page_tab(_Page(tab), "项目投后管理")

    assert tab.active and tab.clicks == 1


def test_activate_page_tab_keeps_active_tab_unchanged():
    tab = _Tab(active=True)

    activate_page_tab(_Page(tab), "项目投前管理")

    assert tab.clicks == 0
