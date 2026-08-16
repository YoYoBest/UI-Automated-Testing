from ei_ui_smoke.interactions import FIELD_LABEL_SELECTORS, FieldInteractor
from ei_ui_smoke.models import DomField, FieldDefinition, ResolvedField


def test_locator_supports_runtime_label_class():
    assert "[class*='label']" in FIELD_LABEL_SELECTORS


class FakeLocator:
    def __init__(self, *, role="", readonly=None, editable=True, classes=""):
        self.attributes = {"role": role, "readonly": readonly, "class": classes}
        self.editable = editable
        self.fill_calls = []

    def evaluate(self, script):
        return "input"

    def get_attribute(self, name):
        return self.attributes.get(name)

    def is_editable(self):
        return self.editable

    def fill(self, value):
        self.fill_calls.append(value)


def test_fill_routes_readonly_numeric_labeled_combobox_to_select(monkeypatch):
    locator = FakeLocator(role="combobox", readonly="", editable=False, classes="el-select__input")
    interactor = FieldInteractor(object())
    monkeypatch.setattr(interactor, "locate", lambda field: locator)
    monkeypatch.setattr(interactor, "_select", lambda control, value: "selected")
    field = ResolvedField(FieldDefinition("amount", "认缴出资额", "NUMBER"))

    assert interactor.fill(field, 1) == "selected"
    assert locator.fill_calls == []


def test_fill_fails_fast_for_genuinely_readonly_text_control(monkeypatch):
    locator = FakeLocator(readonly="", editable=False)
    interactor = FieldInteractor(object())
    monkeypatch.setattr(interactor, "locate", lambda field: locator)
    field = ResolvedField(FieldDefinition("name", "名称", "TEXT"))

    try:
        interactor.fill(field, "value")
    except AssertionError as error:
        assert "Field is not editable: name" in str(error)
    else:
        raise AssertionError("readonly text control should fail before locator.fill")
    assert locator.fill_calls == []


def test_fill_routes_date_to_picker_without_manual_input(monkeypatch):
    locator = FakeLocator()
    interactor = FieldInteractor(object())
    monkeypatch.setattr(interactor, "locate", lambda _field: locator)
    monkeypatch.setattr(interactor, "_select_date", lambda _control, value: f"picked:{value}")
    field = ResolvedField(FieldDefinition("dueDate", "Due date", "DATE"))

    assert interactor.fill(field, "2026-08-15") == "picked:2026-08-15"
    assert locator.fill_calls == []


def test_date_picker_clicks_matching_day_and_verifies_value(monkeypatch):
    class Text:
        def __init__(self, value):
            self.value = value

        @property
        def first(self):
            return self

        def inner_text(self):
            return self.value

    class Items:
        def __init__(self, items=()):
            self.items = list(items)

        @property
        def first(self):
            return self.items[0] if self.items else Missing()

        @property
        def last(self):
            return self.items[-1] if self.items else Missing()

        def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

    class Missing:
        @property
        def first(self):
            return self

        @property
        def last(self):
            return self

        def count(self):
            return 0

        def is_visible(self):
            return False

    class Input:
        def __init__(self):
            self.value = ""

        def input_value(self):
            return self.value

    class Cell:
        def __init__(self, day, target, panel):
            self.day = day
            self.target = target
            self.panel = panel
            self.clicked = False

        def is_visible(self):
            return True

        def locator(self, _selector):
            return Text(str(self.day))

        def click(self, **_kwargs):
            self.clicked = True
            self.target.value = "2026-08-15"
            self.panel.visible = False

    class Panel:
        def __init__(self, target):
            self.visible = True
            self.cells = Items([Cell(14, target, self), Cell(15, target, self)])

        @property
        def last(self):
            return self

        def wait_for(self, **_kwargs):
            return None

        def is_visible(self):
            return self.visible

        def locator(self, selector):
            if "header-label" in selector:
                return Items([Text("2026"), Text("8")])
            if "el-date-table" in selector:
                return self.cells
            return Items()

    class Page:
        def __init__(self, panel):
            self.panel = panel

        def locator(self, _selector):
            return self.panel

        def wait_for_timeout(self, _milliseconds):
            return None

    class Wrapper:
        def click(self, **_kwargs):
            return None

    target = Input()
    panel = Panel(target)
    interactor = FieldInteractor(Page(panel))
    monkeypatch.setattr(interactor, "_date_picker", lambda _locator: Wrapper())

    assert interactor._select_date(target, "2026-08-15") == "2026-08-15"
    assert panel.cells.nth(1).clicked


def test_date_picker_uses_escape_when_selection_does_not_auto_close():
    class Missing:
        @property
        def last(self):
            return self

        def count(self):
            return 0

        def is_visible(self):
            return False

    class Panel:
        def __init__(self):
            self.visible = True

        def is_visible(self):
            return self.visible

        def locator(self, _selector):
            return Missing()

    class Keyboard:
        def __init__(self, panel):
            self.panel = panel
            self.presses = []

        def press(self, key):
            self.presses.append(key)
            self.panel.visible = False

    class Page:
        def __init__(self, panel):
            self.keyboard = Keyboard(panel)

        def wait_for_timeout(self, _milliseconds):
            return None

    panel = Panel()
    page = Page(panel)
    FieldInteractor(page)._close_date_picker(panel)

    assert page.keyboard.presses == ["Escape"]
    assert not panel.visible


def test_locate_select_falls_back_to_normalized_prompt_label():
    class Locator:
        def __init__(self, selector="", *, visible=False, tag="div", role=""):
            self.selector = selector
            self.visible = visible
            self.tag = tag
            self.role = role

        @property
        def first(self):
            return self

        def count(self):
            return int(self.visible)

        def is_visible(self):
            return self.visible

        def evaluate(self, _script):
            return self.tag

        def get_attribute(self, name):
            return self.role if name == "role" else None

        def locator(self, _selector):
            return Locator(
                self.selector + " >> control",
                visible=True,
                tag="input",
                role="combobox",
            )

    class Root:
        def __init__(self):
            self.seen = []

        def locator(self, selector):
            self.seen.append(selector)
            if ':text-is("项目类型")' in selector and ".el-select" in selector:
                return Locator(selector, visible=True, tag="div")
            return Locator(selector, visible=False)

    root = Root()
    interactor = FieldInteractor(object())
    field = ResolvedField(
        FieldDefinition("projClassify", "请选择项目类型", "ElSelect-SELECT"),
        DomField("projClassify", "项目类型", "select", "#stale-select"),
    )

    locator = interactor.locate(field, root=root)

    assert locator.get_attribute("role") == "combobox"
    assert any(':text-is("项目类型")' in selector for selector in root.seen)
