#!/bin/sh
# DSM-Fenster → Ordner-Regal. Sucht ein Python 3 und startet die Weiterleitung.
TARGET="${ORG_PKG_TARGET:-/var/packages/ordnerregal/target}"
for PY in $(cat "$TARGET/.python" 2>/dev/null) /usr/bin/python3 /usr/local/bin/python3 /bin/python3 \
          /var/packages/Python3*/target/usr/bin/python3 /var/packages/python3*/target/bin/python3; do
    if [ -x "$PY" ]; then
        exec "$PY" "$TARGET/dsm_proxy.py"
    fi
done
printf 'Status: 503 Service Unavailable\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nKein Python 3 gefunden.\n'
