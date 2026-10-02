// 预设样例配置：输入区左栏「预设样例」列表的数据源。
//
// 这个文件不是被打包进 bundle 的模块，而是由前端按 URL 动态 import 的 ES 模块
// （见 `frontend/src/presets.ts` 的 loadPresets），所以改它不用改任何代码。
// dev 下刷新页面即生效；若在用 `frontend/dist/` 的构建产物，要重新 build，
// 或直接改 dist 里那份同名副本（它是 build 时从 public/ 拷过去的）。
//
// 每个条目三个字段：
//   kind —— 按钮上的短标签（标题），例如 'PDF'
//   desc —— 一句话说明，写清体积与看点
//   url  —— 点击后填入输入框并复制的地址，必须是 http/https
//
// 写法约定：kind 与 url 必填，desc 可以留空串（表示只显示标题）。字段名写错、类型不对、
// url 不是 http(s) 的条目会被跳过，界面在列表下方列出原因，控制台里也有同样的明细——不静默吞掉。
// 条目顺序就是界面上的顺序：想调整就先后来挪。
//
// 来源：对外可访问的测试资源，加一张文字密集的赛事海报
// （专门用来走「判型 → Paddle OCR」那一支）。
// 2026-09-12 逐个探活并记录了体积，挑选标准是「点一下能在几十秒内出结果」。
//
// 刻意排除的两类：
//  - CDN 上那两个文档（109 MB 的 PDF、71 MB 的 PPTX）——能跑通，但当预设等太久；
//  - 文件名带真实人名的那份简历 PDF——体积不大也不该放进仓库。

const OSS = 'https://insnail-asr-nls.oss-cn-shenzhen.aliyuncs.com'

export default [
  {
    kind: 'PDF',
    desc: '107 KB · 文档，两条管线都能出正文',
    url: `${OSS}/multimodal2text/tests/5986627c-48d5-11ef-9304-9cc2c4428788/test.pdf`,
  },
  {
    kind: 'DOCX',
    desc: '15 KB · Word 文档，体积小、正文短',
    url: `${OSS}/multimodal2text/tests/2b62ce44-4dc6-11ef-8736-9cc2c4428788/test1.docx`,
  },
  {
    kind: 'PPTX',
    desc: '2.4 MB · 演示文稿',
    url: `${OSS}/multimodal2text/tests/d8a7288c-4d5a-11ef-a74e-9cc2c4428788/test.pptx`,
  },
  {
    kind: 'XLSX',
    desc: '13 KB · 表格',
    url: `${OSS}/multimodal2text/tests/6f738a5e-4d5a-11ef-9639-9cc2c4428788/test.xlsx`,
  },
  {
    kind: 'CSV',
    desc: '2 KB · 纯文本表格，秒出',
    url: `${OSS}/multimodal2text/tests/599afa84-48d5-11ef-9304-9cc2c4428788/test.csv`,
  },
  {
    kind: 'ODT',
    desc: '1.0 MB · ODF 文本文档',
    url: `${OSS}/multimodal2text/tests/59a6d9d0-48d5-11ef-9304-9cc2c4428788/test.odt`,
  },
  {
    kind: '网页',
    desc: 'HTML 文章页',
    url: `${OSS}/file/36kr_article.html`,
  },
  {
    kind: 'JPG',
    desc: '208 KB · 赛事海报，文字密集；模型增强判型后走本机 Paddle OCR',
    url: 'https://aike-12times-wx.damselfish.cn/ap/m/in/101189/moment/20260513/bbfde469-4ab6-4456-af70-7c5da22f7b03.jpg',
  },
  {
    kind: 'JPG',
    desc: '191 KB · 位图，纯代码只给结构信息',
    url: `${OSS}/multimodal2text/tests/a2ddb8a4-48ca-11ef-8706-9cc2c4428788/test.jpg`,
  },
  {
    kind: 'PNG',
    desc: '5.1 MB · 位图，大图',
    url: `${OSS}/multimodal2text/tests/a33f54c4-48ca-11ef-8706-9cc2c4428788/test.png`,
  },
  {
    kind: 'GIF',
    desc: '951 KB · 动图，会报帧数',
    url: `${OSS}/multimodal2text/tests/a33237da-48ca-11ef-8706-9cc2c4428788/test.gif`,
  },
  {
    kind: 'MP4 视频',
    desc: '308 KB · 纯代码给音轨参数，模型增强抽音轨后转写',
    url: `${OSS}/multimodal2text/tests/a397f9ee-48ca-11ef-8706-9cc2c4428788/test.mp4`,
  },
  {
    kind: 'MP3 音频',
    desc: '74 KB · 3 秒音频，模型增强交本机 Qwen3-ASR 转写',
    url: `${OSS}/multimodal2text/tests/a376ba40-48ca-11ef-8706-9cc2c4428788/test.mp3`,
  },
  {
    kind: '音频2',
    desc: '30s+ 音频',
    url: `https://it-snail-prd.oss-cn-shenzhen.aliyuncs.com/enterprise-wechat-context/enterprise/voice/4df94aa054ead04cada4bd283ec3a271.mp3`,
  },
  {
    kind: '无扩展名',
    desc: '1.0 MB · URL 没有后缀，靠内容头识别',
    url: 'https://wework.qpic.cn/wwpic3az/wwwx_8e284d368405804350eace5c4984edaa/0',
  },
]
