"""录放接缝测试共用的替身与载荷构造。

放在一处有两个理由：`calls` 计数是"record 阶段真的走了网络"这件事的唯一外部证据，各写一份
就有三种口径；而 OpenAI 的响应形状（`completion` / `chunk`）原本在三个测试文件里各有一份
同名不同义的实现 —— 改一次响应形状要改四处，正是这套测试最不该有的形状。
"""
import json

import httpx

JSON_CT = "application/json"
SSE_CT = "text/event-stream"


def completion(content="盐类水解的实质是弱离子结合水电离出的 H+ 或 OH-。", tool_calls=None,
               finish="stop", usage=None, model="deepseek-chat"):
    """非流式响应：openai SDK 会把它解析成 ChatCompletion 对象。返回 (bytes, content-type)。"""
    message = {"role": "assistant", "content": None if tool_calls else content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    body = {"id": "chatcmpl-fake", "object": "chat.completion", "created": 1, "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}]}
    if usage:
        body["usage"] = usage
    return json.dumps(body, ensure_ascii=False).encode(), JSON_CT


def usage(prompt=21, completion_tokens=15):
    return {"prompt_tokens": prompt, "completion_tokens": completion_tokens,
            "total_tokens": prompt + completion_tokens}


def chunk(delta, finish=None, usage=None, model="deepseek-chat"):
    """流式的一行 `data:`。整串磁带用 `sse([chunk(...), ...])` 拼。"""
    body = {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": 1,
            "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage:
        body["usage"] = usage
    return "data: " + json.dumps(body, ensure_ascii=False) + "\n\n"


def sse(lines):
    return ("".join(lines) + "data: [DONE]\n\n").encode(), SSE_CT


def tool_call(call_id="call_1", name="diagnose_student", arguments='{"student_id": "S-1"}'):
    return [{"id": call_id, "type": "function",
             "function": {"name": name, "arguments": arguments}}]


def tool_call_delta(call_id="call_1", name="diagnose_student", arguments='{"student_id": "S-1"}'):
    return {"tool_calls": [{"index": 0, "id": call_id, "type": "function",
                           "function": {"name": name, "arguments": arguments}}]}


class ScriptedAsync(httpx.AsyncBaseTransport):
    """异步的"真网络"（agent 回路与 `DeepSeekProvider` 用）：`calls` 就是网络被打了多少次。"""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    async def handle_async_request(self, request):
        self.calls += 1
        body, content_type = self.payloads.pop(0)
        return httpx.Response(200, headers={"content-type": content_type}, content=body)


class ScriptedSync(httpx.BaseTransport):
    """同步版（`LLMService` 那条出口用）：形状与异步版一致，只差一个 await。"""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def handle_request(self, request):
        self.calls += 1
        body, content_type = self.payloads.pop(0)
        return httpx.Response(200, headers={"content-type": content_type}, content=body)
