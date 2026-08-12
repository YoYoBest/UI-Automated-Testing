from ei_ui_smoke.urls import (
    align_application_url,
    application_base_url,
    build_module_url,
    detail_parent_url,
)


def test_extracts_business_app_from_uim_login_redirect():
    login = "http://172.29.237.39:5443/uim-view/#/login?redirect=%2Fei-view%2F%23%2FequityAffiliateFund%2Findex"
    assert application_base_url(login) == "http://172.29.237.39:5443/ei-view/#"
    assert build_module_url(login, "/selfManagedFunds/index") == "http://172.29.237.39:5443/ei-view/#/selfManagedFunds/index"


def test_accepts_business_app_url_directly():
    assert build_module_url("https://host/ei-view/#/home", "/cash/fund") == "https://host/ei-view/#/cash/fund"


def test_detail_parent_url_preserves_app_and_removes_detail_suffix():
    assert detail_parent_url(
        "http://host/fi-view/#/buildProject/detail"
    ) == "http://host/fi-view/#/buildProject"


def test_aligns_uim_login_redirect_without_replacing_login_application():
    login = (
        "https://host/uim-view/#/login?ticket=abc&"
        "redirect=%2Fei-view%2F%23%2FbuildProject%2Findex"
    )

    assert align_application_url(login, "fi-view") == (
        "https://host/uim-view/#/login?ticket=abc&"
        "redirect=%2Ffi-view%2F%23%2FbuildProject%2Findex"
    )


def test_aligns_direct_application_url_and_preserves_query_and_fragment():
    url = "https://host/gateway/ei-view/?tenant=main#/buildProject?tab=todo"

    assert align_application_url(url, "fi-view") == (
        "https://host/gateway/fi-view/?tenant=main#/buildProject?tab=todo"
    )


def test_aligned_application_url_is_unchanged():
    url = "https://host/fi-view/#/buildProject"
    login = (
        "https://host/uim-view/#/login?ticket=a%20b&"
        "redirect=%2Ffi-view%2F%23%2FbuildProject"
    )

    assert align_application_url(url, "fi-view") == url
    assert align_application_url(login, "fi-view") == login


def test_aligns_last_view_segment_for_custom_application_name():
    url = "https://host/shell-view/apps/ei-view/#/home"

    assert align_application_url(url, "abc-view") == (
        "https://host/shell-view/apps/abc-view/#/home"
    )


def test_missing_view_name_keeps_original_url():
    url = "https://host/ei-view/#/home"

    assert align_application_url(url) == url
    assert align_application_url(url, "") == url
