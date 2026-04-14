import chromadb

# Conectar a la base vectorial
client = chromadb.PersistentClient(path="./chroma_db")

# Abrir la colección
collection = client.get_or_create_collection("erp_docs")

# Realizar búsqueda
resultado = collection.query(
    query_texts=["No puedo iniciar sesión en pagos"],
    n_results=1
)

print("Resultado de la búsqueda:")
print(resultado)