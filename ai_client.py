"""DeepSeek OpenAI-compatible client with explicit, user-facing failures."""

import json

import requests


DEFAULT_API_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
PROVIDER_NAME = "DeepSeek"


class AIError(Exception):
    def __init__(self, message, *, code="ai_error", retryable=False):
        super().__init__(str(message or "未知 AI 错误"))
        self.code = code
        self.retryable = retryable


def _content_text(content):
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [_content_text(item) for item in content]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(content, dict):
        for key in ("text", "content", "value", "output_text"):
            value = _content_text(content.get(key))
            if value:
                return value
    return ""


def _api_error_text(data, status_code):
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            for key in ("message", "type", "code"):
                if error.get(key):
                    return str(error[key])
        elif error:
            return str(error)
        for key in ("message", "detail"):
            if data.get(key):
                return str(data[key])
    return f"HTTP {status_code}"


class AIClient:
    def __init__(
        self,
        api_key,
        api_base=DEFAULT_API_BASE,
        model=DEFAULT_MODEL,
        timeout=120,
        use_system_proxy=False,
    ):
        self.api_key = (api_key or "").strip()
        self.api_base = (api_base or DEFAULT_API_BASE).strip().rstrip("/")
        self.model = (model or DEFAULT_MODEL).strip()
        self.timeout = timeout
        self.use_system_proxy = bool(use_system_proxy)

    def _session(self):
        session = requests.Session()
        session.trust_env = self.use_system_proxy
        return session

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _request_json(self, method, path, payload=None):
        if not self.api_key:
            raise AIError(
                "API Key 未配置，请打开“AI 设置”填写 DeepSeek API Key。",
                code="missing_key",
            )
        url = f"{self.api_base}/{path.lstrip('/')}"
        try:
            response = self._session().request(
                method,
                url,
                headers=self._headers(),
                json=payload,
                timeout=(10, self.timeout),
            )
        except requests.exceptions.Timeout as error:
            raise AIError(
                "连接 DeepSeek 超时。请稍后重试，并检查网络或系统代理设置。",
                code="timeout",
                retryable=True,
            ) from error
        except requests.exceptions.ConnectionError as error:
            raise AIError(
                "无法连接 DeepSeek。请检查网络、API Base 或系统代理设置。",
                code="connection",
                retryable=True,
            ) from error
        except requests.exceptions.RequestException as error:
            raise AIError(
                f"DeepSeek 网络请求失败：{error}",
                code="network",
                retryable=True,
            ) from error

        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError) as error:
            request_id = response.headers.get("x-request-id", "未提供")
            raise AIError(
                f"DeepSeek 返回了无法解析的数据（HTTP {response.status_code}，请求 ID：{request_id}）。",
                code="invalid_response",
                retryable=response.status_code >= 500,
            ) from error

        if not 200 <= response.status_code < 300:
            message = _api_error_text(data, response.status_code)
            if response.status_code in (401, 403):
                raise AIError(
                    "DeepSeek API Key 无效、已失效或没有模型权限。请在“AI 设置”中重新检查。",
                    code="authentication",
                )
            if response.status_code == 402:
                raise AIError(
                    "DeepSeek 账户余额不足，请充值后重试。",
                    code="insufficient_balance",
                )
            if response.status_code == 404:
                raise AIError(
                    f"DeepSeek 模型或接口不存在：{self.model}。请测试连接后重新选择模型。",
                    code="not_found",
                )
            if response.status_code in (400, 422):
                raise AIError(
                    f"DeepSeek 拒绝了本次请求：{message}",
                    code="invalid_request",
                )
            if response.status_code == 429:
                raise AIError(
                    "DeepSeek 请求过于频繁，或账户并发额度已用完。请稍后重试。",
                    code="rate_limit",
                    retryable=True,
                )
            raise AIError(
                f"DeepSeek 服务错误（HTTP {response.status_code}）：{message}",
                code="service_error",
                retryable=response.status_code >= 500,
            )
        if not isinstance(data, dict):
            raise AIError(
                "DeepSeek 返回格式不正确：响应不是 JSON 对象。",
                code="invalid_response",
            )
        return data

    def list_models(self):
        data = self._request_json("GET", "models")
        models = []
        for item in data.get("data") or []:
            if isinstance(item, dict) and item.get("id"):
                models.append(str(item["id"]))
        return sorted(set(models))

    def chat_completion(
        self,
        messages,
        temperature=None,
        max_completion_tokens=3072,
    ):
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": int(max_completion_tokens),
            "stream": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        data = self._request_json("POST", "chat/completions", payload)
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise AIError(
                "DeepSeek 没有返回候选答案。请稍后重试。",
                code="empty_choices",
                retryable=True,
            )

        choice = choices[0]
        message = choice.get("message") or {}
        content = _content_text(message.get("content"))
        if not content:
            content = _content_text(message.get("reasoning_content"))
        if not content:
            content = _content_text(choice.get("text"))
        if not content:
            finish_reason = choice.get("finish_reason") or "未提供"
            request_id = data.get("id") or "未提供"
            raise AIError(
                "DeepSeek 返回了空答案"
                f"（finish_reason：{finish_reason}，响应 ID：{request_id}）。请重试或切换模型。",
                code="empty_content",
                retryable=True,
            )
        return content
