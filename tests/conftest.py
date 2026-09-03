"""Per-test reset of the client's process-level state.

`_SHARED_CACHE` exists so a fan-out fetches the fleet-wide coverage tables once instead of
once per instance. Inside one CLI run that is exactly right; inside a test session it means
one test's fake response answers the next test's question — which is how adding the cache
turned four unrelated passing tests red.

The module object has to be the one the tests actually drive (`client_module`), not a second
copy loaded here: a fresh copy has its own globals, so clearing it clears nothing.
"""

import pytest


@pytest.fixture(autouse=True)
def _reset_client_state(request):
    module = getattr(request.module, "client_module", None)
    if module is not None:
        module._SHARED_CACHE.clear()
        module._DEGRADED.clear()
    yield
    if module is not None:
        module._SHARED_CACHE.clear()
        module._DEGRADED.clear()
