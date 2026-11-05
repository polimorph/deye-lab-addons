ARG BUILD_FROM=python:3.12-slim-bookworm
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Dependențe de sistem pentru numpy/h5py/cvxpy (aceleași familii ca imaginea EMHASS oficială).
# EMHASS însuși NU e instalat aici: se instalează la pornire într-un venv din /data,
# la exact versiunea de producție, ca să nu fie nevoie de rebuild la fiecare update.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl tini \
        gcc g++ cmake ninja-build patchelf \
        libgfortran5 libopenblas0-pthread libopenblas-dev \
        libhdf5-dev libhdf5-serial-dev libaec0 libsz2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY run.sh /app/run.sh
COPY lab /app/lab
RUN chmod +x /app/run.sh

ENTRYPOINT ["/usr/bin/tini", "--", "/app/run.sh"]
