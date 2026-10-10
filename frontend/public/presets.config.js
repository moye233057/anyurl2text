// 预设样例配置：输入区左栏「预设样例」列表的数据源。
//
// 这个文件不是被打包进 bundle 的模块，而是由前端按 URL 动态 import 的 ES 模块
// （见 `frontend/src/presets.ts` 的 loadPresets），所以改它不用改代码。
// dev 下刷新页面即生效；若在用 `frontend/dist/` 的构建产物，要重新 build，
// 或直接改 dist 里那份同名副本（它是 build 时从 public/ 拷过去的）。
//
// 每个条目四个字段里用三个：
//   kind —— 按钮上的短标签（标题），例如 'PDF'
//   desc —— 一句话说明，写清体积与看点
//   file —— 相对本站根的路径（落在 `public/` 下），点击后走「本地文件」上传口
//   url  —— http(s) 地址，点击后填入「远程 URL」框（与 file 二选一）
//
// 写法约定：kind 必填；desc 可以留空串；file 与 url 必须恰好一个有值。
// 字段名写错、类型不对、或两个都填/都不填的条目会被跳过，界面在列表下方列出原因。
// 条目顺序就是界面上的顺序。
//
// 样例文件来自本仓 `frontend/public/samples/`（由 backend/data 里的生成样例拷贝），
// 不依赖外网私有桶或租户域名。刻意排除：带真实人名的简历、企业租户素材。

export default [
  {
    kind: 'PDF',
    desc: '43 KB · 文档，两条管线都能出正文',
    file: 'samples/manual.pdf',
  },
  {
    kind: 'DOCX',
    desc: '118 KB · Word 文档',
    file: 'samples/report.docx',
  },
  {
    kind: 'PPTX',
    desc: '113 KB · 演示文稿',
    file: 'samples/deck.pptx',
  },
  {
    kind: 'XLSX',
    desc: '6 KB · 表格',
    file: 'samples/inventory.xlsx',
  },
  {
    kind: 'CSV',
    desc: '329 B · 纯文本表格，秒出',
    file: 'samples/table.csv',
  },
  {
    kind: 'ODT',
    desc: '2 KB · ODF 文本文档',
    file: 'samples/sample.odt',
  },
  {
    kind: 'EPUB',
    desc: '2 KB · 电子书，按 spine 拼章节',
    file: 'samples/sample.epub',
  },
  {
    kind: 'HTML',
    desc: '1.5 KB · 本地网页文件',
    file: 'samples/page.html',
  },
  {
    kind: 'TXT',
    desc: '1 KB · 纯文本',
    file: 'samples/plain.txt',
  },
  {
    kind: 'JSON',
    desc: '680 B · 结构化文本',
    file: 'samples/sample.json',
  },
  {
    kind: 'PNG 文字图',
    desc: '92 KB · 程序绘制的文字图；模型增强需本机 OCR 服务',
    file: 'samples/screenshot.png',
  },
  {
    kind: 'PNG 语义图',
    desc: '5 KB · 纯代码只给结构信息；模型增强走内容描述',
    file: 'samples/photo.png',
  },
  {
    kind: 'MP4',
    desc: '26 KB · 纯代码给音轨参数；模型增强需本机 ASR',
    file: 'samples/sample.mp4',
  },
  {
    kind: 'WAV',
    desc: '94 KB · 音频样例；模型增强需本机 ASR',
    file: 'samples/sample.wav',
  },
  {
    kind: '无扩展名',
    desc: '43 KB · 文件名无后缀，靠内容头识别（内容是 PDF）',
    file: 'samples/noext_sample',
  },
  {
    kind: '远程网页',
    desc: '公共示例页 · 走远程 URL 下载',
    url: 'https://example.com/',
  },
]
