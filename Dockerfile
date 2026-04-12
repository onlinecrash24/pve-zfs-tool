FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for running the application
RUN groupadd -r zfstool && useradd -r -g zfstool -d /home/zfstool -m zfstool \
    && mkdir -p /home/zfstool/.ssh /app/data \
    && chown -R zfstool:zfstool /home/zfstool /app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh && chown -R zfstool:zfstool /app

ENV FLASK_APP=app.main
ENV FLASK_ENV=production

USER zfstool

EXPOSE 5000

ENTRYPOINT ["./entrypoint.sh"]
