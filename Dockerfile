# Pinned by digest so a rebuild months from now produces the same base rather
# than whatever :3.10-slim-bookworm happens to point at. Bump deliberately.
ARG PYTHON_IMAGE=python@sha256:68d914ec641a0b69267ce65184d000a2bc3a9ee2590ab702b82250ab2385735a

# ---------------------------------------------------------------- build stage
FROM ${PYTHON_IMAGE} AS builder

# Compilers and headers are needed to build any dependency without a wheel for
# this platform. They stay in this stage and never reach the runtime image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Dependencies first so code changes do not invalidate the pip layer.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ------------------------------------------------------------------ dev stage
# Test tooling, kept out of the runtime image. The runtime user deliberately
# cannot write to the virtualenv, so tests get their own target rather than
# the production image being loosened to accommodate them.
#
#   docker build --target dev -t carprice:dev .
#   docker run --rm carprice:dev pytest
FROM builder AS dev

COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

WORKDIR /app
COPY . .
CMD ["pytest"]

# -------------------------------------------------------------- runtime stage
FROM ${PYTHON_IMAGE} AS runtime

# libgomp is the OpenMP runtime scikit-learn links against. It is the one
# system library the slim image is missing that the model actually needs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --create-home --home-dir /home/app app

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --chown=app:app . .
RUN chmod +x scripts/entrypoint.sh

# Nothing in this container needs to write outside /tmp, and a process that
# does not need root should not have it.
USER app

EXPOSE 8000

CMD ["./scripts/entrypoint.sh"]
