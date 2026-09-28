# Smart Bike Station platform as a container - alternative to the Proxmox LXC (deploy/install-server.sh).
# Build from the repository root:  docker compose -f deploy/docker/compose.yaml up -d --build
# On the first start it creates a data key and its own CA + server certificate in /data (volume) and
# prints a one-time setup link in the log:  docker compose -f deploy/docker/compose.yaml logs platform
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
ARG WITH_ML=1

RUN useradd --system --uid 10001 --user-group --home-dir /data --shell /usr/sbin/nologin bikestation \
 && mkdir -p /data /opt/bike-station && chown bikestation:bikestation /data

COPY server/requirements.txt server/requirements-ml.txt /opt/bike-station/server/
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi \
 && pip install --no-cache-dir -r /opt/bike-station/server/requirements.txt \
 && if [ "$WITH_ML" = "1" ]; then pip install --no-cache-dir -r /opt/bike-station/server/requirements-ml.txt; fi

COPY server /opt/bike-station/server
COPY web /opt/bike-station/web
COPY agent /opt/bike-station/agent
COPY ml /opt/bike-station/ml
COPY deploy/docker/certs.py /opt/bike-station/deploy/certs.py
COPY deploy/docker/entrypoint.sh /usr/local/bin/bike-station-entrypoint
RUN printf '#!/bin/sh\nexec /usr/local/bin/bike-station-entrypoint cli "$@"\n' > /usr/local/bin/bike-station \
 && chmod 0755 /usr/local/bin/bike-station /usr/local/bin/bike-station-entrypoint
# Demo AI model trained on SIMULATED data (labelled as such in the portal); replace it with a real one later.
RUN if [ "$WITH_ML" = "1" ] && ! ls /opt/bike-station/ml/models/*.joblib >/dev/null 2>&1; then \
      cd /opt/bike-station && python ml/generate_synthetic.py >/dev/null && python ml/train.py ml/data/synthetic.csv >/dev/null \
      && rm -f ml/data/*.csv; \
    fi && find /opt/bike-station -name __pycache__ -prune -exec rm -rf {} +

ENV PYTHONUNBUFFERED=1 BIKE_CONFIG=/opt/bike-station/server/config.toml
WORKDIR /opt/bike-station/server
USER bikestation
VOLUME ["/data"]
EXPOSE 8443
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import ssl, urllib.request; urllib.request.urlopen('https://127.0.0.1:8443/health', context=ssl._create_unverified_context(), timeout=4)"
ENTRYPOINT ["/usr/local/bin/bike-station-entrypoint"]
