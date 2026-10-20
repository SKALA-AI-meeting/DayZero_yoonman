"""Frozen initial session, workspace, immutable journal and outbox schema."""
from alembic import op
revision = '0001'
down_revision = None


def upgrade():
    op.execute('CREATE TABLE companies (\n\tid VARCHAR NOT NULL, \n\temail VARCHAR NOT NULL, \n\tpassword VARCHAR NOT NULL, \n\tname VARCHAR NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (email)\n)')
    op.execute('CREATE TABLE sessions (\n\tid VARCHAR NOT NULL, \n\tcompany_id VARCHAR NOT NULL, \n\talias VARCHAR NOT NULL, \n\temail VARCHAR NOT NULL, \n\ttoken_hash VARCHAR NOT NULL, \n\texpires FLOAT NOT NULL, \n\tconsented BOOLEAN NOT NULL, \n\tstate VARCHAR NOT NULL, \n\tstarted FLOAT, \n\tended FLOAT, \n\tbinding JSONB, \n\tsnapshot JSONB, \n\treport JSONB, \n\tnegotiation_rounds INTEGER NOT NULL, \n\tdivision_agreed BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(company_id) REFERENCES companies (id), \n\tUNIQUE (token_hash)\n)')
    op.execute('CREATE INDEX ix_sessions_company_id ON sessions (company_id)')
    op.execute('CREATE TABLE events (\n\tid VARCHAR NOT NULL, \n\tsession_id VARCHAR NOT NULL, \n\tkey VARCHAR NOT NULL, \n\tdue FLOAT NOT NULL, \n\trecord_id VARCHAR, \n\tstored_at FLOAT, \n\treceived_at FLOAT, \n\tPRIMARY KEY (id), \n\tUNIQUE (session_id, key), \n\tFOREIGN KEY(session_id) REFERENCES sessions (id)\n)')
    op.execute('CREATE INDEX ix_events_session_id ON events (session_id)')
    op.execute('CREATE TABLE jobs (\n\tid VARCHAR NOT NULL, \n\tsession_id VARCHAR NOT NULL, \n\tflow VARCHAR NOT NULL, \n\tsequence INTEGER NOT NULL, \n\tinput_id VARCHAR NOT NULL, \n\tstatus VARCHAR NOT NULL, \n\tpublished BOOLEAN NOT NULL, \n\tattempts INTEGER NOT NULL, \n\tlease_until FLOAT NOT NULL, \n\tlease_id VARCHAR, \n\terror VARCHAR, \n\tresult JSONB, \n\tPRIMARY KEY (id), \n\tUNIQUE (session_id, flow, sequence), \n\tFOREIGN KEY(session_id) REFERENCES sessions (id)\n)')
    op.execute('CREATE INDEX ix_jobs_session_id ON jobs (session_id)')
    op.execute('CREATE TABLE parts (\n\tid VARCHAR NOT NULL, \n\tsession_id VARCHAR NOT NULL, \n\tnumber INTEGER NOT NULL, \n\ttitle VARCHAR NOT NULL, \n\tbody TEXT NOT NULL, \n\towner VARCHAR NOT NULL, \n\tstatus VARCHAR NOT NULL, \n\tversion INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (session_id, number), \n\tFOREIGN KEY(session_id) REFERENCES sessions (id)\n)')
    op.execute('CREATE INDEX ix_parts_session_id ON parts (session_id)')
    op.execute('CREATE TABLE records (\n\tid VARCHAR NOT NULL, \n\tsession_id VARCHAR NOT NULL, \n\tkind VARCHAR NOT NULL, \n\tactor VARCHAR NOT NULL, \n\tat FLOAT NOT NULL, \n\telapsed FLOAT NOT NULL, \n\tdata JSONB NOT NULL, \n\toperation_id VARCHAR NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (session_id, operation_id), \n\tFOREIGN KEY(session_id) REFERENCES sessions (id)\n)')
    op.execute('CREATE INDEX ix_records_session_id ON records (session_id)')
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.execute('''CREATE TABLE company_documents (
      id text PRIMARY KEY, company_id text NOT NULL REFERENCES companies(id),
      slug text NOT NULL, title text NOT NULL, version integer NOT NULL,
      body text NOT NULL, release_after integer NOT NULL, approved boolean NOT NULL DEFAULT true,
      embedding vector(1024), UNIQUE(company_id, slug, version))''')
    op.execute('GRANT SELECT ON company_documents TO rag')
    # Authoritative journal and submission input cannot be edited through SQL by a worker.
    op.execute('''CREATE FUNCTION prevent_record_rewrite() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'records are append-only'; END $$''')
    op.execute('CREATE TRIGGER records_immutable BEFORE UPDATE OR DELETE ON records FOR EACH ROW EXECUTE FUNCTION prevent_record_rewrite()')



def downgrade():
    raise RuntimeError('Destructive downgrade is intentionally unsupported; restore a backup')
