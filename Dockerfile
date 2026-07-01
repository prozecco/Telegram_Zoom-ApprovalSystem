FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Expose port 7860 for Hugging Face Spaces health checks
EXPOSE 7860
ENV PORT=7860

# Run the application
CMD ["python", "app.py"]
