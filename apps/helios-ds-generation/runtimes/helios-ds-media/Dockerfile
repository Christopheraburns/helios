# helios-ds-media-runtime: helios-ds-runtime + FFmpeg and eSpeak NG for audio/video workers.
# Build helios-ds-runtime first and pass its pushed reference as BASE_IMAGE.
# The registered image digest is the reproducibility anchor for media bytes
# (spec: media byte determinism); tool versions are recorded in the image.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg espeak-ng \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /opt/helios-ds \
 && { ffmpeg -version | head -1; espeak-ng --version; } > /opt/helios-ds/media-tool-versions.txt
USER cdsw

ENV ML_RUNTIME_EDITION="helios-ds-media" \
    ML_RUNTIME_DESCRIPTION="helios-ds-media: helios-ds runtime plus FFmpeg and eSpeak NG"

LABEL com.cloudera.ml.runtime.edition=$ML_RUNTIME_EDITION \
      com.cloudera.ml.runtime.description=$ML_RUNTIME_DESCRIPTION
