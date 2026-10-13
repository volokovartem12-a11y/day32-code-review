"""
День 32. Автоматическое AI-ревью pull request.

Один запуск делает:
  1) берёт diff между базовой веткой и head (git diff base...head) и список изменённых файлов;
  2) собирает RAG-индекс по документации и коду репозитория (заново, на текущей версии кода);
  3) для каждого изменённого файла достаёт похожие фрагменты репозитория;
  4) отправляет всё модели (AITunnel, gpt-5.6-luna) и пишет review.md.

    python review/review.py --base origin/main --head HEAD --out review.md     # в GitHub Actions
    python review/review.py --repo "C:\\проект" --base main --head feature      # на ПК, локально
    python review/review.py --base main --dry-run                               # без модели: только промпт

Код выхода: 0 — готово (или нечего ревьюить / нет ключа), 1 — ошибка модели, 2 — ошибка git.
"""

import argparse
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from code_index import build_repo_index, retrieve_for_files  # noqa: E402
from git_changes import GitError, collect  # noqa: E402
from index_store import Index  # noqa: E402
from llm import DEFAULT_AITUNNEL_MODEL, DEFAULT_OLLAMA_MODEL, make_llm  # noqa: E402
from reviewer import build_prompt, review, to_markdown  # noqa: E402


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Автоматическое AI-ревью PR: diff + RAG по репозиторию")
    ap.add_argument("--repo", default=".", help="папка git-репозитория")
    ap.add_argument("--base", default="origin/main", help="базовая ветка PR")
    ap.add_argument("--head", default="HEAD", help="ветка или коммит PR")
    ap.add_argument("--out", default="review.md", help="куда записать ревью")
    ap.add_argument("--llm", choices=["aitunnel", "ollama"], default="aitunnel")
    ap.add_argument("--model", help=f"модель: aitunnel — {DEFAULT_AITUNNEL_MODEL}, ollama — {DEFAULT_OLLAMA_MODEL}")
    ap.add_argument("--embed", choices=["local", "ollama"], default="local", help="эмбеддинги для RAG")
    ap.add_argument("--dry-run", action="store_true", help="не вызывать модель, а записать промпт в --out")
    a = ap.parse_args(argv)

    repo = os.path.realpath(a.repo)
    try:
        pr = collect(repo, a.base, a.head)
    except GitError as e:
        print(f"[ошибка git] {e}")
        return 2

    if not pr.files:
        _write(a.out, "# Автоматическое AI-ревью\n\nВ этом PR нет изменённых файлов.\n")
        print("Изменений нет: ревью не нужно.")
        return 0
    if not pr.diffs:
        _write(a.out, "# Автоматическое AI-ревью\n\nВсе изменённые файлы пропущены: "
                      + "; ".join(pr.skipped) + "\n")
        print("Все файлы пропущены, модель не вызывалась.")
        return 0

    if a.llm == "aitunnel" and not a.dry_run and not os.environ.get("AITUNNEL_API_KEY") and not sys.stdin.isatty():
        print("AITUNNEL_API_KEY не задан: ревью пропущено. В GitHub добавьте секрет репозитория AITUNNEL_API_KEY.")
        return 0

    index_path = os.path.join(tempfile.gettempdir(), f"ai-review-{os.getpid()}.sqlite")
    try:
        info = build_repo_index(repo, index_path, a.embed, None)
        print(f"Индекс репозитория: документов {info['documents']}, фрагментов {info['chunks']}, "
              f"эмбеддинги {info['embedder']}")
        paths = [p for s, p in pr.files if s != "D"]
        contexts = retrieve_for_files(Index(index_path), paths) if info["chunks"] else []

        if a.dry_run:
            _write(a.out, build_prompt(pr, contexts) + "\n")
            print(f"Промпт записан в {a.out}: {len(contexts)} фрагментов контекста. Модель не вызывалась.")
            return 0

        llm = make_llm(a.llm, a.model)
        label = f"{a.llm}:{a.model or (DEFAULT_AITUNNEL_MODEL if a.llm == 'aitunnel' else DEFAULT_OLLAMA_MODEL)}"
        result = review(llm, pr, contexts)
        _write(a.out, to_markdown(pr, result, label))
        print(f"Ревью записано в {a.out}: символов промпта {result['prompt_chars']}, "
              f"фрагментов контекста {len(contexts)}, токены {result['usage']}")
        return 0
    except RuntimeError as e:
        print(f"[ошибка] {e}")
        return 1
    finally:
        for p in (index_path, os.path.splitext(index_path)[0] + ".chunks.json"):
            if os.path.exists(p):
                os.remove(p)


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


if __name__ == "__main__":
    sys.exit(main())
