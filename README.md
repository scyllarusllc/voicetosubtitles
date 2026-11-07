# VoiceToSubtitles — Open-source AI Subtitle Generator

一个实际可运行的 React + FastAPI 网站：粘贴 YouTube 视频链接，或上传视频 / 音频，提取已有字幕；没有可用字幕时以 faster-whisper 本地识别，编辑后下载 UTF-8 SRT、VTT、TXT。

默认中文界面。游客无需注册即可使用；可选 Google 登录会把字幕保存到账户历史。语音识别无需 API 密钥。只处理你拥有或已获授权使用的内容；不绕过登录、付费、DRM 或访问限制。画面内嵌字幕不做 OCR，语音识别结果不保证与画面字幕逐字相同。

## 已检查的运行环境

本次开发机器为 Apple M1、8 GB、macOS arm64，有 Metal GPU，但 faster-whisper 的 GPU 路径使用 CUDA，所以本项目实际采用 **CPU + int8**。已验证 Python 3.11.16、Node 26.8.1、FFmpeg 9.0.2。系统默认 Python 3.14 未用于项目，以减少原生依赖兼容问题。

实施时核对了官方文档：[faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[yt-dlp](https://github.com/yt-dlp/yt-dlp#embedding-yt-dlp)、[FastAPI 文件上传](https://fastapi.tiangolo.com/tutorial/request-files/)、[React](https://react.dev/learn/creating-a-react-app)、[Vite](https://vite.dev/guide/)。Python 的实际安装版本在 `requirements.lock`，前端在 `frontend/package-lock.json`。这里选用 Vite 7 的兼容版本，不依赖最新主版本功能。推荐 Python 3.11、Node 22.12+；Vite 要求 Node 20.19+ / 22.12+。

## 安装和启动

先准备 Python 3.11、Node.js、FFmpeg（`ffmpeg` 和 `ffprobe` 都必须在 PATH）。macOS 可通过 Homebrew 安装，Ubuntu 可用系统包管理器安装 FFmpeg。这些命令由使用者按需执行。

```bash
git clone https://github.com/scyllarusllc/voicetosubtitles.git
cd voicetosubtitles
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
cp .env.example .env
cd frontend
npm ci
npm run build
cd ..
./scripts/start.sh
```

若已有 `uv`，也可用 `uv venv --python 3.11 .venv` 和 `uv pip install --python .venv/bin/python -r requirements.lock` 替代 venv / pip 步骤。

打开 **http://127.0.0.1:8765**；API 文档位于 **http://127.0.0.1:8765/docs**。项目放到其他目录时，将上方 `cd` 替换为实际目录。Windows 使用 `.venv\Scripts\python.exe`，用下面命令代替 shell 启动脚本：

```bash
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765 --workers 1
```

首次识别自动从 Hugging Face 下载公开 Whisper 模型，需联网且可能较慢；下载完成后模型缓存于 Hugging Face 默认缓存目录，上传识别可离线使用。不需要 Hugging Face token；匿名请求可能有速率限制。也可将 `WHISPER_MODEL` 指向已下载的 CTranslate2 Whisper 模型目录。

开发前端时，先在一个终端启动后端，再另开终端：

```bash
cd frontend
npm run dev
```

访问 Vite 打印的地址（一般为 http://127.0.0.1:5173）。`/api` 代理到 8765。构建后的前端由 FastAPI 同源提供，无需单独运行 Node 服务。

## 使用

1. 选择“粘贴视频链接”或“上传文件”，选择自动检测 / 中文 / 英文 / 西班牙语。
2. 点击“获取字幕”。提交后立即返回任务 ID，浏览器每 1.5 秒查询；不显示虚构百分比。
3. 根据真实阶段显示等待、获取媒体、提取音频、识别、完成、失败，也支持取消。
4. 完成后查看字幕来源和时间轴，直接修改文字；点击“保存修改”，或点击下载自动先保存。
5. 下载 SRT / VTT / TXT。请求失败时显示错误，不会把旧字幕当成新任务成功结果。
6. 链接处理失败时可点“改为上传文件”。重试通过重新提交同一链接 / 浏览器仍选择的文件完成；上传文件在后端任务结束后已删除，刷新页面后需重新选文件。

支持 MP4、MOV、MP3、M4A、WAV、WebM、Ogg、FLAC、AAC、AIFF 等 FFmpeg 可识别的常见容器。文件名扩展名和浏览器 MIME 不作为信任依据，真正以 ffprobe 探测的容器、音轨、时长为准。文件损坏或容器无法探测时明确失败。

## 字幕判定与错误分类

| 情况 | 任务中的标记 / 行为 |
| --- | --- |
| 人工字幕可用 | `caption_check=found`、`source=manual`，直接提取 |
| 原始平台自动字幕可用 | `source=automatic`，直接提取；不将自动翻译轨道冒充原始字幕 |
| 元数据成功且无所选语言可用轨道 | `unavailable_for_language`，转入音频识别 |
| 平台 / 网络 / 提取失败，或空列表伴随提取警告 | `unknown`，绝不声明“视频没有字幕” |
| 发现字幕但下载或解析失败 | `download_failed`，保留说明并尝试音频 |
| 音频获取失败 | `audio_unavailable`，提示上传文件 |
| 实际内容无效 | `invalid_media` |
| 文件无音轨 | `no_audio` |
| VAD / ASR 未返回可识别人声 | `no_speech`；无声、纯音乐、人声太弱均有可能 |
| ASR 模型 / 服务运行失败 | `asr_failed`，可重试；不当作无声 |
| 超时、时长、文件大小、队列限制 | 独立明确错误，不生成伪造字幕 |

自动语言下优先选平台原始语言的人工字幕，再考虑原始自动字幕。手动选择语言表示“优先提取该语言字幕；否则以该语言识别音频”，**不表示翻译**。字幕提取使用 JSON3 轨道；有轨道但格式不可用也会明确说明，而不是断言视频没有任何字幕。

## 配置

复制 `.env.example` 到 `.env`，改动后重启后端：

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `WHISPER_MODEL` | `base` | 多语言模型，可改 `tiny` / `small` 或本地模型路径 |
| `WHISPER_DEVICE` | `cpu` | 有兼容 NVIDIA 环境时可选 `cuda` |
| `WHISPER_COMPUTE_TYPE` | `int8` | CPU 低内存配置；CUDA 按官方兼容表选择 |
| `WHISPER_THREADS` | `4` | 单识别进程的 CPU 线程数 |
| `MAX_UPLOAD_MB` | `200` | 上传和链接媒体大小上限，MiB 计量 |
| `MAX_DURATION_SECONDS` | `3600` | 最大时长；未知时长和直播拒绝处理 |
| `MAX_CONCURRENT_JOBS` | `1` | 同时运行任务数；8 GB 机器建议保持 1 |
| `MAX_PENDING_JOBS` | `8` | 活动任务之外的等候容量 |
| `JOB_TTL_SECONDS` | `3600` | 完成 / 失败后的结果保留时间，约每 30 秒回收 |
| `TASK_TIMEOUT_SECONDS` | `7200` | 单任务处理上限（不含排队时间） |
| `DATA_DIR` | 项目下 `.data` | 专用临时目录，**不可与其他实例或其他文件共用** |

界面从 `/api/config` 展示真实限制；首次下载模型包含在识别阶段。想提升中文质量可选择 `small`，但处理时间、内存和模型下载体积随之增加。识别引擎独立于 Web 层，见 `backend/engine.py` 的 `Engine` 协议和进度回调；未来添加云端实现时，只需接入同样的时间轴返回契约并在 worker 中选择实现。云端密钥只应读取后端环境变量，不得放前端、日志或仓库。

## 后台任务与安全边界

- asyncio 本地队列 + 可取消子进程。**必须单实例、单 Uvicorn worker**；不要用 `--workers 2`。运行中的队列与临时任务状态在内存中，重启不恢复未完成任务。游客已完成字幕保存在浏览器 localStorage；登录用户的已完成字幕保存在 SQLite 账户历史，重启仍可打开。专用目录锁会拒绝同时使用同一临时目录的另一实例，测试目录也独立隔离。第一版不是持久化分布式队列。
- 每个任务独立临时目录，完成、失败、取消后删除媒体和工具日志；过期回收临时任务的内存结果，不删除浏览器本地历史或账户历史。下次启动清理 `DATA_DIR/job-*` 中断残留。模型缓存保留。
- 限制请求体大小（含无 Content-Length 的流式请求）、文件大小、时长、活动任务数和运行时间；下载媒体期间也检查已下载字节。multipart 允许额外 1 MiB 请求开销，但单文件仍严格按配置上限验证。
- 只接受合法 HTTPS YouTube 单视频 URL，规范化为固定 `youtube.com/watch?v=...`；拒绝用户凭证、非标准端口、任意域名和播放列表。
- yt-dlp 仅启用 YouTube 提取器和受控 urllib 后端；每个请求与重定向检查媒体域名白名单和 HTTPS，底层连接拒绝私网、回环、链路本地等非公网 IP，并将连接绑定到已检查的地址。禁止代理、浏览器 Cookie、远程 JS 组件和用户工具参数。
- FFmpeg / ffprobe 使用参数数组执行，不经 shell；仅允许 `file,pipe` 协议，并验证容器与音轨。不执行用户文件。
- 默认仅监听 127.0.0.1，写请求拒绝不同 Origin；游客和登录用户均通过 HttpOnly 会话 Cookie 隔离任务；账户历史按 Google 用户 ID 隔离，单独知道任务 ID 不能读写。当前仍默认本地运行。公网部署还需 HTTPS、反向代理与请求级配额，运行队列仍为单实例。
- 字幕编辑只修改文字；后端仍校验有限时间值、开始早于结束、开始时间顺序、字幕数量和长度。导出不产生零毫秒时长。

## 测试

核心测试（部分分支明确使用模拟平台响应；文件校验实际调用 FFmpeg）：

```bash
.venv/bin/python -m pytest tests/test_core.py -q
```

真实端到端测试，先启动 8765 后端，准备一段中文人声 WAV 和无声 WAV。开发机的可复现样本命令（macOS，其他系统自行提供语音文件）：

```bash
mkdir -p tests/artifacts
say -v Tingting '你好，欢迎使用人工智能字幕工具。今天我们测试中文语音识别。识别完成以后，可以编辑文字，并下载字幕文件。' -o tests/artifacts/chinese.aiff
ffmpeg -i tests/artifacts/chinese.aiff -y tests/artifacts/chinese.wav
ffmpeg -f lavfi -i anullsrc=r=16000:cl=mono -t 4 -y tests/artifacts/silence.wav
.venv/bin/python tests/real_acceptance.py tests/artifacts/chinese.wav
.venv/bin/python tests/real_edges.py
# 可选：对用户给出的在线视频做真实完整处理（访问情况不保证）
.venv/bin/python tests/real_example.py
.venv/bin/python tests/real_cancel_retry.py
```

`real_acceptance.py` 真实访问不可用 YouTube 视频，随后上传真实音频、运行真实模型、校验时间顺序、导出编码和编辑结果，再识别无声文件。它不 mock Whisper。语音样本由 TTS 生成，**音源是合成语音，识别过程是真实推理**；不将其说成真人录音测试。`real_edges.py` 实际发送超过当前配置的文件，并上传伪装 WAV。报告和下载文件写入 `tests/artifacts`（忽略提交）。测试使用不可用视频 ID，该 ID 的可访问性若变化，失败恢复部分需换成当前不可用 ID。

更多真实结果、模拟范围和未验证项见 `VERIFICATION.md`。

## 常见问题

- **“No Subtitles found”是否证明没字幕？** 不是。网络、平台限制、工具兼容性都可能造成无法获取；本项目分别显示 `unknown` 和已返回列表中无可用轨道。
- **YouTube 为什么失败？** 可能是视频删除、地区或平台访问限制、网络错误、工具尚未适配或缺少平台要求的执行环境。项目不会绕过限制，直接改用本地文件。
- **为什么识别阶段很慢？** 首次下载模型；随后 CPU 识别也需时间。默认不显示猜测百分比。`tiny` 更快但精度较低，`small` 更准确但更慢。
- **模型下载失败？** 检查后端网络和模型仓库可达性，或指定已缓存本地模型路径。失败显示 `asr_failed`，不伪装成“没有人声”。
- **中文有错字？** Whisper 输出可能误识别、漏词或幻觉；界面可编辑文字。第一版不保证专业字幕质量、说话人区分或逐字对齐。
- **取消后能直接重试吗？** 浏览器仍持有原 File 对象时可以重新提交；后端不会长期保存原媒体，刷新后需重新选择。
- **关闭终端后网站打不开？** 本地服务需保持运行；重新执行 `./scripts/start.sh`。要更换端口可运行 `PORT=8766 ./scripts/start.sh`；开发代理和验收脚本默认仍指向 8765。
- **字体需要联网吗？** 使用系统中文字体，无需第三方字体资源。

## 代码结构

```text
backend/app.py       API、队列、取消、清理、媒体验证
backend/worker.py    受控平台获取 / 独立 ASR 子进程
backend/engine.py    可替换识别引擎协议和 faster-whisper 实现
backend/core.py      URL、字幕契约、时间轴和导出
backend/progress.py  子进程真实进度快照
backend/accounts.py  Google OIDC、会话、SQLite 账户历史
frontend/src/        React 界面、集中中文文案、响应式样式
scripts/start.sh     本地单进程启动
.env.example        环境配置模板
requirements.lock   本机验证的 Python 依赖版本
```


## 真实进度与历史记录（新增）

模型处理会显示检查缓存、首次下载、加载模型、检测人声/语言、语音识别、整理字幕。已有缓存时不会提示正在下载。识别进度来自模型实际返回的音频时间戳：显示已识别到的时间、总时长、字幕条数和时间轴覆盖百分比。百分比不是速度预测或剩余时间估计；无可测百分比的阶段仅显示活动状态与真实用时。模型下载可显示已完成文件数，不把大小不同的文件数冒充字节进度。

游客完成的字幕和文字编辑稿自动保存到当前浏览器 localStorage。最多 50 条、约 4 MiB；达到限制会提示下载并清理部分历史，不自动删除旧记录。刷新可恢复最近字幕；即使服务重启、原任务已过期，仍可直接编辑和下载本机历史。只保存文字、时间轴和基本信息，不保存原媒体。浏览器禁止存储时会提示，仍允许下载当前结果；清除浏览器数据或使用其他设备不会带走游客历史。

登录用户的字幕不会写入游客 localStorage，而保存在后端 SQLite。历史列表显示最近 100 条，可重新打开、编辑、下载、删除；账户间隔离，注销撤销会话。游客历史与账户历史不会自动混合或上传。原始音视频仍在任务结束后清理。

### 配置 Google 登录

1. 在 Google Cloud Console 为本产品创建 OAuth 客户端，类型选择“Web 应用”，配置同意屏幕；测试模式需要添加允许登录的测试用户。
2. 授权重定向 URI 精确填写 `http://127.0.0.1:8765/api/auth/google/callback`。正式站使用自己的 HTTPS 域名和相同回调路径。
3. 将客户端凭据放进后端 `.env`，不要发送到聊天中，不要写入前端：

```dotenv
PUBLIC_BASE_URL=http://127.0.0.1:8765
GOOGLE_CLIENT_ID=你的Web客户端ID
GOOGLE_CLIENT_SECRET=你的客户端Secret
GOOGLE_REDIRECT_URI=http://127.0.0.1:8765/api/auth/google/callback
SESSION_TTL_SECONDS=2592000
# HISTORY_DB_PATH=/absolute/private/path/accounts.sqlite3
```

4. 重启服务后，点击“使用 Google 登录”。未配置时按钮显示“Google 登录暂不可用”，游客功能正常。

登录实现采用 Google OIDC 服务端授权码、PKCE、一次性 state、nonce 和官方 google-auth ID Token 验签；仅请求 `openid email profile`，不申请 Drive、YouTube 或邮件访问。Google access/refresh token 不落库，浏览器只保存 HttpOnly 会话 Cookie，数据库只存会话 token 哈希。正式 HTTPS 环境自动使用 Secure Cookie。参考 [Google 官方 OIDC 文档](https://developers.google.com/identity/openid-connect/openid-connect)。

SQLite 默认位于 `.data/accounts.sqlite3`，不要在清理媒体时删除整个 `.data`。它包含账户信息与字幕历史，应放在私有持久目录并做好备份；任务清理仅处理 `job-*` 子目录。

新增验证命令：

```bash
.venv/bin/python -m pytest tests/test_core.py tests/test_accounts.py tests/test_progress.py tests/test_job_accounts.py -q
cd frontend
npm test
npm run build
cd ..
# 实际识别进度：传入至少60秒的人声文件，base模型应已缓存
.venv/bin/python tests/real_progress.py tests/artifacts/progress-speech.wav
```

真实 Google 登录需要站点自己的凭据与用户完成授权；当前本机未配置这些凭据，因此只验证了模拟 Google 交换下的回调安全、真实 SQLite 持久化和账户隔离，没有把模拟登录描述为真实 Google 授权成功。

## License

[MIT](LICENSE) © 2026 scyllarusllc.
