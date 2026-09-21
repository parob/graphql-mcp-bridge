"""The proxy runs each instance's lifespan in its own task and can retire it."""

import pytest

from bridge.config import Settings
from bridge.proxy import Proxy


@pytest.mark.asyncio
async def test_instances_are_started_and_closed_cleanly(upstream_graphql):
    proxy = Proxy(Settings(allow_internal_hosts=True))
    built = await proxy.get(upstream_graphql)
    assert not built._lifespan_task.done()
    assert await proxy.get(upstream_graphql) is built  # cached

    await proxy.close_all()
    assert built._lifespan_task.done()
    assert built._lifespan_task.exception() is None
    assert len(proxy.cache) == 0

    # A fresh build after shutdown-and-restart works too.
    rebuilt = await proxy.get(upstream_graphql)
    assert rebuilt is not built
    await proxy.close_all()


@pytest.mark.asyncio
async def test_credentials_select_distinct_instances(upstream_graphql):
    proxy = Proxy(Settings(allow_internal_hosts=True))
    anonymous = await proxy.get(upstream_graphql)
    alice = await proxy.get(upstream_graphql, {"authorization": "Bearer a"})
    alice_again = await proxy.get(upstream_graphql, {"authorization": "Bearer a"})
    bob = await proxy.get(upstream_graphql, {"authorization": "Bearer b"})
    assert alice is alice_again
    assert len({id(anonymous), id(alice), id(bob)}) == 3
    # Credentials are used for introspection only, never stored.
    assert "authorization" not in {
        k.lower() for k in alice.graphql_mcp.remote_client.headers}
    await proxy.close_all()
