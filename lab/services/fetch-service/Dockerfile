# fetch-service: a deliberately dangerous internal service used for the
# M2 transitive-enforcement experiment. See app.py.

FROM python:3.12-slim

WORKDIR /app
COPY lab/services/fetch-service/app.py /app/app.py

RUN useradd --create-home --shell /usr/sbin/nologin svc
USER svc

EXPOSE 9000
CMD ["python3", "/app/app.py"]
