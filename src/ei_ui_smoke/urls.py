from __future__ import annotations

import re
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlsplit, urlunsplit


_VIEW_SEGMENT = re.compile(r"(^|/)([^/]+-view)(?=/|$)", re.IGNORECASE)


def _replace_last_view_segment(path: str, view_name: str) -> str:
    matches = list(_VIEW_SEGMENT.finditer(path))
    if not matches:
        return path
    match = matches[-1]
    return path[: match.start(2)] + view_name + path[match.end(2) :]


def _align_redirect_target(redirect: str, view_name: str) -> str:
    parsed = urlsplit(redirect)
    aligned_path = _replace_last_view_segment(parsed.path, view_name)
    if aligned_path == parsed.path:
        return redirect
    return urlunsplit(
        (parsed.scheme, parsed.netloc, aligned_path, parsed.query, parsed.fragment)
    )


def _align_redirect_query(query: str, view_name: str) -> tuple[str, bool]:
    pairs = parse_qsl(query, keep_blank_values=True)
    has_redirect = any(key == "redirect" for key, _value in pairs)
    if not has_redirect:
        return query, False
    aligned = [
        (key, _align_redirect_target(value, view_name) if key == "redirect" else value)
        for key, value in pairs
    ]
    if aligned == pairs:
        return query, True
    return urlencode(aligned, doseq=True), True


def align_application_url(
    login_or_app_url: str,
    view_name: str | None = None,
) -> str:
    """Align a deployed URL to the selected source application's ``*-view``."""
    if not view_name:
        return login_or_app_url
    normalized_view = view_name.strip().strip("/")
    if not normalized_view or not _VIEW_SEGMENT.fullmatch(normalized_view):
        return login_or_app_url

    parsed = urlsplit(login_or_app_url)
    query, query_has_redirect = _align_redirect_query(parsed.query, normalized_view)

    fragment_route, separator, fragment_query = parsed.fragment.partition("?")
    aligned_fragment_query, fragment_has_redirect = _align_redirect_query(
        fragment_query, normalized_view
    )
    fragment = fragment_route
    if separator:
        fragment += separator + aligned_fragment_query

    path = parsed.path
    if not query_has_redirect and not fragment_has_redirect:
        path = _replace_last_view_segment(path, normalized_view)
    if path == parsed.path and query == parsed.query and fragment == parsed.fragment:
        return login_or_app_url
    return urlunsplit((parsed.scheme, parsed.netloc, path, query, fragment))


def application_base_url(login_or_app_url: str) -> str:
    """Return the deployed business-app base, including its hash separator."""
    raw = login_or_app_url.strip()
    parsed = urlsplit(raw)
    query = parsed.query
    if not query and "?" in parsed.fragment:
        query = parsed.fragment.split("?", 1)[1]
    redirect = parse_qs(query).get("redirect", [""])[0]
    redirect = unquote(redirect)
    if redirect:
        app_path = redirect.split("#", 1)[0].rstrip("/")
        return urlunsplit((parsed.scheme, parsed.netloc, app_path, "", "")) + "/#"
    if "/#/" in raw:
        return raw.split("/#/", 1)[0].rstrip("/") + "/#"
    return raw.rstrip("/") + "/#"


def build_module_url(login_or_app_url: str, route: str) -> str:
    route = route.strip()
    if route.startswith(("http://", "https://")):
        return route
    return application_base_url(login_or_app_url) + "/" + route.lstrip("/")


def detail_parent_url(detail_url: str) -> str:
    """Return the list entry for a synthetic */detail runtime route."""
    parsed = urlsplit(detail_url)
    route, separator, query = parsed.fragment.partition("?")
    route = re.sub(r"/detail/?$", "", route)
    fragment = route + (separator + query if separator else "")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, fragment))
