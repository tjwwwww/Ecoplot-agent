# EcoPlot Agent Git 部署说明

建议项目名：

- 中文名：生态样地调查智能体
- 英文名：EcoPlot Agent
- 页面短名：EcoPlot

这个名字比“林木智能助手”更适合当前定位：以样地调查为核心，同时后续可以扩展到森林、草地、湿地等生态监测场景。

## 一、本地首次提交

在 Windows PowerShell 中进入项目目录：

```powershell
cd "E:\Project_Participate\东盟人工智能创新大赛\code\ForestryAgent"
```

查看状态：

```powershell
git status
```

添加文件：

```powershell
git add .
```

提交：

```powershell
git commit -m "initial ecoplot agent deployment"
```

## 二、创建远程仓库

推荐用 Gitee 或 GitHub 私有仓库。

仓库名建议：

```text
ecoplot-agent
```

创建仓库后，复制仓库地址，例如：

```text
https://gitee.com/你的用户名/ecoplot-agent.git
```

或：

```text
git@github.com:你的用户名/ecoplot-agent.git
```

## 三、绑定远程仓库并推送

HTTPS 示例：

```powershell
git remote add origin https://gitee.com/你的用户名/ecoplot-agent.git
git branch -M main
git push -u origin main
```

如果已经绑定过 origin，改地址：

```powershell
git remote set-url origin https://gitee.com/你的用户名/ecoplot-agent.git
git push -u origin main
```

## 四、服务器首次拉取

登录阿里云 ECS：

```powershell
ssh root@8.141.113.44
```

服务器上执行：

```bash
cd /root
git clone https://gitee.com/你的用户名/ecoplot-agent.git ForestryAgent
cd ForestryAgent
```

## 五、服务器配置环境

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv nodejs npm unzip curl
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
chmod +x start_api.sh start_h5.sh
```

`.env` 不要提交到 Git。需要手动上传或在服务器创建：

```bash
nano .env
```

第一版不部署 Neo4j，建议写：

```env
ENABLE_KG=false
```

其他 API Key、数据库路径按你的本地 `.env` 配置。

## 六、启动服务

测试启动：

```bash
./start_api.sh
```

另开一个 SSH：

```bash
cd /root/ForestryAgent
./start_h5.sh
```

访问：

```text
http://8.141.113.44:5173/
```

## 七、使用 PM2 后台运行

```bash
sudo npm install -g pm2
cd /root/ForestryAgent
pm2 start ./start_api.sh --name ecoplot-api
pm2 start ./start_h5.sh --name ecoplot-h5
pm2 save
pm2 startup
```

查看日志：

```bash
pm2 logs ecoplot-api
pm2 logs ecoplot-h5
```

## 八、以后每次更新代码

本地：

```powershell
git add .
git commit -m "update ecoplot agent"
git push
```

服务器：

```bash
cd /root/ForestryAgent
git pull
pm2 restart ecoplot-api ecoplot-h5
```

## 九、数据文件策略

不建议把真实调查数据、`.env`、大图表输出提交到 Git。

推荐：

- 代码、本体、工具、H5 前端：走 Git。
- `.env`：服务器手动配置。
- 数据库文件：小测试库可临时上传；真实数据建议单独 `scp` 上传。
- `reports/`、`visualizations/`：服务器运行时自动生成。
