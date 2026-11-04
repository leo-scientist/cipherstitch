# Popular tools for tiers 1 and 3 references (arm64 native under OrbStack).
#   docker build -t cipherstitch-detectability-tools benchmarks/detectability/tools
# The tools named below are pinned; Debian's packages are trixie's at build time (their versions
# go to results/environment.txt). StegExpose has no licence: downloaded here, never vendored.

FROM debian:trixie-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ca-certificates cargo cmake curl git libfontconfig1-dev \
        libjpeg-dev liblzma-dev libmcrypt-dev libmhash-dev pkg-config rustc zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# binwalk v3.1.0 (Debian ships 2.x)
RUN cargo install binwalk --version 3.1.0 --locked --root /opt/binwalk

# StegSeek v0.6 (its release has an amd64 .deb only)
ARG STEGSEEK_COMMIT=0e10a674b326298c5d51af8f2f7219ccac45e123
RUN git clone https://github.com/RickdeJager/stegseek.git /src/stegseek \
    && git -C /src/stegseek checkout "$STEGSEEK_COMMIT" \
    && cmake -S /src/stegseek -B /src/stegseek/build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /src/stegseek/build -j "$(nproc)" \
    && install -m 755 /src/stegseek/build/src/stegseek /usr/local/bin/stegseek

FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        binutils ca-certificates curl default-jre-headless file foremost libfontconfig1 \
        libimage-exiftool-perl libjpeg62-turbo libmcrypt4 libmhash2 outguess pngcheck \
        ruby steghide unzip \
    && rm -rf /var/lib/apt/lists/*

RUN gem install zsteg -v 0.2.14 --no-document

# StegExpose at its last commit (2018-08-14)
ARG STEGEXPOSE_COMMIT=e8312cb0973d2a97deffc771d8e5910df8494698
RUN mkdir -p /opt/stegexpose \
    && curl -fsSL -o /opt/stegexpose/StegExpose.jar \
        "https://github.com/b3dk7/StegExpose/raw/${STEGEXPOSE_COMMIT}/StegExpose.jar"

# OpenStego 0.8.6
RUN curl -fsSL -o /tmp/openstego.zip \
        https://github.com/syvaidya/openstego/releases/download/openstego-0.8.6/openstego-0.8.6.zip \
    && unzip -q /tmp/openstego.zip -d /opt \
    && mv /opt/openstego-0.8.6 /opt/openstego \
    && rm /tmp/openstego.zip

COPY --from=build /opt/binwalk/bin/binwalk /usr/local/bin/binwalk
COPY --from=build /usr/local/bin/stegseek /usr/local/bin/stegseek

WORKDIR /data
