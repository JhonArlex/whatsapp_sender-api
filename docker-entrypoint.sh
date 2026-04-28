#!/bin/sh
set -e
# Datos empaquetados en la imagen (COPY en Dockerfile). El volumen /app/data puede
# montarse vacío en el servidor y ocultar el contenido; en ese caso rellenamos
# mensaje/ y el CSV por defecto si faltan.
DEFAULT=/opt/default-data
CSV_NAME="${CSV_FILENAME:-grupos_chinatowm.csv}"

mkdir -p /app/data/mensaje

if [ ! -f /app/data/mensaje/msg.txt ] && [ -f "$DEFAULT/mensaje/msg.txt" ]; then
  cp -a "$DEFAULT/mensaje/." /app/data/mensaje/
fi

if [ ! -f "/app/data/$CSV_NAME" ] && [ -f "$DEFAULT/$CSV_NAME" ]; then
  cp -a "$DEFAULT/$CSV_NAME" "/app/data/$CSV_NAME"
fi

exec "$@"
