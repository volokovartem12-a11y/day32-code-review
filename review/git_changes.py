"""
День 32. Что изменил PR: список файлов, diff по каждому файлу и лимиты.

Берём diff от базовой ветки до head: `git diff base...head`. Три точки важны: берётся
общий предок, а не текущее состояние base, поэтому чужие изменения в main не попадают в ревью.

Пропускаются: lock-файлы, бинарники и удалённые файлы (их не ревьюим, но перечисляем).
Лимиты: diff одного файла и весь diff обрезаются, а что не влезло — записывается в «пропущено».
"""

import os
import subprocess
from dataclasses import dataclass, field

SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock",
              "go.sum", "Cargo.lock", "composer.lock"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar", ".7z",
            ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".mp4", ".sqlite", ".pyc", ".exe", ".dll", ".so", ".bin"}
MAX_FILE_DIFF_CHARS = 12_000
MAX_TOTAL_DIFF_CHARS = 60_000


class GitError(RuntimeError):
    pass


@dataclass
class PRDiff:
    files: list                      # [(status, path)] — как в git: M, A, D, R…
    diffs: list = field(default_factory=list)     # [(path, status, text)] — то, что уходит в ревью
    skipped: list = field(default_factory=list)   # строки «путь (причина)»
    added: int = 0
    removed: int = 0


def git(repo: str, *args: str) -> str:
    try:
        p = subprocess.run(["git", "-C", repo, *args], capture_output=True, timeout=120)
    except FileNotFoundError as e:
        raise GitError("git не найден в PATH") from e
    if p.returncode != 0:
        raise GitError(f"git {args[0]}: {p.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return p.stdout.decode("utf-8", "replace")


def changed_files(repo: str, base: str, head: str) -> list:
    out = git(repo, "diff", "--name-status", "-M", f"{base}...{head}")
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0][:1], parts[-1]))  # у переименования путь нового файла — последний
    return rows


def skip_reason(path: str, status: str):
    name = os.path.basename(path)
    if name in SKIP_NAMES:
        return "lock-файл"
    if os.path.splitext(name)[1].lower() in SKIP_EXT:
        return "бинарный файл"
    if status == "D":
        return "файл удалён"
    return None


def collect(repo: str, base: str, head: str) -> PRDiff:
    files = changed_files(repo, base, head)
    result = PRDiff(files=files)
    total = 0
    for status, path in files:
        reason = skip_reason(path, status)
        if reason:
            result.skipped.append(f"{path} ({reason})")
            continue
        text = git(repo, "diff", f"{base}...{head}", "-U3", "--no-color", "--", path)
        if len(text) > MAX_FILE_DIFF_CHARS:
            text = text[:MAX_FILE_DIFF_CHARS] + "\n… diff файла обрезан по лимиту"
        if total + len(text) > MAX_TOTAL_DIFF_CHARS:
            result.skipped.append(f"{path} (не вошёл в общий лимит diff)")
            continue
        total += len(text)
        result.diffs.append((path, status, text))
        for line in text.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                result.added += 1
            elif line.startswith("-") and not line.startswith("---"):
                result.removed += 1
    return result
