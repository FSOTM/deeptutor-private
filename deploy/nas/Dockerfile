ARG BASE_IMAGE
FROM ${BASE_IMAGE}
LABEL org.opencontainers.image.source="https://github.com/FSOTM/deeptutor-private"
WORKDIR /app
# Python dependencies come from the digest-pinned matching upstream image.
# The complete maintained frontend is built in CI and verified before copying.
RUN pip install --no-cache-dir edge-tts==7.3.1 && rm -rf /app/web
COPY web-runtime/ /app/web/
COPY deeptutor/ /app/deeptutor/
COPY deploy/nas/tests/ /opt/deeptutor-nas/tests/
COPY deploy/nas/verify_runtime.py deploy/nas/updater.py deploy/nas/frontend_bundle.py /opt/deeptutor-nas/
RUN python -m compileall -q /app/deeptutor && \
    python -m unittest discover -s /opt/deeptutor-nas/tests -p 'test_*.py' -v
ARG SOURCE_COMMIT
LABEL org.opencontainers.image.revision="${SOURCE_COMMIT}"
