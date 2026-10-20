import asyncio
import json
import time
import aio_pika
from sqlalchemy import select
from dayzero.backend.db import DB, Job
from dayzero.shared.config import settings


async def topology(channel):
    dead = await channel.declare_queue('dayzero.dlq', durable=True)
    queues = {}
    for flow in ['ai.leader', 'ai.colleague', 'ai.chat', 'reports']:
        queues[flow] = await channel.declare_queue('dayzero.'+flow, durable=True,
            arguments={'x-dead-letter-exchange': '', 'x-dead-letter-routing-key': dead.name})
    return queues


def collect_outbox():
    with DB.begin() as db:
        for job in db.scalars(select(Job).where(Job.status == 'running',
                Job.lease_until < time.time()).with_for_update(skip_locked=True)):
            job.status, job.published = 'pending', False
        db.flush()
        return [(j.id, 'reports' if j.flow == 'reports' else 'ai.'+j.agent_id, j.attempts) for j in db.scalars(select(Job).where(
            Job.status == 'pending', Job.published.is_(False)))]


def mark_published(jid, attempts):
    with DB.begin() as db:
        job = db.get(Job, jid)
        if job.attempts == attempts and job.status == 'pending':
            job.published = True


async def publish_outbox():
    pending = await asyncio.to_thread(collect_outbox)
    if not pending:
        return
    async with await aio_pika.connect_robust(settings().rabbitmq_url, timeout=3) as connection:
        channel = await connection.channel(publisher_confirms=True)
        await topology(channel)
        for jid, flow, attempts in pending:
            await channel.default_exchange.publish(aio_pika.Message(
                body=json.dumps({'job_id': jid}).encode(), message_id=jid,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT), routing_key='dayzero.'+flow)
            await asyncio.to_thread(mark_published, jid, attempts)
