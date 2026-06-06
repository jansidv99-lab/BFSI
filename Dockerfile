FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY pages/ pages/
COPY ingestion/ ingestion/
COPY agents/ agents/
COPY auth/ auth/
COPY api/ api/
COPY utils/ utils/
COPY evals/ evals/
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.headless=true"]
