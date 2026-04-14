# 🔥 Asistente Inteligente ERP – Cuerpo de Bomberos de Machala

Sistema backend basado en **RAG (Retrieval-Augmented Generation)** para asistencia inteligente sobre manuales, incidencias y procedimientos del ERP institucional.

Este proyecto permite:

- 📚 Indexar documentos institucionales
- 🔎 Realizar búsquedas semánticas
- 🧠 Recuperar conocimiento contextual
- 🗂 Preparar la base para integrar IA generativa
- 🏢 Escalar a integración empresarial con PostgreSQL

---

# 📌 Estado actual del proyecto

Actualmente el proyecto cuenta con:

- ✅ entorno virtual configurado
- ✅ dependencias instaladas
- ✅ base vectorial con ChromaDB
- ✅ indexación de documentos
- ✅ recuperación semántica
- 🔄 pendiente integración completa con LLM / API REST

---

# 📂 Estructura del proyecto

```text
mi-asistente/
│
├── knowledge/              # Documentos fuente
├── chroma_db/              # Base vectorial persistente
├── venv/                   # Entorno virtual (NO subir)
│
├── .env                    # Variables sensibles (NO subir)
├── .gitignore
├── requirements.txt
│
├── rag_test.py             # Prueba inicial ChromaDB
├── index_documents.py      # Indexación documental
├── search_documents.py     # Búsqueda semántica
├── test_api.py             # Prueba conexión OpenAI
└── app.py                  # Futuro backend / interfaz
```

---

# ⚙️ REQUISITOS

Instalar previamente:

- Python 3.10 o superior
- Git
- VS Code (recomendado)
- PowerShell / Terminal

Verificar Python:

```bash
python --version
```

---

# 🚀 INSTALACIÓN PASO A PASO (DETALLADA)

---

## 1) Clonar repositorio

```bash
git clone https://github.com/TU_USUARIO/TU_REPOSITORIO.git
cd mi-asistente
```

---

## 2) Crear entorno virtual

Este paso crea un entorno aislado de Python.

```bash
python -m venv venv
```

Esto generará la carpeta:

```text
venv
```

---

## 3) Activar entorno virtual

### Windows PowerShell

```powershell
.\venv\Scripts\Activate.ps1
```

Si todo salió bien, debe verse así:

```text
(venv) PS C:\ruta\proyecto>
```

---

## ❗ Error común: no se activa el entorno

### ❌ Error
```text
El módulo 'venv' no pudo cargarse
```

### ✅ Solución
Verifica que estés en la carpeta correcta:

```powershell
dir
```

Debes ver:

```text
venv
requirements.txt
```

Luego activa nuevamente:

```powershell
.\venv\Scripts\Activate.ps1
```

---

## 4) Instalar dependencias

```bash
pip install -r requirements.txt
```

Esto instalará:

- openai
- chromadb
- langchain
- streamlit
- fastapi
- python-dotenv
- pypdf

---

## 5) Crear archivo `.env`

Crear archivo:

```text
.env
```

Contenido:

```text
OPENAI_API_KEY=tu_api_key_aqui
```

---

## ❓ ¿Dónde obtener la API key?

Ingresar a:

https://platform.openai.com/api-keys

Crear nueva clave y copiarla.

---

## ❗ Error común: quota insuficiente

### ❌ Error
```text
Error 429 insufficient_quota
```

### ✅ Significado
La API está bien conectada, pero la cuenta no tiene saldo disponible.

Esto **NO es error del código**.

Solución:

- agregar método de pago
- cargar saldo en OpenAI Platform

---

# 🧠 ¿Qué significa indexar?

Indexar significa:

```text
documento → texto → vector → almacenamiento
```

Es decir, preparar documentos para búsquedas inteligentes.

Ejemplo:

```text
manual ERP
↓
embeddings
↓
ChromaDB
```

---

# 📄 PREPARAR DOCUMENTOS

Colocar documentos dentro de:

```text
knowledge/
```

Ejemplo:

```text
knowledge/
├── incidencias.txt
├── manual_bodega.txt
└── faq_pagos.txt
```

---

## ✅ Formato recomendado

Preferiblemente:

- `.txt`
- `.pdf` con texto seleccionable

---

## ❌ Evitar

- PDFs escaneados como imagen
- capturas de pantalla
- documentos sin estructura

---

# 📥 INDEXAR DOCUMENTOS

Ejecutar:

```bash
python index_documents.py
```

Salida esperada:

```text
Documento indexado correctamente.
```

---

# 🔍 REALIZAR BÚSQUEDA

Ejecutar:

```bash
python search_documents.py
```

Ejemplo de consulta:

```text
No puedo iniciar sesión en pagos
```

Resultado esperado:

```text
incidencia_1
```

---

# 🧪 PRUEBA DE CHROMADB

Para validar la base vectorial:

```bash
python rag_test.py
```

Debe recuperar el documento correcto según la consulta.

---

# 🛠 ERRORES FRECUENTES Y SOLUCIONES

---

## Error: no reconoce comando python

```text
python no se reconoce
```

### Solución
Verificar instalación:

```bash
python --version
```

Si no funciona, reinstalar Python marcando:

```text
Add Python to PATH
```

---

## Error: entorno no activado

Si no aparece:

```text
(venv)
```

la terminal NO está usando el proyecto.

Activar nuevamente:

```powershell
.\venv\Scripts\Activate.ps1
```

---

## Error: archivos no encontrados

Ejemplo:

```text
knowledge/incidencias.txt
```

### Solución
Verificar carpeta con:

```powershell
dir
```

---

# 🧱 ARQUITECTURA ACTUAL

```text
Usuario
   ↓
Consulta
   ↓
Python Backend
   ↓
ChromaDB
   ↓
Documento recuperado
```

---

# 🏢 FUTURA INTEGRACIÓN EMPRESARIAL

Próximamente se integrará:

```text
PostgreSQL + pgvector
```

para conexión con ERP institucional.

---

# 👨‍💻 Autor

César Andrés Mendieta Espinoza  
Proyecto de titulación – Tecnologías de la Información  
Universidad Técnica de Machala