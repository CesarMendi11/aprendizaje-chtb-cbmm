import chromadb

# Crear conexión persistente con la base vectorial
client = chromadb.PersistentClient(path="./chroma_db")

# Crear o abrir una colección
collection = client.get_or_create_collection("erp_docs")

# Limpiar datos anteriores de prueba (opcional)
try:
    collection.delete(ids=["doc1", "doc2"])
except:
    pass

# Agregar documentos simulados
collection.add(
    documents=[
        "El módulo de recursos humanos permite registrar empleados y gestionar nómina.",
        "El módulo de pagos permite registrar pagos institucionales y comprobantes."
    ],
    ids=["doc1", "doc2"]
)

# Buscar el documento más relacionado
resultado = collection.query(
    query_texts=["pagos"],
    n_results=1
)

print("Resultado encontrado:")
print(resultado)