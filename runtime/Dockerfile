FROM python:3.12-slim

WORKDIR /app
COPY runtime/runtime.py runtime/handler.py ./

RUN pip install requests

CMD ["python", "runtime.py"]