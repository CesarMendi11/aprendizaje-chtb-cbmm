import shutil
import chromadb
from pathlib import Path
import uuid

CHROMA_PATH = "./chroma_db"
COLLECTION_NAME = "erp_docs"
KNOWLEDGE_PATH = Path("./knowledge")


def reset_chroma_db():
    chroma_path = Path(CHROMA_PATH)

    if chroma_path.exists():
        shutil.rmtree(chroma_path)
        print("🧹 Base vectorial anterior eliminada.")


def dividir_en_chunks(texto):
    partes = texto.split("---")

    chunks = []

    for parte in partes:
        parte = parte.strip()

        if parte:
            chunks.append(parte)

    return chunks


reset_chroma_db()

client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = client.get_or_create_collection(COLLECTION_NAME)

files = list(KNOWLEDGE_PATH.glob("*.txt"))

print(f"Encontrados {len(files)} archivos para indexar...\n")

for file in files:
    print(f"Procesando: {file.name}")

    contenido = file.read_text(encoding="utf-8")
    chunks = dividir_en_chunks(contenido)

    for index, chunk in enumerate(chunks, start=1):
        doc_id = str(uuid.uuid4())

        collection.add(
            documents=[chunk],
            ids=[doc_id],
            metadatas=[
                {
                    "archivo": file.name,
                    "chunk": index
                }
            ]
        )

    print(f"✔ Indexado en {len(chunks)} fragmentos: {file.name}")

print("\n✅ Indexación completa.")