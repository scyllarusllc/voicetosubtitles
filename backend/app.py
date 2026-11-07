import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid
from dotenv import load_dotenv
from filelock import FileLock
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from backend.core import Cue, export, validate_cues, youtube_url
from backend.accounts import Store, identity, router as accounts_router
from backend.progress import PHASE_MESSAGES

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
MAX_MB = int(os.getenv('MAX_UPLOAD_MB', '200'))
MAX_SECONDS = int(os.getenv('MAX_DURATION_SECONDS', '3600'))
CONCURRENCY = int(os.getenv('MAX_CONCURRENT_JOBS', '1'))
MAX_PENDING = int(os.getenv('MAX_PENDING_JOBS', '8'))
TTL = int(os.getenv('JOB_TTL_SECONDS', '3600'))
TIMEOUT = int(os.getenv('TASK_TIMEOUT_SECONDS', '7200'))
if min(MAX_MB, MAX_SECONDS, CONCURRENCY, TTL, TIMEOUT) < 1 or MAX_PENDING < 0:
    raise RuntimeError('限制参数必须为正整数，MAX_PENDING_JOBS 可以为 0')
DATA = Path(os.getenv('DATA_DIR', str(ROOT / '.data'))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
jobs = {}
queue = asyncio.Queue()
TERMINAL = {'completed', 'failed', 'cancelled'}

class Failure(Exception):
    def __init__(self, code, message): self.code, self.message = code, message

class LinkInput(BaseModel):
    url: str = Field(max_length=2048)
    language: str = 'auto'

class EditInput(BaseModel):
    cues: list[Cue] = Field(max_length=50000)

def language_check(value):
    if value not in ('auto', 'zh', 'en', 'es'):
        raise HTTPException(422, '语言选择无效')

def get_job(job_id, request):
    job = jobs.get(job_id)
    who = identity(request)
    if not job or not who or job.get('owner') != who['owner'] or job.get('history_deleted'):
        raise HTTPException(404, '任务不存在、已过期或无权访问')
    if job.get('account_id') and job.get('history_saved') and job['status'] in TERMINAL:
        saved = request.app.state.store.get_history(job['account_id'], job_id)
        if saved is None:
            raise HTTPException(404, '历史记录已删除')
        job.update(cues=saved.get('cues', []), updated_at=saved['updated_at'])
    return job

def public(job):
    return {k: v for k, v in job.items() if k not in ('dir', 'proc', 'cancel', 'input', 'owner', 'account_id', 'history_deleted')}

def stage(job, status, message):
    if job['cancel']: raise asyncio.CancelledError()
    job.update(status=status, message=message, updated_at=time.time())
    if status != 'recognizing':
        job['progress'] = None
    if status not in ('waiting', *TERMINAL) and not job.get('started_at'):
        job['started_at'] = time.time()

def new_job(language, kind, value=None, who=None):
    if sum(j['status'] not in TERMINAL for j in jobs.values()) >= MAX_PENDING + CONCURRENCY:
        raise HTTPException(429, '任务队列已满，请稍后重试')
    job_id = uuid.uuid4().hex
    directory = Path(tempfile.mkdtemp(prefix='job-', dir=DATA))
    job = dict(id=job_id, status='waiting', message='等待处理', language=language, kind=kind, source=None, caption_check='not_checked', notices=[], cues=[], error_code=None, created_at=time.time(), updated_at=time.time(), dir=directory, proc=None, cancel=False, input=value,
               owner=who['owner'], account_id=who['user']['id'] if who['user'] else None,
               storage='account' if who['user'] else 'guest', progress=None, history_saved=False)
    jobs[job_id] = job
    return job

def save_account_history(job):
    if not job.get('account_id') or job['status'] not in TERMINAL or job.get('history_deleted'):
        return
    try:
        job['history_saved'] = True
        saved = app.state.store.save_history(job['account_id'], public(job))
        if saved is None:
            job.update(history_saved=False, history_deleted=True)
    except Exception:
        job['history_saved'] = False
        logging.getLogger('subtitles').exception('Unable to save account history for task %s', job['id'])
        message = '账户历史保存失败，请先下载字幕，再重试保存。'
        if message not in job['notices']:
            job['notices'].append(message)

async def monitor_progress(job, proc):
    last_report = None
    while proc.returncode is None:
        if job['status'] == 'recognizing' and not job['cancel']:
            try:
                data = json.loads((job['dir'] / 'progress.json').read_text())
                if data.get('phase') in PHASE_MESSAGES and data.get('reported_at') != last_report:
                    job['progress'] = data
                    job['message'] = PHASE_MESSAGES[data['phase']]
                    job['updated_at'] = time.time()
                    last_report = data.get('reported_at')
            except (FileNotFoundError, json.JSONDecodeError):
                pass
        await asyncio.sleep(.2)

async def command(job, args, timeout=TIMEOUT):
    if job['cancel']: raise asyncio.CancelledError()
    log = job['dir'] / ('stderr-' + uuid.uuid4().hex + '.log')
    out = job['dir'] / ('stdout-' + uuid.uuid4().hex + '.log')
    with log.open('wb') as err, out.open('wb') as stdout:
        proc = await asyncio.create_subprocess_exec(*args, stdout=stdout, stderr=err, cwd=ROOT)
        job['proc'] = proc
        progress_task = asyncio.create_task(monitor_progress(job, proc))
        try:
            await asyncio.wait_for(proc.wait(), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if proc.returncode is None: proc.kill()
            await proc.wait()
            if job['cancel']: raise asyncio.CancelledError()
            raise Failure('tool_timeout', '处理超时，请重试或上传较短文件')
        finally:
            progress_task.cancel()
            await asyncio.gather(progress_task, return_exceptions=True)
            job['proc'] = None
    if job['cancel']: raise asyncio.CancelledError()
    stderr = log.read_text(errors='replace')[-1600:]
    if proc.returncode:
        raise Failure('tool_error', stderr or '媒体工具执行失败')
    return out.read_text(errors='replace')

async def worker_call(job, mode, path):
    return json.loads(await command(job, [sys.executable, '-m', 'backend.worker', mode, str(path), job['language'], str(job['dir'])]))

async def media_probe(job, path):
    try:
        data = json.loads(await command(job, ['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_format', '-show_streams', '-of', 'json', str(path)], 30))
        formats = set(data.get('format', {}).get('format_name', '').split(','))
        if not formats.intersection({'mov','mp4','m4a','3gp','3g2','mj2','mp3','wav','matroska','webm','ogg','flac','aac','aiff'}):
            raise Failure('invalid_media', '文件实际内容不是支持的视频或音频格式')
        duration = float(data['format']['duration'])
        if not 0 < duration <= MAX_SECONDS:
            raise Failure('duration_limit', f'文件时长必须在 0 到 {MAX_SECONDS} 秒之间')
        if not any(s['codec_type'] == 'audio' for s in data['streams']):
            raise Failure('no_audio', '文件没有音轨，请上传包含人声的文件')
        job['duration'] = duration
    except Failure as exc:
        if exc.code == 'tool_error':
            raise Failure('invalid_media', '无法验证文件真实内容，请上传有效的视频或音频文件')
        raise
    except Exception:
        raise Failure('invalid_media', '无法验证文件内容或时长，请上传有效的 MP4、MOV、MP3、M4A、WAV 文件')

async def process(job):
    try:
        async with asyncio.timeout(TIMEOUT):
            path = job['dir'] / 'upload'
            if job['kind'] == 'link':
                stage(job, 'fetching_media', '正在检查平台字幕')
                try:
                    info = await worker_call(job, 'metadata', job['input'])
                except Failure as exc:
                    job['caption_check'] = 'unknown'
                    raise Failure('caption_unknown', '平台限制、网络或提取工具错误，无法判断是否有字幕。未绕过访问限制，请上传你有权使用的文件。工具信息：' + exc.message[-500:])
                job['title'] = info.get('title')
                if info.get('is_live') or not info.get('duration') or info['duration'] > MAX_SECONDS:
                    raise Failure('duration_limit', f'暂不支持直播、未知时长或超过 {MAX_SECONDS} 秒的视频')
                selected = None
                for key, source in [('subtitles', 'manual'), ('automatic_captions', 'automatic')]:
                    tracks = info.get(key) or {}
                    keys = list(tracks)
                    if job['language'] != 'auto':
                        keys = [k for k in keys if k == job['language'] or k.startswith(job['language'] + '-')]
                    else:
                        keys.sort(key=lambda k: (k != info.get('language'), k not in ('zh', 'zh-Hans', 'en', 'es')))
                    for lang in keys:
                        # Exclude translated automatic tracks; use original platform tracks.
                        track = next((t for t in tracks[lang] if t.get('ext') == 'json3' and 'tlang=' not in t.get('url', '')), None)
                        if track:
                            selected = (track['url'], source, lang)
                            break
                    if selected: break
                if selected:
                    job['caption_check'] = 'found'
                    try:
                        result = await worker_call(job, 'caption', selected[0])
                        cues = validate_cues([Cue(**c) for c in result['cues']])
                        job.update(cues=[c.model_dump() for c in cues], source=selected[1], detected_language=selected[2])
                        stage(job, 'completed', '已提取现有字幕，可编辑并下载')
                        return
                    except (Failure, ValueError) as exc:
                        job['caption_check'] = 'download_failed'
                        job['notices'].append('发现字幕轨道，但获取或解析失败；这不表示没有字幕。尝试获取音频进行识别。')
                elif info.get('warnings'):
                    job['caption_check'] = 'unknown'
                    job['notices'].append('平台提取过程中有警告，不能确认是否存在字幕；尝试获取音频识别。若受访问限制，请改为上传。')
                else:
                    job['caption_check'] = 'unavailable_for_language'
                    job['notices'].append('平台返回的字幕列表中，没有可用的所选语言原始字幕轨道，转为音频识别。')
                stage(job, 'fetching_media', '正在获取音频；若平台限制访问，请使用上传入口')
                try:
                    await worker_call(job, 'audio', job['input'])
                    files = [p for p in job['dir'].glob('media.*') if p.suffix not in ('.part', '.ytdl')]
                    if not files: raise ValueError('未获取到媒体')
                    path = files[0]
                    if path.stat().st_size > MAX_MB * 1024**2: raise ValueError('媒体文件超过大小限制')
                except (Failure, ValueError) as exc:
                    raise Failure('audio_unavailable', '音频获取失败，请上传你有权使用的文件。' + str(getattr(exc, 'message', exc))[-500:])
            stage(job, 'extracting_audio', '验证真实文件格式并提取音频')
            await media_probe(job, path)
            audio = job['dir'] / 'audio.wav'
            await command(job, ['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-i', str(path), '-vn', '-ac', '1', '-ar', '16000', '-t', str(MAX_SECONDS), '-y', str(audio)])
            stage(job, 'recognizing', '正在检查语音识别模型')
            try:
                result = await worker_call(job, 'asr', audio)
            except Failure as exc:
                raise Failure('asr_failed', '语音识别引擎失败，请检查模型下载、内存或后端配置后重试。' + exc.message[-400:])
            if not result['cues']:
                raise Failure('no_speech', '未检测到可识别的人声；可能是无声、纯音乐或人声过弱，请换一个文件')
            try:
                cues = validate_cues([Cue(**c) for c in result['cues']])
            except ValueError as exc:
                raise Failure('asr_failed', '识别引擎返回无效时间轴，未生成可下载字幕，请重试') from exc
            job.update(cues=[c.model_dump() for c in cues], source='ai', detected_language=result['language'])
            stage(job, 'completed', '字幕生成完成，可编辑并下载')
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling():
            raise
        job.update(status='cancelled', message='任务已取消', updated_at=time.time())
    except TimeoutError:
        job.update(status='failed', error_code='task_timeout', message='任务总处理时间超过限制，请重试较短文件', updated_at=time.time())
    except Exception as exc:
        logging.getLogger('subtitles').exception('Task %s failed', job['id'])
        job.update(status='failed', error_code=getattr(exc, 'code', 'processing_failed'), message=getattr(exc, 'message', '处理失败，请检查媒体文件后重试'), updated_at=time.time())
    finally:
        save_account_history(job)
        shutil.rmtree(job['dir'], ignore_errors=True)

async def consumer():
    while True:
        job = await queue.get()
        try:
            if job['cancel']:
                save_account_history(job)
                shutil.rmtree(job['dir'], ignore_errors=True)
            else: await process(job)
        finally: queue.task_done()

async def cleaner():
    while True:
        await asyncio.sleep(30)
        for key, job in list(jobs.items()):
            if job['status'] in TERMINAL and time.time() - job['updated_at'] > TTL:
                shutil.rmtree(job['dir'], ignore_errors=True)
                jobs.pop(key, None)

@asynccontextmanager
async def lifespan(app):
    with FileLock(str(DATA / '.instance.lock'), timeout=0):
        app.state.store = Store(Path(os.getenv('HISTORY_DB_PATH', str(DATA / 'accounts.sqlite3'))))
        # Single-process application owns this directory; remove interrupted work.
        for path in DATA.glob('job-*'): shutil.rmtree(path, ignore_errors=True)
        tasks = [asyncio.create_task(consumer()) for _ in range(CONCURRENCY)] + [asyncio.create_task(cleaner())]
        yield
        for job in jobs.values():
            job['cancel'] = True
            if job['proc'] and job['proc'].returncode is None: job['proc'].kill()
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

app = FastAPI(title='VoiceToSubtitles', lifespan=lifespan)

class UploadBodyLimit:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['path'] != '/api/jobs/upload':
            return await self.app(scope, receive, send)
        limit = MAX_MB * 1024**2 + 1024**2  # multipart header overhead
        headers = dict(scope.get('headers', []))
        try: length = int(headers.get(b'content-length', b'0'))
        except ValueError: length = 0
        if length > limit:
            response = Response(json.dumps({'detail': f'上传请求超过 {MAX_MB} MB 文件限制'}, ensure_ascii=False), status_code=413, media_type='application/json')
            return await response(scope, receive, send)
        total = 0
        async def limited_receive():
            nonlocal total
            message = await receive()
            total += len(message.get('body', b''))
            if total > limit:
                raise HTTPException(413, f'上传请求超过 {MAX_MB} MB 文件限制')
            return message
        await self.app(scope, limited_receive, send)

app.add_middleware(UploadBodyLimit)
app.include_router(accounts_router)


@app.middleware('http')
async def origin_guard(request: Request, call_next):
    # No cross-origin mutations of a local, unauthenticated application.
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin and origin != str(request.base_url).rstrip('/'):
        return Response('不允许跨站请求', status_code=403)
    response = await call_next(request)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response

@app.get('/api/config')
async def config():
    return dict(max_upload_mb=MAX_MB, max_duration_seconds=MAX_SECONDS, concurrency=CONCURRENCY, max_pending=MAX_PENDING, ttl_seconds=TTL, model=os.getenv('WHISPER_MODEL', 'base'))

@app.post('/api/jobs/link', status_code=202)
async def link(body: LinkInput, request: Request, response: Response):
    language_check(body.language)
    try: url = youtube_url(body.url)
    except ValueError as exc: raise HTTPException(422, str(exc))
    job = new_job(body.language, 'link', url, who=identity(request, response))
    queue.put_nowait(job)
    return public(job)

@app.post('/api/jobs/upload', status_code=202)
async def upload(request: Request, response: Response, file: UploadFile = File(...), language: str = Form('auto')):
    language_check(language)
    job = new_job(language, 'upload', who=identity(request, response))
    job['title'] = Path(file.filename or '上传文件').name[:200]
    size = 0
    try:
        with (job['dir'] / 'upload').open('wb') as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_MB * 1024**2: raise HTTPException(413, f'文件超过 {MAX_MB} MB 限制')
                out.write(chunk)
        if not size: raise HTTPException(422, '文件为空')
    except BaseException:
        shutil.rmtree(job['dir'], ignore_errors=True)
        jobs.pop(job['id'], None)
        raise
    finally: await file.close()
    queue.put_nowait(job)
    return public(job)

@app.get('/api/jobs/{job_id}')
async def status(job_id: str, request: Request): return public(get_job(job_id, request))

@app.post('/api/jobs/{job_id}/cancel')
async def cancel(job_id: str, request: Request):
    job = get_job(job_id, request)
    if job['status'] not in TERMINAL:
        job['cancel'] = True
        job.update(status='cancelled', message='任务已取消', updated_at=time.time())
        if job['proc'] and job['proc'].returncode is None: job['proc'].kill()
        save_account_history(job)
    return public(job)

@app.put('/api/jobs/{job_id}/cues')
async def edit(job_id: str, body: EditInput, request: Request):
    job = get_job(job_id, request)
    if job['status'] != 'completed': raise HTTPException(409, '任务尚未完成')
    try: validate_cues(body.cues)
    except ValueError as exc: raise HTTPException(422, str(exc))
    job.update(cues=[c.model_dump() for c in body.cues], updated_at=time.time())
    save_account_history(job)
    return public(job)

@app.get('/api/jobs/{job_id}/download/{kind}')
async def download(job_id: str, kind: str, request: Request):
    job = get_job(job_id, request)
    if kind not in ('srt', 'vtt', 'txt'): raise HTTPException(422, '格式无效')
    if job['status'] != 'completed': raise HTTPException(409, '任务尚未完成')
    content = export([Cue(**c) for c in job['cues']], kind)
    return Response(content.encode('utf-8'), media_type='text/vtt' if kind == 'vtt' else 'text/plain', headers={'Content-Disposition': f'attachment; filename="subtitles.{kind}"'})

DIST = ROOT / 'frontend' / 'dist'
if DIST.exists(): app.mount('/', StaticFiles(directory=DIST, html=True), name='frontend')
