"""Real HTTP/file validation checks against the default local server."""
import httpx
import json
from pathlib import Path
import tempfile
import time
client=httpx.Client(base_url='http://127.0.0.1:8765',timeout=30)
config=client.get('/api/config').raise_for_status().json()
out=Path(__file__).parent/'artifacts'
report={}
with tempfile.TemporaryFile() as f:
    f.truncate((config['max_upload_mb']+2)*1024**2)
    f.seek(0)
    response=client.post('/api/jobs/upload',files={'file':('oversized.wav',f,'audio/wav')})
assert response.status_code==413,response.text
report['oversized_real_http']={'status':response.status_code,'detail':response.json()}
response=client.post('/api/jobs/upload',files={'file':('disguised.wav',b'This is not audio','audio/wav')}).raise_for_status()
job_id=response.json()['id']
for _ in range(60):
    data=client.get('/api/jobs/'+job_id).raise_for_status().json()
    if data['status'] in ('completed','failed'):break
    time.sleep(.2)
assert data['status']=='failed' and data['error_code']=='invalid_media',data
report['disguised_file']=data
(out/'edge-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('REAL FILE VALIDATION PASSED')
