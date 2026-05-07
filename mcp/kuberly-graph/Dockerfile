FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN pip install --upgrade pip && pip wheel --no-deps -w /wheels .

FROM python:3.12-slim
WORKDIR /srv
COPY --from=builder /wheels /wheels
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir /wheels/*.whl && pip install --no-cache-dir .
ENV KUBERLY_REPO=/srv/repo
EXPOSE 8765
ENTRYPOINT ["kuberly-graph"]
CMD ["serve", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8765"]
