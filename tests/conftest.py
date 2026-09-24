"""Shared fixtures: isolate every framework-owned process global."""

import logging

import pytest

from activegraph import clear_discovery_cache, clear_registry, clear_tool_registry
from activegraph.observability import logging as activegraph_logging
from activegraph.packs import loader as pack_loader
from activegraph.runtime._live import _clear_for_test as _clear_live_runtimes


@pytest.fixture(autouse=True)
def _isolate_process_state():
    logger = logging.getLogger(activegraph_logging.LOGGER_ROOT)
    logging_state = {
        "level": logger.level,
        "handlers": list(logger.handlers),
        "propagate": logger.propagate,
        "disabled": logger.disabled,
    }
    redactor = activegraph_logging._payload_redactor_state["fn"]

    clear_registry()
    clear_tool_registry()
    clear_discovery_cache()
    pack_loader._manifest_checked.clear()
    # v1.0.2.post1: the live-Runtime WeakSet is module-level state used
    # for cross-provider validation. It auto-cleans on GC in production,
    # but pytest's exception machinery keeps Runtimes alive within a
    # test session via traceback strong-refs, so clear it explicitly
    # between tests to prevent cross-test bleed.
    _clear_live_runtimes()
    try:
        yield
    finally:
        clear_registry()
        clear_tool_registry()
        clear_discovery_cache()
        pack_loader._manifest_checked.clear()
        _clear_live_runtimes()

        # configure_logging() deliberately mutates only the activegraph
        # logger hierarchy. Tests own and restore that mutation so ordering
        # cannot suppress caplog or redirect later tests to a closed stream.
        logger.handlers[:] = logging_state["handlers"]
        logger.setLevel(logging_state["level"])
        logger.propagate = logging_state["propagate"]
        logger.disabled = logging_state["disabled"]
        activegraph_logging.set_payload_redactor(redactor)
