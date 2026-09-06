FROM python:3.11.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_HEADLESS=true

WORKDIR /app

RUN groupadd --system appuser \
    && useradd --system --gid appuser --create-home appuser

COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m pip install --no-cache-dir .

COPY streamlit_app.py runtime-assets.json ./
COPY data/processed ./data/processed

RUN chown -R appuser:appuser /app /home/appuser
USER appuser

EXPOSE 8501

CMD ["python", "-m", "streamlit", "run", "streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
