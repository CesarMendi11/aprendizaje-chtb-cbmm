# 🔥 Aprendizaje-CHTB-CBMM

## 📌 Descripción

Proyecto de investigación y desarrollo orientado a la construcción de un asistente inteligente basado en RAG (Retrieval-Augmented Generation) para sistemas ERP institucionales.

La propuesta busca construir conocimiento funcional de manera semiautomática mediante navegación automatizada sobre interfaces ERP reales utilizando Playwright, modelos de lenguaje (LLMs), bases vectoriales y bases de grafos.

El sistema está enfocado principalmente en:

- orientación funcional del ERP
- soporte técnico básico
- navegación guiada por módulos y pantallas
- resolución de incidencias comunes
- construcción semiautomática de conocimiento funcional

---

## 🎯 Objetivo principal

Desarrollar una metodología semiautomática para la construcción de conocimiento funcional en sistemas ERP institucionales mediante:

- navegación automatizada
- extracción de componentes de interfaz
- organización inteligente del conocimiento
- validación humana
- almacenamiento híbrido usando Neo4j y ChromaDB

---

## 🧠 Arquitectura general

```text
ERP institucional
↓
Playwright navega y extrae información
↓
LLM organiza conocimiento funcional
↓
Validación humana
↓
Neo4j + ChromaDB
↓
RAG híbrido
↓
Asistente inteligente
```

---

## 🏗 Arquitectura híbrida

### 🔹 Neo4j

Neo4j almacena la estructura real del ERP obtenida desde la interfaz en producción.

Ejemplos:

- módulos
- pantallas
- botones
- formularios
- rutas
- campos
- permisos y roles

Ejemplo conceptual:

```text
ERP → módulo → pantalla → botón → acción
```

### 🔹 ChromaDB

ChromaDB almacena conocimiento funcional y semántico.

Ejemplos:

- explicaciones funcionales
- preguntas y respuestas
- incidencias comunes
- soporte técnico
- documentación validada
- embeddings semánticos

---

## 🔄 Flujo general del sistema

```text
ERP institucional
↓
Playwright extrae información real
↓
LLM organiza y redacta conocimiento funcional
↓
Validación humana
↓
Neo4j almacena relaciones estructurales
↓
ChromaDB almacena conocimiento semántico
↓
RAG híbrido consulta ambas fuentes
↓
LLM genera respuesta final
```

---

## ⚙️ Tecnologías utilizadas

| Tecnología            | Propósito                       |
| --------------------- | ------------------------------- |
| Python                | Backend principal               |
| Playwright            | Navegación automatizada del ERP |
| FastAPI               | API del asistente               |
| Ollama                | Ejecución local de modelos LLM  |
| Llama 3 / 3.2         | Modelo de lenguaje              |
| Neo4j                 | Base de grafos                  |
| ChromaDB              | Base vectorial                  |
| Sentence Transformers | Embeddings                      |
| PyPDF                 | Lectura de PDFs                 |
| Git/GitHub            | Control de versiones            |

---

## 📂 Estructura del proyecto

```text
aprendizaje-chtb-cbmm/
│
├── data/
│   ├── raw/
│   ├── processed/
│   ├── review/
│   ├── approved/
│   └── rejected/
│
├── docs/
│
├── knowledge/
│
├── scripts/
│
├── src/
│   ├── api/
│   ├── extraction/
│   ├── graph/
│   ├── llm/
│   └── rag/
│
├── app.py
├── requirements.txt
├── README.md
└── .gitignore
```

---

## 📚 Carpeta `knowledge`

La carpeta `knowledge/` almacena conocimiento complementario validado manualmente.

Ejemplos:

- soporte técnico
- incidencias comunes
- preguntas frecuentes
- manuales oficiales
- documentación institucional
- procedimientos

Esta información complementa el conocimiento extraído automáticamente desde el ERP.

---

## ✅ Flujo de validación

```text
Playwright extrae información
↓
LLM genera conocimiento funcional
↓
review/
↓
Validación humana
↓
approved/
↓
Indexación en Neo4j y ChromaDB
```

---

## 🚧 Estado actual del proyecto

Actualmente el proyecto cuenta con:

- arquitectura base definida
- estructura modular organizada
- diseño de flujo de conocimiento
- integración conceptual RAG híbrido
- entorno preparado para Playwright
- entorno preparado para Neo4j
- entorno preparado para ChromaDB
- validación humana contemplada en la arquitectura

---

## 🐧 Instalación del entorno

### 🔹 Requisitos

- Python 3.13+
- Git
- Node.js
- npm
- Ollama
- Debian/Linux recomendado

### 🔹 Crear entorno virtual

```bash
python3 -m venv .venv
```

### 🔹 Activar entorno

```bash
source .venv/bin/activate
```

### 🔹 Instalar dependencias

```bash
pip install -r requirements.txt
```

### 🔹 Instalar navegador Playwright

```bash
playwright install chromium
```

---

## 🧪 Objetivo investigativo

El objetivo principal de investigación no es únicamente implementar un chatbot, sino proponer una metodología para reducir el esfuerzo manual de construcción y mantenimiento de bases de conocimiento funcionales en sistemas ERP institucionales.

---

## 👨‍💻 Equipo de desarrollo

- César Andrés Mendieta Espinoza
- Jonathan Joseph Chalco Berrezueta

Proyecto de investigación y desarrollo  
Tecnologías de la Información  
Universidad Técnica de Machala
