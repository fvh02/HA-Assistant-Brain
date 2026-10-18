ARG BUILD_FROM=python:3.12-slim-bookworm
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git tzdata && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# The Agent SDK ships a native Claude Code binary; expose it as `claude` (used with an own token).
RUN bundled="$(python -c 'import claude_agent_sdk, os; print(os.path.join(os.path.dirname(claude_agent_sdk.__file__), "_bundled", "claude"))')" \
    && test -x "$bundled" && ln -s "$bundled" /usr/local/bin/claude && claude --version

COPY app /app
COPY run.sh /run.sh
RUN chmod a+x /run.sh
CMD [ "/run.sh" ]
