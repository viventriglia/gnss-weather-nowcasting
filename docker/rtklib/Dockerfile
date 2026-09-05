FROM python:3.11-slim-bookworm

ARG RTKLIB_COMMIT=180043ee24b6d2b168f98b64be15f69d50046b1a

RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential ca-certificates gfortran git \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/tomojitakasu/RTKLIB.git /opt/rtklib \
    && cd /opt/rtklib \
    && git checkout "${RTKLIB_COMMIT}"

RUN cd /opt/rtklib/lib/iers/gcc \
    && make \
    && cd /opt/rtklib/app/consapp/rnx2rtkp/gcc \
    && make \
    && install -m 0755 rnx2rtkp /usr/local/bin/rnx2rtkp

RUN python -m pip install --no-cache-dir hatanaka==2.8.1

COPY container_runner.py /usr/local/bin/gnss-weather-rtklib
RUN chmod 0755 /usr/local/bin/gnss-weather-rtklib

ENTRYPOINT ["gnss-weather-rtklib"]
