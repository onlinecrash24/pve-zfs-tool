FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    tzdata \
    fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENV FLASK_APP=app.main
ENV FLASK_ENV=production

# The release this image was built from, passed in by CI as the git tag. Kept
# as a build arg rather than a file in the repo so it cannot drift from the tag
# it claims to be -- there is nothing to remember to bump. A source build with
# no --build-arg says "dev", which is honest: it is not a release.
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION

EXPOSE 5000

ENTRYPOINT ["./entrypoint.sh"]
