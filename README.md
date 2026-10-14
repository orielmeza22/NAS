# 🏫 NAS Cloud-Native Escolar

Infraestructura de almacenamiento y colaboración moderna, escalable y 100% automatizada mediante GitOps. Diseñada para entornos educativos, combina la robustez de ZFS con la agilidad de Kubernetes.

## 🏗️ Arquitectura

- **Orquestación:** K3s (Kubernetes ligero)
- **GitOps:** ArgoCD (Sincronización declarativa)
- **Almacenamiento:** ZFS con snapshots automáticos vía CronJob
- **Aplicaciones:** Nextcloud, Samba, Portal QR
- **Monitorización:** Prometheus + Grafana
- **Acceso Remoto:** Cloudflare Tunnel (Zero Trust)

##  Despliegue Rápido

### Prerrequisitos
- Ubuntu 22.04 / 24.04
- K3s instalado
- ZFS instalado

### Instalación

```bash
# Clonar el repositorio
git clone https://github.com/orielmeza22/NAS.git
cd NAS

# Instalar ArgoCD
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# Aplicar la configuración
kubectl apply -f apps/
kubectl apply -f infrastructure/
