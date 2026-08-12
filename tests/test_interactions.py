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
