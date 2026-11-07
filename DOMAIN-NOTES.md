# 域名与 GEO / AI 搜索建议

当前项目名称已确定为 **VoiceToSubtitles**，仓库名为 `voicetosubtitles`，拟使用域名 `voicetosubtitles.com`。2026-11-08 Verisign RDAP 查询未发现注册记录；尚未购买，最终可用性以注册商为准。下文为早期候选研究，保留作参考。

早期命名建议：**GetVideoSubtitles.com**，品牌写作 **Get Video Subtitles**。本次只做研究，没有购买域名或更改网站域名。

选择原因：

- 用户容易直接理解它能做什么；Subtitle 比 CC 缩写更清楚。
- Video 让主题明确，避免把 captions 理解为社交媒体帖文文案。
- 不限 YouTube，也适合当前已经支持的本地音视频上传、字幕生成、编辑和下载。
- Get 可以涵盖提取已有字幕和通过音频生成字幕。
- 缺点是名字较长、品牌独特性一般；如果未来做大型产品，可考虑更短的独立品牌。

2026-11-03 查询 .com 注册局 Verisign RDAP 时，以下三个候选均返回404（未找到注册记录）。这不是注册商库存或价格保证，购买时须再次确认：

| 候选 | 建议用途 | 主要取舍 |
| --- | --- | --- |
| GetVideoSubtitles.com | 首选，面向字幕下载与生成的工具 | 意图直接，名字较长 |
| GetVideoCaptions.com | 更强调英文用户的 captions / closed captions | 直观，但要在页面说明也提供 transcript |
| SubtitlePort.com | 更偏独立品牌、未来功能扩展 | 较短，首次看到时不如首选直接 |

注册局查询：[GetVideoSubtitles](https://rdap.verisign.com/com/v1/domain/getvideosubtitles.com)、[GetVideoCaptions](https://rdap.verisign.com/com/v1/domain/getvideocaptions.com)、[SubtitlePort](https://rdap.verisign.com/com/v1/domain/subtitleport.com)。

CaptionFetch.com 已是同类产品，未选用。[产品官方介绍](https://captionfetch.com/ja/about)

## 域名和 GEO 的实际关系

Google 会把域名中的词作为众多相关性信号之一，同时使用 exact-match domain 系统避免给整句关键词域名过多权重。因此，不建议为了 `get cc from youtube video` 这样的整句查询注册很长的域名，也不承诺域名含关键词就能排名。[Google 排名系统说明](https://developers.google.com/search/docs/appearance/ranking-systems-guide#exact-match-domain-system)

Google 对 AI Overviews / AI Mode 的官方建议仍以可抓取、可索引、清晰可靠的内容为基础，没有必须添加的特殊 AI 文件或神奇结构化标记。命名应服务可理解性和记忆，而不是假设 `.ai` 后缀会自动增加 AI 引用。[Google AI 搜索指南](https://developers.google.com/search/docs/appearance/ai-features)

## 适合本产品的内容与页面

这只是下一步建议，当前代码尚未加入这些公开 SEO 页面。现有 localhost 地址不会被公网搜索引擎索引。

| 页面 | 对应用户意图 | 应有的实际内容 |
| --- | --- | --- |
| `/en/youtube-subtitle-downloader` | download YouTube subtitles / get CC from YouTube | 可用工具、提取步骤、人工与自动字幕区别、SRT/VTT/TXT 示例 |
| `/en/youtube-transcript` | YouTube transcript / video to text | 带时间轴字幕与纯文字记录的差异，复制和 TXT 下载 |
| `/en/ai-subtitle-generator` | generate subtitles from video / audio | 上传入口、语言和时长限制、真实识别示例 |
| `/en/help/no-subtitles-found` | no subtitles found / transcript unavailable | 区分无轨道、访问限制与工具错误，给出上传后识别的可行路径 |

“CC downloader”不必再造一个近乎重复的薄页面，放在字幕下载页的标题层级与 FAQ 中即可。英文工具页和中文工具界面可并存，使用明确语言路径、canonical、hreflang 与互相链接；不要仅机械翻译大量低价值页面。

建议的英文标题：`YouTube Subtitle Downloader & AI Generator | Get Video Subtitles`。

主标题可以是 `YouTube Subtitle Downloader`，说明文字写：`Download existing captions, or generate subtitles from your audio and video files.` 页面如实说明链接获取失败时需要上传，而不是宣称所有 YouTube 视频都能访问。

可被检索和引用的优势内容应来自已经做过的真实工作：

1. 先用简短段落直接回答“怎么下载”“没有字幕怎么办”“SRT与VTT如何选择”，再提供工具与详细步骤。
2. 使用经授权的测试音频，展示实际字幕、错字修正、编码和时间轴校验，并标明测试环境及日期。
3. 把功能、失败原因、限制、隐私处理和运营主体讲清楚；不要伪造评分、用户数量或成功率。
4. 公共说明页采用静态生成或服务端渲染，让关键信息直接出现在 HTML 中；登录历史与私人字幕保持私有，不拿用户内容做公开 SEO 页面。
5. 部署公网 HTTPS 后再配置 robots、sitemap、Search Console 和 Bing Webmaster Tools，观察真实检索、引用与转化，不承诺一定出现 AI 引用。
