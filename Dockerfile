FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY trendscout ./trendscout
RUN pip install --no-cache-dir .

# Mount a folder here to keep config.yaml, history and reports between restarts.
WORKDIR /radar
COPY config.yaml /radar/config.yaml

# The app, scanning on the schedule in /radar/config.yaml.
# Publish it on your own machine only: -p 127.0.0.1:8765:8765
EXPOSE 8765

ENTRYPOINT ["trendscout"]
CMD ["ui", "--host", "0.0.0.0", "--no-browser"]
