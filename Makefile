.PHONY: all help status smoke-test backup-now dr-drill rollback-latest list-backups bootstrap

all: help

help:
	@echo "============================================================"
	@echo "    🛠️  ENTERPRISE NAS PLATFORM — CLI DE ADMINISTRACIÓN    "
	@echo "============================================================"
	@echo "  make status          - Ver estado de Pods, RAID-Z1 y Túnel"
	@echo "  make smoke-test      - Ejecutar suite de pruebas (18 checks)"
	@echo "  make backup-now      - Disparar backup inmediato a Cloudflare R2"
	@echo "  make dr-drill        - Ejecutar simulacro de Disaster Recovery"
	@echo "  make list-backups    - Listar snapshots ZFS y objetos R2"
	@echo "  make rollback-latest - Revertir al snapshot local más reciente"
	@echo "  make bootstrap       - Aprovisionar servidor desde cero (Zero-Touch)"
	@echo "============================================================"

status:
	@echo "=== ESTADO DE PODS (K3s) ==="
	kubectl get pods -A -l "app.kubernetes.io/part-of=nas-platform"
	@echo ""
	@echo "=== ESTADO DEL POOL OPENZFS (RAID-Z1) ==="
	sudo zpool status tank
	@echo ""
	@echo "=== TÚNEL ZERO-TRUST WAN ==="
	@cat /tmp/nas-tunnel/url.txt 2>/dev/null || echo "Túnel no iniciado."

smoke-test:
	bash tests/smoke-test.sh

backup-now:
	@echo "Disparando Backup Offsite a Cloudflare R2..."
	kubectl -n kube-system delete job manual-r2-backup --ignore-not-found=true
	kubectl -n kube-system create job --from=cronjob/zfs-offsite-backup manual-r2-backup
	kubectl -n kube-system wait --for=condition=complete job/manual-r2-backup --timeout=90s
	kubectl -n kube-system logs job/manual-r2-backup

dr-drill:
	bash tests/disaster-recovery-drill.sh

list-backups:
	bash scripts/nas-recover.sh list

rollback-latest:
	@LATEST_SNAP=$$(sudo zfs list -t snapshot -o name -s creation -H | tail -n 1); \
	if [ -n "$$LATEST_SNAP" ]; then \
		echo "Revirtiendo a $$LATEST_SNAP..."; \
		bash scripts/nas-recover.sh rollback-local "$$LATEST_SNAP"; \
	else \
		echo "No se encontraron snapshots."; \
	fi

bootstrap:
	bash scripts/bootstrap-enterprise-nas.sh
