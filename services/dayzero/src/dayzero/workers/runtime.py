import asyncio
import json
import signal
import sys
import time
import httpx
import aio_pika
from dayzero.shared.config import settings
from dayzero.shared.internal import Internal
from dayzero.llm.schemas import ModelError


async def heartbeat(internal):
    while True:
        await asyncio.sleep(5)
        await internal.call('heartbeat', 'POST')


async def run_job(job, internal, handler):
    processing = asyncio.create_task(handler(job, internal))
    beat = asyncio.create_task(heartbeat(internal))
    try:
        async with asyncio.timeout(max(0, job.get('execution_deadline', time.time()+150)-time.time())):
            done, _ = await asyncio.wait([processing, beat], return_when=asyncio.FIRST_COMPLETED)
            if processing in done:
                await processing
            else:
                await beat  # Cancelled session or stale lease stops in-flight CLI/MCP too.
    finally:
        processing.cancel()
        beat.cancel()
        await asyncio.gather(processing, beat, return_exceptions=True)


async def connect(attempts=30):
    # connect_robust only reconnects after a first success; a broker still booting must be retried here.
    for attempt in range(attempts):
        try:
            return await aio_pika.connect_robust(settings().rabbitmq_url)
        except (aio_pika.exceptions.AMQPConnectionError, ConnectionError, OSError):
            if attempt == attempts-1:
                raise
            print('broker_unavailable retrying', flush=True)
            await asyncio.sleep(2)


async def consume(flows, handler):
    if sys.platform != 'win32':
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, asyncio.current_task().cancel)
    async with await connect() as connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=8)
        dead = await channel.declare_queue('dayzero.dlq', durable=True)

        async def receive(message):
            jid = json.loads(message.body)['job_id']
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    response = await client.post(settings().backend_url+'/internal/jobs/'+jid+'/claim',
                        headers={'X-Internal-Secret': settings().internal_secret.get_secret_value()})
            except httpx.HTTPError:
                await asyncio.sleep(0.5)
                await message.nack(requeue=True)
                return
            if response.status_code == 409:
                await asyncio.sleep(0.5)
                await message.nack(requeue=True)
                return
            if response.is_error:
                await message.reject(requeue=False)
                return
            job = response.json()
            if job['terminal']:
                if job.get('status') == 'failed':
                    await message.reject(requeue=False)
                else:
                    await message.ack()
                return
            internal = Internal(jid, job['token'])
            try:
                await run_job(job, internal, handler)
                await message.ack()
            except asyncio.CancelledError:
                # Normal shutdown releases the durable job. SIGKILL is recovered by lease expiry.
                try:
                    async with asyncio.timeout(5):
                        await internal.call('result', 'POST', {'error': 'worker_shutdown', 'retryable': True})
                except (httpx.HTTPError, TimeoutError):
                    pass
                await message.nack(requeue=True)
                raise
            except (ModelError, TimeoutError, httpx.HTTPError) as error:
                if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 409:
                    await message.ack()  # Closed session / fenced lease; do not write a late result.
                    return
                code = error.code if isinstance(error, ModelError) else type(error).__name__
                retryable = error.retryable if isinstance(error, ModelError) else True
                if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in [401, 403, 404]:
                    retryable = False
                try:
                    result = await internal.call('result', 'POST', {'error': code, 'retryable': retryable})
                    if result['status'] == 'failed':
                        await message.reject(requeue=False)
                    else:
                        await message.ack()
                except httpx.HTTPError:
                    await message.nack(requeue=True)
            except Exception as error:
                print('worker_error', jid, type(error).__name__, flush=True)
                try:
                    result = await internal.call('result', 'POST', {'error': type(error).__name__, 'retryable': True})
                    if result['status'] == 'failed':
                        await message.reject(requeue=False)
                    else:
                        await message.ack()
                except httpx.HTTPError:
                    await message.nack(requeue=True)

        for flow in flows:
            queue = await channel.declare_queue('dayzero.'+flow, durable=True,
                arguments={'x-dead-letter-exchange': '', 'x-dead-letter-routing-key': dead.name})
            await queue.consume(receive)
        await asyncio.Future()
