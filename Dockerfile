FROM python:3.13-slim-bookworm AS dependencies
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements/base.txt requirements/base.txt
RUN pip install --no-cache-dir --require-hashes -r requirements/base.txt

FROM python:3.13-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY --from=dependencies /opt/venv /opt/venv
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
COPY --chown=app:app . .
USER app
EXPOSE 8000 9000
ENTRYPOINT ["sh", "/app/entrypoint.sh"]
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app.main:create_app()"]

FROM runtime AS test
ENV COVERAGE_FILE=/tmp/fastapi-test.coverage
USER root
COPY requirements/dev.txt requirements/dev.txt
RUN pip install --no-cache-dir --require-hashes -r requirements/dev.txt
USER app
CMD ["python", "-m", "scripts.test"]
