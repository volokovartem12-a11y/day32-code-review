"""
День 32. RAG по документации И коду репозитория.

Отличие от дня 31: там индексировалась только документация, здесь — ещё и код, чтобы ревью
видело, как устроен проект: что вызывает изменённый файл, какие правила задают docs/.

Что попадает:
  • документация: *.md, *.txt и всё из папки docs/;
  • код: .py, .js, .ts, .tsx, .jsx, .go, .java, .cs, .cpp, .c, .h, .rb, .php, .kt, .rs, .ps1, .sh, .sql.
Не попадают: .git, node_modules, venv, dist, build, output, vendor и файлы больше 200 КБ.

Индекс собирается заново при каждом запуске ревью: репозиторий маленький, а устаревший индекс
дал бы ревью про старую версию кода.
"""

import os

from chunking import structure_chunks
from embeddings import make_embedder
from index_store import Index, save_index
from loaders import Document, _title

DOC_EXT = {".md": "markdown", ".txt": "text"}
CODE_EXT = {".py": "python", ".js": "text", ".ts": "text", ".tsx": "text", ".jsx": "text", ".go": "text",
            ".java": "text", ".cs": "text", ".cpp": "text", ".c": "text", ".h": "text", ".rb": "text",
            ".php": "text", ".kt": "text", ".rs": "text", ".ps1": "text", ".sh": "text", ".sql": "text"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", "output", "vendor", "logs"}
MAX_FILE_BYTES = 200_000


def _kind(rel: str, name: str):
    ext = os.path.splitext(name)[1].lower()
    parts = rel.split("/")
    if parts[0] == "docs" and ext in DOC_EXT:
        return DOC_EXT[ext]
    if ext in DOC_EXT:
        return DOC_EXT[ext]
    if ext in CODE_EXT:
        return CODE_EXT[ext]
    return None


def collect_repo(root: str) -> list:
    root = os.path.abspath(root)
    docs = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace("\\", "/")
            kind = _kind(rel, name)
            if kind is None or os.path.getsize(path) > MAX_FILE_BYTES:
                continue
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                text = f.read().replace("\r\n", "\n").replace("\r", "\n").strip()
            if text:
                docs.append(Document(rel, name, kind, _title(kind, text, name), text))
    return docs


def build_repo_index(root: str, index_path: str, backend: str = "local", model: str = None) -> dict:
    docs = collect_repo(root)
    chunks = [c for d in docs for c in structure_chunks(d)]
    if not chunks:
        return {"documents": 0, "chunks": 0, "sources": [], "embedder": None, "index": index_path}
    # В вектор попадают путь и раздел: запрос про «платёж» находит payments/charge.py по пути
    texts = [f"{c.source} {c.section}\n{c.text}" for c in chunks]
    embedder = make_embedder(backend, model).fit(texts)
    vectors = embedder.embed(texts)
    save_index(index_path, chunks, vectors, {
        "strategy": "structure",
        "project": os.path.basename(root),
        "documents": [d.source for d in docs],
        "embedder": embedder.state(),
    })
    return {"documents": len(docs), "chunks": len(chunks), "sources": [d.source for d in docs],
            "embedder": embedder.label, "index": index_path}


def retrieve_for_files(index: Index, paths: list, per_file: int = 3, limit: int = 12) -> list:
    """Для каждого изменённого файла — лучшие фрагменты ДРУГИХ файлов (сам файл уже есть в diff)."""
    picked, seen = [], set()
    for path in paths:
        query = f"{path} " + os.path.splitext(os.path.basename(path))[0].replace("_", " ")
        got = 0
        for hit in index.search(query, k=per_file * 4 + len(paths)):
            if hit["source"] in paths or hit["chunk_id"] in seen:
                continue
            seen.add(hit["chunk_id"])
            picked.append(hit)
            got += 1
            if got >= per_file or len(picked) >= limit:
                break
        if len(picked) >= limit:
            break
    return picked
