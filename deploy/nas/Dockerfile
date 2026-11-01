ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG SOURCE_COMMIT
LABEL org.opencontainers.image.source="https://github.com/FSOTM/deeptutor-private"
LABEL org.opencontainers.image.revision="${SOURCE_COMMIT}"
WORKDIR /app
# Frontend and dependencies come from the digest-pinned matching upstream image.
# Python sources are the reviewed Git tree, including all upstream changes.
COPY deeptutor/ /app/deeptutor/
COPY deploy/nas/tests/ /opt/deeptutor-nas/tests/
COPY deploy/nas/verify_runtime.py /opt/deeptutor-nas/verify_runtime.py
COPY deploy/nas/updater.py /opt/deeptutor-nas/updater.py
RUN python -m compileall -q /app/deeptutor && \
    python -m unittest discover -s /opt/deeptutor-nas/tests -p 'test_*.py' -v
