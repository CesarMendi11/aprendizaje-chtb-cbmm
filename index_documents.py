import chromadb
from pathlib import Path
import uuid

# Conectar a ChromaDB (base vectorial)
client = chromadb.PersistentClient(path="./chroma_db")

# Crear o abrir colección
collection = client.get_or_create_collection("erp_docs")

# Carpeta donde están los documentos
knowledge_path = Path("./knowledge")

# Buscar todos los archivos .txt
files = list(knowledge_path.glob("*.txt"))

print(f"Encontrados {len(files)} archivos para indexar...\n")

for file in files:
    print(f"Procesando: {file.name}")

    # Leer contenido
    contenido = file.read_text(encoding="utf-8")

    # Crear ID único para evitar conflictos
    doc_id = str(uuid.uuid4())

    # Guardar en ChromaDB
    collection.add(
        documents=[contenido],
        ids=[doc_id]
    )

    print(f"✔ Indexado: {file.name}")

print("\n✅ Indexación completa.")