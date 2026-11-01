#!/bin/sh
# Synology startup hook; the scheduler belongs only to this deployment.
PROGRAM=/volume1/docker/deeptutor/deploy/private-updater/updater.py
STATE=/volume1/docker/deeptutor/.updates
case "${1:-start}" in
  start)
    umask 077
    mkdir -p "$STATE"
    nohup /usr/bin/python3 "$PROGRAM" --daemon >> "$STATE/daemon.log" 2>&1 < /dev/null &
    ;;
  stop)
    /usr/bin/python3 "$PROGRAM" --stop-daemon
    ;;
  status)
    /usr/bin/python3 "$PROGRAM" --daemon-status
    ;;
  *) exit 2 ;;
esac
