"""后端整套测试的根配置：把「请求级」文件状态按用例还原。

ADR-0020 把文件访问档位、工作区覆盖与 bash 会话 cwd 收进 contextvars。其中会话 cwd
存在一个每请求新建的**可变盒子**里（跨 ToolNode 子 task 必须可见，否则 `cd` 写下的
目录在下一轮工具调用时消失）。盒子共享是刻意的，代价落在测试基座：pytest 里所有用例
跑在同一个上下文，一个用例 `cd` 进临时目录、盒子内容就一直留在那儿，几百个用例之后
的 `bash` 仍在一个已删除的目录里执行（实测：`tests/test_session_cwd_across_tool_tasks.py`
污染 `evals/test_shell_tools.py`，NotADirectoryError）。

生产不会走到这一步——三个对话入口每个请求都 `set_file_access_level` 换新盒子。所以守
这条边界的地方是这里，而不是把盒子改回不共享（那会重新引入 `cd` 失效那个回归）。
"""
import os

# 开发机的 `chemai-backend/.env` 会决定 `app/config.py` / `app.core.config.settings` 在
# import 时读到什么，于是同一套测试在两台机器上跑两种行为（实测：`.env` 把 DEEPSEEK_MODEL
# 设成 deepseek-flash 后，一条断言磁带里模型名的用例就红了 —— 它红的不是代码，是配置）。
# `load_dotenv` 与 pydantic-settings 都不覆盖已存在的环境变量，所以在这里钉住即可与谁的
# .env 无关。这行必须早于任何 app 导入，所以它待在本文件而不是 tests/conftest.py。
os.environ.setdefault("DEEPSEEK_MODEL", "deepseek-chat")
# 各家 key 同理钉成"没配"：测试里要 key 的用例自己往实例上塞（既有做法），而不是指望
# 跑测试这台机器恰好有一把真 key —— 那会让同一条用例在有 key 的机器上走真通路。
for _k in ("DEEPSEEK_API_KEY", "XIAOMI_API_KEY", "DASHSCOPE_API_KEY",
           "ANYSEARCH_API_KEY", "BAIDU_OCR_API_KEY", "BAIDU_OCR_SECRET_KEY"):
    os.environ.setdefault(_k, "")
# JWT 密钥现在是"没有就拒绝 import"（auth.py），所以基座必须给一把固定的：
# 测试里签出的 token 只在本进程内有效，用哪把都无所谓，但换机器不能换结论。
os.environ.setdefault("CHEMAI_JWT_SECRET", "test-only-jwt-secret-not-a-real-one")
os.environ.setdefault("CHEMAI_JWT_SECRETS_PREVIOUS", "")

# 测试不许写这台机器上的生产件（ADR-0026）。`chemai.db` 是被 git 跟踪的演示数据，
# `data/chromadb` 是它的向量索引；这两条路径都在 import 期定死，所以唯一能改的地方是这里，
# 和上面钉 `.env` 的那几行是同一个理由。落点是临时目录里的一份**副本**：演示数据照样读得到
# （不少用例断言的就是那 340 个学生里的某一位），写进去的东西随目录一起消失，不回仓库。
# 评估正门 `evals/run_evals.py` 走同一个 `app.shadow_data` —— 它的 `kind="script"` 与
# `kind="function"` 两类条目不经过本文件，少那一处"跑测试不写生产件"就只剩半句真话。
from app.shadow_data import activate as _activate_shadow  # noqa: E402

_SHADOW_HOME = _activate_shadow()

# 影子库是演示 chemai.db 的副本，可能落后于模型（新列未迁移）。create_all 只建缺失
# 的表不补列——这里对副本幂等补齐 X1 的 question_type（student-daily-loop），
# 保证任何检出状态（含未跑 migrate 的 CI）下 questions 表都能落新模型。
from sqlalchemy import create_engine as _mk_engine  # noqa: E402

from app.utils.migrate import migrate_question_type as _mig_question_type  # noqa: E402
from app.utils.migrate import (  # noqa: E402
    migrate_agent_gates_plan_step as _mig_gate_plan_step,
    migrate_agent_gates_replay_payload as _mig_gate_payload,
)

_shadow_engine = _mk_engine(f"sqlite:///{_SHADOW_HOME}/chemai.db")
_mig_question_type(_shadow_engine)  # 幂等：X1 题型列，副本可能落后于模型
_mig_gate_plan_step(_shadow_engine)  # 幂等：gate-lifecycle 批次 1 的门步号列
# 幂等：2.6 的重放载荷指纹列。**入库的 chemai.db 未必带这两列**（迁移在服务启动时
# 跑），而 pytest 的影子库是它的副本 —— 不在这儿补，新克隆的仓库会当场
# `no such column: agent_gates.args_hash`。
_mig_gate_payload(_shadow_engine)
_shadow_engine.dispose()

import pytest  # noqa: E402  —— 上面那几行钉环境的语句必须排在任何 app 导入之前

from agent.tools import filesystem as fs  # noqa: E402  —— 钉环境的那几行必须排在它前面


@pytest.fixture(autouse=True)
def _restore_filesystem_request_state():
    """用例开始前记下请求态，结束后原样装回。

    `set_file_access_level` 顺带清掉一次性放行与会话 cwd（它换的是新盒子），
    正是还原想要的动作；工作区覆盖要单独装回，读 `_ws_override_var` 是唯一
    能拿到「老师是否按对话覆盖过」原值的地方——`workspace_root()` 返回的
    是解析后的路径，装回它会顺手建出一个工作区骨架。
    """
    saved_level = fs.file_access_level()
    saved_ws = fs._ws_override_var.get()
    yield
    fs.set_file_access_level(saved_level)
    fs.set_workspace_root(saved_ws)
