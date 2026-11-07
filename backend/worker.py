"""Isolated, cancellable media/ASR process. stdout is JSON only."""
import json
import os
import socket
import sys
from urllib.parse import urlparse
from backend.engine import FasterWhisperEngine
from backend.progress import ProgressWriter

ALLOWED = ('youtube.com', 'youtu.be', 'googlevideo.com', 'ytimg.com', 'google.com', 'ggpht.com', 'youtube-nocookie.com')

def secure_network():
    # Guard every yt-dlp request and redirect, and pin actual socket connections
    # to public addresses. No cookies, proxy, credentials or generic extractor.
    import ipaddress
    import urllib.request
    from yt_dlp.networking._urllib import RedirectHandler
    from yt_dlp.networking.common import RequestDirector
    def check(url):
        p = urlparse(url)
        host = p.hostname or ''
        if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443) or not any(host == d or host.endswith('.' + d) for d in ALLOWED):
            raise ValueError('拒绝非允许的媒体来源或重定向')
    original_send = RequestDirector.send
    def send(self, request):
        check(request.url)
        return original_send(self, request)
    RequestDirector.send = send
    original_redirect = RedirectHandler.redirect_request
    def redirect(self, req, fp, code, msg, headers, newurl):
        check(newurl)
        return original_redirect(self, req, fp, code, msg, headers, newurl)
    RedirectHandler.redirect_request = redirect
    original_connect = socket.socket.connect
    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            for result in socket.getaddrinfo(address[0], address[1], self.family, socket.SOCK_STREAM):
                if not ipaddress.ip_address(result[4][0]).is_global:
                    raise ValueError('拒绝访问内网地址')
            # use resolved IP to avoid a second DNS lookup / rebinding
            address = (result[4][0], address[1])
        return original_connect(self, address)
    socket.socket.connect = connect

class QuietLogger:
    def __init__(self): self.warnings = []
    def debug(self, msg): pass
    def warning(self, msg):
        self.warnings.append(str(msg)[-500:])
        print(msg, file=sys.stderr)
    def error(self, msg): print(msg, file=sys.stderr)

def main():
    mode, path, language = sys.argv[1:4]
    if mode == 'asr':
        result = FasterWhisperEngine().transcribe(path, None if language == 'auto' else language, ProgressWriter(sys.argv[4]))
    else:
        secure_network()
        from yt_dlp import YoutubeDL
        def size_guard(progress):
            if progress.get('downloaded_bytes', 0) > int(os.getenv('MAX_UPLOAD_MB', '200')) * 1024**2:
                raise ValueError('媒体下载超过文件大小限制')
        logger = QuietLogger()
        opts = {'format': 'bestaudio/best', 'progress_hooks': [size_guard], 'quiet': True, 'logger': logger, 'noplaylist': True, 'socket_timeout': 20, 'retries': 1, 'extractor_retries': 1, 'proxy': '', 'cachedir': False, 'remote_components': set(), 'js_runtimes': {'node': {}}, 'allowed_extractors': ['youtube'], 'outtmpl': os.path.join(sys.argv[4], 'media.%(ext)s'), 'max_filesize': int(os.getenv('MAX_UPLOAD_MB', '200')) * 1024 * 1024}
        with YoutubeDL(opts) as ydl:
            # Keep one HTTP backend so all redirect paths use the guard above.
            director = ydl._request_director
            for key in list(director.handlers):
                if key != 'Urllib':
                    del director.handlers[key]
            if mode == 'metadata':
                info = ydl.extract_info(path, download=False)
                result = {k: info.get(k) for k in ('title', 'duration', 'subtitles', 'automatic_captions', 'language', 'is_live')}
                result['warnings'] = logger.warnings
            elif mode == 'caption':
                req = ydl.urlopen(path)
                body = req.read(8 * 1024 * 1024 + 1)
                if len(body) > 8 * 1024 * 1024:
                    raise ValueError('字幕文件超过限制')
                data = json.loads(body)
                cues = []
                for e in data.get('events', []):
                    text = ''.join(s.get('utf8', '') for s in e.get('segs', [])).strip()
                    start = e.get('tStartMs', 0) / 1000
                    end = start + e.get('dDurationMs', 0) / 1000
                    if text and end > start:
                        cues.append({'start': start, 'end': end, 'text': text})
                result = {'cues': sorted(cues, key=lambda c: c['start'])}
            else:
                ydl.extract_info(path, download=True)
                result = {'ok': True}
    print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    main()
