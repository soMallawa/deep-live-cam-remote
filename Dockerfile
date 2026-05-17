FROM nvidia/cuda:12.4.1-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility,video
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    ffmpeg \
    git \
    libgl1 \
    libglib2.0-0 \
    software-properties-common \
    wget \
    xz-utils \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-dev \
    python3.11-venv \
    && rm -rf /var/lib/apt/lists/*

RUN wget -qO /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py \
    && python3.11 /tmp/get-pip.py \
    && rm -f /tmp/get-pip.py

RUN wget -qO /tmp/mediamtx.tar.gz \
    "$(wget -qO- https://api.github.com/repos/bluenviron/mediamtx/releases/latest \
      | python3.11 -c "import json,sys; data=json.load(sys.stdin); print(next(a['browser_download_url'] for a in data['assets'] if 'linux_amd64.tar.gz' in a['name']))")" \
    && tar -xzf /tmp/mediamtx.tar.gz -C /tmp mediamtx \
    && mv /tmp/mediamtx /usr/local/bin/mediamtx \
    && chmod +x /usr/local/bin/mediamtx \
    && rm -f /tmp/mediamtx.tar.gz

COPY requirements.txt /app/requirements.txt
RUN python3.11 -m pip install --no-cache-dir --upgrade pip \
    && python3.11 -m pip install --no-cache-dir -r /app/requirements.txt

RUN git clone --depth 1 https://github.com/hacksider/Deep-Live-Cam.git /app/Deep-Live-Cam \
    && if [ -f /app/Deep-Live-Cam/requirements.txt ]; then \
      grep -Ev '^(opencv-python|onnxruntime-gpu|onnxruntime-silicon)' /app/Deep-Live-Cam/requirements.txt > /tmp/dlc-requirements.txt; \
      python3.11 -m pip install --no-cache-dir -r /tmp/dlc-requirements.txt; \
      python3.11 -m pip install --no-cache-dir -r /app/requirements.txt; \
    fi

COPY . /app
RUN chmod +x /app/entrypoint.sh /app/scripts/stream-in.sh

EXPOSE 8080 8889 8554 8189/udp

ENTRYPOINT ["/app/entrypoint.sh"]
