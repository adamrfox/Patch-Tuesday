FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p /app/web/templates
RUN mkdir -p /app/app

# Expose port
EXPOSE 5000

# Run as root (bind mount requires write access on the host path)
# RUN adduser --disabled-password --gecos '' appuser
# USER appuser

# Run the application
CMD ["python", "-m", "app"]