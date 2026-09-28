# Home Assistant add-on image of the Smart Bike Station agent (BUILD_FROM comes from build.yaml).
ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM}

RUN pip install --no-cache-dir "pyserial==3.5" "paho-mqtt==2.1.0"

COPY VERSION /opt/bike-agent/VERSION
COPY bikeagent /opt/bike-agent/bikeagent
COPY run.sh /run.sh

# /data is the add-on's persistent storage (options.json from Home Assistant + the agent state).
# Self-update is off: Home Assistant updates the add-on as a whole.
ENV PYTHONPATH=/opt/bike-agent PYTHONUNBUFFERED=1 BIKE_AGENT_STATE=/data BIKE_AGENT_SELF_UPDATE=0
RUN chmod a+x /run.sh
CMD ["/run.sh"]
