import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aio_pika
import pytest

from dayzero.workers import runtime


@pytest.mark.parametrize('failure', [
    aio_pika.exceptions.AMQPConnectionError('broker starting'),
    ConnectionRefusedError('broker starting'),
    TimeoutError('broker starting'),
])
async def test_initial_broker_connection_retries_then_recovers(monkeypatch, failure):
    connection = object()
    connect = AsyncMock(side_effect=[failure, failure, connection])
    sleep = AsyncMock()
    monkeypatch.setattr(runtime, 'settings', lambda: SimpleNamespace(rabbitmq_url='amqp://test'))
    monkeypatch.setattr(runtime.aio_pika, 'connect_robust', connect)
    monkeypatch.setattr(runtime.asyncio, 'sleep', sleep)

    assert await runtime.connect(attempts=3) is connection
    assert connect.await_count == 3
    assert sleep.await_count == 2


async def test_initial_broker_connection_exhaustion_is_visible(monkeypatch):
    failure = ConnectionRefusedError('broker unavailable')
    connect = AsyncMock(side_effect=failure)
    sleep = AsyncMock()
    monkeypatch.setattr(runtime, 'settings', lambda: SimpleNamespace(rabbitmq_url='amqp://test'))
    monkeypatch.setattr(runtime.aio_pika, 'connect_robust', connect)
    monkeypatch.setattr(runtime.asyncio, 'sleep', sleep)

    with pytest.raises(ConnectionRefusedError) as caught:
        await runtime.connect(attempts=3)
    assert caught.value is failure
    assert connect.await_count == 3
    assert sleep.await_count == 2


async def test_initial_broker_connection_stops_on_shutdown(monkeypatch):
    connect = AsyncMock(side_effect=asyncio.CancelledError())
    sleep = AsyncMock()
    monkeypatch.setattr(runtime, 'settings', lambda: SimpleNamespace(rabbitmq_url='amqp://test'))
    monkeypatch.setattr(runtime.aio_pika, 'connect_robust', connect)
    monkeypatch.setattr(runtime.asyncio, 'sleep', sleep)

    with pytest.raises(asyncio.CancelledError):
        await runtime.connect(attempts=3)
    assert connect.await_count == 1
    sleep.assert_not_awaited()
