from fastapi import HTTPException
from sqlalchemy import text
from dayzero.shared.scenarios import ROOT, load_scenario, document_slug


def seed_documents(db, company_id, definition=None):
    definition = definition or load_scenario('looplab-v1')
    for document in definition['documents']:
        d = {**document, 'slug': document_slug(definition, document['slug'])}
        db.execute(text('''INSERT INTO company_documents
            (id, company_id, slug, title, version, body, release_after, approved, embedding)
            VALUES (:id,:company_id,:slug,:title,1,:body,:release_after,true,
                (SELECT embedding FROM company_documents WHERE body=:body
                 AND embedding IS NOT NULL LIMIT 1))
            ON CONFLICT (company_id,slug,version) DO NOTHING'''),
            {**d, 'id': company_id+':'+d['slug']+':v1', 'company_id': company_id})


def visible_documents(db, company_id, elapsed, definition=None):
    definition = definition or load_scenario('looplab-v1')
    slugs = [document_slug(definition, d['slug']) for d in definition['documents']]
    rows = db.execute(text('''SELECT id,title,version,body,release_after FROM company_documents
        WHERE company_id=:company_id AND slug = ANY(:slugs) AND approved AND release_after<=:elapsed ORDER BY title'''),
        {'company_id': company_id, 'elapsed': elapsed, 'slugs': slugs})
    return [dict(r._mapping) for r in rows]


def get_document(db, company_id, elapsed, doc_id, definition=None):
    for d in visible_documents(db, company_id, elapsed, definition):
        if d['id'] == doc_id:
            return d
    raise HTTPException(404, 'document_not_available')
