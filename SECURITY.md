# Política de Seguridad

## Reportar una Vulnerabilidad

Si encuentras una vulnerabilidad de seguridad, por favor **no** la reportes como un Issue público.

Envía un correo o reporte privado a través de las alertas de seguridad de GitHub en el repositorio o contacta a: **orielmeza@gmail.com** (o vía GitHub Security Advisory).

## Prácticas de Seguridad

- Las credenciales se gestionan mediante Kubernetes Secrets
- El acceso externo se controla mediante Cloudflare Zero Trust
- No se almacenan contraseñas en texto plano en el repositorio
- Los contenedores corren con los mínimos privilegios necesarios
