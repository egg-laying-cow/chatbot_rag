import os
from flask import render_template, stream_with_context, current_app
from langchain_elasticsearch import ElasticsearchStore
from elasticsearch_client import (
    elasticsearch_client,
    get_elasticsearch_chat_message_history,
)
from llm_integrations import get_llm
from grade_documents import document_relevant, YES, NO, AMBIGUOUS
from web_search import web_search

INDEX = os.getenv("ES_INDEX", "workplace-app-docs")
INDEX_CHAT_HISTORY = os.getenv(
    "ES_INDEX_CHAT_HISTORY", "workplace-app-docs-chat-history"
)
ELSER_MODEL = os.getenv("ELSER_MODEL", ".elser_model_2")
SESSION_ID_TAG = "[SESSION_ID]"
SOURCE_TAG = "[SOURCE]"
DONE_TAG = "[DONE]"


store = ElasticsearchStore(
    es_connection=elasticsearch_client,
    index_name=INDEX,
    strategy=ElasticsearchStore.SparseVectorRetrievalStrategy(model_id=ELSER_MODEL),
)


@stream_with_context
def ask_question(question, session_id):
    yield f"data: {SESSION_ID_TAG} {session_id}\n\n"
    current_app.logger.debug("Chat session ID: %s", session_id)

    chat_history = get_elasticsearch_chat_message_history(
        INDEX_CHAT_HISTORY, session_id
    )

    if len(chat_history.messages) > 0:
        # nhờ llm tạo câu hỏi cô đọng từ lịch sử và câu hỏi hiện tại của người dùng
        condense_question_prompt = render_template(
            "condense_question_prompt.txt",
            question=question,
            chat_history=chat_history.messages,
        )
        condensed_question = get_llm().invoke(condense_question_prompt).content
    else:
        condensed_question = question

    current_app.logger.debug("Condensed question: %s", condensed_question)
    current_app.logger.debug("Question: %s", question)

    # trả về tài liệu sau khi tìm kiếm trong cơ sở dũ liệu
    docs = store.as_retriever().invoke(condensed_question)
    
    current_app.logger.debug("%s", docs)

    # kiểm tra tài liệu trả về có liên quan hay không
    is_relevant = document_relevant(question=question, docs=docs, chat_history=chat_history.messages)

    if is_relevant == YES:
        prompt_file = "rag_prompt.txt"
        yield f"data: ***Sử dụng dữ liệu trong kho dữ liệu*** <br><br>"
    elif is_relevant == NO:
        # tra dữ liệu trên internet
        docs = web_search(condensed_question)
        current_app.logger.debug("internet: %s", docs)
        prompt_file = "rag_prompt2.txt"
        yield f"data: ***Tra cứu dữ liệu trên internet*** <br><br>"
    else:
        # Sử dụng dữ liệu trong kho dữ liệu kết hợp tra cứu dữ liệu trên internet
        docs.append(web_search(condensed_question))
        current_app.logger.debug("internet: %s", docs)
        prompt_file = "rag_prompt2.txt"
        yield f"data: ***Sử dụng dữ liệu trong kho dữ liệu kết hợp tra cứu dữ liệu trên internet*** <br><br>"

    # tạo prompt
    qa_prompt = render_template(
        prompt_file,
        question=question,
        docs=docs,
        chat_history=chat_history.messages,
    )

    current_app.logger.debug("/n/n")
    current_app.logger.debug("prompt: %s", qa_prompt)
    current_app.logger.debug("/n/n")

    answer = ""

    # gủi prompt và nhận câu trả lời
    for chunk in get_llm().stream(qa_prompt):
        content = chunk.content.replace(
            "\n", "<p>"
        )  # the stream can get messed up with newlines
        yield f"data: {content}\n\n"
        answer += chunk.content

    yield f"data: {DONE_TAG}\n\n"
    current_app.logger.debug("Answer: %s", answer)

    chat_history.add_user_message(question)
    chat_history.add_ai_message(answer)
