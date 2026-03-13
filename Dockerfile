# --- Build stage: install dependencies + build UI if needed ---
FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project files
COPY . .

# Create a minimal UI dist placeholder (pyproject.toml force-includes ui/dist)
RUN mkdir -p ui/dist && \
    echo '<html><body>E2E Test</body></html>' > ui/dist/index.html

# hatch-vcs needs git to determine version; .git is excluded by .dockerignore.
# Use SETUPTOOLS_SCM_PRETEND_VERSION to provide a fake version for the build.
ARG SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0.dev0
RUN SETUPTOOLS_SCM_PRETEND_VERSION=${SETUPTOOLS_SCM_PRETEND_VERSION} \
    pip install --no-cache-dir -e .

# Runtime config
ENV VIBE_REMOTE_HOME=/data/vibe_remote
ENV PYTHONUNBUFFERED=1

EXPOSE 5123

COPY scripts/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
