FROM python:3.11-slim-bookworm

# Dépendances système pour Chromium/Playwright sur Debian Bookworm
RUN apt-get update && apt-get install -y \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libxkbcommon0 libpango-1.0-0 libcairo2 libasound2 \
    libatspi2.0-0 libx11-6 libxext6 libxcb1 libxshmfence1 \
    wget ca-certificates fonts-liberation \
    --no-install-recommends && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Installer Playwright + chromium (sans install-deps, les dépendances sont déjà là)
RUN python -m playwright install chromium

COPY . .

CMD ["python", "shein_bot.py"]
