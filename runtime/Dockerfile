FROM python:3.12-slim

WORKDIR /app
COPY runtime.py ./

RUN pip install requests

CMD ["python", "runtime.py"]