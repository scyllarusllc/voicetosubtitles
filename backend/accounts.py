"""Google OpenID Connect login and private, persistent subtitle history.

The app owns Store initialization and the same-origin write-request guard.
No Google access/refresh tokens are persisted. Guest subtitle data stays in the
browser; only short-lived opaque guest session identities are kept here.
"""
import asyncio
import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from backend.core import Cue, export, validate_cues

router = APIRouter()
SESSION_COOKIE = 'subtitle_session'
OAUTH_COOKIE = 'subtitle_oauth'
GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
OAUTH_TTL = 600
HISTORY_FIELDS = {
    'id', 'status', 'message', 'language', 'kind', 'source', 'caption_check',
    'notices', 'cues', 'error_code', 'created_at', 'updated_at', 'title',
    'duration', 'detected_language', 'progress', 'storage', 'started_at',
}
logger = logging.getLogger('subtitles.accounts')


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _settings():
    base = os.getenv('PUBLIC_BASE_URL', 'http://127.0.0.1:8765').rstrip('/')
    redirect = os.getenv('GOOGLE_REDIRECT_URI', base + '/api/auth/google/callback')
    client_id = os.getenv('GOOGLE_CLIENT_ID', '').strip()
    secret = os.getenv('GOOGLE_CLIENT_SECRET', '').strip()
    parsed = urlparse(base)
    callback = urlparse(redirect)
    safe_base = (
        parsed.hostname and not parsed.username and not parsed.password
        and not parsed.query and not parsed.fragment and parsed.path in ('', '/')
        and (parsed.scheme == 'https' or (
            parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1')
        ))
    )
    safe_callback = (
        callback.scheme == parsed.scheme and callback.netloc == parsed.netloc
        and callback.path == '/api/auth/google/callback'
        and not callback.query and not callback.fragment
    )
    ttl = int(os.getenv('SESSION_TTL_SECONDS', str(30 * 86400)))
    if ttl < 60:
        raise RuntimeError('SESSION_TTL_SECONDS 必须至少为 60 秒')
    return dict(base=base, redirect=redirect, client_id=client_id, secret=secret,
                enabled=bool(client_id and secret and safe_base and safe_callback),
                secure=parsed.scheme == 'https', ttl=ttl)


class Store:
    def __init__(self, db_path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, guest_id TEXT NOT NULL,
                    user_id TEXT REFERENCES users(id), expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS oauth_states (
                    state_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL,
                    nonce TEXT NOT NULL, verifier TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS history (
                    user_id TEXT NOT NULL REFERENCES users(id), job_id TEXT NOT NULL,
                    snapshot TEXT NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, job_id)
                );
                CREATE INDEX IF NOT EXISTS history_user_time ON history(user_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS history_tombstones (
                    user_id TEXT NOT NULL REFERENCES users(id), job_id TEXT NOT NULL,
                    deleted_at REAL NOT NULL,
                    PRIMARY KEY (user_id, job_id)
                );
            ''')
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_session(self, user_id=None, ttl=None):
        token = secrets.token_urlsafe(48)
        guest_id = secrets.token_hex(24)
        with self.connection() as db:
            db.execute('DELETE FROM sessions WHERE expires_at <= ?', (time.time(),))
            db.execute('INSERT INTO sessions VALUES (?, ?, ?, ?)',
                       (_hash(token), guest_id, user_id, time.time() + (ttl or _settings()['ttl'])))
        return token

    def session(self, token):
        if not token or len(token) > 256:
            return None
        with self.connection() as db:
            row = db.execute('''
                SELECT s.guest_id, s.user_id, u.name, u.email
                FROM sessions s LEFT JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ? AND s.expires_at > ?
            ''', (_hash(token), time.time())).fetchone()
        if not row:
            return None
        if row['user_id']:
            user = dict(id=row['user_id'], name=row['name'], email=row['email'])
            return {'owner': 'user:' + row['user_id'], 'user': user}
        return {'owner': 'guest:' + row['guest_id'], 'user': None}

    def revoke_session(self, token):
        if token and len(token) <= 256:
            with self.connection() as db:
                db.execute('DELETE FROM sessions WHERE token_hash = ?', (_hash(token),))

    def upsert_user(self, claims):
        user = {'id': claims['sub'], 'name': str(claims.get('name') or claims['email'])[:200],
                'email': claims['email']}
        with self.connection() as db:
            db.execute('''INSERT INTO users VALUES (?, ?, ?, ?)
                          ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                          email=excluded.email, updated_at=excluded.updated_at''',
                       (user['id'], user['name'], user['email'], time.time()))
        return user

    def create_oauth(self, state, browser, nonce, verifier):
        with self.connection() as db:
            db.execute('DELETE FROM oauth_states WHERE expires_at <= ?', (time.time(),))
            db.execute('INSERT INTO oauth_states VALUES (?, ?, ?, ?, ?)',
                       (_hash(state), _hash(browser), nonce, verifier, time.time() + OAUTH_TTL))

    def consume_oauth(self, state, browser):
        if not state or not browser or len(state) > 256 or len(browser) > 256:
            return None
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM oauth_states WHERE state_hash = ?', (_hash(state),)).fetchone()
            if not row or row['expires_at'] <= time.time() or not hmac.compare_digest(row['browser_hash'], _hash(browser)):
                return None
            db.execute('DELETE FROM oauth_states WHERE state_hash = ?', (_hash(state),))
            return dict(row)

    def save_history(self, user_id, snapshot):
        data = {k: v for k, v in snapshot.items() if k in HISTORY_FIELDS}
        if not isinstance(data.get('id'), str) or not data['id']:
            raise ValueError('历史记录缺少任务 ID')
        if data.get('status') == 'completed':
            validate_cues([Cue(**cue) for cue in data.get('cues', [])])
        data['history_saved'] = True
        data['storage'] = 'account'
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
        with self.connection() as db:
            # Serialize the tombstone check with the upsert. A task's finalizer
            # must never resurrect an entry that its owner has deleted.
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM history_tombstones WHERE user_id = ? AND job_id = ?',
                          (user_id, data['id'])).fetchone():
                return None
            db.execute('''INSERT INTO history VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, job_id) DO UPDATE SET
                snapshot=excluded.snapshot, updated_at=excluded.updated_at''',
                       (user_id, data['id'], encoded, data.get('updated_at', time.time())))
        return data

    def get_history(self, user_id, job_id):
        with self.connection() as db:
            row = db.execute('SELECT snapshot FROM history WHERE user_id = ? AND job_id = ?',
                             (user_id, job_id)).fetchone()
        if not row:
            return None
        item = json.loads(row['snapshot'])
        item['storage'] = 'account'
        return item

    def list_history(self, user_id):
        with self.connection() as db:
            # Select only the newest 100 rows and strip cues inside SQLite so
            # listing does not materialize every transcript in Python memory.
            rows = db.execute("""
                SELECT json_remove(snapshot, '$.cues') AS metadata,
                       json_array_length(snapshot, '$.cues') AS cue_count
                FROM history WHERE user_id = ?
                ORDER BY updated_at DESC LIMIT 100
            """, (user_id,)).fetchall()
        items = []
        for row in rows:
            item = json.loads(row['metadata'])
            item['cue_count'] = row['cue_count'] or 0
            item['storage'] = 'account'
            items.append(item)
        return items

    def delete_history(self, user_id, job_id):
        with self.connection() as db:
            deleted = db.execute('DELETE FROM history WHERE user_id = ? AND job_id = ?',
                                 (user_id, job_id)).rowcount > 0
            if deleted:
                db.execute("""INSERT INTO history_tombstones VALUES (?, ?, ?)
                    ON CONFLICT(user_id, job_id) DO UPDATE SET deleted_at=excluded.deleted_at""",
                           (user_id, job_id, time.time()))
            return deleted


def _cookie(response, name, value, max_age):
    response.set_cookie(name, value, max_age=max_age, httponly=True,
                        secure=_settings()['secure'], samesite='lax', path='/')


def identity(request: Request, response: Response | None = None):
    store = request.app.state.store
    found = store.session(request.cookies.get(SESSION_COOKIE))
    if found or response is None:
        return found
    token = store.create_session()
    _cookie(response, SESSION_COOKIE, token, _settings()['ttl'])
    return store.session(token)


def _user(request):
    found = identity(request)
    if not found or not found['user']:
        raise HTTPException(401, '请先使用 Google 登录')
    return found['user']


def _history(request, job_id):
    user = _user(request)
    item = request.app.state.store.get_history(user['id'], job_id)
    if item is None:
        raise HTTPException(404, '历史记录不存在')
    return user, item


@router.get('/api/auth/me')
async def me(request: Request, response: Response):
    found = identity(request, response)
    response.headers['Cache-Control'] = 'no-store'
    return {'user': found['user'], 'google_enabled': _settings()['enabled']}


@router.get('/api/auth/google')
async def google_login(request: Request):
    settings = _settings()
    if not settings['enabled']:
        raise HTTPException(503, 'Google 登录暂不可用')
    state, browser, nonce, verifier = (secrets.token_urlsafe(48) for _ in range(4))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    request.app.state.store.create_oauth(state, browser, nonce, verifier)
    query = urlencode(dict(client_id=settings['client_id'], redirect_uri=settings['redirect'],
                          response_type='code', scope='openid email profile', state=state,
                          nonce=nonce, code_challenge=challenge, code_challenge_method='S256',
                          prompt='select_account'))
    response = RedirectResponse(GOOGLE_AUTH_URL + '?' + query, status_code=302)
    response.headers['Cache-Control'] = 'no-store'
    _cookie(response, OAUTH_COOKIE, browser, OAUTH_TTL)
    return response


async def _exchange_code(code, verifier, settings):
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
        response = await client.post(GOOGLE_TOKEN_URL, data={
            'code': code, 'client_id': settings['client_id'], 'client_secret': settings['secret'],
            'redirect_uri': settings['redirect'], 'grant_type': 'authorization_code',
            'code_verifier': verifier,
        })
        response.raise_for_status()
        token = response.json().get('id_token')
        if not isinstance(token, str) or not token or len(token) > 20000:
            raise ValueError('Missing ID token')
        return token


def _verify_google_token(token, audience):
    # google-auth checks Google's signatures, expiry and audience. This call is
    # offloaded to a thread; the transport has a bounded request timeout.
    import requests
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2 import id_token
    with requests.Session() as session:
        session.trust_env = False
        transport = GoogleRequest(session=session)
        def bounded_request(url, method='GET', body=None, headers=None, **kwargs):
            kwargs['timeout'] = 15
            return transport(url, method=method, body=body, headers=headers, **kwargs)
        return id_token.verify_oauth2_token(token, bounded_request, audience=audience)


def _validate_claims(claims, nonce, audience):
    if claims.get('iss') not in ('accounts.google.com', 'https://accounts.google.com'):
        raise ValueError('Invalid issuer')
    if claims.get('aud') != audience or claims.get('azp', audience) != audience:
        raise ValueError('Invalid audience')
    if not isinstance(claims.get('exp'), (int, float)) or claims['exp'] <= time.time():
        raise ValueError('Expired ID token')
    if not isinstance(claims.get('nonce'), str) or not hmac.compare_digest(claims['nonce'], nonce):
        raise ValueError('Invalid nonce')
    if not isinstance(claims.get('sub'), str) or not claims['sub'] or len(claims['sub']) > 255:
        raise ValueError('Invalid subject')
    if claims.get('email_verified') is not True or not isinstance(claims.get('email'), str) or not claims['email']:
        raise ValueError('Verified email required')


@router.get('/api/auth/google/callback')
async def google_callback(request: Request, state: str = '', code: str = '', error: str = ''):
    settings = _settings()
    response = RedirectResponse('/?auth=error', status_code=302)
    response.headers['Cache-Control'] = 'no-store'
    response.delete_cookie(OAUTH_COOKIE, path='/', secure=settings['secure'], httponly=True, samesite='lax')
    pending = request.app.state.store.consume_oauth(state, request.cookies.get(OAUTH_COOKIE, ''))
    if not settings['enabled'] or not pending or error or not code or len(code) > 10000:
        return response
    try:
        token = await _exchange_code(code, pending['verifier'], settings)
        claims = await asyncio.to_thread(_verify_google_token, token, settings['client_id'])
        _validate_claims(claims, pending['nonce'], settings['client_id'])
        user = request.app.state.store.upsert_user(claims)
        request.app.state.store.revoke_session(request.cookies.get(SESSION_COOKIE))
        session = request.app.state.store.create_session(user['id'], settings['ttl'])
    except Exception as exc:
        # Do not log provider responses, token bodies, auth codes, or secrets.
        logger.warning('Google login failed (%s)', type(exc).__name__)
        return response
    response.headers['location'] = '/?auth=success'
    _cookie(response, SESSION_COOKIE, session, settings['ttl'])
    return response


@router.post('/api/auth/logout')
async def logout(request: Request, response: Response):
    request.app.state.store.revoke_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path='/', secure=_settings()['secure'], httponly=True, samesite='lax')
    response.headers['Cache-Control'] = 'no-store'
    return {'user': None}


class HistoryEdit(BaseModel):
    cues: list[Cue] = Field(max_length=50000)


@router.get('/api/history')
async def list_history(request: Request, response: Response):
    user = _user(request)
    response.headers['Cache-Control'] = 'no-store'
    return {'items': request.app.state.store.list_history(user['id'])}


@router.get('/api/history/{job_id}')
async def history_item(request: Request, response: Response, job_id: str):
    _, item = _history(request, job_id)
    response.headers['Cache-Control'] = 'no-store'
    return item


@router.put('/api/history/{job_id}')
async def edit_history(request: Request, response: Response, job_id: str, body: HistoryEdit):
    user, item = _history(request, job_id)
    if item['status'] != 'completed':
        raise HTTPException(409, '只能编辑已完成的字幕')
    try:
        validate_cues(body.cues)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    item.update(cues=[cue.model_dump() for cue in body.cues], updated_at=time.time())
    response.headers['Cache-Control'] = 'no-store'
    saved = request.app.state.store.save_history(user['id'], item)
    if saved is None:
        raise HTTPException(404, '历史记录已删除')
    return saved


@router.delete('/api/history/{job_id}')
async def remove_history(request: Request, response: Response, job_id: str):
    user, _ = _history(request, job_id)
    request.app.state.store.delete_history(user['id'], job_id)
    response.headers['Cache-Control'] = 'no-store'
    return {'deleted': True}


@router.get('/api/history/{job_id}/download/{kind}')
async def download_history(request: Request, job_id: str, kind: str):
    _, item = _history(request, job_id)
    if kind not in ('srt', 'vtt', 'txt'):
        raise HTTPException(422, '格式无效')
    if item['status'] != 'completed':
        raise HTTPException(409, '字幕尚未完成')
    content = export([Cue(**cue) for cue in item['cues']], kind)
    return Response(content.encode('utf-8'), media_type='text/vtt' if kind == 'vtt' else 'text/plain',
                    headers={'Content-Disposition': f'attachment; filename="subtitles.{kind}"',
                             'Cache-Control': 'no-store'})
