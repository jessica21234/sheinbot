FROM mcr.microsoft.com/playwright/python:v1.44.0-noble

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright est déjà installé dans l'image, on installe juste chromium
RUN playwright install chromium

COPY . .

CMD ["python", "shein_bot.py"]
