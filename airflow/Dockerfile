FROM apache/airflow:3.2.2-python3.11
USER airflow
COPY requirements.txt constraints-3.11.txt /tmp/project-deps/
RUN pip install --no-cache-dir "apache-airflow==3.2.2" \
    --constraint /tmp/project-deps/constraints-3.11.txt \
    -r /tmp/project-deps/requirements.txt \
    && pip check
WORKDIR /opt/project
