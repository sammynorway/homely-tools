FROM python:latest

WORKDIR /homely-tools

COPY mqtt/requirements.txt /homely-tools/mqtt/requirements.txt

RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r /homely-tools/mqtt/requirements.txt

COPY API /homely-tools/API
COPY mqtt /homely-tools/mqtt

ENV PYTHONPATH="/homely-tools/API"
CMD [ "python3", "/homely-tools/mqtt/homely2mqtt.py", "-v" ]
