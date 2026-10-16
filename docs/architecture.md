# 目标架构

核心功能、核心模块、暂时或明确不做。

## 1. 系统是什么

把任意 URL 或本地文件，转成尽可能保留的**原始文本**或**摘要**。LLM 与工作流节点是调用场景，不是第二种产品。默认只做确定性抽取；OCR / ASR / LLM 是可选扩展，由这次请求的开关和扩展是否可用共同决定。本机 Web 界面只为调试后端。

**明确不做：**

- 不做鉴权、多租户、限流、平台计费——本机自用。
- 不把 URL 交给任何上游代下载：图片自己下载后传字节，音频自己抽音轨后传字节。
- 远程仓不收录 OCR / ASR 权重；使用者放到各服务自己的 `models/`。
- 不把另起外部模型仓当作默认部署；OCR / ASR 要起就在本仓 `services/`。
- 不静默截断：截断、跳过、降级必须在结果里声明。
- 压缩包只列条目，不解压、不递归。
- 老版 Office（doc / ppt / wps）不做深度解析，只降级。
- 不为提高成功率放松下载安全（私网 / 体积 / TLS / 请求体）。
- 不附带生产托管脚手架（容器、系统服务、完整反代）。
- 核心后端只用本仓 uv 环境；扩展可自带独立环境。

状态：**已实现** = 代码位置与目标相符；**内联** = 逻辑有但未拆开；**未做** = 目标有、代码没有；**明确不做** = 只标边界。

## 2. 后端 · 转换主流程

一次 URL 或文件从进到出。HTTP `/convert` 只是走进调度层的门；探测和前端都不在这条链上。

```
backend/

服务入口    app.py
            加载配置、挂路由、起进程；不含转换业务。  已实现

调度层      按序调用下面各层，解释这次开了哪些扩展
└── 统一流程    pipelines/convert.py
                一份源从取文件到组装只走这一条。
                入口：routers/convert/（收参、回 HTTP，不实现格式）。  已实现

下载层      把 URL 或本地上传落成临时文件，并认清真实类型
├── 下载与安全  pipelines/steps/step1_download.py
                用户 URL 能不能下、下多大、TLS / SSRF。  已实现
├── 上传落盘    同 step1 / step3
                本地文件进入同一套临时文件。  已实现
├── 类型判断    pipelines/steps/step2_detect.py、ole_directory.py
                文件头、容器指纹、扩展名 → 真实格式。  已实现
└── 源组装      pipelines/steps/step3_prepare.py
                补后缀，做成后面共用的源。  已实现

处理器层    按格式做确定性抽取，不调 OCR / ASR / LLM
            落点均为 pipelines/processors/<名>.py，一类格式一个文件。  已实现
├── pdf         抽出文字层与页序；无文字层时只声明，页面渲染交给扩展
├── docx        抽出块级正文、链接与文本框
├── pptx        抽出幻灯片正文与备注
├── odf         抽出 ODT / ODP 等 OpenDocument 正文
├── excel       把 xlsx / xls / ods / csv 渲成对齐的逻辑表
├── html        抽出网页正文、标题、链接与表格
├── epub        按阅读顺序抽出章节正文
├── rtf         抽出 RTF 正文与表
├── ofd         抽出版式文件里的字；认字走扩展
├── 纯文本      按编码读出全文
├── 图片        只给格式、尺寸等结构信息
├── 音视频      只给时长、采样率等音轨参数
└── 压缩包 / 安装包  只列条目，不解压

扩展层      在确定性结果上叠加认字、转写、看图、摘要；没有则跳过
├── 能力注册    configs/extensions.py、extensions/registry.py
                谁能干这件事、这次选哪家。  已实现
├── 看图与读素材  extensions/reading.py、images.py
                判型、扫描页、内嵌图。  已实现
├── LLM 出口    extensions/deepseek.py
                摘要、问答、看图调用。  已实现
├── OCR 出口    extensions/paddle_ocr.py
                独立文字图交给哪家。  已实现
└── ASR 出口    extensions/qwen3_asr.py
                音轨转写。  已实现

扩展进程层  本仓独有的独立进程；核心只通过 HTTP / 字节调用，不 import
├── OCR 实现    services/ocr/<实现名>/     已实现（权重自备）
└── ASR 实现    services/asr/<实现名>/     已实现（权重自备）

交付层      拼出对外的原始文本和摘要，失败也同形
├── 正文合并    utils/delivery_content.py
                确定性段 + 本次实际跑过的转写。  已实现
└── 响应组装    pipelines/steps/step6_assemble.py
                同一套形状；截断、降级要声明。  已实现
```

后端内部依赖单向（结构测试守）：

```
app → routers → pipelines → { extensions , utils } → configs
```

`services/` 不加入这张 import 图。`configs/` 与 `utils/` 不是阶段层，是被多层共用的配置和通用件。

## 3. 前端 · 本机调试

不是后端的一层。前端一块地、后端一块地，只通过 HTTP 说话。

```
frontend/           调试界面：选源、开开关、看结果。  已实现

探测（旁路）        routers/probe/
                    独立接口，只给界面问扩展能不能用；不算转换主流程。  已实现
```

## 4. 仓库根

```
start.py            前后端以及本机扩展进程一起起，减少调试步骤。  已实现
```
