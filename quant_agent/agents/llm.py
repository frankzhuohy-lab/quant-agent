#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenAI 兼容 LLM 客户端（默认本地小模型 xiaoji，免 key，无外发）。

只用于: 假设提案（Strategy Researcher）与反证审查（Quant Critic）。
任何 LLM 输出都必须过严格 schema 校验；失败有限次重试，最终失败记
ERROR/INFO —— LLM 不可用绝不产生虚构实验结果。
"""
from __future__ import print_function
import os, json, urllib.request, urllib.error

DEFAULT_BASE = "http://192.168.50.148:8080/v1"
DEFAULT_MODEL = "qwen3.6-27b"


def chat(messages, base_url=None, model=None, temperature=0.2,
         timeout=120):
    base = base_url or os.environ.get("QUANT_AGENT_LLM_BASE") or DEFAULT_BASE
    mdl = model or os.environ.get("QUANT_AGENT_LLM_MODEL") or DEFAULT_MODEL
    payload = {"model": mdl, "messages": messages,
               "temperature": temperature}
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    return out["choices"][0]["message"]["content"]


def chat_json(messages, max_retries=2, **kw):
    """请求 JSON 输出并解析；重试后仍失败抛异常（由调用方记 ERROR）。"""
    last = None
    for i in range(max_retries + 1):
        try:
            content = chat(messages + [
                {"role": "system",
                 "content": "只输出合法 JSON，不要输出 markdown 代码围栏。"}],
                **kw)
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            return json.loads(content)
        except (urllib.error.URLError, TimeoutError, ValueError,
                KeyError) as e:
            last = e
    raise RuntimeError("chat_json failed after %d retries: %s" %
                       (max_retries + 1, last))
