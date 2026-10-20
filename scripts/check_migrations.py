"""Create only an isolated fixture DB, migrate twice, verify shape, then remove it."""
import os
import subprocess
import uuid
import psycopg
from psycopg import sql
from dayzero.shared.config import settings

base=settings().database_url.replace('postgresql+psycopg://','postgresql://')
name='dayzero_migration_'+uuid.uuid4().hex
admin=psycopg.connect(base,autocommit=True)
admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
try:
    prefix=base.rsplit('/',1)[0]+'/'
    env={**os.environ,'DATABASE_URL':prefix.replace('postgresql://','postgresql+psycopg://')+name}
    for _ in range(2):
        subprocess.run(['services/dayzero/.venv/bin/alembic','-c','services/dayzero/alembic.ini','upgrade','head'],env=env,check=True)
    with psycopg.connect(prefix+name) as db:
        assert db.execute('SELECT version_num FROM alembic_version').fetchone()[0]=='0006'
        assert db.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='sessions' AND column_name='report_binding'").fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='jobs' AND column_name IN ('model_calls','execution_started','tool_operations')").fetchone()[0]==3
        assert db.execute("SELECT format_type(atttypid,atttypmod) FROM pg_attribute WHERE attrelid='company_documents'::regclass AND attname='embedding'").fetchone()[0]=='vector(1024)'
    print('clean install, repeat upgrade, execution budget columns and vector(1024): passed')
finally:
    # Unique fixture DB created above; never touches the product database.
    admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
    admin.close()
