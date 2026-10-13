"""
День 32. Ревьюер: собирает промпт из diff и контекста RAG, вызывает модель, оформляет Markdown.

Ответ модели строго по трём разделам: потенциальные баги, архитектурные проблемы, рекомендации.
"""

from git_changes import PRDiff

CONTEXT_CHARS = 1500

SYSTEM_PROMPT = """Ты — автоматический ревьюер кода в pull request. Тебе дан diff, список изменённых файлов
и фрагменты документации и кода репозитория (контекст RAG).

Пиши ревью по-русски, по делу, без воды. Ответ строго в три раздела с этими заголовками:
## Потенциальные баги
## Архитектурные проблемы
## Рекомендации

В каждом пункте укажи: что не так, где (файл и строка из diff, если их видно), почему это важно и как исправить.

Правила:
- Опирайся только на diff и контекст. Не выдумывай функции, файлы, строки и поведение, которых там нет.
- Если строку в diff определить нельзя, так и напиши: «по контексту», без номера.
- Если в разделе проблем не нашлось, напиши «Не найдено». Не придумывай замечания ради объёма.
- Не придирайся к стилю, если правила стиля нет в документации проекта.
- Если правило из документации проекта нарушено, сошлись на источник в квадратных скобках: [путь].
- Пункты «Рекомендации» — только то, что улучшает изменение; не переписывай PR целиком."""


def build_prompt(pr: PRDiff, contexts: list) -> str:
    lines = ["=== Изменённые файлы ==="]
    lines += [f"{status} {path}" for status, path in pr.files] or ["(нет файлов)"]
    if pr.skipped:
        lines += ["", "Не вошли в анализ: " + "; ".join(pr.skipped)]
    lines += ["", "=== Diff ==="]
    for path, status, text in pr.diffs:
        lines += [f"--- файл {path} ({status}) ---", text, ""]
    lines += ["=== Контекст из документации и кода репозитория (RAG) ==="]
    for h in contexts:
        lines += [f"[{h['source']} — {h['section']}]", h["text"][:CONTEXT_CHARS], ""]
    if not contexts:
        lines.append("(релевантного контекста не найдено)")
    return "\n".join(lines)


def review(llm, pr: PRDiff, contexts: list) -> dict:
    user = build_prompt(pr, contexts)
    text, usage = llm.chat(SYSTEM_PROMPT, user)
    sources = []
    for h in contexts:
        if h["source"] not in sources:
            sources.append(h["source"])
    return {"text": text, "sources": sources, "usage": usage, "prompt_chars": len(user)}


def to_markdown(pr: PRDiff, result: dict, model_label: str) -> str:
    stats = f"файлов в PR: {len(pr.files)} · строк +{pr.added} / −{pr.removed} · модель: {model_label}"
    out = [
        "# Автоматическое AI-ревью",
        "",
        "> Это автоматический анализ. Он может ошибаться: решение о правках принимает человек.",
        "",
        f"_{stats}_",
        "",
        result["text"].strip(),
        "",
        "---",
    ]
    if result["sources"]:
        out.append("Контекст из репозитория: " + "; ".join(result["sources"]))
    if pr.skipped:
        out.append("Не вошли в анализ: " + "; ".join(pr.skipped))
    return "\n".join(out) + "\n"
