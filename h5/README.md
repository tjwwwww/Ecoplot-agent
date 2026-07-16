# 林木智能助手 H5 前端框架

这是一个 H5 原型框架。由于后端接口未返回 CORS 头，本地预览建议通过 `server.js` 启动同源代理服务。

## 文件结构

- `index.html`：页面结构，包含对话区、样方图、样木清单、输入栏和底部导航。
- `styles.css`：移动端样式，按原型图实现整体视觉。
- `app.js`：样方 ID 查询、Canvas 样方图绘制、树种筛选、样木清单和详情交互。
- `server.js`：静态资源服务和 `/api` 代理，转发到 `http://10.2.30.15:8000`。

## 本地启动

```bash
node server.js
```

访问：

- 电脑：`http://localhost:5173/`
- 手机同 Wi-Fi：`http://电脑局域网IP:5173/`

## 接口

前端请求：

```txt
/api/subplots/{subplot_id}/trees?sort_by=tree_id&order=asc&offset=0&limit=5000&include_unverified_volume=false
```

本地 `server.js` 会代理到：

```txt
http://10.2.30.15:8000
```

接口返回字段示例：

```js
{
  tree_id: "QSL01010001",
  subplot_id: "0101",
  species: "青海云杉",
  tree_dbh_cm: 16.3,
  tree_height_m: 16,
  tree_x_m: 4110560.791,
  tree_y_m: 535332.916,
  crown_width_mean_m: 3,
  crown_base_height_m: 3,
  health_status: "健康"
}
```

当前样方图按 `species` 动态分配颜色，健康状态在点位描边、样木清单和样木详情中体现。
