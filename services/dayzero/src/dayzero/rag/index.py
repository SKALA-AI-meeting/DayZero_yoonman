"""Explicit BGE-M3 index command; never silently substitutes an embedding model."""
import time
from sqlalchemy import text
from dayzero.backend.db import DB


def main():
    from sentence_transformers import SentenceTransformer
    start = time.monotonic()
    model = SentenceTransformer('BAAI/bge-m3')
    with DB.begin() as db:
        docs = list(db.execute(text('SELECT id,body FROM company_documents WHERE approved')))
        vectors = model.encode([r.body for r in docs], normalize_embeddings=True)
        if vectors.shape != (len(docs), 1024):
            raise RuntimeError('BGE-M3 must produce 1024-dimensional embeddings')
        for row, vector in zip(docs, vectors):
            db.execute(text('UPDATE company_documents SET embedding=CAST(:v AS vector) WHERE id=:id'),
                       {'id': row.id, 'v': str(vector.tolist())})
    print({'documents': len(docs), 'dimensions': 1024, 'seconds': round(time.monotonic()-start, 3)})


if __name__ == '__main__':
    main()
