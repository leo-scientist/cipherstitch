# HStego v0.6.1 (MIT) for the references (amd64 under Rosetta: its STC code uses SSE2).
#   docker build --platform linux/amd64 -t cipherstitch-hstego benchmarks/detectability/hstego
# Its JPEG toolbox needs IJG libjpeg 9, which Debian no longer ships: 9c, the version HStego
# bundles for Windows, is built here. HStego declares no dependencies: these are its README's.

FROM --platform=linux/amd64 debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl git python3-dev python3-venv \
    && rm -rf /var/lib/apt/lists/*

ARG LIBJPEG_SHA256=682aee469c3ca857c4c38c37a6edadbfca4b04d42e56613b11590ec6aa4a278d
RUN curl -fsSL -o /tmp/jpegsrc.tar.gz https://www.ijg.org/files/jpegsrc.v9c.tar.gz \
    && echo "$LIBJPEG_SHA256  /tmp/jpegsrc.tar.gz" | sha256sum -c - \
    && tar xzf /tmp/jpegsrc.tar.gz -C /tmp \
    && cd /tmp/jpeg-9c \
    && ./configure --prefix=/opt/libjpeg --disable-static \
    && make -j "$(nproc)" \
    && make install \
    && cp jpegint.h /opt/libjpeg/include/ \
    && rm -rf /tmp/jpeg-9c /tmp/jpegsrc.tar.gz

ARG HSTEGO_COMMIT=bf71f6e0d7faaa632ad8a988c0393e94bfd13b2a
RUN python3 -m venv /opt/hstego \
    && /opt/hstego/bin/pip install --no-cache-dir imageio==2.37.0 numba==0.61.2 \
        numpy==2.2.6 Pillow==11.3.0 pycryptodome==3.23.0 scipy==1.15.3 \
    && CFLAGS="-I/opt/libjpeg/include" \
        LDFLAGS="-L/opt/libjpeg/lib -Wl,-rpath,/opt/libjpeg/lib" \
        /opt/hstego/bin/pip install --no-cache-dir \
        "git+https://github.com/daniellerch/hstego.git@${HSTEGO_COMMIT}"

COPY embed.py /opt/hstego/embed.py

WORKDIR /data
