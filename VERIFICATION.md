# 验证记录

日期：2026-11-03（Pacific/Saipan）。最终采用本机 Apple M1 CPU int8，Whisper base；无云端识别 API。

## 真实测试

- **用户提供的视频完整处理：通过。** `https://www.youtube.com/watch?v=OZKUJDEMX1w` 本次元数据返回无可用字幕轨道，成功获取约 612 秒音频，自动检测为中文，真实本地 ASR 生成 **180 条字幕**，SRT / VTT / TXT 下载均通过，时间轴有效。此结果不承诺视频今后始终可访问。

- **上传中文语音 → 本地 Whisper → 字幕下载：通过。** 音源为 macOS Tingting 合成语音，不是人工录音；ASR 是真实模型推理，不是 mock。原始结果有“识别 / 时别”等错字，未宣称逐字准确。
- **中文编码：通过。** 实际下载 SRT、VTT、TXT，全部按 UTF-8 解码并与实际识别文本比对。
- **时间轴：通过。** 校验有限、非负、开始小于结束，字幕开始时间顺序正确。毫秒导出不产生零长度段。
- **编辑下载：通过。** API 编辑后检查三个真实下载文件均包含修改后的中文；浏览器实际上传、识别、编辑、点击 TXT 下载，主机下载文件内容也已核对。
- **链接失败后上传：通过。** 真实请求不可用 YouTube ID，收到 `caption_unknown`，随后独立上传中文文件，识别成功。
- **无声文件：通过。** FFmpeg 生成 4 秒无声 WAV，真实 VAD / Whisper 返回 `no_speech`，不生成假字幕。
- **超限：通过。** 实际 HTTP 提交超过当前 200 MiB 限制的文件，返回 413；核心测试还以临时 1 MiB 配置验证文件本身和无 Content-Length 的流式请求限制。
- **真实文件类型：通过。** 将纯文本伪装为 WAV 上传，ffprobe 拒绝，返回 `invalid_media`。
- **时长 / 无音轨：通过。** 测试真实调用 FFmpeg 生成媒体，以测试专用时长限制拒绝超时长 WAV；无音轨 MP4 返回 `no_audio`。
- **浏览器：通过。** 真实浏览器中检查桌面页面和 390 px 手机宽度布局，上传、状态轮询、编辑、下载控件可用；无 Android 原生应用测试。
- **取消和重试：通过。** 在真实 ASR 阶段发出取消请求，状态保持 `cancelled`；随后重新上传同一文件，以自动语言模式真实识别完成并检测为中文。见 `tests/artifacts/cancel-retry-report.json`。
- **前端构建：通过。** 最终 `npm run build` 成功。

真实脚本与产物：

- `tests/real_acceptance.py` → `tests/artifacts/real-report.json`、`original.*`、`edited.*`
- `tests/real_edges.py` → `tests/artifacts/edge-report.json`
- `tests/real_example.py` → `tests/artifacts/example-report.json`、完成时生成 `example.*`
- `tests/artifacts/browser-result.png` / `mobile-result.png` 为实际浏览器截图。

`tests/artifacts` 是本地测试产物，默认不纳入源码包或 Git，避免提交音频和视频内容。实际结果以对应 JSON 为准，不将“正在处理”计为成功。

## 核心测试与模拟范围

最终 `python -m pytest tests/test_core.py -q`：**29 项通过**。

人工字幕、平台自动字幕、字幕轨道下载失败、部分提取警告、ASR 引擎异常、队列满和取消的分支测试使用明确模拟响应或等待。它们用于校验程序行为，**不等于真实平台字幕提取成功或真实云端识别测试**。URL 校验、编码导出、字幕契约、文件大小、流式请求大小及 FFmpeg 媒体验证执行真实实现。

## 未验证和限制

- 未使用真人录音、大量噪声、多说话人、超长一小时样本或并发压力测试；没有量化识别准确率。
- 本次没有对真实人工 / 自动字幕轨道分别完成在线提取验收；这两个成功分支为模拟覆盖。YouTube 的实际访问情况随网络和平台变化，不能保证每次可用。
- 未验证 NVIDIA CUDA、Windows / Linux、云端 ASR；已真实验证的是本机 macOS CPU 配置。
- 浏览器完成真实 TXT 下载；三个格式的下载和编辑内容均另有真实 HTTP 验证。
- 单实例单 worker，运行中的任务在重启后丢失；已完成的游客字幕保存在 localStorage，账户历史保存在 SQLite，重启仍可打开。媒体任务结束删除，不能恢复原媒体重试。
- 取消是终止运行中的媒体 / ASR 子进程，非暂停恢复；前端不会伪造成功。
- Whisper 可能错字、漏词、重复或产生幻觉。没有 OCR、翻译、说话人分离、逐字对齐和音视频播放器校对。
- 有关 YouTube 测试视频的字幕为空，仅解释为本次提取返回列表没有可用轨道；提取有警告或错误则标记无法判断。


## 进度与历史功能新增验证

- 后端测试：61 项通过（原核心测试、真实存储/隔离整合、进度契约、OAuth 安全分支）。
- 前端 Node 测试：11 项通过，覆盖 localStorage 恢复、编辑草稿、存储配额/损坏/禁用、账户字幕拒绝写入游客存储，以及离线导出。
- 真实 CPU 识别进度：通过。约90秒中文合成语音的实际模型推理中，观察到音频时间轴覆盖率 0%、33.1%、38.2%、91.5% 后完成，百分比与实际返回时间戳相符。缓存模型没有错误显示为正在下载。报告：`tests/artifacts/progress-report-v2.json`。
- 会话隔离：通过。同一个真实任务在其游客 Cookie 下可查询，不带 Cookie 的另一客户端返回404。
- 账户数据库重开、跨用户访问、编辑/下载/删除，以及删除后后台不得重新保存：真实 SQLite 操作通过；测试身份和 Google token 交换明确为模拟。
- Google 登录暂未进行真实授权验收：本机尚无站点自己的 Google OAuth Client ID / Secret。缺配置时前端明确显示暂不可用，后端拒绝启动登录，不创建伪造账户。
- 冷缓存模型下载阶段的文件计数使用官方下载库真实回调，当前自动测试模拟该回调，未清空已下载模型缓存来声称重新下载实测。

浏览器实际验证：编辑中文后立即刷新仍恢复；重启后端使原任务失效后，仍从本机历史成功下载含修改内容的 SRT。截图 `tests/artifacts/history-browser-v2.png`，报告 `tests/artifacts/history-browser-v2.json`。
