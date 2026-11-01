# AGENTS.md — Reglas de agente (Antigravity)

## SDLC
- **Plan primero:** cambios no triviales → `implementation_plan.md`, esperar aprobación si es arquitectónico.
- **YAGNI:** solo el código pedido. Prohibido: `// resto del código`, `// TODO implementar`, `pass` placeholder.
- **Ciclo:** Análisis → Diseño → Código → Test → Doc. Deuda técnica = pasivo; minimizar siempre.

## Reglas duras
- **Cero alucinación:** nunca inventar paquetes/APIs. Si hay duda, stdlib o preguntar.
- **No dupliques libs existentes:** (revisa antes de añadir: axios vs fetch, lodash vs ramda, etc.).
- **Comandos de shell idempotentes:** (`mkdir -p`, checks previos).
- **Detecta el SO/shell activo:** adapta sintaxis (bash/zsh vs PowerShell) y rutas.
- **Dependencias y Entornos:** Lock files SIEMPRE comprometidos. Versiones fijas en producción.
- **Variables de entorno:** `.env.example` documentado. Validar variables de entorno al iniciar.

## Edición de código
- **Diffs quirúrgicos:** (SEARCH/REPLACE); contexto mínimo (±1 línea), nunca funciones enteras si solo cambia una línea.
- **El bloque REPLACE** contiene únicamente lo nuevo/modificado.
- **Diff falla:** releer archivo y corregir en el acto, sin preguntar. Reportar solo errores fatales.
- **Operaciones independientes:** batch/paralelo.
- **Cambio masivo (>500 líneas):** pedir confirmación antes de ejecutar.
- **Git y Versionado:** Commits atómicos con Conventional Commits (`feat`, `fix`, `chore`, etc.). Nunca commitear `.env`, `node_modules`, `dist`, `__pycache__`.

## Calidad y arquitectura (Estándar Staff+)
- **Producción por defecto:** manejo de errores, edge cases, tipos, logging estructurado.
- **Límites de Modularidad:** Archivo ≤ 400 líneas. Función ≤ 30 líneas. Un nivel de abstracción por función.
- **Funciones pequeñas y puras:** Responsabilidad única (SRP). Composición sobre herencia.
- **Comentarios de Intención:** Documentar el "porqué" (decisiones de negocio o limitaciones técnicas), jamás el "qué".
- **Seguridad por defecto (DevSecOps):** Zero-Trust en fronteras, sanitizar input, mitigar OWASP Top 10, cero secrets hardcodeados.
- **Resiliencia (Fail-Fast):** Validar precondiciones al inicio. PROHIBIDO silenciar errores con `catch`/`except` vacíos.
- **Escalabilidad Stateless:** Sin estado en RAM del servidor. Asincronía no bloqueante. Gestión explícita de recursos (RAII/context managers).
- **Cero Placeholders:** Prohibido `// resto del código`, `// TODO`, `pass` placeholder. Implementaciones completas y funcionales.
- **No romper contratos públicos/firmas** sin pedirlo explícitamente.
- **Si existen tests,** correrlos tras cada cambio; si no, proponer los críticos.
- **Tipado Estricto:** Tipado estricto obligatorio. Prohibido `any` (TS) o tipos débiles sin justificación.
- **Base de Datos:** Migraciones versionadas y reversibles. Prohibido modificar esquema manualmente. Transacciones para operaciones atómicas.
- **Accesibilidad (a11y):** UI con contraste AA, focus rings visibles, áreas táctiles $\ge44\text{px}$, semántica HTML correcta.

## El Filtro del Mundo Real y Cero Complacencia (Anti-Happy Path)
- **Prohibido "Listo para Producción" por Tests:** Un test en verde o comando con exit code 0 solo valida ejecución sintética, NUNCA seguridad, resiliencia ni viabilidad operativa.
- **Los 4 Filtros de Resiliencia Física Obligatorios:**
  1. *Falla de Hardware:* Si el servidor se destruye hoy, ¿cómo se recupera? Si las claves de descifrado/backup mueren con el host, el sistema es INVÁLIDO.
  2. *Corte de Luz:* ¿Qué ocurre ante un corte abrupto sin UPS? Evaluar riesgo de corrupción en DB y storage (ZFS/RAID).
  3. *Atacante en LAN:* Evaluar privilegios excesivos (`hostPID`, root, HTTP plano sin TLS, tokens adivinables, scripts sin firma digital).
  4. *Huella de Recursos Real:* La RAM y CPU deben dimensionarse con concurrencia real y cachés activas (ej. ZFS ARC), nunca en reposo.
- **Cuestionamiento de Premisas (Anti-Resume Driven Development):** Prohibido aceptar acríticamente sobreingeniería desmedida para el contexto operativo (ej. K8s multi-servicio para una escuela con un solo nodo). Señalar siempre la deuda técnica y fragilidad operativa.
- **Chaos & Pruebas Negativas:** Probar obligatoriamente qué pasa cuando las cosas fallan (archivos bloqueados, red caída, disco al 100%, secretos faltantes).

## Formato de salida
- **Razonamiento/plan** dentro de bloque oculto de pensamiento; fuera de él, solo código, diffs o respuesta directa.
- **Cero preámbulo** ("voy a...", "primero..."). Directo al resultado.
- **Tarea grande** → dividir en varias llamadas a herramientas, no truncar en una sola respuesta.

## Específico Antigravity
- Cada tarea no trivial cierra con **Artifact:** `implementation_plan.md` (antes de ejecutar) y `walkthrough.md` (al finalizar, con capturas si hay UI).
- **Verificar de verdad:** usar terminal (sandboxed) y/o browser view para confirmar que corre y pasa tests — no asumir.
- **Detalle de bajo uso** (guías largas, convenciones de un framework específico, checklists puntuales) va en **Skills** (`SKILL.md` dentro de tu carpeta de skills del proyecto, p. ej. `.agent/skills/`), no en este archivo. Antigravity solo carga la metadata de cada skill y expande el contenido completo cuando hace falta: mover reglas ahí ahorra tokens en cada turno.
- **Tareas repetibles** (pipelines, checklists multi-paso) → workflow reusable como slash command, no reescribir el prompt cada vez.
- **MCP:** usar conectores ya configurados en el proyecto antes de proponer una integración nueva.
- **Este archivo** tiene prioridad sobre `GEMINI.md` y se carga completo en cada sesión → mantenerlo corto es la optimización de mayor impacto; todo lo situacional sale de aquí.

## Contexto de Ejecución de Comandos
- **Frontend Commands**: Ejecutar siempre en `c:\Users\oriel\Desktop\untitled`.
- **Backend Commands**: Ejecutar siempre en `c:\Users\oriel\Desktop\untitled` (squad_local_refactored) o `c:\Users\oriel\Desktop\proyecto` (ai-engine) según corresponda.
- **Compatibilidad de rutas:** Si el entorno es Linux/macOS, adaptar rutas a formato POSIX automáticamente.

## Activación de Skills
- Al recibir "revisar", "review" o "PR" -> Cargar y aplicar la skill `code-review`.
- Al recibir "error", "falla", "bug" o "crash" -> Cargar y aplicar la skill `debugging`.
- Al finalizar tarea o recibir "listo", "terminado" -> Cargar y aplicar la skill `pre-delivery`.
- Al organizar contexto largo o estructurar información -> Cargar y aplicar la skill `context-engineering`.
- Al diseñar prompts o definir tareas de subagentes -> Cargar y aplicar la skill `prompt-contracts`.
- Al realizar compilación estática, corregir imports o requerir guardrails -> Cargar y aplicar la skill `deterministic-validation`.
- Al crear o modificar UI, layouts o interactividad visual -> Cargar y aplicar la skill `bespoke-ux`.
- Al escribir, modificar o revisar código en cualquier proyecto -> Cargar y aplicar la skill `engineering-standards`.



