"""SQLite/account tests. Google identity exchange is explicitly mocked here.

These tests verify OAuth safety and per-account persistence, not a successful
live Google login. Real Google login needs configured provider credentials.
"""
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import accounts


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('PUBLIC_BASE_URL', 'http://127.0.0.1:8765')
    monkeypatch.delenv('GOOGLE_CLIENT_ID', raising=False)
    monkeypatch.delenv('GOOGLE_CLIENT_SECRET', raising=False)
    monkeypatch.delenv('GOOGLE_REDIRECT_URI', raising=False)
    monkeypatch.delenv('SESSION_TTL_SECONDS', raising=False)
    app = FastAPI()
    app.state.store = accounts.Store(tmp_path / 'accounts.sqlite3')
    app.include_router(accounts.router)
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        yield client


def configure_google(monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'test-client-id')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'test-client-secret')


def claims(sub='google-user-1', nonce='nonce', **overrides):
    return dict(sub=sub, name='测试用户', email=f'{sub}@example.test', email_verified=True,
                iss='https://accounts.google.com', aud='test-client-id', nonce=nonce,
                exp=time.time() + 3600, **overrides)


def login_direct(client, app, sub):
    """Seed a simulated authenticated identity; not a real provider login."""
    app.state.store.upsert_user(claims(sub))
    token = app.state.store.create_session(sub)
    client.cookies.set(accounts.SESSION_COOKIE, token)
    return token


def snapshot(job_id='job-1'):
    return dict(id=job_id, status='completed', title='中文录音.wav', language='zh',
                detected_language='zh', source='ai', created_at=1, updated_at=2,
                cues=[dict(start=0, end=1.2, text='你好，世界。')],
                owner='must-not-leak', proc='must-not-persist', input='/private/media')


def test_disabled_google_and_stable_guest_cookie(client):
    response = client.get('/api/auth/me')
    assert response.json() == {'user': None, 'google_enabled': False}
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'SameSite=lax' in response.headers['set-cookie']
    token = client.cookies.get(accounts.SESSION_COOKIE)
    assert token
    client.get('/api/auth/me')
    assert client.cookies.get(accounts.SESSION_COOKIE) == token
    assert client.get('/api/auth/google').status_code == 503
    assert client.get('/api/history').status_code == 401


def test_session_tokens_are_hashed_and_survive_store_restart(app):
    store = app.state.store
    token = store.create_session()
    found = store.session(token)
    assert found['owner'].startswith('guest:')
    restarted = accounts.Store(store.path)
    assert restarted.session(token) == found
    with store.connection() as db:
        row = dict(db.execute('SELECT * FROM sessions').fetchone())
    assert token not in json.dumps(row)
    assert row['token_hash'] == hashlib.sha256(token.encode()).hexdigest()


def test_expired_session_is_rejected(app):
    token = app.state.store.create_session(ttl=-1)
    assert app.state.store.session(token) is None


def test_history_is_private_and_persistent(app):
    store = app.state.store
    store.upsert_user(claims('alice'))
    store.upsert_user(claims('bob'))
    store.save_history('alice', snapshot())
    store = accounts.Store(store.path)
    assert store.get_history('bob', 'job-1') is None
    assert store.list_history('bob') == []
    actual = store.get_history('alice', 'job-1')
    assert actual['cues'][0]['text'] == '你好，世界。'
    assert actual['history_saved'] is True
    assert 'owner' not in actual and 'proc' not in actual and 'input' not in actual
    listing = store.list_history('alice')
    assert listing[0]['cue_count'] == 1 and 'cues' not in listing[0]


def test_account_history_edit_download_delete(client, app):
    login_direct(client, app, 'alice')
    app.state.store.save_history('alice', snapshot())
    assert len(client.get('/api/history').json()['items']) == 1
    response = client.put('/api/history/job-1', json={'cues': [dict(start=0, end=1.2, text='编辑后的中文字幕')]})
    assert response.status_code == 200
    for kind in ('srt', 'vtt', 'txt'):
        response = client.get('/api/history/job-1/download/' + kind)
        assert response.status_code == 200
        assert '编辑后的中文字幕' in response.content.decode('utf-8')
        assert response.headers['cache-control'] == 'no-store'
    assert client.delete('/api/history/job-1').json() == {'deleted': True}
    assert client.get('/api/history/job-1').status_code == 404


def test_other_account_cannot_read_edit_download_or_delete(client, app):
    app.state.store.upsert_user(claims('alice'))
    app.state.store.save_history('alice', snapshot())
    login_direct(client, app, 'bob')
    assert client.get('/api/history').json()['items'] == []
    assert client.get('/api/history/job-1').status_code == 404
    assert client.get('/api/history/job-1/download/txt').status_code == 404
    assert client.put('/api/history/job-1', json={'cues': snapshot()['cues']}).status_code == 404
    assert client.delete('/api/history/job-1').status_code == 404
    assert app.state.store.get_history('alice', 'job-1')


def test_history_rejects_invalid_timeline(client, app):
    login_direct(client, app, 'alice')
    app.state.store.save_history('alice', snapshot())
    assert client.put('/api/history/job-1', json={'cues': [dict(start=3, end=2, text='坏时间轴')]}).status_code == 422
    assert client.put('/api/history/job-1', json={'cues': []}).status_code == 422


def test_logout_revokes_session_but_preserves_history(client, app):
    token = login_direct(client, app, 'alice')
    app.state.store.save_history('alice', snapshot())
    assert client.post('/api/auth/logout').json() == {'user': None}
    assert app.state.store.session(token) is None
    assert client.get('/api/history').status_code == 401
    assert app.state.store.get_history('alice', 'job-1')


def test_oauth_redirect_has_pkce_nonce_and_only_identity_scopes(client, monkeypatch):
    configure_google(monkeypatch)
    response = client.get('/api/auth/google', follow_redirects=False)
    assert response.status_code == 302
    parsed = urlparse(response.headers['location'])
    assert parsed.scheme == 'https' and parsed.netloc == 'accounts.google.com'
    query = parse_qs(parsed.query)
    assert query['code_challenge_method'] == ['S256']
    assert query['scope'] == ['openid email profile']
    assert query['state'][0] and query['nonce'][0] and query['code_challenge'][0]
    assert 'test-client-secret' not in response.headers['location']
    assert client.cookies.get(accounts.OAUTH_COOKIE)


def test_oauth_rejects_wrong_browser_and_replay(app, client, monkeypatch):
    configure_google(monkeypatch)
    started = client.get('/api/auth/google', follow_redirects=False)
    state = parse_qs(urlparse(started.headers['location']).query)['state'][0]
    with TestClient(app, base_url='http://127.0.0.1:8765') as attacker:
        response = attacker.get('/api/auth/google/callback', params={'state': state, 'code': 'fake-code'}, follow_redirects=False)
        assert response.headers['location'] == '/?auth=error'
    # Valid browser provider denial consumes state, so it cannot be replayed.
    response = client.get('/api/auth/google/callback', params={'state': state, 'error': 'access_denied'}, follow_redirects=False)
    assert response.headers['location'] == '/?auth=error'
    assert app.state.store.consume_oauth(state, 'anything') is None


def test_simulated_google_login_rotates_session_and_validates_nonce(client, app, monkeypatch):
    configure_google(monkeypatch)
    client.get('/api/auth/me')
    old = client.cookies.get(accounts.SESSION_COOKIE)
    started = client.get('/api/auth/google', follow_redirects=False)
    query = parse_qs(urlparse(started.headers['location']).query)
    async def exchange(code, verifier, settings):
        assert code == 'fake-auth-code' and len(verifier) >= 43
        return 'fake-signed-token'
    monkeypatch.setattr(accounts, '_exchange_code', exchange)
    monkeypatch.setattr(accounts, '_verify_google_token', lambda token, audience: claims(nonce=query['nonce'][0]))
    response = client.get('/api/auth/google/callback', params={'state': query['state'][0], 'code': 'fake-auth-code'}, follow_redirects=False)
    assert response.headers['location'] == '/?auth=success'
    assert app.state.store.session(old) is None
    assert client.get('/api/auth/me').json()['user']['id'] == 'google-user-1'
    assert client.cookies.get(accounts.SESSION_COOKIE) != old
    assert client.get('/api/auth/google/callback', params={'state': query['state'][0], 'code': 'fake-auth-code'}, follow_redirects=False).headers['location'] == '/?auth=error'


@pytest.mark.parametrize('field,value', [('nonce','wrong'),('iss','https://evil.test'),('aud','other-client'),('azp','other-client'),('exp',0),('sub',''),('email_verified',False)])
def test_claim_validation_rejects_untrusted_identity(field, value):
    actual = claims()
    actual[field] = value
    with pytest.raises(ValueError):
        accounts._validate_claims(actual, 'nonce', 'test-client-id')


def test_wrong_nonce_callback_does_not_log_in(client, monkeypatch):
    configure_google(monkeypatch)
    response = client.get('/api/auth/google', follow_redirects=False)
    query = parse_qs(urlparse(response.headers['location']).query)
    async def exchange(*args): return 'mock-token'
    monkeypatch.setattr(accounts, '_exchange_code', exchange)
    monkeypatch.setattr(accounts, '_verify_google_token', lambda *args: claims(nonce='wrong-nonce'))
    response = client.get('/api/auth/google/callback', params={'state': query['state'][0], 'code': 'mock-code'}, follow_redirects=False)
    assert response.headers['location'] == '/?auth=error'
    assert client.get('/api/auth/me').json()['user'] is None


def test_https_sets_secure_cookie(client, monkeypatch):
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://subtitles.example')
    response = client.get('/api/auth/me')
    assert 'Secure' in response.headers['set-cookie']


def test_misconfigured_callback_does_not_enable_google(client, monkeypatch):
    configure_google(monkeypatch)
    monkeypatch.setenv('GOOGLE_REDIRECT_URI', 'https://evil.test/callback')
    assert client.get('/api/auth/me').json()['google_enabled'] is False
    assert client.get('/api/auth/google').status_code == 503


def test_account_storage_and_start_time_survive_all_reads(app):
    store = app.state.store
    store.upsert_user(claims('alice'))
    data = snapshot()
    data.update(storage='guest', started_at=123.0)
    saved = store.save_history('alice', data)
    assert saved['storage'] == 'account' and saved['started_at'] == 123.0
    assert store.get_history('alice', data['id'])['storage'] == 'account'
    assert store.list_history('alice')[0]['storage'] == 'account'
    assert store.list_history('alice')[0]['started_at'] == 123.0


def test_deleted_history_cannot_be_resurrected_by_task_finalizer(app):
    store = app.state.store
    store.upsert_user(claims('alice'))
    store.save_history('alice', snapshot())
    assert store.delete_history('alice', 'job-1') is True
    # Simulate a still-running task finally saving its stale completion snapshot.
    assert store.save_history('alice', snapshot()) is None
    assert store.get_history('alice', 'job-1') is None
    assert store.list_history('alice') == []
    # The deletion barrier must survive a server/store restart as well.
    restarted = accounts.Store(store.path)
    assert restarted.save_history('alice', snapshot()) is None
    assert restarted.get_history('alice', 'job-1') is None


def test_deletion_barrier_is_scoped_to_the_account(app):
    store = app.state.store
    for name in ('alice', 'bob'):
        store.upsert_user(claims(name))
        store.save_history(name, snapshot())
    store.delete_history('alice', 'job-1')
    assert store.save_history('alice', snapshot()) is None
    assert store.save_history('bob', snapshot()) is not None
    assert store.get_history('bob', 'job-1') is not None


def test_history_listing_is_limited_without_discarding_older_records(app):
    store = app.state.store
    store.upsert_user(claims('alice'))
    for number in range(105):
        data = snapshot('job-' + str(number))
        data['updated_at'] = number
        store.save_history('alice', data)
    listing = store.list_history('alice')
    assert len(listing) == 100
    assert listing[0]['id'] == 'job-104' and listing[-1]['id'] == 'job-5'
    assert all('cues' not in item and item['cue_count'] == 1 for item in listing)
    assert store.get_history('alice', 'job-0') is not None
