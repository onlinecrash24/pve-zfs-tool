FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENV FLASK_APP=app.main
ENV FLASK_ENV=production

EXPOSE 5000

ENTRYPOINT ["./entrypoint.sh"]
