from ei_ui_smoke.module_driver import (
    ModuleBranchSelection,
    ModuleSmokeDriver,
    ModuleSmokeResult,
)
from ei_ui_smoke.source_form import SourceBranchCandidate


def _candidate(driver, value, affected):
    return SourceBranchCandidate(driver, "eq", value, affected, "visible")


def test_branch_probe_contexts_include_upstream_driver_path():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_branch_candidates = (
        _candidate("investType", "NON_EQUITY", "tradeType"),
        _candidate("tradeType", "CROSS_BORDER", "currency"),
    )

    contexts = driver._branch_driver_probe_contexts()

    assert contexts == (
        ("investType", ()),
        (
            "tradeType",
            (ModuleBranchSelection("investType", "NON_EQUITY"),),
        ),
    )


def test_runtime_branch_cases_cover_every_confirmed_option_and_nested_context():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_branch_candidates = (
        _candidate("investType", "NON_EQUITY", "tradeType"),
        _candidate("tradeType", "CROSS_BORDER", "currency"),
    )
    probes = []

    def probe(field_code, prerequisites):
        probes.append((field_code, prerequisites))
        if field_code == "investType":
            return (
                ModuleBranchSelection(field_code, "EQUITY", "Equity"),
                ModuleBranchSelection(field_code, "NON_EQUITY", "Non-equity"),
            )
        return (
            ModuleBranchSelection(field_code, "LOCAL", "Local"),
            ModuleBranchSelection(field_code, "CROSS_BORDER", "Cross-border"),
        )

    driver._probe_runtime_branch_options = probe

    cases = driver._discover_runtime_branch_cases()

    assert probes == [
        ("investType", ()),
        (
            "tradeType",
            (ModuleBranchSelection(
                "investType", "NON_EQUITY", "Non-equity"
            ),),
        ),
    ]
    assert cases == (
        (ModuleBranchSelection("investType", "EQUITY", "Equity"),),
        (ModuleBranchSelection("investType", "NON_EQUITY", "Non-equity"),),
        (
            ModuleBranchSelection("investType", "NON_EQUITY", "Non-equity"),
            ModuleBranchSelection("tradeType", "LOCAL", "Local"),
        ),
        (
            ModuleBranchSelection("investType", "NON_EQUITY", "Non-equity"),
            ModuleBranchSelection("tradeType", "CROSS_BORDER", "Cross-border"),
        ),
    )


def test_nested_branch_uses_runtime_parent_option_when_source_code_is_not_exposed():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_branch_candidates = (
        _candidate("investType", "2", "tradeType"),
        _candidate("tradeType", "CROSS_BORDER", "currency"),
    )
    child_prefixes = []

    def probe(field_code, prerequisites):
        if field_code == "investType":
            return (
                ModuleBranchSelection(field_code, "股权类投资", "股权类投资"),
                ModuleBranchSelection(field_code, "非股权类投资", "非股权类投资"),
            )
        child_prefixes.append(prerequisites)
        if prerequisites[0].value != "非股权类投资":
            return ()
        return (ModuleBranchSelection(field_code, "跨境", "跨境"),)

    driver._probe_runtime_branch_options = probe

    cases = driver._discover_runtime_branch_cases()

    assert child_prefixes == [
        (ModuleBranchSelection("investType", "股权类投资", "股权类投资"),),
        (ModuleBranchSelection("investType", "非股权类投资", "非股权类投资"),),
    ]
    assert (
        ModuleBranchSelection("investType", "非股权类投资", "非股权类投资"),
        ModuleBranchSelection("tradeType", "跨境", "跨境"),
    ) in cases


def test_run_all_branches_uses_fresh_verified_create_for_each_option(monkeypatch):
    monkeypatch.setenv("EI_REQUIRE_ADD", "true")
    driver = object.__new__(ModuleSmokeDriver)
    cases = (
        (ModuleBranchSelection("investType", "1", "Equity"),),
        (ModuleBranchSelection("investType", "2", "Non-equity"),),
    )
    driver._discover_runtime_branch_cases = lambda: cases
    calls = []
    commits = []
    driver.release_pending_unique_reservations = lambda: None
    driver.commit_pending_unique_reservations = lambda: commits.append(True)

    def create(**kwargs):
        calls.append(kwargs)
        index = len(calls)
        return ModuleSmokeResult(
            mode="add_and_detail_verified",
            business_id=f"record-{index}",
            detail_url=f"/detail/record-{index}",
        )

    driver._run_create = create

    results = driver.run_all_branches()

    assert [result.business_id for result in results] == ["record-1", "record-2"]
    assert [result.branch_conditions for result in results] == [
        (("investType", "Equity"),),
        (("investType", "Non-equity"),),
    ]
    assert [call["branch_selections"] for call in calls] == list(cases)
    assert commits == [True, True]


def test_run_all_branches_rejects_any_result_without_persistence_readback(monkeypatch):
    monkeypatch.setenv("EI_REQUIRE_ADD", "true")
    driver = object.__new__(ModuleSmokeDriver)
    driver._discover_runtime_branch_cases = lambda: (
        (ModuleBranchSelection("investType", "1", "Equity"),),
    )
    driver._run_create = lambda **_kwargs: ModuleSmokeResult(
        mode="page_access"
    )
    driver.release_pending_unique_reservations = lambda: None
    driver.commit_pending_unique_reservations = lambda: None

    try:
        driver.run_all_branches()
    except AssertionError as exc:
        assert "未完成保存后回读" in str(exc)
    else:
        raise AssertionError("an unverified branch result must fail")
