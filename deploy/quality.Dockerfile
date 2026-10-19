FROM python:3.12.15-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 HOME=/tmp XDG_CACHE_HOME=/tmp/cache PIP_CACHE_DIR=/tmp/pip-cache
WORKDIR /workspace/consultora-tickets-backend
COPY requirements.txt requirements-ci.txt ./
RUN pip install --no-cache-dir -r requirements-ci.txt
COPY app ./app
COPY scripts ./scripts
COPY pyproject.toml ./
RUN useradd -r -u 1000 checker && chown -R checker /workspace
USER checker
CMD ["ruff", "check", "--no-cache", "."]
