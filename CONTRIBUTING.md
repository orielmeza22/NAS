# Contribuir al Proyecto NAS

Gracias por tu interés en contribuir a este proyecto.

## Proceso

1. Haz fork del repositorio
2. Crea una rama descriptiva: `feat/mi-mejora` o `fix/mi-correccion`
3. Realiza tus cambios siguiendo las convenciones del proyecto
4. Asegúrate de que los manifiestos YAML son válidos
5. Abre un Pull Request con una descripción clara

## Convenciones

- **Commits:** Usa [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `chore:`)
- **YAML:** Indentación de 2 espacios, sin tabs
- **Labels K8s:** Usa labels estándar `app.kubernetes.io/*`
- **Secrets:** Nunca commitear credenciales en texto plano. Usa Kubernetes Secrets

## Reportar Problemas

Abre un [Issue](https://github.com/orielmeza22/NAS/issues) describiendo:
- Qué esperabas que pasara
- Qué pasó realmente
- Pasos para reproducir
