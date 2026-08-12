FROM python:3.13.13-slim-bookworm@sha256:355bfa66770995d7e9a0da4b3473b44d0cb451f6b56f5615ad9c39e3c4eca03f

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        git=1:2.39.5-0+deb12u3 \
        git-man=1:2.39.5-0+deb12u3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/zzcode-eval
COPY requirements.lock setup.sh evaluate.sh ./
RUN chmod 0555 setup.sh evaluate.sh \
    && ./setup.sh

# DockerRunner also enforces this uid at create time and validates it with inspect.
USER 65532:65532
WORKDIR /workspace
ENTRYPOINT ["/opt/zzcode-eval/evaluate.sh"]
