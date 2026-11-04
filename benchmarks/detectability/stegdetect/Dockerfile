# stegdetect 0.6 for tier 1 (best effort: dead since 2004, 32-bit era code; amd64 under Rosetta).
#   docker build --platform linux/amd64 -t cipherstitch-stegdetect benchmarks/detectability/stegdetect

FROM --platform=linux/amd64 ubuntu:18.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        autoconf automake bison build-essential ca-certificates flex git libtool \
    && rm -rf /var/lib/apt/lists/*

ARG STEGDETECT_COMMIT=28a4f074a71c682581314491224f9f41511c82e7
RUN git clone https://github.com/abeluck/stegdetect.git /src/stegdetect \
    && git -C /src/stegdetect checkout "$STEGDETECT_COMMIT"

WORKDIR /src/stegdetect
RUN linux32 ./configure || ./configure
RUN make CFLAGS="-O2 -fcommon -Wno-error" || make
RUN install -m 755 stegdetect /usr/local/bin/stegdetect

WORKDIR /data
