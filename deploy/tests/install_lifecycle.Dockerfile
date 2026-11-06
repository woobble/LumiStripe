FROM debian:trixie-slim

RUN apt-get update \
  && apt-get install --yes --no-install-recommends \
    bash ca-certificates coreutils findutils git gawk grep gzip python3 sed sudo util-linux \
  && rm -rf /var/lib/apt/lists/*

COPY repo/ /opt/lumistripe/
COPY install_lifecycle.sh /usr/local/sbin/lumistripe-install-lifecycle
COPY install_binary_lifecycle.sh /usr/local/sbin/lumistripe-install-binary-lifecycle
COPY install_lifecycle_setup.sh /tmp/install_lifecycle_setup.sh

RUN chmod 755 \
      /usr/local/sbin/lumistripe-install-lifecycle \
      /usr/local/sbin/lumistripe-install-binary-lifecycle \
  && bash /tmp/install_lifecycle_setup.sh \
  && rm /tmp/install_lifecycle_setup.sh

CMD ["/usr/local/sbin/lumistripe-install-lifecycle"]
