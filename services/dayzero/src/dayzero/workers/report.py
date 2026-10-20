import asyncio
from dayzero.reporting.build import generate
from dayzero.workers.runtime import consume


async def main():
    async def handler(job, internal):
        snapshot = await internal.call('context')
        report = await generate(snapshot, reserve=lambda: internal.call('model-attempt', 'POST'))
        await internal.call('result', 'POST', {'report': report, 'usage': report['usage']})
    await consume(['reports'], handler)


if __name__ == '__main__':
    asyncio.run(main())
