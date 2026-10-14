# Política de Seguridad

## Reportar una Vulnerabilidad

Si encuentras una vulnerabilidad de seguridad, por favor **no** la reportes como un Issue público.

Envía un correo a: **[security contact - update this]**

## Prácticas de Seguridad

- Las credenciales se gestionan mediante Kubernetes Secrets
- El acceso externo se controla mediante Cloudflare Zero Trust
- No se almacenan contraseñas en texto plano en el repositorio
- Los contenedores corren con los mínimos privilegios necesarios
