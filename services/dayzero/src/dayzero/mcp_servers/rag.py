from urllib.parse import urlsplit
import asyncio
import time
from mcp.server.fastmcp import FastMCP
from dayzero.shared.internal import Internal
from dayzero.shared.config import settings

mcp = FastMCP('Day Zero RAG', host=settings().mcp_host, port=urlsplit(settings().rag_mcp_url).port or 8002)
_model = None


def rank(query, docs):
    # BGE-M3 is mandatory. Missing weights do not silently enable lexical search.
    global _model
    from sentence_transformers import SentenceTransformer
    if _model is None:
        _model = SentenceTransformer('BAAI/bge-m3', local_files_only=True)
    vector = _model.encode(query, normalize_embeddings=True)
    if vector.shape != (1024,):
        raise RuntimeError('embedding_dimension_mismatch')
    import psycopg
    from dayzero.shared.config import settings
    allowed = {d['id']: d for d in docs}
    with psycopg.connect(settings().rag_database_url) as db:
        rows = db.execute("""SELECT id FROM company_documents
            WHERE id = ANY(%s) AND approved AND embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector LIMIT 3""", (list(allowed), str(vector.tolist()))).fetchall()
    if not rows:
        raise RuntimeError('documents_not_indexed')
    return [allowed[row[0]] for row in rows]



@mcp.tool()
async def rag_call(job_id: str, job_token: str, name: str, arguments: dict) -> dict:
    """Search or read documents after backend authorization at invocation time."""
    docs = await Internal(job_id, job_token).call('documents')
    if name == 'get_document':
        result = [d for d in docs if d['id'] == arguments.get('document_id')]
        if not result:
            raise ValueError('document_not_available')
    elif name == 'search_documents':
        if not isinstance(arguments.get('query'), str) or len(arguments['query']) > 500:
            raise ValueError('invalid_query')
        start = time.monotonic()
        result = await asyncio.to_thread(rank, arguments['query'], docs)
        return {'documents': result, 'search_seconds': time.monotonic()-start, 'embedding': 'BAAI/bge-m3'}
    else:
        raise ValueError('tool_not_allowed')
    return {'documents': result}


if __name__ == '__main__':
    mcp.run(transport='streamable-http')
