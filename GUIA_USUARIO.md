# Guía de usuario — DIAN Web (Consultas de Información)

Guía en lenguaje sencillo para quienes usan la plataforma: contadores y
usuarios que consultan la información de sus clientes en la DIAN.

> Si algo no te coincide o algo no funciona, revisa la sección
> **"Solución de problemas"** al final.

---

## 1. ¿Qué hace esta plataforma?

Te ayuda a consultar, para cada cliente, **la información que la DIAN tiene
registrada** (Información Exógena) y a determinar si el cliente **debe declarar**
renta ese año, según los topes de la ley. También descarga la **Facturación
Electrónica** del cliente.

Al final de cada consulta obtienes un **archivo Excel** con 3 hojas:
1. **Información Exógena** — los valores que la DIAN reporta del cliente.
2. **Renta** — si debe declarar (Sí/No) y las razones.
3. **Facturación Electrónica** — el reporte (o un aviso si no hay información).

Para que la plataforma consulte, necesitas el **número de documento** y la
**contraseña** que usa el cliente en el portal de la DIAN. La plataforma los usa
**solo durante la consulta** y no los guarda.

---

## 2. Primeros pasos

1. Entra a la dirección de la plataforma (te la da quien la administra).
2. Pulsa **Solicitar alta / Registrarse** y crea tu usuario con una contraseña.
3. Tu solicitud queda **pendiente**: el administrador debe **aprobarla** antes de que
   puedas entrar. Una vez aprobada, inicia sesión con tu usuario y contraseña.

> ¿No puedes entrar? Puede ser que tu cuenta aún no esté aprobada o que fue
> bloqueada. Consulta con el administrador.

---

## 3. Consulta individual (un solo cliente)

1. En el menú entra a **Consulta individual** (`/panel`).
2. Escribe el **tipo de documento**, el **número de documento** y la **contraseña**
   del cliente en el portal DIAN.
3. Pulsa **CONSULTAR**.
4. Mientras trabaja verás el **progreso**. Cuando termine, pulsa
   **DESCARGAR RESULTADO** para bajar el Excel de 3 hojas.

---

## 4. Consulta masiva (varios clientes a la vez) — Panel Contadores

Si el administrador te **activó el acceso de Contador**, también tendrás el panel
**Contadores** (`/contadores`) para procesar muchos clientes de una sola vez.

### 4.1 Descargar la plantilla

Pulsa **DESCARGAR PLANTILLA**. Se baja un archivo `.xlsx` con las columnas ya
preparadas:

| tipo_documento | numero_documento | contrasena | fecha_vencimiento | estado |
|---|---|---|---|---|

> No borres ni cambies los nombres de las columnas. Solo llena los datos.

### 4.2 Llenar el archivo

- Una fila = un cliente.
- **Obligatorias**: `tipo_documento`, `numero_documento` y `contrasena`.
- `fecha_vencimiento` y `estado`: **el sistema las llena solas** al terminar; déjalas
  vacías. No deben ir datos inventados.
- Guarda el archivo en formato **`.xlsx`** (Excel 2007 o superior). Si tiene otra
  extensión (`.xls`, `.csv`), no se podrá cargar.

### 4.3 Subir y procesar

1. En **Contadores**, elige el archivo `.xlsx` y pulsa **INICIAR CONSULTA MASIVA**.
2. Verás una barra de **progreso** con cuántos clientes van de cuántos.
3. Cuando termine, pulsa **DESCARGAR RESULTADO (ZIP)**. El ZIP contiene:
   - `resultado_masiva.xlsx` — tu mismo archivo pero con las columnas de
     `estado` y `fecha_vencimiento` **ya alimentadas**.
   - una carpeta `clientes/` con un archivo por cada cliente procesado con éxito.

### 4.4 Estados de cada cliente (columna `estado`)

| Estado | Significado |
|---|---|
| `ok` | Procesado con éxito; su archivo está en la carpeta `clientes`. |
| `error_credenciales` | Clave o documento rechazados por la DIAN (revisa la contraseña del cliente). |
| `desconocido` | No se pudo determinar el resultado del login. |
| `excepcion` | Ocurrió un error técnico al procesar ese cliente. |

Solo los clientes con `ok` generan su archivo en `clientes/`.

### 4.5 Re-ejecutar el mismo archivo

Vuelve a cargar el mismo archivo: las filas que ya tienen `estado = ok` se **saltan**
(no se reprocesan), y solo continúa con los que quedaron pendientes o con error.
Es útil para reenviar solo los que fallaron sin perder el trabajo hecho.

---

## 5. Privacidad de la información

- Los **documentos y contraseñas** de tus clientes se usan **solo para la consulta**
  y **no se guardan** en la plataforma.
- La plataforma guarda únicamente tu cuenta y el registro de que hiciste una consulta
  (sin el número de documento ni el contenido de los reportes).
- Lee la política completa en `/privacidad` (enlace al pie de la página).

---

## 6. Solución de problemas

- **"El archivo debe ser .xlsx"** → el archivo no tiene extensión `.xlsx`.
  Guarda el Excel como `Archivo > Guardar como > .xlsx`.
- **"Faltan columnas"** → tu archivo no tiene los encabezados exactos. Usa el botón
  **DESCARGAR PLANTILLA** y vuelve a llenarlo.
- **El archivo no se sube / error "archivo"** → vuelve a intentar con un `.xlsx`
  descargado con la plantilla. (Una versión anterior tenía un error que ya se corrigió.)
- **Cliente en `error_credenciales`** → revisa que el documento y la contraseña del
  cliente sean correctos e inténtalo de nuevo.
- **No me deja entrar a Contadores** → el administrador no ha activado tu acceso de
  Contador. Comunícate con él.
- **Mi cuenta no inicia sesión** → puede estar pendiente de aprobación o bloqueada.
  Consulta con el administrador.
