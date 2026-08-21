# 国内方案：基础镜像走 DaoCloud 加速源，避免 Docker Hub 拉取超时。
# 如果该源不可用，可换成：docker.1ms.run/library/python:3.13-slim
#                        docker.1panel.live/library/python:3.13-slim
#                        hub.rat.dev/library/python:3.13-slim
FROM docker.m.daocloud.io/library/python:3.13-slim

WORKDIR /app

COPY requirements.txt .

# 国内方案：依次尝试 清华 -> 阿里 -> 腾讯 -> 官方 PyPI，
# 任一成功即继续构建，避免单个镜像源不稳定导致失败。
RUN pip install --no-cache-dir --timeout 30 --retries 3 \
        -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt \
    || pip install --no-cache-dir --timeout 30 --retries 3 \
        -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt \
    || pip install --no-cache-dir --timeout 30 --retries 3 \
        -i https://mirrors.cloud.tencent.com/pypi/simple/ -r requirements.txt \
    || pip install --no-cache-dir --timeout 30 --retries 3 \
        -r requirements.txt

COPY app ./app
COPY channels.json ./channels.json

ENV MIGU_HOST=0.0.0.0 \
    MIGU_PORT=8090 \
    PYTHONUNBUFFERED=1

EXPOSE 8090

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
