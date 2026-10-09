FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    tzdata \
    fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# Install, then remove the installers. Nothing at runtime imports pip or
# setuptools (checked: the only references are cffi's build helpers and
# Werkzeug's demo app), but both ship vendored copies of msgpack and urllib3
# that image scanners flag -- and that cannot be fixed by upgrading pip, which
# vendors them at whatever version it was released with. An image without a
# package manager is also one less tool for anyone who gets a shell in it.
# Same layer as the install, so the removed files do not survive underneath.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y pip setuptools \
    && rm -rf /usr/local/lib/python3.13/site-packages/{pip,setuptools,pkg_resources,_distutils_hack}* \
              /usr/local/lib/python3.13/site-packages/distutils-precedence.pth

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

# Liveness for `docker ps`, compose `depends_on: service_healthy`, and any
# orchestrator that restarts unhealthy containers.
#
# /login is the one path the app serves without a session (check_auth
# allowlists it), and it renders a real template -- so a pass means Flask and
# gunicorn are actually serving, not merely that the port is open. gunicorn
# always binds plain HTTP here; FORCE_HTTPS only marks the session cookie
# secure and never redirects, so this holds behind a TLS proxy too.
#
# Uses Python rather than curl or wget: neither is in python:*-slim, and the
# interpreter is guaranteed present. The client timeout stays below the
# HEALTHCHECK timeout so a hung request is reported by us, not killed by Docker.
HEALTHCHECK --interval=30s --timeout=6s --start-period=20s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/login', timeout=4).status == 200 else 1)"

ENTRYPOINT ["./entrypoint.sh"]
