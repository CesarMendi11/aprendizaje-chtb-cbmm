import os
import argparse
import requests
import chromadb
from dotenv import load_dotenv
from openai import OpenAI


# Cargar variables del archivo .env
load_dotenv()


# =========================
# 1. BUSCAR CONTEXTO EN CHROMADB
# =========================
def search_context(query, n_results=2):
    """
    Busca en ChromaDB los documentos o fragmentos más relacionados con la pregunta.
    """

    client = chromadb.PersistentClient(path="./chroma_db")
    collection = client.get_or_create_collection("erp_docs")

    result = collection.query(
        query_texts=[query],
        n_results=n_results
    )

    documents = result.get("documents", [[]])[0]

    if not documents:
        return "No se encontró contexto relevante en la base documental."

    context = "\n\n---\n\n".join(documents)
    return context


# =========================
# 2. CONSTRUIR PROMPT
# =========================
def build_prompt(user_question, context):
    return f"""
Eres un asistente técnico del ERP del Cuerpo de Bomberos de Machala.

Responde SOLO con la información del contexto.

Reglas:
- NO agregues texto adicional.
- NO hagas introducciones.
- NO hagas preguntas al usuario.
- NO uses frases como "te ayudo", "claro", etc.
- Responde de forma directa y en lista si aplica.

Contexto:
{context}

Pregunta:
{user_question}

Respuesta:
"""

# =========================
# 3. OPENROUTER / OPENAI
# =========================
def ask_openai_compatible(prompt, provider, model):
    """
    Consulta proveedores compatibles con la API de OpenAI.
    Sirve para OpenAI y OpenRouter.
    """

    if provider == "openrouter":
        api_key = os.getenv("OPENROUTER_API_KEY")
        base_url = "https://openrouter.ai/api/v1"

        if not api_key:
            raise ValueError("Falta OPENROUTER_API_KEY en el archivo .env")

        client = OpenAI(
            api_key=api_key,
            base_url=base_url
        )

    elif provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise ValueError("Falta OPENAI_API_KEY en el archivo .env")

        client = OpenAI(api_key=api_key)

    else:
        raise ValueError(f"Proveedor no compatible: {provider}")

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.2
    )

    return response.choices[0].message.content


# =========================
# 4. OLLAMA LOCAL
# =========================
def ask_ollama(prompt, model):
    """
    Consulta Ollama local.
    Ollama debe estar activo en http://localhost:11434
    """

    url = "http://localhost:11434/api/generate"

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2
        }
    }

    try:
        response = requests.post(url, json=payload, timeout=120)
    except requests.exceptions.ConnectionError:
        raise ConnectionError(
            "No se pudo conectar con Ollama. "
            "Verifica que esté activo con: ollama serve"
        )

    except requests.exceptions.Timeout:
        raise TimeoutError(
            "Ollama tardó demasiado en responder. "
            "Puede ser por el hardware o por un modelo muy pesado."
        )

    if response.status_code != 200:
        raise ValueError(f"Error al consultar Ollama: {response.text}")

    data = response.json()
    return data.get("response", "No se recibió respuesta desde Ollama.")


# =========================
# 5. ELEGIR PROVEEDOR
# =========================
def ask_llm(prompt, provider, model):
    """
    Decide qué proveedor utilizar.
    """

    provider = provider.lower()

    if provider in ["openai", "openrouter"]:
        return ask_openai_compatible(prompt, provider, model)

    if provider == "ollama":
        return ask_ollama(prompt, model)

    raise ValueError(
        f"Proveedor no soportado: {provider}. "
        "Usa: ollama, openrouter u openai."
    )


# =========================
# 6. PROGRAMA PRINCIPAL
# =========================
def main():
    parser = argparse.ArgumentParser(description="Asistente RAG ERP - CBMM")

    parser.add_argument(
        "question",
        help="Pregunta del usuario"
    )

    parser.add_argument(
        "--provider",
        default=os.getenv("MODEL_PROVIDER", "ollama"),
        help="Proveedor: ollama, openrouter u openai"
    )

    parser.add_argument(
        "--model",
        default=os.getenv("MODEL_NAME", "llama3"),
        help="Modelo a utilizar"
    )

    parser.add_argument(
        "--results",
        type=int,
        default=2,
        help="Cantidad de fragmentos recuperados desde ChromaDB"
    )

    args = parser.parse_args()

    print("\n==============================")
    print("ASISTENTE RAG ERP - CBMM")
    print("==============================")
    print(f"Proveedor: {args.provider}")
    print(f"Modelo: {args.model}")
    print(f"Pregunta: {args.question}")

    print("\n🔎 Buscando contexto en ChromaDB...\n")
    context = search_context(args.question, args.results)

    print("📚 Contexto encontrado:\n")
    print(context)

    prompt = build_prompt(args.question, context)

    print("\n🤖 Generando respuesta...\n")

    try:
        answer = ask_llm(prompt, args.provider, args.model)

        print("\n✅ Respuesta final:\n")
        print(answer)

    except Exception as error:
        print("\n❌ Ocurrió un error:\n")
        print(error)


if __name__ == "__main__":
    main()