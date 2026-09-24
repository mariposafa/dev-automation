# D2: image Python 3.13 yang disiapkan untuk Podman dan OpenShift.
# Image Debian sebelumnya membutuhkan GID 65534 yang tidak tersedia di Sandbox.
FROM quay.io/fedora/python-313:latest

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Direktori kerja bawaan image SCLorg/Fedora.
WORKDIR /opt/app-root/src

# Install Python dependencies
COPY requirements.txt ./

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the FastAPI application code into the container
COPY main.py config.py profiling.py ./

# Expose the port that the FastAPI application will run on
EXPOSE 8000

# Start the FastAPI application
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
