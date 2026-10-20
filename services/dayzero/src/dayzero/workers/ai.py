import asyncio
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from dayzero.shared.config import settings
from dayzero.agents.colleague.graph import compile as colleague
from dayzero.agents.leader.graph import compile as leader
from dayzero.agents.ai_chat.graph import compile as chat
from dayzero.workers.runtime import consume


async def main():
    async with AsyncPostgresSaver.from_conn_string(settings().checkpoint_database_url) as saver:
        await saver.setup()
        graphs = {'leader': leader(saver), 'colleague': colleague(saver), 'chat': chat(saver)}
        async def handler(job, internal):
            graph = graphs[job['agent_id']]
            config = {'configurable': {'thread_id': job['session_id']+':'+job['agent_id']}, 'recursion_limit': 20}
            # Resume only the same pending job. New jobs load the current backend state.
            previous = await graph.aget_state(config)
            value = None if job.get('resume', True) and previous.next and previous.values.get('job_id') == job['id'] else {
                'job_id': job['id'], 'agent_id': job['agent_id']}
            await graph.ainvoke(value, config, context={'job_token': job['token']}, durability='sync')
        await consume(['ai.leader', 'ai.colleague', 'ai.chat'], handler)


if __name__ == '__main__':
    asyncio.run(main())
