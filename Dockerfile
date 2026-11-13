# supermicro-x9-webkvm: browser KVM + IPMI statistics for Supermicro X9 (ATEN) BMCs

# --- noVNC (core/ and vendor/ modules only, pinned version) ----------------------------------
FROM python:3.12-slim AS novnc
ARG NOVNC_VERSION=1.6.0
# sha256 of the v1.6.0 tarball; set it empty to skip the check (e.g. with another version)
ARG NOVNC_SHA256=5066103959ef4e9b10f37e5a148627360dd8414e4cf8a7db92bdbd022e728aaa
ADD https://github.com/novnc/noVNC/archive/refs/tags/v${NOVNC_VERSION}.tar.gz /tmp/novnc.tgz
RUN set -e; \
    if [ -n "$NOVNC_SHA256" ]; then echo "$NOVNC_SHA256  /tmp/novnc.tgz" | sha256sum -c -; fi; \
    mkdir -p /opt/novnc; \
    tar -xzf /tmp/novnc.tgz -C /opt/novnc --strip-components=1 \
        noVNC-${NOVNC_VERSION}/core noVNC-${NOVNC_VERSION}/vendor noVNC-${NOVNC_VERSION}/LICENSE.txt

# --- final image -------------------------------------------------------------------------------
FROM python:3.12-slim
# ipmitool is optional (IPMI_BACKEND=ipmitool); the default backend is pure Python (pyghmi)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ipmitool \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 1000 --home /app webkvm \
 && mkdir -p /data && chown webkvm /data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=novnc /opt/novnc /opt/novnc
COPY webkvm/ /app/webkvm/

ENV NOVNC_DIR=/opt/novnc \
    DATA_DIR=/data \
    PYTHONUNBUFFERED=1
USER webkvm
VOLUME ["/data"]
EXPOSE 8080 5900
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)" || exit 1
CMD ["python", "-m", "webkvm"]
