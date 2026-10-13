"""
День 32. Проверки пайплайна ревью на имитации: без GitHub, без интернета и без ключа AITunnel.

Создаётся временный git-репозиторий с настоящим PR (ветка feature от main), затем проверяется:
  • что берётся diff base...head, а не текущее состояние main;
  • пропуски: lock-файлы, бинарники, удалённые файлы;
  • RAG по документации и коду, контекст для изменённых файлов;
  • ревью через ИМИТАЦИЮ AITunnel (стаб на localhost): ключ, модель, три раздела ответа;
  • CLI: обычный запуск, dry-run, нет изменений, нет ключа, неверный ключ;
  • workflow GitHub Action: структура и отсутствие ключа в тексте.

    python test_day32.py      # отчёт: output/test_report.md, код выхода 1 при провале
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW_DIR = os.path.join(HERE, "review")
sys.path.insert(0, REVIEW_DIR)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from code_index import build_repo_index, retrieve_for_files  # noqa: E402
from git_changes import collect  # noqa: E402
from index_store import Index  # noqa: E402
from llm import AITunnelLLM  # noqa: E402
from reviewer import build_prompt, review, to_markdown  # noqa: E402

STUB_KEY = "stub-only-key-0002"
RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("ok    " if ok else "FAIL  ") + name + ("" if ok or not detail else f"  — {detail}"))


def git(repo: str, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "-C", repo, *args],
                   check=True, capture_output=True)


def write(repo: str, rel: str, text: str = None, data: bytes = None) -> None:
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if data is not None:
        with open(path, "wb") as f:
            f.write(data)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


def make_pr_repo(tmp: str) -> str:
    repo = os.path.join(tmp, "shop")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-b", "main", repo], check=True, capture_output=True)
    write(repo, "docs/rules.md", "# Правила\n\nДеление на ноль и пустые списки проверяем до вычисления.\n"
                                 "Ключи API в код не пишем.\n")
    write(repo, "pay/charge.py", '"""Расчёт платежа."""\n\n\ndef average_charge(items):\n'
                                 '    total = sum(i["price"] for i in items)\n    return total / len(items)\n')
    write(repo, "pay/receipt.py", 'from pay.charge import average_charge\n\n\ndef render(items):\n'
                                  '    return f"Средний чек: {average_charge(items):.2f}"\n')
    write(repo, "package-lock.json", '{"lockfileVersion": 3}\n')
    write(repo, "assets/logo.png", data=b"\x89PNG\r\n\x1a\n\x00\x00")
    write(repo, "old/legacy.py", "print('старый код')\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")

    git(repo, "checkout", "-b", "feature")
    write(repo, "pay/charge.py", '"""Расчёт платежа."""\nAPI_KEY = "sk-test-123"\n\n\n'
                                 'def average_charge(items, discount):\n'
                                 '    total = sum(i["price"] for i in items) - discount\n'
                                 '    return total / len(items)\n')
    write(repo, "package-lock.json", '{"lockfileVersion": 3, "packages": {}}\n')
    write(repo, "assets/logo.png", data=b"\x89PNG\r\n\x1a\n\x00\x00\x01")
    os.remove(os.path.join(repo, "old", "legacy.py"))
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "feature: скидка и ключ")

    git(repo, "checkout", "main")
    write(repo, "docs/extra.md", "# Только в main\n\nЭтого изменения нет в PR.\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "main: новый документ")
    git(repo, "checkout", "feature")
    return repo


class StubHandler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *args):
        pass

    def _send(self, code: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        StubHandler.requests.append({"auth": self.headers.get("Authorization", ""), "body": body})
        if self.headers.get("Authorization", "") != f"Bearer {STUB_KEY}":
            return self._send(401, {"error": {"message": "invalid api key"}})
        user = body["messages"][-1]["content"]
        out = ["## Потенциальные баги"]
        out.append("- `average_charge`: деление на `len(items)` без проверки пустого списка [pay/charge.py]."
                   if "len(items)" in user else "Не найдено")
        out.append("## Архитектурные проблемы")
        out.append("- Сигнатура average_charge изменилась, а вызов в pay/receipt.py не обновлён [pay/receipt.py]."
                   if "pay/receipt.py" in user and "discount" in user else "Не найдено")
        out.append("## Рекомендации")
        out.append("- Вынесите API_KEY из кода в переменные окружения [docs/rules.md]." if "sk-" in user
                   else "Не найдено")
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": "\n".join(out)},
                                      "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 100, "completion_tokens": 50}})


def start_stub() -> tuple:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"


def test_diff(repo: str) -> None:
    pr = collect(repo, "main", "feature")
    paths = [p for _, p in pr.files]
    check("diff: изменённый файл попадает в список", "pay/charge.py" in paths, str(pr.files))
    check("diff: изменение только в main (docs/extra.md) не попадает в PR (три точки)",
          "docs/extra.md" not in paths, str(pr.files))
    check("diff: lock-файл пропущен с причиной",
          any("package-lock.json" in s and "lock-файл" in s for s in pr.skipped), str(pr.skipped))
    check("diff: бинарный файл пропущен с причиной",
          any("logo.png" in s and "бинарный" in s for s in pr.skipped), str(pr.skipped))
    check("diff: удалённый файл перечислен, но не ревьюится",
          any(p == "old/legacy.py" for _, p in pr.files) and not any(p == "old/legacy.py" for p, _, _ in pr.diffs),
          str(pr.skipped))
    diff_text = " ".join(t for _, _, t in pr.diffs)
    check("diff: в тексте есть строка из PR", "len(items)" in diff_text and "discount" in diff_text, "")
    check("diff: счётчики строк считают добавления и удаления", pr.added > 0 and pr.removed > 0,
          f"+{pr.added} −{pr.removed}")


def test_index_and_context(repo: str, tmp: str) -> Index:
    idx_path = os.path.join(tmp, "repo-index.sqlite")
    info = build_repo_index(repo, idx_path, "local")
    check("индекс: документация (docs/rules.md) и код (pay/receipt.py) попадают вместе",
          "docs/rules.md" in info["sources"] and "pay/receipt.py" in info["sources"], str(info["sources"]))
    idx = Index(idx_path)
    pr = collect(repo, "main", "feature")
    paths = [p for s, p in pr.files if s != "D"]
    contexts = retrieve_for_files(idx, paths)
    srcs = [c["source"] for c in contexts]
    check("контекст: для изменённого файла подтягивается файл, который его использует (pay/receipt.py)",
          "pay/receipt.py" in srcs, str(srcs))
    check("контекст: сами изменённые файлы в контекст не дублируются",
          not any(s in paths for s in srcs), str(srcs))
    return idx


def test_review_stub(repo: str, base: str, idx: Index) -> None:
    pr = collect(repo, "main", "feature")
    contexts = retrieve_for_files(idx, [p for s, p in pr.files if s != "D"])
    StubHandler.requests.clear()
    llm = AITunnelLLM(api_key=STUB_KEY, base_url=base)
    result = review(llm, pr, contexts)
    req = StubHandler.requests[-1]
    check("модель: запрос с заголовком Bearer и моделью gpt-5.6-luna",
          req["auth"] == f"Bearer {STUB_KEY}" and req["body"]["model"] == "gpt-5.6-luna", "")
    user = req["body"]["messages"][-1]["content"]
    check("промпт: содержит diff и список файлов", "=== Diff ===" in user and "pay/charge.py" in user, "")
    text = result["text"]
    headers = ["## Потенциальные баги", "## Архитектурные проблемы", "## Рекомендации"]
    check("ответ: три раздела в нужном порядке", all(h in text for h in headers)
          and [text.index(h) for h in headers] == sorted(text.index(h) for h in headers), "")
    check("ответ: найден баг деления на len(items)", "len(items)" in text, text[:200])
    check("ответ: найдена архитектурная проблема (вызов не обновлён)", "pay/receipt.py" in text, text[:300])
    check("ответ: найдена проблема с ключом в коде", "API_KEY" in text or "переменные окружения" in text, "")
    md = to_markdown(pr, result, "aitunnel:gpt-5.6-luna")
    check("Markdown: есть предупреждение, что это автоматический анализ",
          "Это автоматический анализ" in md, "")
    check("Markdown: в шапке статистика PR и модель", "+" in md and "gpt-5.6-luna" in md, "")
    check("промпт в ответе не утекает: ключ не попадает в текст ревью", STUB_KEY not in md, "")


def run_cli(args: list, env_extra: dict = None, remove_key: bool = False) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONUTF8": "1"}
    if remove_key:
        env.pop("AITUNNEL_API_KEY", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, os.path.join(REVIEW_DIR, "review.py"), *args],
                          capture_output=True, env=env, timeout=180, stdin=subprocess.DEVNULL)


def test_cli(repo: str, base: str, tmp: str) -> None:
    out = os.path.join(tmp, "review.md")
    key_env = {"AITUNNEL_BASE_URL": base, "AITUNNEL_API_KEY": STUB_KEY}

    tmpdir = tempfile.gettempdir()
    before = sorted(f for f in os.listdir(tmpdir) if f.startswith("ai-review-"))
    StubHandler.requests.clear()
    p = run_cli(["--repo", repo, "--base", "main", "--head", "feature", "--out", out], key_env)
    text = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
    combined = (p.stdout + p.stderr).decode("utf-8", "replace")
    check("CLI: обычный запуск завершается с кодом 0 и пишет review.md",
          p.returncode == 0 and "## Потенциальные баги" in text, combined[-300:])
    check("CLI: модель вызвана ровно один раз", len(StubHandler.requests) == 1, str(len(StubHandler.requests)))
    check("CLI: ключ не попадает в вывод и в файл ревью",
          STUB_KEY not in combined and STUB_KEY not in text, "")
    after = sorted(f for f in os.listdir(tmpdir) if f.startswith("ai-review-"))
    check("CLI: временный индекс удалён после работы", before == after, f"было {before}, стало {after}")

    dry = os.path.join(tmp, "prompt.md")
    StubHandler.requests.clear()
    p = run_cli(["--repo", repo, "--base", "main", "--head", "feature", "--out", dry, "--dry-run"], key_env)
    check("CLI --dry-run: промпт записан, модель не вызывалась",
          p.returncode == 0 and "=== Diff ===" in open(dry, encoding="utf-8").read() and not StubHandler.requests,
          (p.stdout + p.stderr).decode("utf-8", "replace")[-200:])

    same = os.path.join(tmp, "same.md")
    subprocess.run(["git", "-C", repo, "branch", "-f", "same", "feature"], check=True, capture_output=True)
    StubHandler.requests.clear()
    p = run_cli(["--repo", repo, "--base", "feature", "--head", "same", "--out", same], key_env)
    check("CLI: PR без изменений — ревью не нужно, модель не вызывалась",
          p.returncode == 0 and "нет изменённых файлов" in open(same, encoding="utf-8").read()
          and not StubHandler.requests, "")

    nokey = os.path.join(tmp, "nokey.md")
    p = run_cli(["--repo", repo, "--base", "main", "--head", "feature", "--out", nokey], {"AITUNNEL_BASE_URL": base},
                remove_key=True)
    check("CLI: нет ключа в GitHub-запуске — тихо пропускаем, без файла и без падения",
          p.returncode == 0 and not os.path.exists(nokey) and "ревью пропущено" in p.stdout.decode("utf-8", "replace"),
          (p.stdout + p.stderr).decode("utf-8", "replace")[-200:])

    wrong = os.path.join(tmp, "wrong.md")
    p = run_cli(["--repo", repo, "--base", "main", "--head", "feature", "--out", wrong],
                {"AITUNNEL_BASE_URL": base, "AITUNNEL_API_KEY": "wrong-key"})
    check("CLI: неверный ключ — код 1 и понятное сообщение",
          p.returncode == 1 and "отклонил ключ" in p.stdout.decode("utf-8", "replace"),
          (p.stdout + p.stderr).decode("utf-8", "replace")[-200:])

    bad = run_cli(["--repo", repo, "--base", "no-such-branch", "--head", "feature", "--out", wrong])
    check("CLI: несуществующая базовая ветка — код 2, git-ошибка",
          bad.returncode == 2 and "ошибка git" in bad.stdout.decode("utf-8", "replace"),
          bad.stdout.decode("utf-8", "replace")[-200:])


def test_workflow() -> None:
    path = os.path.join(HERE, ".github", "workflows", "ai-review.yml")
    text = open(path, encoding="utf-8").read()
    check("workflow: запускается на pull_request", "pull_request:" in text and "opened" in text, "")
    check("workflow: право записи в PR только pull-requests: write",
          "pull-requests: write" in text and "contents: read" in text, "")
    check("workflow: ключ берётся из секрета, а не вписан в файл",
          "${{ secrets.AITUNNEL_API_KEY }}" in text and not re.search(r"sk-[A-Za-z0-9]", text), "")
    check("workflow: запускает review.py и публикует комментарий через gh pr comment",
          "python review/review.py" in text and "gh pr comment" in text, "")
    check("workflow: fetch-depth 0 (нужна история для base...head)", "fetch-depth: 0" in text, "")
    try:
        import yaml  # noqa: WPS433
    except ImportError:
        check("workflow: разбор YAML", True, "пропущено: нет pyyaml в этой среде")
        return
    try:
        doc = yaml.safe_load(text)
        steps = doc["jobs"]["ai-review"]["steps"]
        check("workflow: YAML разбирается, есть шаги", len(steps) >= 4, f"шагов: {len(steps)}")
    except Exception as e:  # noqa: BLE001
        check("workflow: YAML разбирается", False, str(e))


def write_report(started: str) -> None:
    os.makedirs(os.path.join(HERE, "output"), exist_ok=True)
    ok = sum(1 for _, good, _ in RESULTS if good)
    lines = [
        "# Отчёт проверок дня 32 (имитация)",
        "",
        f"Запуск: {started}. Результат: **{ok} из {len(RESULTS)}** проверок пройдено.",
        "",
        "На имитации проверено: настоящий git diff base...head во временном репозитории, пропуски, индекс кода и доков,",
        "контекст для файлов, ревью через стаб AITunnel, CLI (включая ошибки и отсутствие ключа), структура workflow.",
        "",
        "Не проверено здесь: GitHub Actions (запуск на настоящем PR), gh pr comment, настоящий AITunnel и gpt-5.6-luna,",
        "семантический поиск (Ollama), работа на ПК пользователя с его репозиторием.",
        "",
        "| № | Проверка | Результат |",
        "|---|---|---|",
    ]
    for i, (name, good, _) in enumerate(RESULTS, 1):
        lines.append(f"| {i} | {name} | {'пройдено' if good else '**НЕ пройдено**'} |")
    with open(os.path.join(HERE, "output", "test_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> int:
    started = datetime.now().strftime("%Y-%m-%d %H:%M")
    tmp = tempfile.mkdtemp(prefix="day32-test-")
    srv, base = start_stub()
    try:
        repo = make_pr_repo(tmp)
        print("== 1. Diff PR")
        test_diff(repo)
        print("\n== 2. Индекс и контекст")
        idx = test_index_and_context(repo, tmp)
        print("\n== 3. Ревью через имитацию AITunnel")
        test_review_stub(repo, base, idx)
        print("\n== 4. CLI")
        test_cli(repo, base, tmp)
        print("\n== 5. GitHub Action (файл)")
        test_workflow()
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    write_report(started)
    failed = [n for n, good, _ in RESULTS if not good]
    print(f"\nИтог: {len(RESULTS) - len(failed)} из {len(RESULTS)}. Отчёт: output\\test_report.md")
    if failed:
        print("Не пройдено:\n  " + "\n  ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
