#!/usr/bin/env python3
"""Regenera la seccion data: del ConfigMap del portal a partir del codigo del repositorio.

Uso:
    python tools/regen_configmap.py          # reescribe apps/portal-qr/configmap.yaml
    python tools/regen_configmap.py --check  # solo verifica, no escribe
"""
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
CONFIGMAP = RAIZ / "apps/portal-qr/configmap.yaml"

ORDEN = [
    ("server.py", "apps/portal-qr/server.py"),
    ("portal_state.py", "apps/portal-qr/portal_state.py"),
    ("task_engine.py", "apps/portal-qr/task_engine.py"),
    ("nas_agentd.py", "apps/portal-qr/nas_agentd.py"),
    ("modules_catalog.py", "apps/portal-qr/modules_catalog.py"),
    ("auth_rbac.py", "apps/portal-qr/auth_rbac.py"),
    ("index.html", "apps/portal-qr/static/index.html"),
]


def main():
    solo_verificar = "--check" in sys.argv

    if solo_verificar:
        import check_configmap
        return check_configmap.main()

    lineas = CONFIGMAP.read_text(encoding="utf-8").splitlines()
    corte = next(i for i, l in enumerate(lineas) if l.strip() == "data:")

    salida = lineas[: corte + 1]
    for clave, ruta in ORDEN:
        contenido = (RAIZ / ruta).read_text(encoding="utf-8").rstrip("\n")
        salida.append(f"  {clave}: |")
        salida.extend("    " + l if l.strip() else "    " for l in contenido.splitlines())

    CONFIGMAP.write_text("\n".join(salida) + "\n", encoding="utf-8", newline="\n")
    print(f"[OK] ConfigMap regenerado con {len(ORDEN)} archivos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
