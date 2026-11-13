# Werkt op Raspberry Pi 4 (arm64) én op x86-64.
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

# Eén proces: http op 8080 en (zodra er een certificaat is) https op 8443, met dezelfde
# collector en sessies. Elke verbinding krijgt een eigen thread (ook open consoles).
EXPOSE 8443
CMD ["python", "run.py", "--no-browser", "--host", "0.0.0.0", "--port", "8080"]
