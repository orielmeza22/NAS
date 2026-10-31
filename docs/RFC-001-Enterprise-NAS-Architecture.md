# RFC-001: Enterprise Distributed NAS & Data Governance Platform
## Specification, Architecture Decision Record (ADR), and Disaster Recovery Standard

```text
Document Identifier: RFC-001-NAS-ENT
Category: Standards Track / Architecture Design Record
Status: Approved / Production-Ready
Author: Oriel Meza <orielmeza@gmail.com>
Distribution: Enterprise Engineering & Infrastructure Operations
Publication Date: October 2026
Standard Compliance: ISO/IEC/IEEE 42010:2011, NIST SP 800-34 Rev. 1, ISO/IEC 27001:2022
```

---

## Abstract

This document defines the architectural specification, security controls, and operational governance for an Enterprise-grade Network Attached Storage (NAS) and Data Collaboration Platform. The system delivers unified multi-protocol file access (SMB 3.1.1 and WebDAV/HTTPS) decoupled from proprietary hardware dependencies. It employs an immutable, copy-on-write storage backend (OpenZFS), a declarative GitOps reconciliation engine (ArgoCD on CNCF-certified K3s), zero-trust ingress networking (Cloudflare Tunnel), asymmetric cryptographic secret management (Bitnami Sealed Secrets), and an automated 3-2-1 Disaster Recovery pipeline targeting an offsite, zero-egress cloud object store (Cloudflare R2).

---

## Normative Language Notice (RFC 2119 & RFC 8174)

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHALL**, **SHALL NOT**, **SHOULD**, **SHOULD NOT**, **RECOMMENDED**, **NOT RECOMMENDED**, **MAY**, and **OPTIONAL** in this document are to be interpreted as described in BCP 14 [RFC 2119] [RFC 8174] when, and only when, they appear in all capitals, as shown here.

---

## 1. Context, Motivation, and Problem Statement

### 1.1 Legacy Shortcomings
Small and medium enterprises (SMEs) and educational institutions traditionally rely on two suboptimal storage architectures:
1. **Proprietary Hardware Appliances (Synology, QNAP):** Exhibit vendor lock-in, proprietary RAID formats, closed firmware vulnerable to zero-day supply chain attacks, and recurring licensing fees for enterprise surveillance and backup features.
2. **Unmanaged Bare-Metal Windows/Linux File Servers:** Lack reproducible infrastructure definitions, rely on manual snapshotting, and present wide attack surfaces through exposed port forwarding (SMB 445 / RDP 3389) subject to automated ransomware exploitation.

### 1.2 Architectural Thesis
An enterprise storage platform **MUST** be defined entirely as software (Software-Defined Storage - SDS) and governed by version-controlled declarations (Infrastructure as Code - IaC). Hardware **SHALL** be treated as disposable, commodity compute/storage nodes capable of cold-start rehydration within defined Recovery Time Objectives (RTO).

---

## 2. Goals and Non-Goals

### 2.1 Goals
- **G-1 (Multi-Protocol Access):** Provide wire-speed local area network file sharing via SMB 3.1.1 and global, mobile-first WebDAV/Cloud collaboration via Nextcloud.
- **G-2 (Declarative GitOps):** All Kubernetes manifests, Helm values, and configuration policies **MUST** reside in a single Git repository. The cluster state **MUST** converge automatically to Git `HEAD`.
- **G-3 (Zero-Trust Secret Management):** No plaintext secrets **SHALL** ever be committed to version control. Secrets **MUST** be encrypted via asymmetric cryptography decryptable only by the cluster's private key.
- **G-4 (Zero Inbound Perimeter):** No router ports **SHALL** be forwarded. All external traffic **MUST** transit outbound-initiated, authenticated micro-tunnels.
- **G-5 (Automated 3-2-1 Backup & Immutability):** Offsite encrypted replication of atomic ZFS snapshots **MUST** execute on a scheduled basis, ensuring recovery capability against total local site loss or catastrophic ransomware events.
- **G-6 (Observable Operations):** Native metrics for storage pools, containers, host I/O, and service latency **MUST** be scraped and visualized in real time.

### 2.2 Non-Goals
- **NG-1 (Active-Active Distributed Ceph/Gluster Cluster):** In Phase 1, multi-node synchronous distributed clustering is explicitly excluded to maintain minimal operational overhead and zero software licensing cost on single-node edge deployments.
- **NG-2 (Public Anonymous File Hosting):** The platform is strictly an authenticated enterprise repository; public anonymous object distribution is out of scope.

---

## 3. Regulatory and Compliance Framework

The design parameters of this specification align with the following international information security and continuity standards:

| Standard / Framework | Control Identifier | Architecture Implementation |
| :--- | :--- | :--- |
| **ISO/IEC 27001:2022** | Control A.8.13 (Information Backup) | Automated daily ZFS export to Cloudflare R2 with 7-day retention and verified restoration routines. |
| **ISO/IEC 27001:2022** | Control A.8.24 (Use of Cryptography) | Envelope encryption via Bitnami Sealed Secrets (RSA-4096) and AES-256-CBC PBKDF2 offsite encryption. |
| **NIST SP 800-34 Rev. 1** | Section 3.2 (Contingency Plan Strategy) | Strict formal definition of RPO ($\le 24\text{h}$) and RTO ($\le 120\text{min}$). |
| **NIST SP 800-88 Rev. 1** | Section 2.4 (Cryptographic Erase - CE) | Snapshot encryption keys enable crypto-shredding on remote buckets upon retention expiry. |
| **CIS Benchmark** | Kubernetes v1.28+ Hardening | Namespaced segmentation, read-only root filesystems where applicable, and restricted Linux capabilities. |

---

## 4. System Architecture & C4 Model Viewpoints

### 4.1 System Context Diagram (C4 Level 1 & 2)

```mermaid
flowchart TD
    subgraph Clients["Enterprise Users & Endpoints"]
        LAN_User["LAN Workstation (Windows/macOS/Linux)"]
        Mobile_User["Remote Mobile / Laptop Client"]
    end

    subgraph Edge["Zero-Trust Perimeter"]
        CF_Edge["Cloudflare Edge Anycast Network"]
        CF_Tunnel["cloudflared Daemon (Outbound Tunnel :7844)"]
    end

    subgraph Compute["K3s Orchestration Engine (Control Plane & Data Plane)"]
        Ingress_Nginx["Traefik / Ingress Routing"]
        Pod_SMB["Samba File Server (SMB 3.1.1)"]
        Pod_NC["Nextcloud Hub (Collaborative Storage)"]
        Pod_Argo["ArgoCD GitOps Controller"]
        Pod_SS["Bitnami Sealed Secrets Controller"]
        Pod_Obs["Prometheus + Grafana Telemetry"]
        Cron_Backup["Offsite Backup CronJob (Rclone + OpenSSL)"]
    end

    subgraph Storage["Immutable Storage Layer (Host Level)"]
        ZFS_Pool[("OpenZFS Storage Pool ('tank/nas')\nCoW, ZSTD Compression, POSIX ACL")]
    end

    subgraph Cloud["Offsite Cloud Object Store"]
        CF_R2[("Cloudflare R2 (S3-Compatible)\nAES-256-CBC Encrypted Snapshots")]
    end

    subgraph Git["Single Source of Truth"]
        GitHub_Repo[("GitHub Enterprise Repository\n(GitOps Declarative State)")]
    end

    %% Network flows
    LAN_User -->|SMB / TCP 445| Pod_SMB
    LAN_User -->|HTTP / TCP 80| Ingress_Nginx
    Mobile_User -->|mTLS / HTTPS 443| CF_Edge
    CF_Edge -->|Multiplexed QUIC/TCP| CF_Tunnel
    CF_Tunnel --> Ingress_Nginx
    Ingress_Nginx --> Pod_NC

    Pod_SMB -->|HostPath /dev/zfs| ZFS_Pool
    Pod_NC -->|PersistentVolumeClaim (Bound)| ZFS_Pool

    GitHub_Repo -->|Pull Reconciliation (3m)| Pod_Argo
    Pod_Argo -->|Deploys / Reconciles| Compute
    Pod_SS -->|Unseals Private Secrets| Compute

    Cron_Backup -->|zfs send| ZFS_Pool
    Cron_Backup -->|Encrypted S3 PutObject| CF_R2
```

---

## 5. Storage Engine Specification (OpenZFS)

### 5.1 Dataset Topology
The storage foundation **MUST** utilize OpenZFS configured on Linux kernel modules. The host pool layout is specified as follows:

- **Root Pool:** `tank`
- **Primary Data Dataset:** `tank/nas`
- **Record Size Configuration:**
  - Database volumes (`nextcloud-db`): `16K` (aligned to InnoDB page size).
  - General file storage (`samba-shares`): `128K` (default POSIX balance).
  - Large streaming media: `1M` (maximizing sequential read performance).
- **Compression:** `zstd` (adaptive level 3) **MUST** be enabled globally. LZ4 **MAY** be utilized if host CPU clock frequency is sub-optimal ($\le 1.8\text{ GHz}$).
- **Integrity Validation:** End-to-end Fletcher4/SHA256 checksumming **MUST** be active across all blocks. Silent data corruption (bit-rot) **MUST** be detected automatically during monthly scrubs.

### 5.2 Atomic Snapshot Strategy
Local snapshots provide instant rollback capability with zero disk allocation at inception:
$$\text{Schedule: } \text{CronJob } \texttt{zfs-snapshot} \longrightarrow \texttt{0 0 * * *} \implies \texttt{tank/nas@k8s-auto-\$TIMESTAMP}$$
Retention policy: Local snapshots **SHALL** be retained for 14 continuous days, pruned by an automated retention script running in host PID namespace.

---

## 6. Container Orchestration & GitOps Layer

### 6.1 Kubernetes Engine (K3s)
The orchestrator **MUST** be lightweight certified Kubernetes (K3s). Docker daemon dependencies are replaced with containerd.
- **Service Networking:** K3s CoreDNS provides internal cluster resolution.
- **Storage Subsystem:** Local volume provisioner binds Kubernetes `PersistentVolume` (PV) objects to host-managed ZFS mountpoints via `ReadWriteOnce` (RWO) semantics.

### 6.2 GitOps Reconciliation (ArgoCD)
The operational paradigm is strictly **Pull-based GitOps**:
- The ArgoCD Application Controller polls the GitHub repository (`https://github.com/orielmeza22/NAS.git`) on a 3-minute interval or upon immediate hard-refresh webhook signal.
- **Automated Self-Healing:** The `syncPolicy.automated.selfHeal = true` directive **MUST** be enforced. Any manual cluster drift or unauthorized `kubectl edit` commands **SHALL** be overwritten and reconciled to the canonical Git manifest state.
- **Prune Policy:** `syncPolicy.automated.prune = true` ensures that deleted manifests in Git are automatically cleaned from the live cluster, preventing orphan workloads.

---

## 7. Security Architecture & Threat Model

### 7.1 Zero-Trust Secret Management (Bitnami Sealed Secrets)
To reconcile public/private Git hosting with strict confidentiality, the system uses an asymmetric cryptographic envelope:
1. **Public Key Infrastructure:** The cluster maintains an in-cluster RSA-4096 private key managed by the `sealed-secrets-controller` pod in `kube-system`.
2. **Offline Encryption:** Operators utilize the `kubeseal` CLI tool on external administrative workstations to encrypt standard Kubernetes `Secret` definitions into custom resource definitions (`SealedSecret`).
3. **Immutability in Transit:** `SealedSecret` resources are scope-bound to target namespaces and metadata names. Even if committed to a public Git repository, third parties cannot decrypt the underlying credentials without the cluster-exclusive private key.

### 7.2 Zero-Inbound Edge Network (Cloudflare Zero Trust)
- Inbound firewall rules on the perimeter router **MUST** drop all unsolicited ingress traffic.
- Connectivity is established by an outbound, authenticated tunnel process (`cloudflared`) maintaining persistent multiplexed connections over port 7844 (UDP/TCP) to Cloudflare Anycast edge servers.
- **DDoS and WAF Protection:** Cloudflare Edge terminates client TLS 1.3 handshakes, mitigates Layer 3/4/7 denial of service attempts, and filters malicious payloads before traffic reaches the internal K3s ingress.

---

## 8. Disaster Recovery & 3-2-1 Cloudflare R2 Pipeline

### 8.1 Contingency Requirements (NIST SP 800-34)
The platform establishes the following business continuity parameters:
- **Recovery Point Objective (RPO):** Maximum **24 hours**. Data loss in a worst-case catastrophic event cannot exceed changes made since the prior night's 03:00 AM backup.
- **Recovery Time Objective (RTO):** Maximum **120 minutes** to provision clean hardware, install K3s, apply GitOps manifests, and restore the encrypted ZFS pool.

### 8.2 Cryptographic Offsite Pipeline Specification
The backup CronJob (`infrastructure/zfs-offsite-backup-cronjob.yaml`) executes the following POSIX pipeline inside an isolated container with host PID access:

```text
[ZFS Pool: tank/nas]
       │ (1. Atomic snapshot query)
       ▼
[nsenter Host Namespace: zfs send -c $LATEST_SNAP]
       │ (2. Compressed binary raw stream)
       ▼
[Host OpenSSL: openssl enc -aes-256-cbc -salt -pbkdf2 -pass "pass:$KEY"]
       │ (3. Military-grade symmetric ciphertext stream)
       ▼
[/tmp/tank-backup-$DATE.zfs.enc]
       │ (4. S3 PutObject multipart upload via rclone)
       ▼
[Cloudflare R2 Object Storage: r2:nas-backup/daily/]
       │ (5. Automated lifecycle rotation: rclone delete --min-age 7d)
       ▼
[Rolling 7-Day Immutable Offsite Retention]
```

### 8.3 Cipher Suite Details
- **Cipher:** AES-256-CBC with PKCS#7 padding.
- **Key Derivation Function (KDF):** PBKDF2 with SHA-256 and randomized cryptographic salt per execution.
- **Egress Cost:** \$0.00 (Cloudflare R2 zero-egress fee architecture).

---

## 9. Standard Operating Procedures (SOP / Runbooks)

### 9.1 SOP-01: Bare-Metal Disaster Recovery from Cloudflare R2
In the event of total server hardware destruction, execute the following restoration sequence:

1. **Host Re-provisioning:** Install baseline Debian/Linux Mint on new hardware.
2. **Storage Initialization:** Recreate target ZFS pool structure:
   ```bash
   zpool create -f tank /dev/disk/by-id/<DISK_ID>
   ```
3. **Artifact Retrieval & Decryption:**
   Configure temporary `rclone` with master credentials and download the desired snapshot:
   ```bash
   rclone copy r2:nas-backup/daily/<TARGET_FILE>.zfs.enc /tmp/
   openssl enc -d -aes-256-cbc -pbkdf2 -pass pass:"$MASTER_KEY" -in /tmp/<TARGET_FILE>.zfs.enc | zfs receive -F tank/nas
   ```
4. **GitOps Rehydration:**
   Install K3s and bootstrap ArgoCD:
   ```bash
   curl -sfL https://get.k3s.io | sh -
   kubectl create namespace argocd
   kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
   kubectl apply -f https://raw.githubusercontent.com/orielmeza22/NAS/main/infrastructure/root-application.yaml
   ```
5. **Secret Decryption:** Apply the stored Bitnami Sealed Secrets private key backup; ArgoCD reconciles the entire software stack into active production state.

---

## 10. Technical Alternatives Evaluation (Trade-off Matrix)

| Evaluation Axis | Chosen Architecture | Evaluated Alternative | Technical Justification for Decision |
| :--- | :--- | :--- | :--- |
| **Filesystem** | **OpenZFS** | ext4 / Btrfs / LVM | Ext4 lacks native snapshots and integrity verification. Btrfs RAID5/6 remains unstable upstream. ZFS delivers production-tested Copy-on-Write and atomic serialization (`zfs send`). |
| **Configuration** | **GitOps (ArgoCD)** | Ansible / Terraform | Procedural scripts (Ansible) suffer from drift when modified locally. ArgoCD continuously asserts desired state with automated drift correction. |
| **Ingress** | **Cloudflare Tunnel** | OpenVPN / WireGuard / Port Forward | VPN requires per-client configuration and open inbound ports. Port forwarding exposes public IPs. Cloudflare Tunnel abstracts network topologies via authenticated zero-trust edge proxies. |
| **Offsite Storage** | **Cloudflare R2** | AWS S3 / Glacier | AWS S3 imposes prohibitive data transfer egress charges (\$0.09/GB) during disaster recovery exercises. Cloudflare R2 features zero egress fees and provides 10 GB free tier. |

---

## 11. Acceptance & Verification Sign-Off

The integrity of this specification has been verified empirically through automated test suites:
- **Smoke Test Suite:** [`tests/smoke-test.sh`](file:///c:/Users/oriel/Desktop/nas-repo/tests/smoke-test.sh) — **10/10 Verification Checks PASS**.
- **Live Test Execution:** Verified Job `test-r2-backup-final` executed successfully with binary verification of `tank-backup-20261019-0410.zfs.enc` stored in Cloudflare R2 bucket `nas-backup`.

```text
[APPROVED FOR ENTERPRISE DEPLOYMENT]
Engineering Lead: Oriel Meza
Architecture Status: STANDARDS_TRACK_COMPLIANT
Revision: 1.0.0
```

---

## Annex A: SMB Control Plane, Identity Provisioning and Client Connector (31/10/2026)

- Portal threading: `ThreadedHTTPServer` plus relaxed probes (`timeoutSeconds: 5`, `failureThreshold: 5`) to remove crashloops caused by a single-threaded server.
- Nextcloud backend moved to PostgreSQL 16 with Redis 7.
- SMB tab: per-share ZFS quotas (`zfs set quota`), live `smbstatus` sessions, controlled Samba restart.
- Identity: unified create/delete/reset across Nextcloud (`occ`) and Samba (`smbpasswd`), including CSV batch import.
- Client connector: public `GET /api/samba/connector` serving `Conectar-Unidad-NAS.bat` (no secrets embedded).
- Known debt: hardcoded `ADMIN_PASS_FALLBACK` in `server.py`; no `.env.example`.

Full reference: `docs/SAMBA_Y_CONECTOR.md`.
