# Enterprise Educational NAS — Cloud-Native Storage & Collaboration Platform

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

1. [Arquitectura y Topología del Sistema](#1-arquitectura-y-topología-del-sistema)
2. [Especificaciones del Stack Tecnológico](#2-especificaciones-del-stack-tecnológico)
3. [Decisiones de Ingeniería (ADR)](#3-decisiones-de-ingeniería-adr)
4. [Estructura del Repositorio](#4-estructura-del-repositorio)
5. [Guía de Aprovisionamiento e Instalación](#5-guía-de-aprovisionamiento-e-instalación)
6. [Operación, Backups y Recuperación ante Desastres (Runbooks)](#6-operación-backups-y-recuperación-ante-desastres-runbooks)
7. [Autoría](#7-autoría)

---

## 1. Arquitectura y Topología del Sistema

El sistema se divide en cuatro planos aislados: **Acceso y Seguridad Perimetral**, **Orquestación de Aplicaciones**, **Observabilidad SRE** y **Persistencia en Disco (OpenZFS)**.

```mermaid
graph TB
    subgraph WAN ["Red Externa (Internet)"]
        RemoteUser["Usuarios Remotos (Docentes / Alumnos)"]
        CFEdge["Cloudflare Edge Network (TLS 1.3 / Zero Trust)"]
    end

    subgraph LAN ["Red Local Institucional (LAN)"]
        LANClient["Equipos de Laboratorio (Windows / Linux)"]
        MobileQR["Dispositivos Móviles (Escaneo QR)"]
        GitAdmin["Ingeniero DevOps / Push a GitHub"]
    end

    subgraph Server ["Servidor Base (Ubuntu Server 22.04 / 24.04 LTS)"]
        subgraph K3s ["Clúster Kubernetes (K3s)"]
            subgraph GitOpsPlane ["Plano de Control GitOps"]
                Argo["ArgoCD Controller"]
            end

            subgraph Workloads ["Capa de Servicios (apps/)"]
                Tunnel["Cloudflared Tunnel"]
                Nextcloud["Nextcloud (Nube Privada)"]
                Samba["Samba Server (SMB/CIFS)"]
                QRPortal["Portal Web QR (Nginx)"]
            end

            subgraph InfraPlane ["Observabilidad y Tareas (infrastructure/)"]
                Prometheus["Prometheus TSDB"]
                Grafana["Grafana (Dashboard NAS Monitor)"]
                CronZFS["CronJob (Snapshot Automático Diario)"]
            end
        end

        subgraph StorageLayer ["Subsistema de Almacenamiento Físico"]
            ZFSPool[("Pool OpenZFS\n(Datasets + Checksums + Snapshots CoW)")]
        end
    end

    RemoteUser -->|HTTPS Cifrado| CFEdge
    CFEdge -->|Túnel Saliente Seguro| Tunnel
    Tunnel -->|Proxy Interno| Nextcloud
    LANClient -->|Protocolo SMB :445| Samba
    MobileQR -->|HTTP Local| QRPortal
    QRPortal -.->|Redirección Dinámica| CFEdge
    GitAdmin -->|Commit YAML| Argo
    Argo -->|Sincronización Continua| Workloads & InfraPlane
    Nextcloud & Samba -->|Volúmenes Persistentes| ZFSPool
    CronZFS -->|Ejecución Programada| ZFSPool
    Prometheus -->|Recolección de Métricas| Workloads & ZFSPool
    Grafana -->|Consulta PromQL| Prometheus
EOS
