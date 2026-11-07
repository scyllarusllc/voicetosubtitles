import asyncio
import io
import socket
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.core import Cue, export, validate_cues, youtube_url
from backend import app as module

@pytest.mark.parametrize('url', ['https://127.0.0.1/watch?v=OZKUJDEMX1w', 'https://www.youtube.com.evil.test/watch?v=OZKUJDEMX1w', 'http://youtube.com/watch?v=OZKUJDEMX1w', 'https://user:pass@youtube.com/watch?v=OZKUJDEMX1w', 'https://youtu.be/no', 'https://youtube.com:8443/watch?v=OZKUJDEMX1w'])
def test_invalid_urls(url):
    with pytest.raises(ValueError): youtube_url(url)

def test_canonical_url():
    assert youtube_url('https://youtu.be/OZKUJDEMX1w?si=x') == 'https://www.youtube.com/watch?v=OZKUJDEMX1w'

@pytest.mark.parametrize('start,end', [(1,1),(2,1),(-1,1),(float('nan'),1),(0,float('inf'))])
def test_bad_timing(start,end):
    with pytest.raises(ValueError): Cue(start=start,end=end,text='中文')

def test_exports():
    cues=[Cue(start=0,end=1.234,text='你好，世界。'),Cue(start=1.3,end=2,text='第二句话')]
    assert '你好，世界。' in export(cues,'txt').encode('utf-8').decode('utf-8')
    assert '00:00:00,000 --> 00:00:01,234' in export(cues,'srt')
    assert export(cues,'vtt').startswith('WEBVTT\n\n')
    with pytest.raises(ValueError): validate_cues(cues[::-1])

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'DATA',tmp_path)
    monkeypatch.setenv('HISTORY_DB_PATH',str(tmp_path/'accounts.sqlite3'))
    monkeypatch.setenv('PUBLIC_BASE_URL','http://127.0.0.1:8765')
    module.jobs.clear()
    module.queue = asyncio.Queue()
    with TestClient(module.app) as c: yield c

def wait(client, job_id):
    import time
    for _ in range(150):
        data=client.get('/api/jobs/'+job_id).json()
        if data['status'] in module.TERMINAL: return data
        time.sleep(.02)
    pytest.fail('job did not finish')

# These tests simulate platform and ASR replies; they do NOT claim real recognition.
@pytest.mark.parametrize('source', ['manual','automatic'])
def test_existing_subtitles(client,monkeypatch,source):
    async def fake(job,mode,path):
        if mode=='metadata':
            key='subtitles' if source=='manual' else 'automatic_captions'
            return {'duration':5,'language':'zh',key:{'zh':[{'ext':'json3','url':'https://www.youtube.com/caption'}]}}
        return {'cues':[{'start':0,'end':2,'text':'测试字幕'}]}
    monkeypatch.setattr(module,'worker_call',fake)
    data=client.post('/api/jobs/link',json={'url':'https://youtu.be/OZKUJDEMX1w'}).json()
    result=wait(client,data['id'])
    assert result['status']=='completed' and result['source']==source
    assert not Path(module.jobs[data['id']]['dir']).exists()
    edit=client.put('/api/jobs/'+data['id']+'/cues',json={'cues':[{'start':0,'end':2,'text':'已编辑的中文'}]})
    assert edit.status_code==200
    for kind in ('srt','vtt','txt'):
        assert '已编辑的中文' in client.get(f"/api/jobs/{data['id']}/download/{kind}").content.decode('utf-8')

def test_unknown_is_not_absent_and_upload_independent(client,monkeypatch):
    async def fake(job,mode,path): raise module.Failure('tool_error','HTTP 403')
    monkeypatch.setattr(module,'worker_call',fake)
    data=client.post('/api/jobs/link',json={'url':'https://youtu.be/OZKUJDEMX1w'}).json()
    result=wait(client,data['id'])
    assert result['caption_check']=='unknown' and result['error_code']=='caption_unknown'
    # Genuine ffprobe rejects text disguised as WAV, independent of link failure.
    data=client.post('/api/jobs/upload',files={'file':('fake.wav',b'not media','audio/wav')}).json()
    assert wait(client,data['id'])['error_code'] in ('tool_error','invalid_media')

def test_upload_limit(client,monkeypatch):
    monkeypatch.setattr(module,'MAX_MB',1)
    response=client.post('/api/jobs/upload',files={'file':('large.wav',b'x'*(1024**2+1),'audio/wav')})
    assert response.status_code==413

def test_cancel(client,monkeypatch):
    async def fake(job,mode,path):
        await asyncio.sleep(.1)
        return {'duration':3,'subtitles':{},'automatic_captions':{}}
    monkeypatch.setattr(module,'worker_call',fake)
    data=client.post('/api/jobs/link',json={'url':'https://youtu.be/OZKUJDEMX1w'}).json()
    assert client.post(f"/api/jobs/{data['id']}/cancel").json()['status']=='cancelled'

def test_cross_origin(client):
    assert client.post('/api/jobs/link',json={'url':'https://youtu.be/OZKUJDEMX1w'},headers={'Origin':'https://evil.test'}).status_code==403

@pytest.mark.parametrize('warning,expected', [(False,'unavailable_for_language'),(True,'unknown')])
def test_empty_tracks_and_audio_failure(client,monkeypatch,warning,expected):
    async def fake(job,mode,path):
        if mode=='metadata': return {'duration':5,'warnings':['partial extraction'] if warning else [],'subtitles':{},'automatic_captions':{}}
        raise module.Failure('tool_error','media blocked')
    monkeypatch.setattr(module,'worker_call',fake)
    data=client.post('/api/jobs/link',json={'url':'https://youtu.be/OZKUJDEMX1w'}).json()
    result=wait(client,data['id'])
    assert result['caption_check']==expected
    assert result['error_code']=='audio_unavailable'

def test_caption_download_error_is_not_no_captions(client,monkeypatch):
    async def fake(job,mode,path):
        if mode=='metadata': return {'duration':5,'subtitles':{'zh':[{'ext':'json3','url':'https://www.youtube.com/caption'}]}}
        raise module.Failure('tool_error','HTTP 429')
    monkeypatch.setattr(module,'worker_call',fake)
    data=client.post('/api/jobs/link',json={'url':'https://youtu.be/OZKUJDEMX1w'}).json()
    result=wait(client,data['id'])
    assert result['caption_check']=='download_failed'
    assert result['error_code']=='audio_unavailable'

def test_queue_limit(client,monkeypatch):
    monkeypatch.setattr(module,'MAX_PENDING',0)
    monkeypatch.setattr(module,'CONCURRENCY',1)
    async def fake(job,mode,path): await asyncio.sleep(30)
    monkeypatch.setattr(module,'worker_call',fake)
    assert client.post('/api/jobs/link',json={'url':'https://youtu.be/OZKUJDEMX1w'}).status_code==202
    assert client.post('/api/jobs/link',json={'url':'https://youtu.be/OZKUJDEMX1w'}).status_code==429


def test_submillisecond_cue_exports_nonzero_duration():
    assert '00:00:00,000 --> 00:00:00,001' in export([Cue(start=0,end=.0001,text='短')],'srt')

def test_real_duration_validation(client,monkeypatch,tmp_path):
    import subprocess
    audio=tmp_path/'long.wav'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','anullsrc=r=16000:cl=mono','-t','2','-y',str(audio)],check=True)
    monkeypatch.setattr(module,'MAX_SECONDS',1)
    with audio.open('rb') as f: data=client.post('/api/jobs/upload',files={'file':('long.wav',f,'audio/wav')}).json()
    assert wait(client,data['id'])['error_code']=='duration_limit'

def test_real_no_audio_track(client,tmp_path):
    import subprocess
    video=tmp_path/'video.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=black:s=64x64:d=1','-c:v','libx264','-y',str(video)],check=True)
    with video.open('rb') as f: data=client.post('/api/jobs/upload',files={'file':('video.mp4',f,'video/mp4')}).json()
    assert wait(client,data['id'])['error_code']=='no_audio'

@pytest.mark.parametrize('mode,expected', [('empty','no_speech'),('error','asr_failed')])
def test_simulated_asr_failures(client,monkeypatch,mode,expected):
    async def probe(job,path): job['duration']=1
    async def command(job,args,timeout=0): return ''
    async def worker(job,kind,path):
        if mode=='error': raise module.Failure('tool_error','model unavailable')
        return {'cues':[], 'language':'zh'}
    monkeypatch.setattr(module,'media_probe',probe)
    monkeypatch.setattr(module,'command',command)
    monkeypatch.setattr(module,'worker_call',worker)
    job=client.post('/api/jobs/upload',files={'file':('mock.wav',b'mocked media')}).json()
    assert wait(client,job['id'])['error_code']==expected

def test_chunked_upload_limit(client,monkeypatch):
    monkeypatch.setattr(module,'MAX_MB',1)
    def chunks():
        yield b'--testboundary\r\nContent-Disposition: form-data; name="file"; filename="large.wav"\r\nContent-Type: audio/wav\r\n\r\n'
        for _ in range(3): yield b'x'*(1024**2)
        yield b'\r\n--testboundary--\r\n'
    response=client.post('/api/jobs/upload',content=chunks(),headers={'Content-Type':'multipart/form-data; boundary=testboundary'})
    assert response.status_code==413,response.text
