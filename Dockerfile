FROM python:latest

ADD app.py /
ADD requirements.txt /

RUN pip install -r requirements.txt

CMD [ "python", "./app.py" ]