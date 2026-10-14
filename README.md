# Enterprise Educational NAS - Cloud-Native Storage & Collaboration Platform

[![Kubernetes](https://img.shields.io/badge/Orchestration-K3s_Kubernetes-326CE5?style=flat-square&logo=kubernetes&logoColor=white)](https://k3s.io/)
[![GitOps](https://img.shields.io/badge/GitOps-ArgoCD-EF7B4D?style=flat-square&logo=argo&logoColor=white)](https://argo-cd.readthedocs.io/)
[![Filesystem](https://img.shields.io/badge/Storage-OpenZFS-215E9A?style=flat-square&logo=linux&logoColor=white)](https://openzfs.org/)
[![Observability](https://img.shields.io/badge/Observability-Prometheus_%26_Grafana-F46800?style=flat-square&logo=grafana&logoColor=white)](https://grafana.com/)
[![Zero Trust](https://img.shields.io/badge/Ingress-Cloudflare_Zero_Trust-F38020?style=flat-square&logo=cloudflare&logoColor=white)](https://www.cloudflare.com/zero-trust/)
[![OS](https://img.shields.io/badge/Host_OS-Ubuntu_22.04_%2F_24.04_LTS-E95420?style=flat-square&logo=ubuntu&logoColor=white)](https://ubuntu.com/)

Repositorio de **Infraestructura como Código (IaC)** para el despliegue automatizado, operación y observabilidad de un servidor de almacenamiento en red (NAS) y plataforma de colaboración institucional.

La arquitectura reemplaza los servidores de archivos monolíticos tradicionales por un plano de control declarativo basado en **K3s (Kubernetes)** y **ArgoCD**, respaldado a nivel físico por almacenamiento transaccional **OpenZFS** y expuesto de forma segura mediante túneles **Zero Trust** sin apertura de puertos en la red escolar.

---

## Tabla de Contenidos

- [Arquitectura y Topología del Sistema](#arquitectura-y-topología-del-sistema)
- [Especificaciones del Stack Tecnológico](#especificaciones-del-stack-tecnológico)
- [Decisiones de Ingeniería (ADR)](#decisiones-de-ingeniería-adr)
- [Estructura del Repositorio](#estructura-del-repositorio)
- [Guía de Aprovisionamiento e Instalación](#guía-de-aprovisionamiento-e-instalación)
- [Operación, Backups y Recuperación ante Desastres (Runbooks)](#operación-backups-y-recuperación-ante-desastres-runbooks)
- [Autoría](#autoría)

---

## Arquitectura y Topología del Sistema

El sistema se divide en cuatro planos aislados: **Acceso y Seguridad Perimetral**, **Orquestación de Aplicaciones**, **Observabilidad SRE** y **Persistencia en Disco (OpenZFS)**.

```mermaid
flowchart LR
    Users[Usuarios] -->|HTTPS| CF[Cloudflare]
    CF -->|Tunnel| NC[Nextcloud]
    CF -->|Tunnel| SM[Samba]
    NC --> ZFS[(ZFS)]
    SM --> ZFS
    Git[GitHub] -->|Sync| Argo[ArgoCD]
    Argo --> NC
    Argo --> SM
    Prom[Prometheus] --> Graf[Grafana]
    Prom --> NC
