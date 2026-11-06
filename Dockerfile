FROM python@sha256:af8a5aa14780025dbfbcfbc81bd8a91ebd87711dd5b0f48ed71090949dd7cb1d

# garmindb 3.9.0 — the newest PyPI release whose dependency tree resolves.
# 4.0.0 (PyPI, 2026-09-16) pins fitfile>=2.0.0 and idbutils>=1.3.0, versions
# never published to PyPI or GitHub; upstream develop still requires them.
# deliberate: 3.9.0 rather than latest 4.0.0 — upgrade when upstream publishes
# the missing fitfile/idbutils versions. Renovate
# tracks the pip pin through the pypi datasource (see renovate.json) and
# rewrites the base-image digest pin (see the tag@digest form above).
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      sqlite3 \
      tini && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "garmindb==3.9.0"

ENV HOME=/local-databases \
    PYTHONUNBUFFERED=1

RUN groupadd -r -g 1000 garmin && useradd -r -u 1000 -g garmin -d /local-databases -s /sbin/nologin garmin && \
    mkdir -p /local-databases && chown garmin:garmin /local-databases

# garmindb_cli.py opens garmindb.log relative to the current working
# directory, so WORKDIR must be a non-root-owned writable path. WORKDIR also
# keeps the smoke test (--help) working in a bare docker run without mounts.
WORKDIR /local-databases

COPY --chmod=0755 entrypoint.sh /entrypoint.sh

USER garmin

# The -g flag forwards signals to the child's process group, ensuring sleep(1)
# is also terminated on SIGTERM for graceful container shutdown.
ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["/entrypoint.sh"]
