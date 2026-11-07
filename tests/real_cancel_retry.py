"""Real process cancellation, followed by resubmitting the same local file."""
from pathlib import Path
import json
import time
import httpx
client=httpx.Client(base_url='http://127.0.0.1:8765',timeout=30)
root=Path(__file__).parent/'artifacts'
def submit():
    with (root/'chinese.wav').open('rb') as f:
        return client.post('/api/jobs/upload',files={'file':('chinese.wav',f,'audio/wav')},data={'language':'auto'}).raise_for_status().json()
job=submit()
for _ in range(600):
    job=client.get('/api/jobs/'+job['id']).raise_for_status().json()
    if job['status']=='recognizing':break
    if job['status'] in ('completed','failed'):raise AssertionError(job)
    time.sleep(.05)
assert job['status']=='recognizing'
cancelled=client.post('/api/jobs/'+job['id']+'/cancel').raise_for_status().json()
assert cancelled['status']=='cancelled'
time.sleep(.3)
assert client.get('/api/jobs/'+job['id']).json()['status']=='cancelled'
retry=submit()
for _ in range(120):
    retry=client.get('/api/jobs/'+retry['id']).raise_for_status().json()
    if retry['status'] in ('completed','failed'):break
    time.sleep(.5)
assert retry['status']=='completed' and retry['detected_language']=='zh',retry
(root/'cancel-retry-report.json').write_text(json.dumps({'cancelled':cancelled,'retry':retry},ensure_ascii=False,indent=2),encoding='utf-8')
print('REAL CANCEL AND RETRY PASSED')
