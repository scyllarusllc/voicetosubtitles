"""Real CPU ASR progress test; accepts a speech file of at least 60 seconds.
No Google login is involved. Uses the same cookie session for all job requests.
"""
import json
from pathlib import Path
import sys
import time
import httpx
client=httpx.Client(base_url='http://127.0.0.1:8765',timeout=30)
source=Path(sys.argv[1])
with source.open('rb') as f:
    job=client.post('/api/jobs/upload',files={'file':(source.name,f,'audio/wav')},data={'language':'zh'}).raise_for_status().json()
assert job['storage']=='guest'
events=[]
last=None
for _ in range(2400):
    job=client.get('/api/jobs/'+job['id']).raise_for_status().json()
    progress=job.get('progress') or {}
    signature=(job['status'],progress.get('phase'),progress.get('percent'))
    if signature!=last:
        events.append({'status':job['status'],'progress':progress})
        print(signature,flush=True)
        last=signature
    if job['status'] in ('completed','failed','cancelled'):break
    time.sleep(.25)
assert job['status']=='completed',job
numeric=[e['progress'] for e in events if e['progress'].get('phase')=='transcribing' and e['progress'].get('percent') is not None]
assert any(0<p['percent']<100 for p in numeric),numeric
assert all(a['percent']<=b['percent'] for a,b in zip(numeric,numeric[1:]))
assert all(abs(p['percent']-p['processed_seconds']/p['total_seconds']*100)<.1 for p in numeric)
assert not any(e['progress'].get('phase')=='downloading_model' for e in events), 'Expected already cached model'
assert httpx.get('http://127.0.0.1:8765/api/jobs/'+job['id']).status_code==404
output=Path(__file__).parent/'artifacts'/'progress-report-v2.json'
output.write_text(json.dumps({'events':events,'result':job},ensure_ascii=False,indent=2),encoding='utf-8')
print('REAL PROGRESS AND SESSION ISOLATION PASSED',flush=True)
