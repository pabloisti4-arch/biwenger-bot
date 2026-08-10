"""Inspector de estructura de los JSON guardados con --save.

Solo lee ficheros locales: no toca la red ni usa el token.
Imprime el esquema (claves, tipos, ejemplos) redactando datos personales,
para poder razonar sobre la forma de la API sin volcar la liga entera.

Uso:
    py inspect_data.py                 # esquema de todos los ficheros de .\\data\\
    py inspect_data.py market          # solo los que contengan "market"
    py inspect_data.py --depth=6
"""

import glob
import json
import os
import sys

# Claves cuyo valor no se imprime: datos personales de la liga o secretos.
SENSITIVE = {
    "email", "name", "icon", "avatar", "image", "token", "password",
    "phone", "ip", "apikey", "key", "secret", "nickname", "username",
}

MAX_LIST_SAMPLE = 1


def redact(key, value):
    if key and key.lower() in SENSITIVE:
        if isinstance(value, str):
            return f"<{len(value)} chars redactado>"
        return "<redactado>"
    if isinstance(value, str) and len(value) > 60:
        return value[:57] + "..."
    return value


def describe(node, depth, max_depth, key=None, indent=0):
    pad = "  " * indent

    if depth > max_depth:
        print(f"{pad}... (profundidad maxima)")
        return

    if isinstance(node, dict):
        if not node:
            print(f"{pad}{{}} (dict vacio)")
            return
        # Si es un mapa id -> objeto homogeneo, describir solo un representante.
        if len(node) > 8 and _looks_like_id_map(node):
            sample_key = next(iter(node))
            print(f"{pad}dict de {len(node)} entradas id->objeto, ejemplo [{sample_key}]:")
            describe(node[sample_key], depth + 1, max_depth, indent=indent + 1)
            return
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                kind = "dict" if isinstance(v, dict) else f"list[{len(v)}]"
                print(f"{pad}{k}: {kind}")
                describe(v, depth + 1, max_depth, key=k, indent=indent + 1)
            else:
                print(f"{pad}{k}: {type(v).__name__} = {redact(k, v)!r}")
        return

    if isinstance(node, list):
        if not node:
            print(f"{pad}[] (lista vacia)")
            return
        types = {type(x).__name__ for x in node}
        print(f"{pad}(tipos: {', '.join(sorted(types))}) primer elemento:")
        for item in node[:MAX_LIST_SAMPLE]:
            describe(item, depth + 1, max_depth, indent=indent + 1)
        return

    print(f"{pad}{type(node).__name__} = {redact(key, node)!r}")


def _looks_like_id_map(node):
    return all(k.isdigit() for k in list(node.keys())[:10] if isinstance(k, str))


def field_frequency(items):
    """Con que frecuencia aparece cada campo en una lista de objetos.

    Distingue campos garantizados de opcionales: clave para no escribir
    codigo que asuma presencia.
    """
    counts = {}
    nulls = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for k, v in item.items():
            counts[k] = counts.get(k, 0) + 1
            if v is None:
                nulls[k] = nulls.get(k, 0) + 1
    total = sum(1 for i in items if isinstance(i, dict))
    print(f"  ({total} objetos)")
    for k in sorted(counts, key=lambda x: (-counts[x], x)):
        flag = "" if counts[k] == total else "  <-- OPCIONAL"
        nul = f", {nulls[k]} nulos" if k in nulls else ""
        print(f"    {k:<16} {counts[k]}/{total}{nul}{flag}")


def main(argv):
    depth = 5
    filters = []
    for arg in argv:
        if arg.startswith("--depth="):
            depth = int(arg.split("=", 1)[1])
        else:
            filters.append(arg)

    paths = sorted(glob.glob(os.path.join("data", "*.json")))
    if filters:
        paths = [p for p in paths if any(f in os.path.basename(p) for f in filters)]
    if not paths:
        print("No hay ficheros en .\\data\\ (ejecuta antes: py main.py --save)")
        return 1

    for path in paths:
        print("=" * 78)
        print(os.path.basename(path))
        print("=" * 78)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        describe(data, 0, depth)

        # Extra: frecuencia de campos en las listas interesantes.
        if isinstance(data, dict):
            for key in ("sales", "offers"):
                value = data.get(key)
                if isinstance(value, list) and value:
                    print(f"\n-- frecuencia de campos en '{key}' --")
                    field_frequency(value)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
