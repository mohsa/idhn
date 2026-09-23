# exfil-sink: a controlled receiver for IDHN's M4 containment experiment.
# See app.py.

FROM python:3.12-slim

WORKDIR /app
COPY lab/services/exfil-sink/app.py /app/app.py

RUN useradd --create-home --shell /usr/sbin/nologin svc
USER svc

EXPOSE 9100
CMD ["python3", "/app/app.py"]
