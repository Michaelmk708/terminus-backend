FROM python:3.11-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1     PYTHONDONTWRITEBYTECODE=1     DEBIAN_FRONTEND=noninteractive     PATH="/opt/venv/bin:$PATH"     PYTHONPATH=/app

# 1. Disable APT translations, pdiffs, and force IPv4 to minimize RAM & bandwidth usage
RUN echo 'Acquire::Languages "none";' > /etc/apt/apt.conf.d/99lean &&     echo 'Acquire::PDiffs "false";' >> /etc/apt/apt.conf.d/99lean &&     echo 'Acquire::ForceIPv4 "true";' >> /etc/apt/apt.conf.d/99lean &&     echo 'Acquire::http::Pipeline-Depth "0";' >> /etc/apt/apt.conf.d/99lean

# 2. Standalone APT update (cached permanently upon success)
RUN apt-get update

# 3. Install lightweight system C-libraries
RUN apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libpq5 curl

# 4. Install heavier OCR and PDF utilities
RUN apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-eng     && apt-get clean && rm -rf /var/lib/apt/lists/*

# 5. Isolated Python virtualenv and dependency installation
RUN python -m venv /opt/venv
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip &&     pip install --no-cache-dir -r requirements.txt

COPY . /app

EXPOSE 8000

# Render sets the PORT env var dynamically. Uvicorn must bind to it via shell evaluation.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
