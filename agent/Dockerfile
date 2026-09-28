# Smart Bike Station agent as a container (alternative to the install script on Raspberry Pi OS).
#   docker build -t bike-station-agent agent/
#   docker run -d --name bike-agent --restart unless-stopped -v bike-agent:/data \
#     --device /dev/ttyACM0 -e BIKE_PLATFORM_URL=https://192.168.1.50 -e BIKE_PAIRING_CODE=XXXXX-XXXXX \
#     -e BIKE_CA_FINGERPRINT=<SHA-256 from the portal> bike-station-agent
# Updates come with a new image (self-update is disabled in containers).
# Behind a TLS-intercepting proxy: docker build --secret id=ca,src=proxy-ca.crt ...
# Registry mirror: --build-arg PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi \
 && pip install --no-cache-dir "pyserial==3.5" "paho-mqtt==2.1.0" \
 && useradd --system --uid 10001 --gid dialout --home-dir /data --shell /usr/sbin/nologin bikeagent \
 && mkdir -p /data /opt/bike-agent && chown bikeagent:dialout /data

COPY VERSION /opt/bike-agent/VERSION
COPY bikeagent /opt/bike-agent/bikeagent
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

ENV PYTHONPATH=/opt/bike-agent \
    PYTHONUNBUFFERED=1 \
    BIKE_AGENT_STATE=/data \
    BIKE_AGENT_SELF_UPDATE=0

USER bikeagent
VOLUME ["/data"]
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
