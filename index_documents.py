import chromadb
from pathlib import Path

# Conectar a ChromaDB
client = chromadb.PersistentClient(path="./chroma_db")
collection = client.get_or_create_collection("erp_docs")

# Ruta del documento
file_path = Path("./knowledge/incidencias.txt")

# Leer contenido
contenido = file_path.read_text(encoding="utf-8")

# Guardar en ChromaDB
collection.add(
    documents=[contenido],
    ids=["incidencia_1"]
)

print("Documento indexado correctamente.")