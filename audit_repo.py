"""Auditoria previa al primer push: que se subiria y que no, y por que.

Aplica las reglas del .gitignore de este proyecto de forma explicita (son
pocas y simples) en lugar de depender de git, que no esta instalado. Ademas
revisa los ficheros que SI se subirian buscando rastro de credenciales.

Uso:
    py audit_repo.py
"""

import os
import re
import sys

# Reglas del .gitignore de este proyecto, en orden. Cada una dice por que.
# (patron_es_ignorado, descripcion)
IGNORED_DIRS = {
    "__pycache__": "cache de Python",
    ".venv": "entorno virtual",
    "venv": "entorno virtual",
    "reports": "reportes generados: pueden citar managers de la liga",
    ".git": "metadatos de git",
}

# Dentro de data/ se ignora TODO menos history/.
DATA_KEEP = {"history"}

SECRET_PATTERNS = [
    (re.compile(r"eyJ[A-Za-z0-9_\-]{15,}"), "posible JWT (token de Biwenger)"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"), "cabecera Bearer con valor"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "email"),
    # Una contrasena de aplicacion son 16 letras minusculas, pero tambien lo son
    # muchas palabras en prosa ("previsualizacion"). Se exige que sea un literal
    # entre comillas para no ahogar el informe en falsos positivos.
    (
        re.compile(r"['\"]([a-z]{16})['\"]"),
        "posible contrasena de aplicacion (literal de 16 letras)",
    ),
]

# Valores que aparecen a proposito en codigo, docs y tests: no son secretos.
SAFE_LITERALS = {
    "biwenger-bot@users.noreply.github.com",
    "prueba@gmail.com",
    "uno@gmail.com",
    "dos@gmail.com",
    "a@b.com",
    "c@d.com",
    "e@f.com",
    "tu-cuenta@gmail.com",
    "abcdefghijklmnop",
    # Palabras castellanas de 16 letras que caen en la heuristica. Se listan una
    # a una en vez de dejar de escanear comentarios, porque una credencial
    # comentada sigue estando en el fichero.
    "previsualizacion",
}


def classify(root="."):
    """Devuelve (subir, ignorar) con (ruta, motivo)."""
    subir, ignorar = [], []

    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""

        # Poda de directorios ignorados enteros.
        pruned = []
        for name in list(dirnames):
            if name in IGNORED_DIRS:
                ignorar.append((f"{rel_dir}/{name}/".lstrip("/"), IGNORED_DIRS[name]))
                dirnames.remove(name)
                pruned.append(name)

        # data/: se ignora todo su contenido directo salvo history/
        if rel_dir == "data":
            for name in list(dirnames):
                if name not in DATA_KEEP:
                    ignorar.append(
                        (f"data/{name}/", "regla data/* (volcados, puede llevar managers)")
                    )
                    dirnames.remove(name)

        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}".lstrip("/")

            if name == ".env" or (name.endswith(".env") and name != ".env.example"):
                ignorar.append((rel, "regla .env / *.env (credenciales)"))
                continue
            if re.search(r"\.py[cod]$", name):
                ignorar.append((rel, "regla *.py[cod]"))
                continue
            if name.endswith(".html"):
                ignorar.append(
                    (rel, "regla *.html (previsualizacion con nombres de managers)")
                )
                continue
            if rel_dir == "data":
                ignorar.append((rel, "regla data/* (volcado, puede llevar managers)"))
                continue

            subir.append(rel)

    return sorted(subir), sorted(ignorar)


def scan_secrets(paths):
    """Busca rastro de credenciales en lo que se subiria."""
    hits = []
    for path in paths:
        if os.path.splitext(path)[1] in (".png", ".jpg", ".ico"):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(lines, start=1):
            for pattern, label in SECRET_PATTERNS:
                for found in pattern.finditer(line):
                    match = found.group(1) if found.groups() else found.group(0)
                    if match in SAFE_LITERALS:
                        continue
                    hits.append((path, number, label, match, line.strip()[:90]))
    return hits


def check_history_contents():
    """El histórico solo debe llevar ids y precios: ni un nombre."""
    directory = os.path.join("data", "history")
    if not os.path.isdir(directory):
        return ["(no hay histórico todavia)"]

    import json

    notas = []
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        claves = sorted(payload.keys())
        no_numericas = [
            k for k in (payload.get("prices") or {}) if not str(k).isdigit()
        ]
        notas.append(
            f"{name}: claves={claves}, "
            f"{len(payload.get('prices') or {})} precios, "
            f"claves no numericas={no_numericas or 'ninguna'}"
        )
    return notas


def main():
    subir, ignorar = classify(".")

    print("=" * 78)
    print(f"SE SUBIRIAN AL REPO ({len(subir)} ficheros)")
    print("=" * 78)
    for path in subir:
        size = os.path.getsize(path)
        print(f"  {path:<52} {size:>8,} B".replace(",", "."))

    print()
    print("=" * 78)
    print(f"NO SE SUBIRIAN ({len(ignorar)} entradas)")
    print("=" * 78)
    for path, motivo in ignorar:
        print(f"  {path:<44} {motivo}")

    print()
    print("=" * 78)
    print("CONTENIDO DEL HISTORICO (lo unico de data/ que se sube)")
    print("=" * 78)
    for nota in check_history_contents():
        print(f"  {nota}")

    print()
    print("=" * 78)
    print("BUSQUEDA DE CREDENCIALES EN LO QUE SE SUBIRIA")
    print("=" * 78)
    hits = scan_secrets(subir)
    if not hits:
        print("  Ningun rastro de credenciales.")
    else:
        for path, number, label, match, context in hits:
            print(f"  {path}:{number}  [{label}]  {match!r}")
            print(f"      {context}")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
