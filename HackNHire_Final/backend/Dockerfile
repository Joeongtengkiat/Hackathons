# Runs the backend's programs in a container. The database stays on Neon.
# From the HackNHire folder (the image reads the one requirements.txt there):
#
#   docker build -f backend/Dockerfile -t eudr-storage .
#   docker run --rm -v "$(pwd)/backend:/project" eudr-storage database/test_connection.py
#   docker run --rm -v "$(pwd)/backend:/project" -v "D:/eudr/inputs:/inputs" eudr-storage pipeline.py \
#       --name jambi --data-dir /inputs --bbox 103.0 -2.3 103.5 -1.8 --test-plots 1000 --verify
#   docker run --rm -v "$(pwd)/backend:/project" eudr-storage compare/compare.py layers/jambi --map jambi
#
# "-v .../backend:/project" shares your backend folder with the container, so it reads your .env and
# writes layers/ and the results/ folders back to your computer; the second -v shares your satellite
# layers. (Windows PowerShell: use "${PWD}/backend:/project" instead of "$(pwd)/backend:/project".)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
COPY requirements.txt /tmp/requirements.txt
# everything but the PyQt5 windows (the ingest window and the desktop app don't run in a container)
RUN grep -viE '^[[:space:]]*pyqt' /tmp/requirements.txt > /tmp/requirements-docker.txt \
    && pip install --no-cache-dir -r /tmp/requirements-docker.txt

WORKDIR /project
COPY backend/ /project

ENTRYPOINT ["python"]
CMD ["database/test_connection.py"]
