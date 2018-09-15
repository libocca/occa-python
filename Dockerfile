FROM ubuntu:latest

RUN apt-get -yqq update
RUN apt-get -yqq install git python3-pip python3.6

RUN git clone https://github.com/libocca/occa.py
WORKDIR occa.py

RUN git submodule update --init
RUN pip3 install -e .
RUN python3 setup.py build_ext
