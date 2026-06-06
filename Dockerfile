FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 9999

CMD ["waitress-serve", "--host=0.0.0.0", "--port=9999", "app:app"]
