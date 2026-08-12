from ei_ui_smoke.module_resolver import ModuleResolutionError, discover_form_codes, resolve_form_code


def make_source(tmp_path, relative, content):
    path = tmp_path / "ei-view" / "src" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_resolves_module_name_to_literal_form_code(tmp_path):
    make_source(tmp_path, "views/fund/base.vue", '<template>基金基本信息</template><script>const FORM_CODE = "FUND_BASICINFO"</script>')
    assert resolve_form_code(tmp_path, "基金基本信息") == "FUND_BASICINFO"


def test_path_name_increases_candidate_score(tmp_path):
    make_source(tmp_path, "views/partnerManage/index.vue", 'const FORM_CODE = "PARTNER_FORM"')
    make_source(tmp_path, "views/other.vue", '// partnerManage\nconst formCode: string = "OTHER_FORM"')
    candidates = discover_form_codes(tmp_path, "partnerManage")
    assert candidates[0].form_code == "PARTNER_FORM"


def test_ambiguous_candidates_fail_instead_of_silently_picking(tmp_path):
    make_source(tmp_path, "views/a.vue", '模块甲 const formCode: string = "FORM_A"')
    make_source(tmp_path, "views/b.vue", '模块甲 const formCode: string = "FORM_B"')
    try:
        resolve_form_code(tmp_path, "模块甲")
    except ModuleResolutionError as exc:
        assert "FORM_A" in str(exc) and "FORM_B" in str(exc)
    else:
        raise AssertionError("ambiguous module must fail")

