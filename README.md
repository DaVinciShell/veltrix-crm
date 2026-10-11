# VELTRIX CRM · versión 2

CRM comercial para prospectar, dar seguimiento, proponer, cerrar y cobrar. Datos en `data/crm.db` (SQLite). Interfaz en español.

## Actualizar desde la versión 1 (sin perder datos)
1. Cierre el CRM (Ctrl + C en la ventana negra).
2. Copie esta carpeta sobre la carpeta de instalación existente (no reemplace la carpeta `data`).
3. Doble clic en `iniciar.bat` (instala `reportlab` la primera vez).
4. Al arrancar, el CRM hace una **copia verificada** (`data\respaldos\crm_pre_migracion_...db`), agrega las columnas nuevas y clasifica la base. Los datos existentes se conservan.
5. Abra http://localhost:5000, cree su usuario y contraseña (mínimo 10 caracteres) y complete **Configuración > Datos del emisor**.

## Qué hay de nuevo
| Área | Qué hace | Dónde |
|---|---|---|
| Calidad de datos | Clasifica válidas / por revisar / excluidas con motivo; excluir y restaurar; CSV; historial de cambios | Calidad de datos |
| Priorización | Puntaje 0-100 configurable; ámbito Cali / Valle / país | Hoy, Empresas, Configuración |
| Propuestas PDF | Numeración `PROP-AAAA-0001`, versiones, estados | Ficha de empresa > + Propuesta |
| Cuentas de cobro PDF | `CC-AAAA-0001`; "pagado" solo con cobros reales | Ficha > Cuenta de cobro; Pipeline > + Cobro |
| Mensajes | Plantillas editables de WhatsApp y correo; abre WhatsApp / correo; usted envía | Botón Mensaje |
| Tareas y avisos | Recordatorios con fecha, hora y frecuencia; centro de notificaciones; 🔔 | Tareas y avisos |
| Tablero | Prioritarias, propuestas, documentos, por cobrar, vencidas, recibido hoy/semana/mes, conversión, sectores | Dashboard |
| Seguridad | Login con hash, sesión protegida, copias automáticas diarias, restauración | Configuración |

## Puntaje: cómo se calcula
Suma ponderada (0-100): ubicación 25, sector 25, tamaño 20, datos de contacto 15, señales financieras 10, experiencia previa 5. Todos los pesos, la tabla de ubicaciones, la de sectores (división CIIU) y los CIIU de casos de éxito se editan en Configuración. Los valores iniciales siguen sus sectores prioritarios (alimentos, manufactura, logística, salud, retail, energía, agroindustria, construcción) y empresas medianas/grandes (percentil 50-99 de activos); son una decisión de enfoque editable, no un hecho de mercado. El puntaje ordena prioridad; no es una probabilidad de cierre. Límites: cifras de un solo corte anual, unidad sin verificar; la base no trae teléfonos ni correos (el componente "datos" es 0 hasta que los registre).

## Avisos: qué funciona realmente
- **Campana y centro de notificaciones:** mientras el CRM esté abierto en el navegador.
- **Avisos de escritorio:** Configuración > "Activar avisos". Solo con el CRM abierto.
- **Resumen por correo:** requiere SMTP. Defina estas variables de entorno (nunca en el código):
  `VELTRIX_SMTP_HOST`, `VELTRIX_SMTP_PORT` (587), `VELTRIX_SMTP_USER`, `VELTRIX_SMTP_PASS`, `VELTRIX_SMTP_FROM`, `VELTRIX_SMTP_TO`.
  Con el servidor encendido se envía una vez al día (8:00). El script `resumen_diario.py` no envía correo ni consulta el CRM; no lo use como sustituto del resumen SMTP.
- Con todo apagado no hay avisos.

## Notificación externa opcional
`resumen_diario.py` solo envía una notificación cuando `VELTRIX_NTFY_ENABLED=1` y `VELTRIX_NTFY_TOPIC` están configuradas. El servidor ntfy usa `VELTRIX_NTFY_SERVER` (por defecto `https://ntfy.sh`) y, si es privado, `VELTRIX_NTFY_TOKEN`. Sin activación explícita, el script no realiza solicitudes de red. No incluya tokens en archivos versionados ni argumentos de comandos.

## Lo que NO incluye (requiere credenciales, permisos o costo)
- **Factura electrónica:** exige un proveedor tecnológico habilitado por la DIAN. El CRM no la simula; genera *cuentas de cobro*.
- **Envío automático por WhatsApp Business API o correo desde el CRM:** requiere cuenta oficial, plantillas aprobadas por Meta y costo por conversación. Hoy se prepara el mensaje y usted lo envía. No hay envío masivo.
- **Varios usuarios con permisos por rol:** hay tabla de usuarios, pero la interfaz de administración no está construida.

## Copias de seguridad y restauración
- Automática diaria (últimas 14) en `data\respaldos\` mientras el servidor esté encendido; además antes de cada migración y con "Crear copia ahora".
- Restaurar (CRM cerrado): `python restaurar.py data\respaldos\ARCHIVO.db`. Verifica la copia y guarda antes la base actual.
- No abra el CRM en red sin contraseña: con `HOST=0.0.0.0` exige la autenticación activa. No lo exponga a internet.

## Pruebas
`python tests/test_flujo.py` (circuito comercial) y `python tests/test_mejoras.py` (funciones nuevas). Usan bases temporales y un Excel sintético generado en memoria; no requieren ni leen la base empresarial original.

## Datos locales
La base operativa vive en `data\crm.db` y se excluye del repositorio junto con claves, respaldos, documentos privados y hojas empresariales. Si ya existe una base con trabajo propio, **no la reemplace**: haga una copia aparte primero. El paquete de código no distribuye datos empresariales.
Primer arranque: `iniciar.bat` (o `python app.py`), abra http://localhost:5000 y cree su usuario.
