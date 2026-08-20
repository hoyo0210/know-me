FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY know_me ./know_me
COPY corpus.example ./corpus.example
COPY persona.example ./persona.example
RUN pip install --no-cache-dir -e .
ENV KNOW_ME_CORPUS_ROOT=corpus.example
ENV KNOW_ME_PERSONA_DIR=persona.example
EXPOSE 8000
CMD ["know-me", "serve", "--host", "0.0.0.0", "--port", "8000"]
