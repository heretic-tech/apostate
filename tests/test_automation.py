"""No trace of automation or of a headless browser reaches the page."""

from __future__ import annotations

CONTEXTS = ("window", "sameOriginFrame", "crossOriginFrame", "worker", "sharedWorker", "serviceWorker")


def contexts(case):
    return {name: case.values.get(name) or {} for name in CONTEXTS}


def test_webdriver_is_false_in_every_context(case, record):
    """False in the window and frames. Workers have no navigator.webdriver in stock Chrome either."""
    found = {name: values.get("webdriver") for name, values in contexts(case).items()}
    record["webdriver"] = found
    assert all(found[name] is False for name in ("window", "sameOriginFrame", "crossOriginFrame")), found
    assert all(found[name] is None for name in ("worker", "sharedWorker", "serviceWorker")), found


def test_no_automation_globals(case, record):
    page = case.values["page"]
    record["globals"] = page["automationGlobals"]
    record["window_keys"] = page["windowKeys"]
    assert page["automationGlobals"] == []
    assert page["windowKeys"] == []


def test_no_headless_marks(case, record):
    marks = []
    for name, values in contexts(case).items():
        if "Headless" in (values.get("userAgent") or ""):
            marks.append(f"{name} userAgent")
        for brand in (values.get("uaData") or {}).get("brands", []):
            if "Headless" in brand["brand"]:
                marks.append(f"{name} brand {brand['brand']}")
    for key in ("user-agent", "sec-ch-ua"):
        if "Headless" in case.document_headers.get(key, ""):
            marks.append(f"request header {key}")
    record["marks"] = marks
    assert marks == []


def test_chrome_object_plugins_and_pdf(case, record):
    page = case.values["page"]
    record.update(chrome=page["chrome"], plugins=len(page["plugins"]), mime_types=len(page["mimeTypes"]),
                  pdf_viewer=page["pdfViewerEnabled"])
    assert page["chrome"]["type"] == "object"
    assert len(page["plugins"]) == 5, page["plugins"]
    assert sorted(page["mimeTypes"]) == ["application/pdf", "text/pdf"]
    assert page["pdfViewerEnabled"] is True


def test_console_arguments_are_not_serialized(case, record):
    """A CDP client with the Runtime domain enabled reads an error's stack when it is logged."""
    record["stack_read"] = case.values["page"]["consoleStackRead"]
    assert case.values["page"]["consoleStackRead"] is False


def test_notification_permission_agrees_with_permissions_api(case, record):
    notification = case.values["page"]["notification"]
    record.update(notification)
    expected = {"default": "prompt", "granted": "granted", "denied": "denied"}[notification["permission"]]
    assert notification["query"] == expected
