# Use a lightweight Python base image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install system dependencies required for audio processing (ffmpeg, libsndfile)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file and install python dependencies
COPY requirements.txt .

# Install dependencies
# Using --no-cache-dir to keep the image size smaller
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Create uploads and outputs directories
RUN mkdir -p uploads outputs

# Expose the port Render expects
EXPOSE 10000

# Start the FastAPI server on port 10000 (Render's default, or uses the $PORT env variable)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
