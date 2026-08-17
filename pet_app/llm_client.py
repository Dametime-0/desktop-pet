# -*- coding: utf-8 -*-
"""大模型 API 客户端（OpenAI 兼容接口，仅用标准库，不引入额外依赖）。

- chat_completion：文本对话（用于聊天）
- image_generation：文生图（用于 AI 形象生成）
  兼容 OpenAI / 硅基流动 / 智谱 / Ollama 等，只要服务端支持
  POST {base_url}/chat/completions 与 POST {base_url}/images/generations 即可。
- build_system_prompt：由人格配置生成聊天系统提示词
"""
import base64
import json
import urllib.error
import urllib.request

from .utils import log

#: OpenAI 图像模型名标志（用于请求/响应格式分叉）
OPENAI_IMAGE_MODEL_MARK = "gpt-image"


def _post_json(url: str, payload: dict, headers: dict,
               timeout: int) -> tuple:
    """POST JSON 通用骨架。

    返回 (data_dict | None, error_str | None, offline: bool)。
    offline=True 表示网络不通（可降级离线库），False 表示鉴权等配置问题。
    """
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None, False
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except OSError:
            pass
        log.warning("LLM HTTP 错误 %s: %s", e.code, body)
        return None, f"HTTP {e.code}", False
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.info("LLM 网络不可用: %s", e)
        return None, str(e), True
    except json.JSONDecodeError as e:
        log.warning("LLM 响应解析失败: %s", e)
        return None, f"响应格式异常: {e}", True
    except Exception as e:                      # 兜底，绝不抛出
        log.warning("LLM 未知异常: %s", e)
        return None, str(e), True


def _download_bytes(url: str, timeout: int = 30) -> bytes:
    """下载 URL 内容为字节（用于文生图返回的临时链接，须立即下载防过期）。"""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def chat_completion(base_url: str, api_key: str, model: str, messages: list,
                    timeout: int = 15, max_tokens: int = 200,
                    temperature: float = 0.9) -> dict:
    """调用对话接口（支持文本与 image_url 多模态内容）。

    返回 {"ok": True, "text": 回复内容}；
    失败返回 {"ok": False, "offline": True/False, "error": 描述}。
    """
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    data, error, offline = _post_json(url, payload, {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }, timeout)
    if error is not None:
        return {"ok": False, "offline": offline, "error": error}
    try:
        text = data["choices"][0]["message"]["content"].strip()
        return {"ok": True, "text": text}
    except (KeyError, IndexError, TypeError, AttributeError) as e:
        log.warning("LLM 响应结构异常: %s", e)
        return {"ok": False, "offline": True, "error": f"响应结构异常: {e}"}


def image_generation(base_url: str, api_key: str, model: str, prompt: str,
                     image_size: str = "768x1024", batch_size: int = 1,
                     guidance_scale: float = 7.5, num_inference_steps: int = 30,
                     seed: int = -1, timeout: int = 120) -> dict:
    """调用文生图接口（OpenAI 兼容 POST /images/generations）。

    - model 含 "gpt-image" → OpenAI 请求结构 {model, prompt, size, n}；
      否则按硅基流动等国内兼容平台 {model, prompt, image_size, batch_size,
      guidance_scale, num_inference_steps, seed(>0 才传)}。
    - 响应兼容三种结构：{"images":[{"url"}]}（硅基流动）、
      {"data":[{"b64_json"}]} 与 {"data":[{"url"}]}（OpenAI 风格）。
    - URL 结果在函数内立即下载（临时链接有效期短）。

    返回 {"ok": True, "bytes": 图片字节} 或 {"ok": False, "offline", "error"}。
    """
    url = base_url.rstrip("/") + "/images/generations"
    if OPENAI_IMAGE_MODEL_MARK in model.lower():
        payload = {"model": model, "prompt": prompt, "size": image_size, "n": batch_size}
    else:
        payload = {
            "model": model, "prompt": prompt, "image_size": image_size,
            "batch_size": batch_size, "guidance_scale": guidance_scale,
            "num_inference_steps": num_inference_steps,
        }
        if seed and seed > 0:
            payload["seed"] = seed
    data, error, offline = _post_json(url, payload, {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }, timeout)
    if error is not None:
        return {"ok": False, "offline": offline, "error": error}

    # 解析三种响应结构 → 图片载体（url 或 base64）
    try:
        item = None
        if isinstance(data.get("images"), list) and data["images"]:
            item = data["images"][0]                       # 硅基流动
        elif isinstance(data.get("data"), list) and data["data"]:
            item = data["data"][0]                         # OpenAI 风格
        if item is None:
            return {"ok": False, "offline": False,
                    "error": "响应中没有图片数据"}
        if item.get("b64_json"):
            return {"ok": True,
                    "bytes": base64.b64decode(item["b64_json"])}
        if item.get("url"):
            return {"ok": True, "bytes": _download_bytes(item["url"], 30)}
        return {"ok": False, "offline": False,
                "error": "响应中缺少 url/b64_json 字段"}
    except (KeyError, TypeError, ValueError, OSError) as e:
        log.warning("文生图响应处理失败: %s", e)
        return {"ok": False, "offline": False, "error": f"图片解析失败: {e}"}


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
        parts.append("专属记忆（与主人的共同经历，可在对话中自然提及）：" + "；".join(facts))
    if user_name:
        parts.append(f"你的主人叫「{user_name}」，要称呼 TA 这个名字。")
    parts += [
        "规则：",
        "1. 始终以该角色的口吻用第一人称回复，绝对不要跳出角色；",
        "2. 回复要简短口语化，一般不超过 60 字，可带 1~2 个 emoji；",
        "3. 只输出纯文本回复本身，不要任何解释、前缀或 Markdown 格式。",
    ]
    return "\n".join(parts)
