# Use the official Python image from Docker Hub
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements.txt file into the container
COPY requirements.txt .

# Install the dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the bot files into the container
COPY . .

# Expose the port if needed (for health checks)
EXPOSE 8080

# Set the environment variable for the bot
ENV PORT=8080

# Command to run the bot (use your bot script here)
CMD ["python", "bot.py"]
