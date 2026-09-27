from patchright.sync_api import expect


def test_signup(page, app_url):
    page.goto(app_url)
    page.fill("#email", "e2e@example.test")
    page.click("button[type=submit]")
    expect(page.locator("#result")).to_have_text("Account created for e2e@example.test")


def test_persona(page, app_url):
    page.goto(app_url)
    assert page.evaluate("navigator.platform") == "Win32"
    assert page.evaluate("[screen.width, screen.height]") == [1920, 1080]
