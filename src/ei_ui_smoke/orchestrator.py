from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
from typing import Any

from .case_data import SmokeCase
from .contracts import runtime_fields
from .dom import scan_dom_fields
from .interactions import FieldInteractor
from .models import FieldDefinition, ResolvedField
from .runtime_api import RuntimeApi
from .schema import discover_form_json, extract_runtime_fields, load_fields_from_json, match_dom_fields, merge_definitions
from .values import ValueFactory
from .verification import (
    assert_page_echo,
    assert_runtime_values,
    extract_business_id,
    response_matches,
)


@dataclass(slots=True)
class SmokeResult:
    business_id: str
    save_status: int
    submitted_values: dict[str, Any]
    save_response: Any


class FormSmokeOrchestrator:
    def __init__(
        self,
        page,
        source_root: Path,
        form_code: str,
        overrides: dict[str, Any] | None = None,
        data_strategy=None,
    ):
        self.page = page
        self.source_root = source_root
        self.form_code = form_code
        self.api = RuntimeApi(page)
        self.values = data_strategy or ValueFactory(overrides)
        self.interactor = FieldInteractor(page)
        self.last_definitions: list[FieldDefinition] = []

    def resolve_fields(self) -> list[ResolvedField]:
        checked_in: list[FieldDefinition] = []
        for path in discover_form_json(self.source_root, self.form_code):
            checked_in.extend(load_fields_from_json(path))
        runtime = extract_runtime_fields(self.api.get_form_config(self.form_code))
        definitions = merge_definitions(checked_in, runtime)
        if not definitions:
            raise AssertionError(f"No field configuration found for formCode={self.form_code}")
        self.last_definitions = definitions
        return match_dom_fields(definitions, scan_dom_fields(self.page))

    def fill_visible_add_fields(self) -> tuple[list[ResolvedField], dict[str, Any]]:
        fields = self.resolve_fields()
        missing_required = [f for f in fields if f.definition.add_visible and f.definition.required and f.dom is None]
        if missing_required:
            names = ", ".join(item.definition.field_code for item in missing_required)
            raise AssertionError(f"Required configured fields not rendered: {names}")
        candidates = [f for f in fields if f.definition.add_visible and f.dom is not None]
        submitted: dict[str, Any] = {}
        for index, field in enumerate(candidates, 1):
            requested = self.values.value_for(field.definition, index)
            actual = self.interactor.fill(field, requested)
            if actual is not None:
                submitted[field.definition.field_code] = actual
        return candidates, submitted

    def run_add_and_verify(self, case: SmokeCase) -> SmokeResult:
        if not case.page.save_api_pattern:
            raise ValueError("page.saveApiPattern is required for end-to-end save verification")
        self.interactor.upload_files = case.upload_files
        fields, submitted = self.fill_visible_add_fields()
        save_button = self.page.locator(case.page.save_button).first
        if not save_button.count() or not save_button.is_visible():
            raise AssertionError(f"Save button not found: {case.page.save_button}")
        with self.page.expect_response(
            lambda response: response_matches(response.url, case.page.save_api_pattern),
            timeout=30000,
        ) as response_info:
            save_button.click()
        response = response_info.value
        if not response.ok:
            raise AssertionError(f"Save API failed: HTTP {response.status} {response.url}")
        try:
            body = response.json()
        except Exception:
            body = response.text()
        business_id = extract_business_id(body)
        if not business_id:
            raise AssertionError(f"Business id not found in save response: {body!r}")

        expected = dict(submitted)
        expected.update(case.expected)
        if runtime_fields(self.last_definitions) and case.runtime_expected:
            runtime_response = self.api.get_form_data(self.form_code, business_id)
            assert_runtime_values(runtime_response, self.last_definitions, case.runtime_expected)

        if case.page.detail_url_template:
            detail_url = case.page.detail_url_template.format(businessId=business_id, id=business_id)
            self.page.goto(detail_url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(500)
            detail_fields = self.resolve_fields()
            assert_page_echo(self.page, detail_fields, expected, self.interactor)

        return SmokeResult(business_id, response.status, submitted, body)
