# ForestryAgent 阿里云 ECS 部署说明

服务器：Ubuntu 22.04 64 位，2 核 4GiB。

## 1. 安全组

在阿里云控制台放行：

- 22：SSH
- 5173：H5 前端访问
- 8000：后端测试用。正式使用时可关闭公网 8000，只保留 5173。

## 2. 安装基础环境

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv nodejs npm unzip curl
```

检查版本：

```bash
python3 --version
node -v
npm -v
```

## 3. 上传项目

本地电脑执行：

```bash
scp -r ForestryAgent root@8.141.113.44:/root/
```

如果你的项目路径有中文或空格，建议先压缩再上传：

```bash
zip -r ForestryAgent.zip ForestryAgent
scp ForestryAgent.zip root@8.141.113.44:/root/
ssh root@8.141.113.44
cd /root
unzip ForestryAgent.zip
```

## 4. 配置 Python 后端

```bash
cd /root/ForestryAgent
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

如果运行时报缺包，根据错误继续安装，例如：

```bash
pip install pandas numpy pydantic python-dotenv pyyaml openai neo4j
```

## 5. 配置 .env

确保服务器项目根目录有 `.env`，至少包含你的模型 API Key、数据库路径等。

第一版如果不部署 Neo4j，建议加：

```env
ENABLE_KG=false
```

不要把 `.env` 发给别人或提交到公开仓库。

## 6. 测试启动后端

```bash
cd /root/ForestryAgent
chmod +x start_api.sh start_h5.sh
./start_api.sh
```

浏览器访问：

```text
http://8.141.113.44:8000/api/health
```

能返回 JSON，说明后端正常。

## 7. 测试启动 H5

新开一个 SSH 窗口：

```bash
cd /root/ForestryAgent
./start_h5.sh
```

手机或电脑访问：

```text
http://8.141.113.44:5173/
```

## 8. 后台运行 PM2

安装 PM2：

```bash
sudo npm install -g pm2
```

启动服务：

```bash
cd /root/ForestryAgent
pm2 start ./start_api.sh --name forestry-api
pm2 start ./start_h5.sh --name forestry-h5
pm2 save
pm2 startup
```

查看日志：

```bash
pm2 logs forestry-api
pm2 logs forestry-h5
```

重启：

```bash
pm2 restart forestry-api forestry-h5
```

## 9. 访问地址

前端：

```text
http://8.141.113.44:5173/
```

后端健康检查：

```text
http://8.141.113.44:8000/api/health
```

## 10. 推荐第一版部署策略

第一版先不部署 Neo4j：

- SQLite / 本体 YAML / 工具函数先跑稳；
- H5 通过 `/api` 和 `/visualizations` 代理到后端；
- 后端和前端都在同一台 ECS 上；
- 如果后续要上域名和 HTTPS，再加 Nginx 反向代理。
