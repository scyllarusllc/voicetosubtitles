"""Optional real YouTube integration; platform availability is not guaranteed."""
import httpx
import json
import time
from pathlib import Path
client=httpx.Client(base_url='http://127.0.0.1:8765',timeout=30)
url='https://www.youtube.com/watch?v=OZKUJDEMX1w'
data=client.post('/api/jobs/link',json={'url':url}).raise_for_status().json()
last=None
for _ in range(450):
    data=client.get('/api/jobs/'+data['id']).raise_for_status().json()
    if last!=data['status']: print(data['status'],flush=True);last=data['status']
    if data['status'] in ('failed','cancelled','completed'):break
    time.sleep(2)
out=Path(__file__).parent/'artifacts'
(out/'example-report.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
if data['status']=='completed':
    assert all(c['start']<c['end'] for c in data['cues'])
    assert all(a['start']<=b['start'] for a,b in zip(data['cues'],data['cues'][1:]))
    for kind in ('srt','vtt','txt'):
        res=client.get(f"/api/jobs/{data['id']}/download/{kind}").raise_for_status()
        res.content.decode('utf-8')
        (out/f'example.{kind}').write_bytes(res.content)
    print('REAL EXAMPLE COMPLETED:',len(data['cues']),data['source'],flush=True)
else:
    print('EXAMPLE NOT COMPLETED:',data['status'],data['message'],flush=True)
