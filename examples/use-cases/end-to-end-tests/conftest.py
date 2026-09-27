import os

import pytest

from apostate import launch
from app import start


@pytest.fixture(scope="session")
def app_url():
    """APP_URL when it is set, for example your staging site; the local app otherwise."""
    if os.environ.get("APP_URL"):
        yield os.environ["APP_URL"]
        return
    server = start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


@pytest.fixture(scope="session")
def browser():
    # A fixed seed gives every run, on every host, the same machine, so a failure reproduces.
    with launch(fingerprint=42, fingerprint_platform="windows",
                locale="en-US", timezone="America/New_York", geoip=False) as browser:
        yield browser


@pytest.fixture
def page(browser):
    # Pages share the launch's one profile, so clear its cookies between tests.
    browser.contexts[0].clear_cookies()
    page = browser.new_page()
    yield page
    page.close()
