# -*- coding: utf-8 -*-
"""大模型 API 客户端（OpenAI 兼容接口，仅用标准库，不引入额外依赖）。

兼容 OpenAI / DeepSeek / Moonshot(Kimi) / 硅基流动 / Ollama 本地模型等，
只要服务端支持 POST {base_url}/chat/completions 即可。
在 config/settings.json 的 llm 节中配置 base_url / api_key / model。
"""
import json
import urllib.error
import urllib.request

from .utils import log


def chat_completion(base_url: str, api_key: str, model: str, messages: list,
                    timeout: int = 15, max_tokens: int = 200,
                    temperature: float = 0.9) -> dict:
    """调用对话接口。

    返回 {"ok": True, "text": 回复内容}；
    失败返回 {"ok": False, "offline": True/False, "error": 描述}。
    offline=True 表示网络不通（可降级离线库），False 表示鉴权等配置问题。
    """
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"].strip()
        return {"ok": True, "text": text}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except OSError:
            pass
        log.warning("LLM HTTP 错误 %s: %s", e.code, body)
        return {"ok": False, "offline": False,
                "error": f"HTTP {e.code}"}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.info("LLM 网络不可用: %s", e)
        return {"ok": False, "offline": True, "error": str(e)}
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        log.warning("LLM 响应解析失败: %s", e)
        return {"ok": False, "offline": True, "error": f"响应格式异常: {e}"}
    except Exception as e:                      # 兜底，绝不抛出
        log.warning("LLM 未知异常: %s", e)
        return {"ok": False, "offline": True, "error": str(e)}


def build_system_prompt(personality, user_name: str = "") -> str:
    """由人格配置生成系统提示词（注入性格/语气/口头禅/记忆库）。"""
    parts = [
        f"你正在扮演一只桌面宠物，名字叫「{personality.name}」。",
    ]
    if personality.background:
        parts.append(f"角色背景：{personality.background}")
    if personality.tone:
        parts.append(f"性格基调：{personality.tone}")
    if personality.speech_style:
        parts.append(f"说话语气与风格：{personality.speech_style}")
    if personality.catchphrases:
        parts.append(f"常用口头禅：{'、'.join(personality.catchphrases)}")
    if personality.memory:
        facts = []
        for key, value in personality.memory.items():
            if str(key).startswith("_"):    # 跳过说明字段
                continue
            fact = value.get("fact", str(value)) if isinstance(value, dict) else str(value)
            facts.append(f"{key}：{fact}")
        parts.append("专属记忆（与对方的共同经历，可在对话中自然提及）：" + "；".join(facts))
    if user_name:
        parts.append(f"正在与你聊天的人叫「{user_name}」，要称呼 TA 这个名字。")
    parts += [
        "规则：",
        "1. 始终以该角色的口吻用第一人称回复，绝对不要跳出角色；",
        "2. 回复要简短口语化，一般不超过 60 字，可带 1~2 个 emoji；",
        "3. 只输出纯文本回复本身，不要任何解释、前缀或 Markdown 格式。",
    ]
    return "\n".join(parts)
