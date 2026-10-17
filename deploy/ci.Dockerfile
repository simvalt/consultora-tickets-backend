FROM python:3.12.15-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /workspace/consultora-tickets-backend
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY app ./app
COPY tests ./tests
COPY scripts ./scripts
COPY pyproject.toml ./
RUN useradd -r -u 1000 tester && chown -R tester /workspace
USER tester
CMD ["sh", "-ec", "ruff check . && pip-audit -r requirements.txt && pytest --junitxml=/tmp/backend-tests.xml"]
