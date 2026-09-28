from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document


def create_vector_store(chunks: list[Document], embedding_model):
    """
    Build a FAISS vector store from a list of LangChain Documents.
    """
    return FAISS.from_documents(
        documents=chunks,
        embedding=embedding_model,
    )
