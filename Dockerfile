# The plugin's image. It runs beside a sidecar in the deployment's cluster, and
# reaches nothing but that sidecar and Alpaca's market data API.
#
# Built on the base image for the SDK this plugin pins: Python and
# open-meridian, the same version as pyproject.toml's, already installed. So
# the image's own layer is this plugin alone (it has no other dependency), and
# an upload sends only that. Move the two together when moving to a new SDK.
#
# BASE is for building against a base made locally rather than a released one.
ARG BASE=ghcr.io/open-meridian/plugin-python:0.26.0
FROM ${BASE}
WORKDIR /plugin
# Installed from a mount rather than copied, so the image holds the installed
# package and no source beside it.
RUN --mount=type=bind,target=/tmp/src,rw pip install --no-cache-dir /tmp/src
USER 65532
CMD ["alpaca"]
