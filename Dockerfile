FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY agentpay ./agentpay
RUN pip install --no-cache-dir .
ENV AGENTPAY_DB_PATH=/data/agentpay.db
VOLUME /data
EXPOSE 8080
CMD ["agentpay", "serve", "--host", "0.0.0.0", "--port", "8080"]
