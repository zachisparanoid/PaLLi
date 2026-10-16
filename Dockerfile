# PaLLi in a container (stranger-proofing week 1, task 5).
#
# This image is the runtime, not the data. PaLLi's whole state — her memory, her
# identity files, your keys — lives in three places that must be MOUNTED, or
# every restart is a new PaLLi with no past:
#
#   docker run --rm -it \
#     -p 127.0.0.1:8000:8000 \
#     -v "$PWD/data:/app/data" \
#     -v "$PWD/identity:/app/identity" \
#     -v "$PWD/.env.local:/app/.env.local:ro" \
#     palli python main.py --preflight-only
#
# Run --preflight-only FIRST, always. It starts nothing. It writes exactly one
# thing, and only on a fresh clone with no config yet: data/palli.yaml, seeded
# once from data/palli.example.yaml. Then it prints a numbered punch list of
# every key that is missing and the file it belongs in. Once that exits 0, drop
# the flag and she boots.
#
# The published port is bound to 127.0.0.1 above on purpose: with
# LOCAL_DASHBOARD_NO_AUTH=true there is no login, and the chat endpoint can
# reach a shell tool. Publishing it on 0.0.0.0 hands that to the network.

FROM python:3.13-slim

# ffmpeg: soundfile and ffmpeg-python shell out to it for every audio path.
# git: some dependencies read their own version from a checkout at import time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies before the source, so editing code does not re-resolve 120 pins.
#
# torch FIRST, from the CPU index: the default wheel is a ~2.5 GB CUDA build
# that a container without a GPU cannot use. requirements.lock carries no
# nvidia-*/cuda-*/triton pins and names torch by bare version, so this wheel
# satisfies it.
COPY requirements.lock ./
RUN pip install --no-cache-dir torch==2.12.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.lock

COPY . /app

# The console script, so `docker exec … palli_ctl brain` works and every "next
# move" line PaLLi prints is a command that runs in here.
RUN pip install --no-cache-dir --no-deps -e .

# Unbuffered, or nothing she prints reaches `docker logs` until she exits.
ENV PYTHONUNBUFFERED=1

# PALLI_PREFLIGHT_OFFLINE is deliberately NOT set. It skips the live check of
# your provider key, which is exactly the check a first run needs. CI sets it;
# a real deployment should not.

EXPOSE 8000

CMD ["python", "main.py"]
