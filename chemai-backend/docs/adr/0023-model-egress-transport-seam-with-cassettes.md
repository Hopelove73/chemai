# ADR-0023: 模型出口收成一条 transport 接缝，配 live 与录放带两种 adapter

**Date**: 2026-03-27
**Status**: Accepted（架构报告候选 2 · grilling Q1–Q14；用户裁定「2 模型接缝 + 录放带」）
补记：同日四轮审查之后加了决策 18（接 `deepseek-flash`：模型名、思考模式按通路分、失效的
输出封顶、按模型定的窗口），并把决策 3/7/8/10/14/16/17 的措辞对齐到代码实际形状。

## Context

改动前，五个地方各自拿 httpx/openai SDK 直接打模型端点：`app/services/llm_service.py:86`
的 `_call_with_retry`（同步 httpx，出题/批改/家长报告都从它走）、`agent/provider/deepseek.py`
（异步 httpx，意图分类与几个 skill）、`agent/guard.py:551` 的 `ChatOpenAI`（LangChain agent
回路），再加 `app/services/vector_search.py:301`（dashscope SDK 直连）与两个百度教育
OCR/VLM 客户端（`grading_client.py`、`paper_cut_client.py`）。后果不是风格问题，是可测性：

- 评估的 `needs_api` / `slow` 层（`evals/layers.py`，timeout 900–1200s）**必须真 key 真付费**
  才能跑，所以它们实际不进 CI，回归靠人肉。老师端最值钱的那几条判据（轨迹、出题质量）
  因此没有可重复的基线。
- 单测只能 monkeypatch 到 `llm_service` 的方法上（仓库现有做法），patch 点跟着实现漂移；
  改一个客户端构造方式就有一片测试静默失去意义。
- 重试、超时、成本统计在三个出口上各写一份，三份语义不完全一致。

## Decision

**接缝放在 HTTP transport 层**（Q1），一条 `app/services/llm_transport.py`，三种进程模式
（`CHEMAI_LLM_MODE=live|record|replay`，默认 live）。以下每一条都是问过后定下的：

1. **接缝位置**：不在 service 层、不在 Runnable 包装层，在 `httpx` transport。理由是它同时
   罩住同步 `httpx.Client`、异步 `httpx.AsyncClient` 与 `ChatOpenAI(http_async_client=...)`
   三种形状，且上层代码一行不用改 —— 它们都以为自己还在跟一个普通 HTTP 端点说话。
2. **两个 adapter**：live（透传，生产默认）与 cassette（record 落盘 / replay 只读）。
   一个 adapter 是假设的接缝，两个才是真的。
3. **命中键** = `provider / model / 端点 / sha1(查询串 + 归一化请求体)[:16]`（Q2、Q11）。
   端点单独一段：同一台 host 挂着 `/v1/chat/completions` 与 `/beta/chat/completions` 两个
   端点，只按 body 算键会让两者互相吃掉对方的重放序号。查询串进键但**凭证类的值先打码**
   （`access_token`/`api_key`/`signature`… → `***`）：Bing 兜底那条是 `GET /search?q=<词>`
   且请求体为空，不进键的话任意两个检索词撞成同一盘带，重放会拿"氧气"的网页答"钠"的问题
   而且一声不响；而百度那类把令牌放查询串的端点，令牌每两小时轮换，照原值算键就是每两小时
   整批 miss。
   `messages` 与 `tools` 一定进键 —— `CONTEXT.md` 写着工具描述是模型选工具的唯一接口，
   改了描述就是改了模型输入。`user`/`timestamp`/`seed`/`request_id` 这类不进键；
   **请求头一个都不进键**，否则密钥轮换会让整批磁带一夜失效，那是最常见的假 miss。
   换 model 即全 miss：拿旧模型的响应测新模型不叫回归。
4. **miss 硬失败**（Q3）：抛 `CassetteMiss`，绝不静默回落 live，并列出最接近的几盘磁带与
   它们差在哪几个字段。回落 live 等于在 CI 里花真钱、还把带真实学生数据的新磁带写进仓库。
5. **录原始字节，断言落在解析后**（Q5）：流式录的就是一整串 `data:` 行，重放原样交回。
   chunk 切法与时间间隔不是 contract 的一部分。实测这一条救了我们：LangChain 的
   `tool_calls` 只在**累加后的 chunk** 上才有值，单个 delta 上永远是空的，所以断言必须
   在解析后的消息上，否则测的是解析器而不是接缝。
   一个诚实的例外：传输层压缩。httpx 在我们读到之前就把 `content-encoding: gzip` 解开了，
   所以磁带存的是解压后的字节 —— 那么重建响应与落盘都必须剥掉那个头，带着它再解一次会
   当场 `DecodingError`（record 路径实测炸过，replay 路径因为剥了头才是对的）。
6. **模式是进程级开关**（Q6）：`CHEMAI_LLM_MODE` + `CHEMAI_CASSETTE_DIR`，replay 不给目录
   当场抛，不猜目录。测试与评估显式进 replay，生产不配就等于没有这层。
7. **磁带只从评估录，CI 永不写**（Q8），评估改跑合成数据集（Q7），磁带因此可以入库。
   服务器进程这边有代码着力点：`app/main.py` 在建好 app 之后立刻调
   `refuse_recording_in_server()`，模式为 record 就拒绝启动（replay 只读磁带，允许）。
   **这个守卫自己读 `.env`** —— 双轴审查实测：`import app.main` 并不拉起 `app/config.py`
   （那是本仓读 `.env` 的地方，`sys.modules` 里没有它），守卫只盯 `os.environ` 的话挡住的
   就只有"命令行显式 export"那一半，而会被忘在仓库里的恰恰是 `.env` 里那一行。路径按本文件
   位置锚定，与启动 cwd 无关。
   另有一条故意**不设**的守卫：CI 里不做"检测到 CI 环境就拒绝 record"。我们自己的测试就在 CI
   里往临时目录录带（那是这套测试的形状），硬守卫会误伤。将来的评估录制入口应当写到仓库外的
   显式目录，再由人挑着提交，而不是靠运行时猜环境。
8. **出口清单以绊线为准，不在文档里数数**。按模块数出来是"三个"，按调用点其实是更多：
   `LLMService`（同步 httpx）、`DeepSeekProvider`（异步 httpx）、agent 的 `ChatOpenAI`、
   `evals/judge.py`（判官），再加 `agent/tools/exam_bank.py` 里多处直接打 chat/completions
   与搜索端点的客户端。双轴审查就是抓到过 `exam_bank` 那一处 —— replay 下它是全仓仍会真
   联网的地方，而"评估全程离线"这句话会在它身上说谎。
   之所以这里不写死一个数字：上一版写了"三个"，代码里其实是四处建模、五处构造，文档跟着
   漂了一次；`llm_transport.py` 的模块 docstring 因此也拒绝数数。防线是一条 AST 绊线：
   生产源码里凡构造 httpx 客户端且打的是模型/喂模型内容的上游，就必须带 `transport=`
   （`tests/test_llm_transport_egresses.py`，并带"扫不到文件≠没有违规"的正向对照）。
   向量嵌入与百度 OCR/VLM 不进这条缝 —— 它们不是 OpenAI 兼容的 HTTP 形状，硬塞进来会让接带的
   字节语义变形。（向量嵌入这条后来干脆不出网了：ADR-0024 把它换成本地 bge 推理。）
9. **注入客户端必须照抄 SDK 的超时、连接上限与重定向**（Q1 的代价）。openai 2.53 建客户端时
   用 `DEFAULT_TIMEOUT = Timeout(connect=5, read/write/pool=600)`、
   `DEFAULT_CONNECTION_LIMITS = (1000/100/5s)`，并且 `follow_redirects=True` —— 而 httpx 自己
   的默认是**不跟重定向**。少抄任何一条，"加一层录放"就顺手改掉了生产语义。每请求超时
   （`request_timeout=60`）仍由 SDK 放在 request extensions 里覆盖，与是否注入无关。
   测试直接跟 `openai._constants` 里的这两个常量比，不跟字面量比：SDK 升版改了默认值会红，
   而不是把旧值钉死。
10. **三种模式一律留 2 次请求级重试；重放游标是进程级的，并按运行边界分本**。
    record 留重试才会把一次限流后成功记成同一盘带里的 `429→200` 两条；replay 若关成 0，
    SDK 拿到第一条 429 就抛 `RateLimitError` —— 录进去的带反而放不出来，而失败长得像
    "供应商挂了"。录的口径与放的口径必须是同一套
    （`test_a_tape_containing_a_rate_limit_still_replays_through_the_sdk`）。
    游标挂在进程（`_engine_for` 按"模式 + 目录"复用同一个引擎），不挂在 transport 实例：
    真实通路里没有任何对象活得比一次调用更长（`LLMService` 每次新建客户端、
    `get_langchain_model` 每轮新建模型），挂在实例上那盘 429→200 会永远放回 429。
    同一轮运行内多调一次 = `cassette exhausted`（行为变了，该响）；跨运行的同名键由
    `replay_scope("case-id")` 划边界 —— 边界给这一轮一个**自己的游标命名空间**，退出时丢掉。
    第一版是"进边界就把所有引擎的游标 `clear()`"，那是错的：两个并发评估会互相抹平记账
    （A 跑到一半，B 进门把 A 的游标归零，A 的第二次调用又拿到第一条响应 —— 该响的那次不响了）。
    嵌套的边界沿用外层那一本，内层另起会放跑"这轮多调了一次"。不带边界时保持严格语义。
    现状要说准：`replay_scope` 由测试在用，`evals/` 那边还没接（接评估跑批是数据集与录制
    流程那一刀的活）。
    openai 的 `_base_client` 用 `except Exception` 把 transport 抛出的任何东西包成
    `APIConnectionError`，所以 miss 的真错在异常链里，断言要顺着 `__cause__` 找。
11. **`CassetteMiss` 不许被任何一层 `except Exception` 降级**。它得从 `LLMService` 的重试循环
    （原本吞成 `{"success": False}`）、`analyze_paper_with_vision` 的两个 provider 兜底
    （原本吞成"换下一个模型再试"/失败字典）里原样穿出来；`DeepSeekProvider` 那两条循环天然
    只认 `TransportError`，不需要额外放行。
12. **用量字段名只在这条缝里认**（Q14）：`normalize_usage` / `prompt_tokens_of` 收
    `prompt_tokens`/`completion_tokens`、`input_tokens`/`output_tokens` 与嵌在
    `response_metadata.token_usage` 里的第三种写法。`agent/context_meter.extract_prompt_tokens`
    保留同名入口但不再自带解析 —— 两个口径会让同一轮对话在两个地方显示两个数。
13. **上下文窗口由后端下发**（Q14）：`/chat/history` 响应与 SSE `context` 事件都带
    `CONTEXT_WINDOW`，前端不再有 `DEFAULT_CONTEXT_WINDOW`。窗口是进程配置，前端那份常量在
    配置改后会安静地显示一个错一半的百分比，而"已用 40%"与"已用 78%"在圆环上看着几乎一样；
    下发缺字段时前端宁可不画圆环，也不按猜的窗口画。
    分工是切开的：**"占了多少"是显示时的占位估算，前端继续算**（首轮流式之前后端也估不出
    准数，系统提示与工具 schema 都不在会话消息里）；**"总共多少"是进程配置，只有后端知道**。
14. **录制、重放、miss 各留一行结构化日志**（Q13），字段名照 OTel GenAI 语义约定取：
    `gen_ai.provider.name`、`gen_ai.request.model`、`gen_ai.usage.input_tokens` /
    `gen_ai.usage.output_tokens`、`http.response.status_code`。record 是 info、replay 命中是
    debug、miss 是 warning（带键与磁带总数）。这也是决策 12 里 `normalize_usage` 在生产中的
    用处 —— **token 数只在 record 那行有**：它是从真响应字节里读出来的，重放那边再解一次
    磁带字节只是白烧一次 CPU（流式还得扫一遍 `data:` 行）。所以一次评估跑完，成本看录那轮
    的日志，重放那轮看的是序号与状态。live 模式一行都不打：这层在生产里必须是无感的。

15. **搜索通道也在缝上**。`exam_bank` 的 AnySearch 与 Bing 抓取不是模型端点，但它们的结果
    原文会被拼进下一次模型请求的 messages：不进磁带的话 replay 下这台机器仍在真联网（Q9 的
    "全程不碰网络"就成了假话），而且键取决于当天的网页字节，带录完再也放不中。缝本来就不要求
    OpenAI 形状 —— 非 JSON 请求体按字节的摘要算键（`request_key`），GET 与表单一样能录能放。
    也正因为空请求体的身份全在 URL 上，`_CassetteEngine.plan` 会把打码后的查询串当作 `query`
    字段留进磁带的 `normalized`：不留的话 miss 报告只能说"两边都是空请求体"，而抓取通道的
    漏放十个里有九个是检索词变了。
16. **"决定单元内不许等网络"（ADR-0021）的判定搬到缝上**（`refuse_model_call_in_work_unit`
    在 `_CassetteEngine.plan` 入口）：record/replay 下缝上的每一处构造都过这一点。live 下
    `ChatOpenAI` 走 langchain 自己的共享客户端（决策 9 的 P1 修复），transport 层够不到，
    所以业务层那两处（`LLMService`、`DeepSeekProvider`）与 `exam_bank` 的客户端工厂继续
    各自判一次 —— 它们是**早失败**（在打包请求之前就炸，而不是等建好客户端才发现），
    不再各自实现判定：三处都转调缝上那个函数本体，`DeepSeekProvider` 那层只转发的同名
    包装已经删掉。诚实的口径是：**live 下 agent 回路那条仍是残余**，不是全覆盖。
17. **磁带里的个人信息有边界**：进键与留档的请求体把 `data:` 图片换成
    `<data-uri sha256=… bytes=…>`（不同图片仍是不同请求，但整张手写答卷不以可还原形式落盘），
    `url` 只记到 path 为止，查询串里凭证形状的参数值（`access_token`/`api_key`/`signature`…）
    一律打码成 `***`（本仓有把令牌放查询串的先例：`grading_client.py:58`），主目录归一成
    `{HOME}`。文本内容仍按原样存 —— 那是重放与 miss 诊断要的东西；防线因此是决策 7 那条
    "服务器不开 record" + `.gitignore` 里的 `**/cassettes/`，不是脱敏。

18. **模型出口服从两件事：模型名跟着配置走，思考模式按通路分**（接 `deepseek-flash` 时
    实测出来的，都属于"请求形状"这一层，所以记在这条 ADR 里）。
    - **写死的模型名清干净**：`.env` 换成 `deepseek-flash` 之后，四处写死 `deepseek-chat`
      的出口仍在打旧模型 —— "我换模型了"只对 agent 回路成立。现在模型名一律读
      `DEEPSEEK_MODEL`（`app/core/config.py` 加了该字段），绊线是
      `test_no_deepseek_egress_hardcodes_the_model_name`（配置本体的默认值除外）。
    - **flash 是思考模型，reasoning token 算在 `max_tokens` 之内**：实测同一句出题请求在
      2048 档被思考吃满，`finish_reason=length` 且 `content` 为空 —— 老师看到的是一条空白
      回答。分通路裁定（用户 D1）：**固定格式的那些出口（出题/诊断/家长报告/搜索结果总结/
      意图分类/判官）一律显式关思考**（`THINKING_OFF`，即 `thinking: {"type": "disabled"}`，
      非标准字段）；**只有对话讲评那条留思考**，因为留思考正是选它的理由，而且它走流式
      （实测首字 0.3s，思考阶段不挡首字）。断言落在录下来的请求体上，不落在源码字符串上。
    - **`ChatOpenAI` 的输出封顶原来是假的**：langchain 1.4.2 把 `max_tokens=4096` 映射成
      `max_completion_tokens`，而 flash 不认那个字段 —— 实测设了 4096 那一轮仍吐出 8941
      输出 token。真封顶要从 `extra_body={"max_tokens": …}` 传（实测生效）。对话那条现在
      是 16384，与 `request_timeout=180` 配套：实测吞吐约 220 token/s，16384 token 最坏约
      74s，留着 60s 会把它掐断在一段没说完的话上。
    - **圆环的分母跟着模型定**：`CONTEXT_WINDOW` 不再是一个常量，而是
      `app/config.py:MODEL_CONTEXT_WINDOWS` 按 `DEEPSEEK_MODEL` 查表（flash 标称 1M、
      chat 128K），认不出的模型退回 128K —— 分母猜小只会提前提醒，猜大会漏报。环境变量
      仍可覆盖，那时圆环的含义从"容量表"变成"预算表"，这是刻意的 —— 本机就把它覆盖成
      200000（用户裁定）：按 1M 画环，一轮几千 token 的对话永远显示 0.4%，那个预警等于没有。
19. **provider 只留两家，视觉复核只有一家能用**（用户裁定：兜底统一用小米
    `mimo-v2.6-flash`，智谱整条删除）。
    - 智谱（`ZHIPU_API_KEY` / `GLM-4-Flash` / `glm-4v` / 已死的 `ChatGLMService`）从
      `agent/guard.py` 的工厂、两份 config、视觉兜底链里全部拆掉；绊线是
      `test_the_zhipu_path_stays_deleted`（扫 `app/` 与 `agent/` 源码）。
    - **文本 provider 与视觉 provider 是两个型号**：实测 `mimo-v2.6-flash` 对
      `image_url` + `data:image/...;base64,` 回 `HTTP 400 Invalid request parameters`
      （裸串 `image_url` 亦 400；顶层 `image_urls:[data]` 会 200 但模型回"我看不到图片"），
      而**同一张图同一形状**在 `mimo-v2.6-pro` 与 `mimo-v2.5` 上都读得对。答题卡是学生本机
      上传的 base64，没有公网链接可给，所以视觉另用 `MIMO_VISION_MODEL`，文本继续用
      `MIMO_MODEL`。附带修一个过去没人看见的 bug：以前 mimo 挂在视觉链第一位，那个 400 被
      `except Exception: pass` 吞掉，于是"MiMo 原生视觉优先"这句注释从来没成立过 ——
      每次都是白打一发再落到下一个后端。MiMo 也接受 `thinking:{type:"disabled"}`（实测
      200、reasoning 归零、正文直出），所以视觉两家统一关思考。
    - 供应商标识名一律读配置，绊线把 `deepseek-chat` / `mimo-v2.5` / `mimo-v2.6-flash` /
      `mimo-v2.6-pro` 一起扫 —— 抓的是"型号名被当值用"的形状（`"model": "<型号>"`、
      `model="<型号>"`、`X_MODEL = "<型号>"`），配置的默认值除外；散文里提型号名不算违规，
      否则等于逼人删掉上面那些实测记录。换型号时"我换模型了"必须对每条通路都成立。

20. **评估 runner 是录放带的正门**（`evals/runner.py` + `--tapes live|record|replay`）。
    接缝建好时评估并没有走它 —— 九个 `needs_api` 条目仍然只能真打模型，于是"离线可重复"这句
    话在 CI 上不存在。现在 runner 只改子进程的环境变量：一条评估一个磁带目录
    （`evals/cassettes/<条目名>`）+ 一个运行边界名，共用目录会让两条评估互相吃掉对方的重放
    序号（测试照样绿，这才是最坏的那种错）。
    - `CHEMAI_CASSETTE_SCOPE` 是给"一个进程就是一轮评估"用的整进程边界。不能靠 runner 在调用处
      包 `replay_scope`：一条评估里既有序事件循环也有线程池，而 `threading.Thread` 起始拿到的是
      contextvar 的默认值，包在主线程上等于只给一半调用起了边界。标签换了就换一本账，所以同一
      进程里连跑的 `kind="function"` 条目（`classifier`）也各自从第一条响应开始放。
    - `--tapes replay` 在起跑前预检磁带目录：缺哪几条就一次报清楚并拒绝开跑。缺磁带时让九条
      各自 miss 一遍，读起来像"系统坏了九次"，而真相是"你没带磁带"。
    - 报告里落一个 `egress` 字段。归档的趋势序列必须能说得出这一次有没有花钱。
    - 快跑层不设模式：它们本来不该打模型，"偷偷打了"由 AST 绊线抓（决策 8），不由这里掩盖。
    - **离线重放真的不需要凭据**：langchain 在**构造** `ChatOpenAI` 时就查 key（比这条缝还早），
      所以 `--tapes replay` 清掉 key 跑 `trajectory_planning`，每一条都红在
      `Missing credentials ... OPENAI_API_KEY` —— 磁带就在旁边，一次都没被问到。`agent/guard.py`
      的工厂现在在非 live 模式下补一个占位值（`OFFLINE_KEY_PLACEHOLDER`）。它不掩盖 live 的缺失：
      没配 key 的 live 进程照旧在构造时就炸，测试两半都钉着。
    - 判官那条也一样：`evals/calibrate.py` 以前按"有没有 key"选真判官还是脚本判官，于是 replay
      下自动降级成脚本判官 —— 一致率永远 1.0，五族全被写成"已启用"，一次什么都没测量的运行记成
      PASS（磁带真答话时同一批样本是 0.043 / 1.0 / 0.25 / 0.091 / 0.273）。现在 record|replay
      下禁止降级，要 fake 必须显式 `--provider fake`。

## Consequences

- 评估的 slow 层第一次有了离线可重复的跑法：`--tapes record` 录 → `--tapes replay` 放。
  本机实测（清空 `DEEPSEEK_API_KEY`/`XIAOMI_API_KEY` 后重放，四条目全部与真打时同一判据）：
  `classifier` 12 盘 11.3s → 2.0s；`judge_calibration` 110 盘 583.3s → 2.6s（五族一致率逐位相同）；
  `trajectory_planning` 8 盘 30.9s → 10.8s；`trajectory_langgraph` 29 盘 —— live 与 replay 都是
  同一条用例红（`interrupt-resume/after_resume_tool_called`），红得一致才算重放 faithfully。
- 单测不再需要 monkeypatch `llm_service` 的方法：给它一个磁带目录就行。patch 点从
  "实现的形状"换成了"网络的边界"。
- 用量解析已收进这条缝（决策 12），上下文窗口已改为后端下发（决策 13）；还没做的是把
  成本统计（每次调用的 token 与花费）也挂到同一个入口上，以及给 `evals/layers.py` 的
  九个 `needs_api` 条目配合成数据集与入库磁带 —— 接缝通了，这两件才有得谈。
- 磁带不是校验和载体：改响应体里的一个字节不会被发现，它会在解析处炸或者给出被改过的
  回答。这层故意不做完整性检查 —— 磁带进 git，靠 review 与 diff 保护，不靠摘要。
- 共享引擎按 `(模式, 目录)` 缓存在进程里，游标推进带一把线程锁（`LLMService` 是同步的、
  agent 是异步的，两边可能并发取同一条磁带）；`replay_scope` 给每一轮一个自己的命名空间，
  退出即回收，所以并发评估不再互相抹平（决策 10）。代价是同进程内"换目录又换回来"会接着
  上次的序号 —— 刻意接受：真实进程只在启动时定一次模式与目录，测试各自用独立 tmp_path。
  落盘那边同理：`CassetteStore` 的锁按磁带文件而不是一把总锁，因为 Windows 上
  `os.replace` 撞上 WinError 5 要睡几百毫秒重试，挂在总锁上就是一个键的杀毒软件拖慢整批录制。
- 与 ADR-0021 的关系：判定搬到缝上（决策 16），业务层那三处转调同一个函数当早失败 ——
  transport 层看不见工作单元，但缝上的 `plan` 看得见。live 下 agent 回路那条是已知残余。
- **推到公开 remote 之前必须先脱敏重录**。磁带里是评估对话的原文，里面会出现真实学生姓名
  （`evals/cassettes/**` 有 106 盘含「陈湘旻 / 周翔 / 李世睿」这类名字）。这不是这次新引入的
  泄露面：这些名字和 340 条学生记录本来就在被跟踪的 `chemai-backend/chemai.db` 里（`930e80c`
  入库）。变的是"同一份真名多了一份以对话原文形式存在的副本，且进了 git 历史"。所以：内部
  remote 可以直接推；要推公开仓、开放 issue/PR 协作、或把仓库副本给外部人之前，先把评估场景
  里的人名换成合成名，`--tapes record` 重录 8 条 needs_api 条目，再连同 `chemai.db` 一起决定
  历史怎么处理。凭据本来就不进磁带（查询串里的 key 形状值在键里就哈希掉），答卷图像只存
  `sha256 + 字节数`。
- 测试基座从此**不许依赖开发机的 `.env`**：`app/config.py` 与 `app/core/config.py` 在 import
  时读 `.env`，于是同一套测试在两台机器上是两种行为（实测：`.env` 把 `DEEPSEEK_MODEL` 设成
  flash 之后，一条断言磁带里模型名的用例就红了 —— 它红的不是代码，是那台机器的配置）。
  钉在 `conftest.py` 顶部（必须早于任何 app 导入，`load_dotenv` 与 pydantic-settings 都不
  覆盖已存在的环境变量），真建 `ChatOpenAI` 的用例改用一个注入假 key 的 fixture。
- 换到 flash 之后对话那条通路的成本与延迟都变了：留思考意味着单轮多烧几 k reasoning token
  （实测一轮 6.6k~9k 输出 token，约 30s），这是"要解析质量"的定价，不是回归。
