FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .

# Uncomment the following lines if the PyPI mirror does not provide precompiled asyncpg wheels.
# RUN apt-get update && \
#     apt-get install -y --no-install-recommends gcc libpq-dev && \
#     rm -rf /var/lib/apt/lists/*

# Use the local PyPI mirror to avoid any need for international internet access.
RUN pip install --no-cache-dir \
    -r requirements.txt \
    -i https://pypi.devneeds.ir/simple/

COPY . .

CMD ["python", "-m", "bot.main"]