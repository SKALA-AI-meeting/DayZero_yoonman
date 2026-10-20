"""Verify read/process denial with non-secret sentinels using the same OS profile."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from dayzero.llm.isolation import profile

with tempfile.TemporaryDirectory(prefix='dayzero-denied-') as outside, tempfile.TemporaryDirectory(prefix='dayzero-allowed-') as allowed:
    root=Path(allowed).resolve();secret=Path(outside).resolve()/'other-candidate.txt';secret.write_text('sentinel-only')
    auth=Path(outside).resolve()/'auth-sentinel.json';auth.write_text('{}')
    binary=Path(sys.executable).resolve()
    sb=profile(binary,root,auth)
    code='''import json,subprocess,os
result={}
for label,path in [('other_candidate',os.environ['PROBE_OUTSIDE']),('project',os.environ['PROBE_PROJECT'])]:
 try: open(path).read();result[label]='READABLE'
 except PermissionError:result[label]='DENIED'
try:subprocess.run(['/bin/sh','-c','echo forbidden'],check=True);result['shell']='EXECUTED'
except PermissionError:result['shell']='DENIED'
print(json.dumps(result))
'''
    # Python probe needs its stdlib, which production CLI does not receive.
    sb+='\n(allow file-read* (subpath "'+str(binary.parents[1])+'"))\n'
    env={k:v for k,v in os.environ.items() if k in ['PATH','HOME','LANG']}
    env.update(PROBE_OUTSIDE=str(secret),PROBE_PROJECT=str(Path('PRD.md').resolve()))
    run=subprocess.run(['/usr/bin/sandbox-exec','-p',sb,str(binary),'-c',code],cwd=root,env=env,capture_output=True,text=True,timeout=10)
    print('exit',run.returncode);print(run.stdout);print(run.stderr[-800:])
    assert run.returncode==0
    assert set(json.loads(run.stdout).values())=={'DENIED'}
