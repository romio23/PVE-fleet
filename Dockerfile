# Works on Raspberry Pi 4 (arm64) and on x86-64.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PVEF_DATA=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY run.py config.example.yml ./

RUN useradd --system --uid 1000 pvef && mkdir -p /data && chown pvef /data
USER pvef

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"

# One process: http on 8080 and (once there is a certificate) https on 8443, with the same
# collector and sessions. Every connection gets its own thread (also open consoles).
EXPOSE 8443
CMD ["python", "run.py", "--no-browser", "--host", "0.0.0.0", "--port", "8080"]
