import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--soak", action="store_true", default=False, help="run slow soak tests")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "soak: slow real-time soak test, run with --soak")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--soak"):
        return
    skip = pytest.mark.skip(reason="soak test: run with --soak (make soak)")
    for item in items:
        if "soak" in item.keywords:
            item.add_marker(skip)
