"""
День 32. Клиент модели для ревью. Модель по умолчанию — AITunnel, gpt-5.6-luna.

Ключ берётся из AITUNNEL_API_KEY (в GitHub Actions это секрет репозитория). Если переменной нет
и запуск идёт из обычного окна — ключ спрашивается скрытым вводом и хранится только в памяти.
Вариант --llm ollama — локальная модель на этом компьютере.
"""

import getpass
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_AITUNNEL_MODEL = "gpt-5.6-luna"
DEFAULT_AITUNNEL_URL = "https://api.aitunnel.ru/v1"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"


class AITunnelLLM:
    name = "aitunnel"

    def __init__(self, model: str = DEFAULT_AITUNNEL_MODEL, api_key: str = None, base_url: str = None,
                 timeout: float = 240):
        self.model = model
        self.api_key = api_key if api_key is not None else os.environ.get("AITUNNEL_API_KEY", "")
        self.base_url = (base_url or os.environ.get("AITUNNEL_BASE_URL") or DEFAULT_AITUNNEL_URL).rstrip("/")
        self.timeout = timeout

    def chat(self, system: str, user: str) -> tuple:
        if not self.api_key:
            raise RuntimeError("Не задан ключ AITunnel: переменная AITUNNEL_API_KEY пуста.")
        body = json.dumps({"model": self.model, "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": user}]},
            ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base_url + "/chat/completions", data=body, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise RuntimeError("AITunnel отклонил ключ (401).") from e
            raise RuntimeError(f"AITunnel: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}") from e
        except OSError as e:
            raise RuntimeError(f"AITunnel недоступен: {e}") from e
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError("AITunnel вернул ответ неожиданного вида") from e
        usage = data.get("usage") or {}
        return text.strip(), {"prompt_tokens": usage.get("prompt_tokens", 0),
                              "completion_tokens": usage.get("completion_tokens", 0)}


def make_llm(kind: str, model: str = None):
    if kind == "ollama":
        from local_llm import OllamaLLM
        return OllamaLLM(model or DEFAULT_OLLAMA_MODEL, temperature=0.2, num_ctx=16384, num_predict=2000)
    llm = AITunnelLLM(model or DEFAULT_AITUNNEL_MODEL)
    if not llm.api_key and sys.stdin.isatty():
        try:
            llm.api_key = getpass.getpass("Ключ AITunnel (ввод скрыт, в файлы не сохраняется): ").strip()
        except (EOFError, KeyboardInterrupt):
            raise RuntimeError("Ключ не введён.")
    return llm
