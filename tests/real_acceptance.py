"""Real integration test. Requires running server, ffmpeg and a Chinese speech file.
Run: .venv/bin/python tests/real_acceptance.py tests/artifacts/chinese.wav
The supplied speech file may be synthetic TTS; inference is REAL, never mocked.
"""
import json
from pathlib import Path
import sys
import time
import httpx

BASE='http://127.0.0.1:8765'
OUT=Path(__file__).parent/'artifacts'
OUT.mkdir(exist_ok=True)
client=httpx.Client(base_url=BASE,timeout=30)
report={}

def wait(job_id, timeout=900):
    deadline=time.monotonic()+timeout
    stages=[]
    while time.monotonic()<deadline:
        data=client.get('/api/jobs/'+job_id).raise_for_status().json()
        if not stages or stages[-1]!=data['status']:
            stages.append(data['status']); print(job_id[:8],data['status'],flush=True)
        if data['status'] in ('completed','failed','cancelled'):
            data['observed_stages']=stages
            return data
        time.sleep(1)
    raise AssertionError('Timed out')

invalid=client.post('/api/jobs/link',json={'url':'https://127.0.0.1/private'})
assert invalid.status_code==422
report['invalid_link']={'status':invalid.status_code,'message':invalid.json()}
# A real inaccessible link: nonexistent YouTube video, with no bypass attempts.
link=client.post('/api/jobs/link',json={'url':'https://www.youtube.com/watch?v=00000000000'}).raise_for_status().json()
failed=wait(link['id'])
assert failed['status']=='failed' and failed['caption_check']=='unknown'
report['inaccessible_link']=failed
with open(sys.argv[1],'rb') as f:
    job=client.post('/api/jobs/upload',files={'file':('chinese.wav',f,'audio/wav')},data={'language':'zh'}).raise_for_status().json()
result=wait(job['id'])
assert result['status']=='completed',result
assert result['source']=='ai' and any('\u4e00'<=c<='\u9fff' for cue in result['cues'] for c in cue['text'])
assert all(0<=c['start']<c['end'] for c in result['cues'])
assert all(a['start']<=b['start'] for a,b in zip(result['cues'],result['cues'][1:]))
report['real_chinese_upload_after_link_failure']=result
for kind in ('srt','vtt','txt'):
    response=client.get(f"/api/jobs/{job['id']}/download/{kind}").raise_for_status()
    text=response.content.decode('utf-8')
    assert result['cues'][0]['text'] in text
    (OUT/f'original.{kind}').write_bytes(response.content)
result['cues'][0]['text']='这是编辑后的中文字幕。'
client.put(f"/api/jobs/{job['id']}/cues",json={'cues':result['cues']}).raise_for_status()
for kind in ('srt','vtt','txt'):
    response=client.get(f"/api/jobs/{job['id']}/download/{kind}").raise_for_status()
    assert '这是编辑后的中文字幕。' in response.content.decode('utf-8')
    (OUT/f'edited.{kind}').write_bytes(response.content)
report['edited_downloads']='SRT / VTT / TXT UTF-8 passed'
with (OUT/'silence.wav').open('rb') as f:
    silence=client.post('/api/jobs/upload',files={'file':('silence.wav',f,'audio/wav')}).raise_for_status().json()
silent=wait(silence['id'])
assert silent['status']=='failed' and silent['error_code']=='no_speech',silent
report['real_silence']=silent
(OUT/'real-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('REAL ACCEPTANCE PASSED',flush=True)
