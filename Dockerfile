FROM python:3.11-slim

WORKDIR /usr/src/app/socket

COPY requirements.txt /usr/src/app/socket

RUN pip install -r /usr/src/app/socket/requirements.txt

COPY . /usr/src/app/socket
