"""App/SQLite integration; identity and ASR are simulated, storage/ownership real."""
import asyncio
import time
import pytest
from fastapi.testclient import TestClient
from backend import app as module
from backend.accounts import SESSION_COOKIE, Store

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'DATA',tmp_path)
    monkeypatch.setenv('HISTORY_DB_PATH',str(tmp_path/'history.sqlite3'))
    monkeypatch.setenv('PUBLIC_BASE_URL','http://127.0.0.1:8765')
    module.jobs.clear()
    module.queue=asyncio.Queue()
    with TestClient(module.app) as client:
        yield client


def simulate_asr(monkeypatch):
    async def probe(job,path): job['duration']=2
    async def command(job,args,timeout=0): return ''
    async def worker(job,kind,path):
        await asyncio.sleep(.02)
        return {'cues':[{'start':0,'end':2,'text':'真实存储，模拟识别'}],'language':'zh'}
    monkeypatch.setattr(module,'media_probe',probe)
    monkeypatch.setattr(module,'command',command)
    monkeypatch.setattr(module,'worker_call',worker)


def wait(client,job_id):
    for _ in range(100):
        data=client.get('/api/jobs/'+job_id).json()
        if data['status'] in module.TERMINAL:return data
        time.sleep(.02)
    pytest.fail('Task not completed')


def test_guest_job_requires_own_cookie_and_not_account_history(client,monkeypatch):
    simulate_asr(monkeypatch)
    job=client.post('/api/jobs/upload',files={'file':('test.wav',b'test')}).json()
    result=wait(client,job['id'])
    assert result['storage']=='guest' and result['status']=='completed'
    assert 'owner' not in result and 'account_id' not in result
    cookie=client.cookies.get(SESSION_COOKIE)
    client.cookies.clear()
    assert client.get('/api/jobs/'+job['id']).status_code==404
    assert client.get('/api/jobs/'+job['id']+'/download/txt').status_code==404
    assert client.post('/api/jobs/'+job['id']+'/cancel').status_code==404
    assert client.get('/api/history').status_code==401
    client.cookies.set(SESSION_COOKIE,cookie)
    assert client.get('/api/jobs/'+job['id']).status_code==200


def test_authenticated_job_persists_and_can_edit_after_memory_cleared(client,monkeypatch):
    simulate_asr(monkeypatch)
    store=module.app.state.store
    user=store.upsert_user({'sub':'test-user','name':'模拟账户','email':'test@example.test'})
    client.cookies.set(SESSION_COOKIE,store.create_session(user['id']))
    job=client.post('/api/jobs/upload',files={'file':('test.wav',b'test')}).json()
    result=wait(client,job['id'])
    assert result['history_saved'] is True and result['storage']=='account'
    assert len(client.get('/api/history').json()['items'])==1
    module.jobs.clear()
    module.app.state.store=Store(store.path)  # actual database reopen
    assert client.get('/api/jobs/'+job['id']).status_code==404
    assert client.get('/api/history/'+job['id']).json()['cues']==result['cues']
    changed=[{'start':0,'end':2,'text':'重新打开后编辑的中文'}]
    assert client.put('/api/history/'+job['id'],json={'cues':changed}).status_code==200
    assert '重新打开后编辑的中文' in client.get('/api/history/'+job['id']+'/download/srt').text
    assert client.post('/api/auth/logout').status_code==200
    assert client.get('/api/history/'+job['id']).status_code==401

def test_account_edits_sync_with_live_job_and_deleted_history_stays_deleted(client,monkeypatch):
    simulate_asr(monkeypatch)
    store=module.app.state.store
    store.upsert_user({'sub':'owner','name':'模拟用户','email':'owner@example.test'})
    client.cookies.set(SESSION_COOKIE,store.create_session('owner'))
    job=client.post('/api/jobs/upload',files={'file':('test.wav',b'test')}).json()
    wait(client,job['id'])
    changed=[{'start':0,'end':2,'text':'历史编辑的新内容'}]
    assert client.put('/api/history/'+job['id'],json={'cues':changed}).status_code==200
    assert client.get('/api/jobs/'+job['id']).json()['cues']==changed
    assert '历史编辑的新内容' in client.get('/api/jobs/'+job['id']+'/download/txt').text
    store.upsert_user({'sub':'outsider','name':'其他用户','email':'other@example.test'})
    original=client.cookies.get(SESSION_COOKIE)
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE,store.create_session('outsider'))
    assert client.get('/api/jobs/'+job['id']).status_code==404
    assert client.put('/api/jobs/'+job['id']+'/cues',json={'cues':changed}).status_code==404
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE,original)
    assert client.delete('/api/history/'+job['id']).status_code==200
    module.save_account_history(module.jobs[job['id']])
    assert store.get_history('owner',job['id']) is None
    assert client.get('/api/jobs/'+job['id']).status_code==404
