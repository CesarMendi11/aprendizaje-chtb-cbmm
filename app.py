import chromadb
import os
import argparse
import requests
from dotenv import load_dotenv
from openai import OpenAI


# Cargar variables del archivo .env
load_dotenv()


# =========================
# 1. BUSCAR CONTEXTO EN CHROMADB
# =========================
def search_context(query, n_results=2):
    """
    Busca en ChromaDB los fragmentos más relacionados con la pregunta.
    """

    client = chromadb.PersistentClient(path="./chroma_db")
    collection = client.get_or_create_collection("erp_docs")

    resultado = collection.query(
        query_texts=[query],
        n_results=n_results
    )

    documentos = resultado.get("documents", [[]])[0]

    if not documentos:
        return "No se encontró contexto relevante en la base documental."

    contexto = "\n\n---\n\n".join(documentos)
    return contexto


# =========================
# 2. CONSTRUIR EL PROMPT
# =========================
def build_prompt(user_question, context):
    """
    Construye el mensaje que se enviará al modelo.
    """

    prompt = f"""
Eres un asistente para el ERP del Cuerpo de Bomberos de Machala.

Responde únicamente usando la información del contexto.
Si la respuesta no está en el contexto, di claramente:
"No encontré esa información en los documentos disponibles."

Contexto:
{context}

Pregunta del usuario:
{user_question}
"""
    return prompt


# =========================
# 3. CONSULTAR OPENROUTER / OPENAI
# =========================
def ask_openai_compatible(prompt, provider, model):
    """
    Sirve para OpenAI y OpenRouter, porque ambos usan cliente tipo OpenAI.
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
        raise ValueError("Proveedor no soportado en ask_openai_compatible")

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )

    return response.choices[0].message.content


# =========================
# 4. CONSULTAR OLLAMA
# =========================
def ask_ollama(prompt, model):
    """
    Hace consulta local a Ollama.
    Debe estar corriendo en tu máquina.
    """

    url = "http://localhost:11434/api/generate"

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False
    }

    response = requests.post(url, json=payload)

    if response.status_code != 200:
        raise ValueError(f"Error al consultar Ollama: {response.text}")

    data = response.json()
    return data["response"]


# =========================
# 5. ELEGIR PROVEEDOR
# =========================
def ask_llm(prompt, provider, model):
    """
    Decide qué proveedor usar.
    """

    if provider in ["openai", "openrouter"]:
        return ask_openai_compatible(prompt, provider, model)

    elif provider == "ollama":
        return ask_ollama(prompt, model)

    else:
        raise ValueError(f"Proveedor no soportado: {provider}")


# =========================
# 6. PROGRAMA PRINCIPAL
# =========================
def main():
    parser = argparse.ArgumentParser(description="Asistente RAG ERP")
    parser.add_argument("question", help="Pregunta del usuario")
    parser.add_argument(
        "--provider",
        default=os.getenv("MODEL_PROVIDER", "openrouter"),
        help="Proveedor del modelo: openrouter, ollama, openai"
    )
    parser.add_argument(
        "--model",
        default=os.getenv("MODEL_NAME", "openrouter/auto"),
        help="Modelo a usar"
    )

    args = parser.parse_args()

    print("\n🔎 Buscando contexto en ChromaDB...\n")
    context = search_context(args.question)

    print("📚 Contexto encontrado:\n")
    print(context)

    prompt = build_prompt(args.question, context)

    print("\n🤖 Generando respuesta...\n")
    answer = ask_llm(prompt, args.provider, args.model)

    print("✅ Respuesta final:\n")
    print(answer)


if __name__ == "__main__":
    main()