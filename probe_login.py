"""Sonda del login programatico: 1 POST (auth) + 1 GET (/account).

Comprueba que email+contrasena dan un token valido y que de ahi se deducen
liga, usuario y sistema de puntuacion. Es la pieza de la que depende el cron
de GitHub Actions, asi que conviene verificarla a mano antes.

Ignora BIWENGER_TOKEN a proposito: aqui se prueba el login, no el token manual.

Uso:
    py probe_login.py --dry-run    # muestra que se va a pedir, sin pedirlo
    py probe_login.py              # hace el login
"""

import argparse
import os
import sys

import config
from auth import ACCOUNT_URL, LOGIN_URL, LoginError, bootstrap


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sonda del login programatico")
    parser.add_argument("--dry-run", action="store_true",
                        help="mostrar las peticiones y salir sin hacerlas")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    email = os.environ.get("BIWENGER_EMAIL", "").strip()
    password = os.environ.get("BIWENGER_PASSWORD", "")
    # El dry-run es introspeccion: debe poder verse sin tener nada definido.
    if not args.dry_run and (not email or not password.strip()):
        print(
            "Faltan BIWENGER_EMAIL y/o BIWENGER_PASSWORD.\n"
            "En PowerShell, solo para esta sesion:\n"
            '  $env:BIWENGER_EMAIL = Read-Host "email de Biwenger"\n'
            '  $env:BIWENGER_PASSWORD = Read-Host "contrasena de Biwenger"',
            file=sys.stderr,
        )
        return 2

    cfg = {
        "mode": "password",
        "email": email,
        "password": password,
        "league": os.environ.get("BIWENGER_LEAGUE", "").strip(),
        "user": os.environ.get("BIWENGER_USER", "").strip(),
        "token": "",
        **config._optionals(),
    }

    print("=" * 72)
    print("SONDA DE LOGIN PROGRAMATICO")
    print("=" * 72)
    print(f"  POST {LOGIN_URL}")
    print(f"       cuerpo: {{'email': '{email or '<BIWENGER_EMAIL>'}', "
          "'password': '<oculta>'}")
    print(f"  GET  {ACCOUNT_URL}")
    print(f"  X-Version: {cfg['version']}")
    print("  peticiones: 1 POST (solo autenticacion) + 1 GET")
    print("  NINGUNA accion sobre la liga.")
    print()

    if args.dry_run:
        print("--dry-run: no se ha hecho ninguna peticion.")
        return 0

    try:
        resolved, avisos = bootstrap(cfg, verbose=True)
    except LoginError as exc:
        print(f"\nERROR de login:\n{exc}", file=sys.stderr)
        return 8

    print()
    print("LOGIN CORRECTO. Valores deducidos:")
    print(f"  liga:        {resolved['league']}")
    print(f"  usuario:     {resolved['user']}")
    print(f"  competicion: {resolved['competition']}")
    print(f"  score:       {resolved['score']}")
    print(f"  token:       {config.mask(resolved['token'])}")
    for aviso in avisos:
        print(f"  AVISO: {aviso}")
    print("\nEl cron de GitHub Actions puede usar este modo sin token manual.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
