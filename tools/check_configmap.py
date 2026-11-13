#!/usr/bin/env python3
"""Comprueba que el ConfigMap del portal contenga exactamente el codigo del repositorio.

El contenedor no ejecuta apps/portal-qr/*.py: ejecuta lo que esta embebido en
apps/portal-qr/configmap.yaml. Si ambos se separan, el despliegue corre codigo
viejo sin que nadie lo note. Este chequeo corre en CI y falla si hay diferencias.
"""
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
CONFIGMAP = RAIZ / "apps/portal-qr/configmap.yaml"

FUENTES = {
    "server.py": "apps/portal-qr/server.py",
    "portal_state.py": "apps/portal-qr/portal_state.py",
    "task_engine.py": "apps/portal-qr/task_engine.py",
    "nas_agentd.py": "apps/portal-qr/nas_agentd.py",
    "modules_catalog.py": "apps/portal-qr/modules_catalog.py",
    "auth_rbac.py": "apps/portal-qr/auth_rbac.py",
    "index.html": "apps/portal-qr/static/index.html",
}


def normalizar(texto):
    return [linea.rstrip() for linea in texto.splitlines()]


def main():
    if not CONFIGMAP.exists():
        print(f"[ERROR] No se encontro {CONFIGMAP}")
        return 1

    data = yaml.safe_load(CONFIGMAP.read_text(encoding="utf-8")).get("data", {})
    fallos = []

    for clave, ruta in FUENTES.items():
        archivo = RAIZ / ruta
        if not archivo.exists():
            fallos.append(f"falta el archivo {ruta}")
            continue
        if clave not in data:
            fallos.append(f"el ConfigMap no incluye la clave '{clave}'")
            continue
        if normalizar(data[clave]) != normalizar(archivo.read_text(encoding="utf-8")):
            fallos.append(f"'{clave}' difiere de {ruta}")

    sobrantes = set(data) - set(FUENTES)
    if sobrantes:
        fallos.append(f"el ConfigMap tiene claves sin archivo de origen: {sorted(sobrantes)}")

    if fallos:
        print("[FALLA] El ConfigMap no coincide con el codigo del repositorio:")
        for f in fallos:
            print(f"  - {f}")
        print("\nRegenere el ConfigMap con: python tools/regen_configmap.py")
        return 1

    print(f"[OK] ConfigMap sincronizado con los {len(FUENTES)} archivos del portal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
