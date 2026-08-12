from ei_ui_smoke.project_layout import discover_detail_prefixes, read_app_id, resolve_view_root


def test_resolves_fi_parent_and_reads_app_id(tmp_path):
    view = tmp_path / "fi-view"
    (view / "src" / "views" / "buildProject" / "detail").mkdir(parents=True)
    (view / ".env").write_text("VITE_APP_ID=10030\n", encoding="utf-8")
    (view / "src" / "views" / "buildProject" / "detail" / "index.vue").write_text(
        'getUserFuncPermTreeByFuncCode(APP_ID, "buildProject")', encoding="utf-8"
    )
    assert resolve_view_root(tmp_path) == view.resolve()
    assert read_app_id(tmp_path) == "10030"
    assert discover_detail_prefixes(tmp_path) == ("buildProject",)


def test_accepts_view_directory_directly(tmp_path):
    view = tmp_path / "ei-view"
    (view / "src" / "views").mkdir(parents=True)
    assert resolve_view_root(view) == view.resolve()
